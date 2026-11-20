/* SPDX-License-Identifier: Apache-2.0 */
/* Own TLS workload for the kebab-observer TLS fixture.
 *
 * One single-threaded process, one in-memory BIO pair, two SSL objects
 * (client + server) and a runtime-generated self-signed EC P-256 certificate
 * that never leaves private memory. No socket, no port, no file write, no
 * SSLKEYLOGFILE, no system trust store: the client context trusts only the
 * certificate this process just generated.
 *
 * Protocol (fixed, shared with the Rust collector):
 *   stdin  : one byte 'G' starts the script; EOF at any time aborts cleanly.
 *   stdout : exactly one READY line, then exactly one DONE line.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <openssl/bio.h>
#include <openssl/err.h>
#include <openssl/evp.h>
#include <openssl/ssl.h>
#include <openssl/x509.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/sysmacros.h>
#include <time.h>
#include <unistd.h>

#define SCRIPT_ID "tlsfix-v1"
#define FORMULA_ID "tlsfix-payload-v1"
#define STEP_COUNT 4
#define PEER_BUF 16384
#define SCRIPT_ALARM_SECONDS 8

/* Declared lengths in fixed script order; direction "c2s" unless noted. */
static const int k_step_len[STEP_COUNT] = {512, 384, 8192, -1};
static const int k_step_is_c2s[STEP_COUNT] = {1, 0, 1, 1};
static const char *k_step_dir[STEP_COUNT] = {"c2s", "s2c", "c2s", "c2s"};

static int g_peer_received[STEP_COUNT];
static int g_peer_match[STEP_COUNT];
static int g_ret[STEP_COUNT];

static void die(const char *why) {
    fprintf(stderr, "tls-fixture: %s\n", why);
    _exit(70);
}

/* Payload formula, shared with the collector:
 *   b[i] = (i < 8) ? "TLSFIX1"[i] : ((0x54 + 7*i) & 0xFF)
 * Index 7 is the C string terminator, i.e. 0. */
static void fill_payload(unsigned char *buf, size_t len) {
    static const char magic[8] = {'T', 'L', 'S', 'F', 'I', 'X', '1', 0};
    size_t i;
    for (i = 0; i < len; i++) {
        if (i < 8) {
            buf[i] = (unsigned char)magic[i];
        } else {
            buf[i] = (unsigned char)((0x54u + 7u * (unsigned int)i) & 0xFFu);
        }
    }
}

static int count_fds(int *sockets) {
    DIR *dir;
    struct dirent *ent;
    int total = 0;
    *sockets = 0;
    dir = opendir("/proc/self/fd");
    if (dir == NULL) return -1;
    while ((ent = readdir(dir)) != NULL) {
        char link[320];
        char target[512];
        ssize_t n;
        int used;
        if (ent->d_name[0] == '.') continue;
        total++;
        used = snprintf(link, sizeof(link), "/proc/self/fd/%s", ent->d_name);
        if (used < 0 || (size_t)used >= sizeof(link)) continue;
        n = readlink(link, target, sizeof(target) - 1);
        if (n < 0) continue;
        target[n] = '\0';
        if (strncmp(target, "socket:", 7) == 0) (*sockets)++;
    }
    closedir(dir);
    return total - 1; /* the DIR* itself */
}

/* The collector owns the descriptor contract: only 0/1/2 may exist when READY
 * is printed.  Everything above 2 is inherited (or opened by the loader) and is
 * closed here, before any other work, using the kernel's own descriptor list. */
static int close_inherited_fds(int *closed_out) {
    DIR *dir;
    struct dirent *ent;
    int fds[256];
    int count = 0;
    int closed = 0;
    int i;

    *closed_out = 0;
    dir = opendir("/proc/self/fd");
    if (dir == NULL) return -1;
    while ((ent = readdir(dir)) != NULL) {
        char *end = NULL;
        long value;
        if (ent->d_name[0] == '.') continue;
        value = strtol(ent->d_name, &end, 10);
        if (end == NULL || *end != '\0') continue;
        if (value <= 2 || value > (1 << 20)) continue;
        if (count >= (int)(sizeof(fds) / sizeof(fds[0]))) continue;
        fds[count++] = (int)value;
    }
    closedir(dir);
    for (i = 0; i < count; i++) {
        if (close(fds[i]) == 0) closed++;
    }
    /* Sweep the low range too, so nothing opened during the enumeration can
     * survive.  Closing an already closed descriptor only yields EBADF. */
    for (i = 3; i < 256; i++) {
        if (close(i) == 0) closed++;
    }
    *closed_out = closed;
    return 0;
}

