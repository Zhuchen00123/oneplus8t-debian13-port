/* SPDX-License-Identifier: Apache-2.0 */
/* Own TLS workload for the external-attach tool (tls-attach).
 *
 * One single-threaded process, one in-memory BIO pair, a runtime-generated
 * self-signed EC P-256 certificate, no socket, no port, no file write, no
 * SSLKEYLOGFILE, no system trust store.
 *
 * Script, in order, so the probe sees every shape it must survive:
 *   1. SSL_write 512   - a realistic HTTP request head (method/path/query/host)
 *   2. SSL_write 384   - a second, shorter fragment (fragmentation)
 *   3. SSL_write 8192  - declared above the 4 KiB sample cap (truncation)
 *   4. SSL_write -1    - declared length below zero (must never be read)
 *   5. server writes 300 bytes, client SSL_read -> response plaintext captured
 *      on the return probe
 *   6. a nested SSL_write issued from a BIO write callback of the *outer* call,
 *      i.e. a real entry/entry/exit/exit sequence in one thread (reentrancy)
 *
 * stdout: one READY line, then one DONE line. argv[1] is the delay in
 * milliseconds before the script runs (default 1500), so the collector has a
 * stable window to attach.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <errno.h>
#include <openssl/bio.h>
#include <openssl/err.h>
#include <openssl/evp.h>
#include <openssl/ssl.h>
#include <openssl/x509.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static int g_ret[5];

static void die(const char *why) {
    fprintf(stderr, "tls-attach-fixture: %s\n", why);
    _exit(70);
}

static void sleep_ms(long ms) {
    struct timespec ts;
    ts.tv_sec = ms / 1000;
    ts.tv_nsec = (ms % 1000) * 1000000L;
    while (nanosleep(&ts, &ts) == -1 && errno == EINTR) {
    }
}

static void drain_peer(SSL *peer) {
    unsigned char buffer[16384];
    int guard = 0;
    while (guard++ < 64) {
        int got = SSL_read(peer, buffer, sizeof(buffer));
        if (got > 0) {
            continue;
        }
        if (SSL_get_error(peer, got) == SSL_ERROR_WANT_READ) {
            break;
        }
        break;
    }
}

/* The BIO pair is not a socket: both handshakes have to be pumped by hand. */
static int drive_handshake(SSL *client, SSL *server) {
    int client_done = 0;
    int server_done = 0;
    int round;
    for (round = 0; round < 256 && !(client_done && server_done); round++) {
        int index;
        for (index = 0; index < 2; index++) {
            SSL *ssl = (index == 0) ? client : server;
            int *done = (index == 0) ? &client_done : &server_done;
            int rc;
            if (*done) {
                continue;
            }
            rc = SSL_do_handshake(ssl);
            if (rc == 1) {
                *done = 1;
                continue;
            }
            {
                int err = SSL_get_error(ssl, rc);
                if (err != SSL_ERROR_WANT_READ && err != SSL_ERROR_WANT_WRITE) {
                    return -1;
                }
            }
        }
    }
    if (!client_done || !server_done) {
        return -1;
    }
    return 0;
}

/* ---- nested write: a BIO that issues one SSL_write from inside the outer
 * SSL_write, giving the probe an entry/entry/exit/exit sequence ------------ */
struct nest_state {
    BIO *next;
    SSL *inner;
    int fired;
};

static int nest_write(BIO *bio, const char *buf, int len) {
    struct nest_state *state = (struct nest_state *)BIO_get_data(bio);
    if (state != NULL && !state->fired && state->inner != NULL) {
        state->fired = 1;
        (void)SSL_write(state->inner, (const void *)"nested-request", 14);
    }
    if (state == NULL || state->next == NULL) {
        return -1;
    }
    return BIO_write(state->next, buf, len);
}

static long nest_ctrl(BIO *bio, int cmd, long num, void *ptr) {
    struct nest_state *state = (struct nest_state *)BIO_get_data(bio);
    if (state == NULL || state->next == NULL) {
        return 0;
    }
    if (cmd == BIO_CTRL_FLUSH) {
        return BIO_ctrl(state->next, BIO_CTRL_FLUSH, num, ptr);
    }
    if (cmd == BIO_CTRL_PENDING) {
        return BIO_ctrl_pending(state->next);
    }
    return 0;
}

static int nest_create(BIO *bio) {
    BIO_set_init(bio, 1);
    BIO_set_data(bio, NULL);
    return 1;
}

static int nest_destroy(BIO *bio) {
    (void)bio;
    return 1;
}

static SSL_CTX *make_ctx(int server, EVP_PKEY *key, X509 *cert) {
    SSL_CTX *ctx = SSL_CTX_new(server ? TLS_server_method() : TLS_client_method());
    if (ctx == NULL) {
        die("SSL_CTX_new");
    }
    /* TLS 1.2 only: a deterministic record layout keeps the probe script stable. */
    if (SSL_CTX_set_min_proto_version(ctx, TLS1_2_VERSION) != 1) die("min_proto");
    if (SSL_CTX_set_max_proto_version(ctx, TLS1_2_VERSION) != 1) die("max_proto");
    SSL_CTX_set_session_cache_mode(ctx, SSL_SESS_CACHE_OFF);
    if (server) {
        if (SSL_CTX_use_certificate(ctx, cert) != 1) die("use_certificate");
        if (SSL_CTX_use_PrivateKey(ctx, key) != 1) die("use_PrivateKey");
        if (SSL_CTX_check_private_key(ctx) != 1) die("check_private_key");
    } else {
        /* The client trusts exactly one certificate: the one generated above.
         * No default verify paths, no VERIFY_NONE, no system CA store. */
        X509_STORE *store;
        SSL_CTX_set_verify(ctx, SSL_VERIFY_PEER, NULL);
        store = SSL_CTX_get_cert_store(ctx);
        if (store == NULL) die("client store missing");
        if (!X509_STORE_add_cert(store, cert)) die("pin fixture certificate");
    }
    return ctx;
}