static void dump_fds(const char *tag) {
    DIR *dir = opendir("/proc/self/fd");
    struct dirent *ent;
    if (dir == NULL) return;
    fprintf(stderr, "tls-fixture: %s fds:", tag);
    while ((ent = readdir(dir)) != NULL) {
        char link[320];
        char target[512];
        ssize_t n;
        int used;
        if (ent->d_name[0] == '.') continue;
        used = snprintf(link, sizeof(link), "/proc/self/fd/%s", ent->d_name);
        if (used < 0 || (size_t)used >= sizeof(link)) continue;
        n = readlink(link, target, sizeof(target) - 1);
        if (n < 0) continue;
        target[n] = '\0';
        fprintf(stderr, " %s->%s", ent->d_name, target);
    }
    fprintf(stderr, "\n");
    closedir(dir);
}

/* Locate the mapped libssl through /proc/self/maps: executable mapping only. */
static int find_libssl(char *path, size_t path_len, unsigned long *dev_out,
                       unsigned long *ino_out, unsigned long long *size_out) {
    FILE *maps;
    char line[1024];
    int found = 0;
    maps = fopen("/proc/self/maps", "re");
    if (maps == NULL) return -1;
    while (fgets(line, sizeof(line), maps) != NULL) {
        unsigned long start = 0, end = 0, off = 0, inode = 0;
        unsigned int maj = 0, min = 0;
        char perms[8];
        char rest[768];
        int consumed = 0;
        if (sscanf(line, "%lx-%lx %7s %lx %x:%x %lu %n", &start, &end, perms, &off,
                   &maj, &min, &inode, &consumed) != 7) {
            continue;
        }
        if (perms[2] != 'x') continue;
        if (consumed <= 0 || (size_t)consumed >= sizeof(line)) continue;
        snprintf(rest, sizeof(rest), "%s", line + consumed);
        {
            char *nl = strchr(rest, '\n');
            if (nl != NULL) *nl = '\0';
        }
        if (strstr(rest, "libssl.so") == NULL) continue;
        if (strlen(rest) + 1 > path_len) return -1;
        memcpy(path, rest, strlen(rest) + 1);
        *dev_out = (unsigned long)makedev((unsigned int)maj, (unsigned int)min);
        *ino_out = inode;
        found = 1;
        break;
    }
    fclose(maps);
    if (!found) return -1;
    {
        struct stat st;
        if (stat(path, &st) != 0) return -1;
        *size_out = (unsigned long long)st.st_size;
        if ((unsigned long)st.st_dev != *dev_out || (unsigned long)st.st_ino != *ino_out) return -1;
    }
    return 0;
}

static int make_certificate(EVP_PKEY **key_out, X509 **cert_out) {
    EVP_PKEY *key;
    X509 *cert;
    X509_NAME *name;
    int ok = 0;

    key = EVP_PKEY_Q_keygen(NULL, NULL, "EC", "P-256");
    if (key == NULL) return -1;
    cert = X509_new();
    if (cert == NULL) {
        EVP_PKEY_free(key);
        return -1;
    }
    if (!X509_set_version(cert, 2)) goto done;
    {
        ASN1_INTEGER *serial = X509_get_serialNumber(cert);
        if (serial == NULL) goto done;
        if (!ASN1_INTEGER_set(serial, 0x6b544631)) goto done;
    }
    if (!X509_gmtime_adj(X509_getm_notBefore(cert), -600)) goto done;
    if (!X509_gmtime_adj(X509_getm_notAfter(cert), 3600)) goto done;
    if (!X509_set_pubkey(cert, key)) goto done;
    name = X509_get_subject_name(cert);
    if (name == NULL) goto done;
    if (!X509_NAME_add_entry_by_txt(name, "CN", MBSTRING_ASC,
                                    (const unsigned char *)"kebab-tls-fixture", -1, -1, 0)) {
        goto done;
    }
    if (!X509_NAME_add_entry_by_txt(name, "O", MBSTRING_ASC,
                                    (const unsigned char *)"kebab-observer-fixture", -1, -1, 0)) {
        goto done;
    }
    if (!X509_set_issuer_name(cert, name)) goto done;
    if (!X509_sign(cert, key, EVP_sha256())) goto done;
    *key_out = key;
    *cert_out = cert;
    ok = 1;
done:
    if (!ok) {
        X509_free(cert);
        EVP_PKEY_free(key);
        return -1;
    }
    return 0;
}