int main(int argc, char **argv) {
    long delay = 1500;
    unsigned char request[8192];
    unsigned char response[300];
    EVP_PKEY *key = NULL;
    X509 *cert = NULL;
    EVP_PKEY_CTX *pkey_ctx = NULL;
    X509_NAME *name;
    BIO *client_bio = NULL;
    BIO *server_bio = NULL;
    SSL_CTX *client_ctx = NULL;
    SSL_CTX *server_ctx = NULL;
    SSL_CTX *inner_ctx = NULL;
    SSL *client = NULL;
    SSL *server = NULL;
    SSL *inner = NULL;
    BIO_METHOD *nest_method = NULL;
    BIO *nest = NULL;
    struct nest_state *nest_state = NULL;
    int rc;
    int nested_result;

    if (argc > 1) {
        delay = strtol(argv[1], NULL, 10);
        if (delay < 0 || delay > 60000) die("delay out of range");
    }
    pkey_ctx = EVP_PKEY_CTX_new_id(EVP_PKEY_EC, NULL);
    if (pkey_ctx == NULL) die("EVP_PKEY_CTX_new_id");
    if (EVP_PKEY_keygen_init(pkey_ctx) <= 0) die("keygen_init");
    if (EVP_PKEY_CTX_set_ec_paramgen_curve_nid(pkey_ctx, NID_X9_62_prime256v1) <= 0) {
        die("paramgen_curve");
    }
    if (EVP_PKEY_keygen(pkey_ctx, &key) <= 0) die("keygen");
    cert = X509_new();
    if (cert == NULL) die("X509_new");
    X509_set_version(cert, 2);
    ASN1_INTEGER_set(X509_get_serialNumber(cert), 1);
    X509_gmtime_adj(X509_get_notBefore(cert), -60);
    X509_gmtime_adj(X509_get_notAfter(cert), 3600);
    name = X509_get_subject_name(cert);
    X509_NAME_add_entry_by_txt(name, "CN", MBSTRING_ASC, (const unsigned char *)"ta-fixture", -1, -1, 0);
    X509_set_issuer_name(cert, name);
    if (X509_set_pubkey(cert, key) != 1) die("set_pubkey");
    if (X509_sign(cert, key, EVP_sha256()) == 0) die("sign cert");

    client_ctx = make_ctx(0, key, cert);
    server_ctx = make_ctx(1, key, cert);
    if (BIO_new_bio_pair(&client_bio, 0, &server_bio, 0) != 1) die("bio pair");
    client = SSL_new(client_ctx);
    server = SSL_new(server_ctx);
    if (client == NULL || server == NULL) die("SSL_new");
    SSL_set_bio(client, client_bio, client_bio);
    SSL_set_bio(server, server_bio, server_bio);
    BIO_up_ref(client_bio);
    BIO_up_ref(server_bio);
    SSL_set_connect_state(client);
    SSL_set_accept_state(server);
    if (drive_handshake(client, server) != 0) die("handshake");

    /* Inner object: never handshaken, so its SSL_write fails fast, but the call
     * is real and gives the probe a nested enter/exit pair. */
    inner_ctx = make_ctx(0, key, cert);
    inner = SSL_new(inner_ctx);
    if (inner == NULL) die("SSL_new inner");
    nest_method = BIO_meth_new(BIO_get_new_index() | BIO_TYPE_SOURCE_SINK, "ta-nest");
    if (nest_method == NULL) die("BIO_meth_new");
    if (BIO_meth_set_write(nest_method, nest_write) != 1) die("meth write");
    if (BIO_meth_set_ctrl(nest_method, nest_ctrl) != 1) die("meth ctrl");
    if (BIO_meth_set_create(nest_method, nest_create) != 1) die("meth create");
    if (BIO_meth_set_destroy(nest_method, nest_destroy) != 1) die("meth destroy");
    nest_state = (struct nest_state *)calloc(1, sizeof(*nest_state));
    if (nest_state == NULL) die("calloc");
    nest_state->inner = inner;
    nest = BIO_new(nest_method);
    if (nest == NULL) die("BIO_new nest");
    /* Writes now go through the nest BIO, which forwards into the client's own
     * pair half; reads keep using the pair half directly so the session stays
     * connected. */
    nest_state->next = client_bio;
    BIO_up_ref(client_bio);
    BIO_set_data(nest, nest_state);
    BIO_set_init(nest, 1);
    /* rbio stays the pair half (reads), wbio becomes the nest BIO (writes). */
    SSL_set_bio(client, client_bio, nest);

    /* Realistic request head so the redacted structure is worth reading. */
    memset(request, 'a', sizeof(request));
    {
        const char *head =
            "POST /api/v1/runs/987654/points?token=0123456789abcdef&lat=39.9&lng=116.3 HTTP/1.1\r\n"
            "Host: ta.example.com:443\r\n"
            "User-Agent: ta-fixture\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: 4\r\n"
            "\r\n"
            "{}";
        size_t head_len = strlen(head);
        memcpy(request, head, head_len < sizeof(request) ? head_len : sizeof(request));
    }
    memset(response, 'r', sizeof(response));
    {
        const char *head =
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: 260\r\n"
            "Set-Cookie: sid=deadbeef\r\n"
            "\r\n";
        size_t head_len = strlen(head);
        memcpy(response, head, head_len < sizeof(response) ? head_len : sizeof(response));
    }

    printf("READY\n");
    fflush(stdout);
    sleep_ms(delay);

    g_ret[0] = SSL_write(client, request, 512);
    drain_peer(server);
    g_ret[1] = SSL_write(client, request, 384);
    drain_peer(server);
    g_ret[2] = SSL_write(client, request, 8192);
    drain_peer(server);
    ERR_clear_error();
    g_ret[3] = SSL_write(client, request, -1);
    ERR_clear_error();
    rc = SSL_write(server, response, (int)sizeof(response));
    if (rc != (int)sizeof(response)) die("server write");
    g_ret[4] = SSL_read(client, request, 4096);
    nested_result = nest_state->fired;

    printf("DONE ret=%d,%d,%d,%d,%d nested=%d\n", g_ret[0], g_ret[1], g_ret[2], g_ret[3], g_ret[4], nested_result);
    fflush(stdout);
    sleep_ms(300);
    return 0;
}