static int drive_handshake(SSL *client, SSL *server) {
    int client_done = 0;
    int server_done = 0;
    int round;
    for (round = 0; round < 256 && !(client_done && server_done); round++) {
        int i;
        for (i = 0; i < 2; i++) {
            SSL *s = (i == 0) ? client : server;
            int *done = (i == 0) ? &client_done : &server_done;
            int rc;
            if (*done) continue;
            rc = SSL_do_handshake(s);
            if (rc == 1) {
                *done = 1;
                continue;
            }
            {
                int err = SSL_get_error(s, rc);
                if (err != SSL_ERROR_WANT_READ && err != SSL_ERROR_WANT_WRITE) return -1;
            }
        }
    }
    if (!client_done || !server_done) return -1;
    if (SSL_is_init_finished(client) != 1 || SSL_is_init_finished(server) != 1) return -1;
    return 0;
}

static int read_exact(SSL *r, unsigned char *buf, int len) {
    int got = 0;
    int guard = 0;
    while (got < len && guard < 100000) {
        int n = SSL_read(r, buf + got, len - got);
        guard++;
        if (n <= 0) {
            int err = SSL_get_error(r, n);
            if (err == SSL_ERROR_WANT_READ || err == SSL_ERROR_WANT_WRITE) continue;
            return -2;
        }
        got += n;
    }
    return (got == len) ? got : -1;
}

static int compare_payload(const unsigned char *buf, int len) {
    int i;
    for (i = 0; i < len; i++) {
        unsigned char want;
        if ((size_t)i < 8) {
            static const char magic[8] = {'T', 'L', 'S', 'F', 'I', 'X', '1', 0};
            want = (unsigned char)magic[i];
        } else {
            want = (unsigned char)((0x54u + 7u * (unsigned int)i) & 0xFFu);
        }
        if (buf[i] != want) return 0;
    }
    return 1;
}

/* Between steps: stdin EOF (collector gone) or an unexpected byte ends the run
 * immediately instead of blocking. */
static int stdin_aborted(void) {
    struct pollfd p;
    char c;
    ssize_t n;
    p.fd = STDIN_FILENO;
    p.events = POLLIN;
    p.revents = 0;
    if (poll(&p, 1, 0) <= 0) return 0;
    if ((p.revents & (POLLIN | POLLHUP | POLLERR)) == 0) return 0;
    n = read(STDIN_FILENO, &c, 1);
    if (n <= 0) return 1;
    return 1;
}

int main(void) {
    EVP_PKEY *key = NULL;
    X509 *cert = NULL;
    SSL_CTX *client_ctx = NULL;
    SSL_CTX *server_ctx = NULL;
    SSL *client = NULL;
    SSL *server = NULL;
    BIO *client_bio = NULL;
    BIO *server_bio = NULL;
    unsigned char *payload = NULL;
    unsigned char *peer = NULL;
    char readied[2048];
    char doneline[4096];
    char libssl_path[512];
    unsigned long libssl_dev = 0, libssl_ino = 0;
    unsigned long long libssl_size = 0;
    struct stat ns_st;
    struct statfs ns_fs;
    int sockets = 0;
    int fds;
    int closed_fds = 0;
    int step;
    int c2s_total = 0;
    int s2c_total = 0;
    int c2s_match = 1;
    int s2c_match = 1;
    int failed = 0;
    const char *failure = NULL;

    signal(SIGPIPE, SIG_IGN);
    /* Descriptor contract first: nothing inherited survives past this call. */
    if (close_inherited_fds(&closed_fds) != 0) die("cannot enumerate own fds");

    if (find_libssl(libssl_path, sizeof(libssl_path), &libssl_dev, &libssl_ino,
                    &libssl_size) != 0) {
        die("cannot locate mapped libssl");
    }
    if (stat("/proc/self/ns/pid", &ns_st) != 0) die("cannot stat pid namespace");
    if (statfs("/proc/self/ns/pid", &ns_fs) != 0) die("cannot statfs pid namespace");
    fds = count_fds(&sockets);
    if (fds < 0) die("cannot count own fds");

    if (make_certificate(&key, &cert) != 0) die("cannot build test certificate");

    server_ctx = SSL_CTX_new(TLS_server_method());
    client_ctx = SSL_CTX_new(TLS_client_method());
    if (server_ctx == NULL || client_ctx == NULL) die("cannot create ssl contexts");
    if (!SSL_CTX_set_min_proto_version(server_ctx, TLS1_2_VERSION) ||
        !SSL_CTX_set_max_proto_version(server_ctx, TLS1_2_VERSION) ||
        !SSL_CTX_set_min_proto_version(client_ctx, TLS1_2_VERSION) ||
        !SSL_CTX_set_max_proto_version(client_ctx, TLS1_2_VERSION)) {
        die("cannot pin TLS1.2");
    }
    SSL_CTX_set_session_cache_mode(server_ctx, SSL_SESS_CACHE_OFF);
    SSL_CTX_set_session_cache_mode(client_ctx, SSL_SESS_CACHE_OFF);
    if (!SSL_CTX_use_certificate(server_ctx, cert)) die("server certificate rejected");
    if (!SSL_CTX_use_PrivateKey(server_ctx, key)) die("server key rejected");
    if (!SSL_CTX_check_private_key(server_ctx)) die("server key mismatch");

    /* Client trusts exactly one certificate: the one generated above. No
     * default verify paths, no VERIFY_NONE, no system CA store. */
    SSL_CTX_set_verify(client_ctx, SSL_VERIFY_PEER, NULL);
    {
        X509_STORE *store = SSL_CTX_get_cert_store(client_ctx);
        if (store == NULL) die("client store missing");
        if (!X509_STORE_add_cert(store, cert)) die("cannot pin fixture certificate");
    }

    if (BIO_new_bio_pair(&client_bio, 0, &server_bio, 0) != 1) die("cannot create bio pair");
    client = SSL_new(client_ctx);
    server = SSL_new(server_ctx);
    if (client == NULL || server == NULL) die("cannot create ssl objects");
    BIO_up_ref(client_bio);
    SSL_set_bio(client, client_bio, client_bio);
    BIO_up_ref(server_bio);
    SSL_set_bio(server, server_bio, server_bio);
    SSL_set_connect_state(client);
    SSL_set_accept_state(server);
    if (drive_handshake(client, server) != 0) die("handshake failed");

    payload = malloc(PEER_BUF);
    peer = malloc(PEER_BUF);
    if (payload == NULL || peer == NULL) die("out of memory");

    /* No descriptors other than 0/1/2 may remain when READY is printed.  The
     * collector re-checks this from /proc and fails the session; the workload
     * only reports the numbers and keeps a diagnostic dump on stderr. */
    fds = count_fds(&sockets);
    if (sockets != 0) {
        dump_fds("pre-ready");
        die("fixture holds a socket descriptor");
    }
    if (fds != 3) dump_fds("pre-ready-non-stdio");

    snprintf(readied, sizeof(readied),
             "{\"record\":\"ready\",\"schema\":1,\"script\":\"%s\","
             "\"payload_formula\":\"%s\",\"tls_version\":\"%s\",\"cipher\":\"%s\","
             "\"pid\":%ld,\"tid\":%ld,\"handshake\":{\"client\":true,\"server\":true},"
             "\"libssl\":{\"path\":\"%s\",\"dev\":%lu,\"ino\":%lu,\"size\":%llu},"
             "\"nsfs\":{\"dev\":%lu,\"ino\":%lu,\"magic\":%llu},"
             "\"open_fds\":%d,\"socket_fds\":%d,\"closed_fds\":%d,"
             "\"fds_only_stdio\":%s}",
             SCRIPT_ID, FORMULA_ID, SSL_get_version(client), SSL_get_cipher_name(client),
             (long)getpid(), (long)getpid(), libssl_path, libssl_dev, libssl_ino,
             libssl_size, (unsigned long)ns_st.st_dev, (unsigned long)ns_st.st_ino,
             (unsigned long long)ns_fs.f_type, fds, sockets, closed_fds,
             (fds == 3 && sockets == 0) ? "true" : "false");
    if (printf("%s\n", readied) < 0) return 71;
    if (fflush(stdout) != 0) return 72;

    {
        char go = 0;
        if (read(STDIN_FILENO, &go, 1) != 1) return 0;
        if (go != 'G') return 0;
    }
    alarm(SCRIPT_ALARM_SECONDS);

    for (step = 0; step < STEP_COUNT && !failed; step++) {
        int declared = k_step_len[step];
        if (stdin_aborted()) return 0;
        if (declared <= 0) {
            int rc = SSL_write(client, payload, declared);
            g_ret[step] = rc;
            g_peer_received[step] = 0;
            g_peer_match[step] = 1;
            if (rc != -1) {
                failed = 1;
                failure = "negative length did not return -1";
            }
            continue;
        }
        fill_payload(payload, (size_t)declared);
        {
            SSL *writer = k_step_is_c2s[step] ? client : server;
            SSL *reader = k_step_is_c2s[step] ? server : client;
            int rc = SSL_write(writer, payload, declared);
            int got;
            g_ret[step] = rc;
            if (rc != declared) {
                failed = 1;
                failure = "SSL_write returned unexpected length";
                break;
            }
            got = read_exact(reader, peer, declared);
            if (got != declared) {
                failed = 1;
                failure = "peer SSL_read did not receive the declared bytes";
                break;
            }
            g_peer_received[step] = got;
            g_peer_match[step] = compare_payload(peer, got);
            if (!g_peer_match[step]) {
                failed = 1;
                failure = "peer bytes do not match the payload formula";
                break;
            }
            if (k_step_is_c2s[step]) {
                c2s_total += got;
                c2s_match = c2s_match && g_peer_match[step];
            } else {
                s2c_total += got;
                s2c_match = s2c_match && g_peer_match[step];
            }
        }
    }
    alarm(0);

    snprintf(doneline, sizeof(doneline),
             "{\"record\":\"done\",\"schema\":1,\"script\":\"%s\",\"failed\":%s,"
             "\"failure\":%s,\"peer\":{\"c2s_received\":%d,\"c2s_match\":%s,"
             "\"s2c_received\":%d,\"s2c_match\":%s},"
             "\"steps\":["
             "{\"index\":1,\"direction\":\"%s\",\"declared\":%d,\"ret\":%d,\"peer_received\":%d,\"peer_match\":%s},"
             "{\"index\":2,\"direction\":\"%s\",\"declared\":%d,\"ret\":%d,\"peer_received\":%d,\"peer_match\":%s},"
             "{\"index\":3,\"direction\":\"%s\",\"declared\":%d,\"ret\":%d,\"peer_received\":%d,\"peer_match\":%s},"
             "{\"index\":4,\"direction\":\"%s\",\"declared\":%d,\"ret\":%d,\"peer_received\":%d,\"peer_match\":%s}"
             "]}",
             SCRIPT_ID, failed ? "true" : "false",
             failure == NULL ? "null" : "non-null",
             c2s_total, c2s_match ? "true" : "false",
             s2c_total, s2c_match ? "true" : "false",
             k_step_dir[0], k_step_len[0], g_ret[0], g_peer_received[0], g_peer_match[0] ? "true" : "false",
             k_step_dir[1], k_step_len[1], g_ret[1], g_peer_received[1], g_peer_match[1] ? "true" : "false",
             k_step_dir[2], k_step_len[2], g_ret[2], g_peer_received[2], g_peer_match[2] ? "true" : "false",
             k_step_dir[3], k_step_len[3], g_ret[3], g_peer_received[3], g_peer_match[3] ? "true" : "false");
    if (printf("%s\n", doneline) < 0) return 73;
    if (fflush(stdout) != 0) return 74;

    /* Stay alive until the collector closes stdin: it re-checks this process
     * right after GO, and that re-check must observe a live workload.  EOF
     * (collector gone) or any byte releases the hold. */
    {
        char hold = 0;
        (void)read(STDIN_FILENO, &hold, 1);
    }

    SSL_shutdown(client);
    SSL_free(client);
    SSL_free(server);
    SSL_CTX_free(client_ctx);
    SSL_CTX_free(server_ctx);
    X509_free(cert);
    EVP_PKEY_free(key);
    free(payload);
    free(peer);
    return failed ? 1 : 0;
}
