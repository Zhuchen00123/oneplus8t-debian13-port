// Self-hashing fixture: proves whether a uprobe is visible from inside the
// target, and gives the TLS collector two ordinary exported symbols to attach
// to.
//
// Why this exists: on arm64 a uprobe replaces the instruction at the probe
// point with brk (arch/arm64/include/asm/uprobes.h: UPROBE_SWBP_INSN =
// BRK64_OPCODE_UPROBES) and the write goes through uprobe_write_opcode() ->
// __replace_page() (kernel/events/uprobes.c), i.e. into a private COW page of
// this very process.  A target that hashes its own .text can therefore see the
// probe.  This fixture does exactly that and reports it, so the claim becomes a
// measurement instead of an assumption.
//
// Output is one JSON object per line:
//   {"record":"before",...}    hash of the window before any probe existed
//   {"record":"detected",...}  first call that saw a different hash
//   {"record":"clean",...}     no difference within the timeout
//   {"record":"signal",...}    stopped by a signal (printed before exit)
//
// Exit codes: 0 in every normal case (including a detection), 1 usage error,
// 2 when the own executable mapping could not be found.

// The fixture is deliberately header-free: it is built twice, once with the
// host compiler and once for static aarch64 with the musl sysroot that rustup
// installs (libraries only, no headers).  Declaring the handful of libc
// symbols used here keeps both builds compiling the exact same source, so the
// x86_64 smoke exercises the code the device runs.
typedef unsigned long size_t;
typedef unsigned long uintptr_t;
typedef unsigned char uint8_t;
typedef unsigned int uint32_t;
typedef unsigned long long uint64_t;
typedef int pid_t;
typedef struct _IO_FILE FILE;

struct timespec {
    long tv_sec;
    long tv_nsec;
};

extern FILE *stderr;
extern FILE *stdout;
extern FILE *fopen(const char *path, const char *mode);
extern int fclose(FILE *stream);
extern char *fgets(char *buf, int size, FILE *stream);
extern int fprintf(FILE *stream, const char *format, ...);
extern int printf(const char *format, ...);
extern int fflush(FILE *stream);
extern int sscanf(const char *text, const char *format, ...);
extern int snprintf(char *buf, size_t size, const char *format, ...);
extern void *memcpy(void *dest, const void *src, size_t len);
extern void *memset(void *dest, int value, size_t len);
extern int memcmp(const void *a, const void *b, size_t len);
extern int strcmp(const char *a, const char *b);
extern char *strchr(const char *text, int needle);
extern long strtol(const char *text, char **end, int base);
extern pid_t getpid(void);
extern int clock_gettime(int clock, struct timespec *ts);
extern int nanosleep(const struct timespec *req, struct timespec *rem);
extern void (*signal(int sig, void (*handler)(int)))(int);

#define NULL ((void *)0)
#define CLOCK_MONOTONIC 1
#define SIGHUP 1
#define SIGINT 2
#define SIGQUIT 3
#define SIGUSR1 10
#define SIGUSR2 12
#define SIGTERM 15

// ---------------------------------------------------------------- SHA-256 ---
struct sha256 {
    uint32_t state[8];
    uint64_t bits;
    uint8_t block[64];
    size_t used;
};

static const uint32_t sha256_k[64] = {
    0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u, 0x3956c25bu, 0x59f111f1u,
    0x923f82a4u, 0xab1c5ed5u, 0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u,
    0x72be5d74u, 0x80deb1feu, 0x9bdc06a7u, 0xc19bf174u, 0xe49b69c1u, 0xefbe4786u,
    0x0fc19dc6u, 0x240ca1ccu, 0x2de92c6fu, 0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau,
    0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u, 0xc6e00bf3u, 0xd5a79147u,
    0x06ca6351u, 0x14292967u, 0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu, 0x53380d13u,
    0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u, 0xa2bfe8a1u, 0xa81a664bu,
    0xc24b8b70u, 0xc76c51a3u, 0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u,
    0x19a4c116u, 0x1e376c08u, 0x2748774cu, 0x34b0bcb5u, 0x391c0cb3u, 0x4ed8aa4au,
    0x5b9cca4fu, 0x682e6ff3u, 0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u,
    0x90befffau, 0xa4506cebu, 0xbef9a3f7u, 0xc67178f2u,
};

static uint32_t ror(uint32_t x, unsigned n) { return (x >> n) | (x << (32 - n)); }

static void sha256_init(struct sha256 *s) {
    static const uint32_t init[8] = {0x6a09e667u, 0xbb67ae85u, 0x3c6ef372u,
                                     0xa54ff53au, 0x510e527fu, 0x9b05688cu,
                                     0x1f83d9abu, 0x5be0cd19u};
    memcpy(s->state, init, sizeof(init));
    s->bits = 0;
    s->used = 0;
}

static void sha256_block(struct sha256 *s, const uint8_t *p) {
    uint32_t w[64];
    for (int i = 0; i < 16; i++) {
        w[i] = ((uint32_t)p[i * 4] << 24) | ((uint32_t)p[i * 4 + 1] << 16) |
               ((uint32_t)p[i * 4 + 2] << 8) | (uint32_t)p[i * 4 + 3];
    }
    for (int i = 16; i < 64; i++) {
        uint32_t s0 = ror(w[i - 15], 7) ^ ror(w[i - 15], 18) ^ (w[i - 15] >> 3);
        uint32_t s1 = ror(w[i - 2], 17) ^ ror(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    uint32_t a = s->state[0], b = s->state[1], c = s->state[2], d = s->state[3];
    uint32_t e = s->state[4], f = s->state[5], g = s->state[6], h = s->state[7];
    for (int i = 0; i < 64; i++) {
        uint32_t s1 = ror(e, 6) ^ ror(e, 11) ^ ror(e, 25);
        uint32_t ch = (e & f) ^ (~e & g);
        uint32_t t1 = h + s1 + ch + sha256_k[i] + w[i];
        uint32_t s0 = ror(a, 2) ^ ror(a, 13) ^ ror(a, 22);
        uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
        uint32_t t2 = s0 + maj;
        h = g;
        g = f;
        f = e;
        e = d + t1;
        d = c;
        c = b;
        b = a;
        a = t1 + t2;
    }
    s->state[0] += a;
    s->state[1] += b;
    s->state[2] += c;
    s->state[3] += d;
    s->state[4] += e;
    s->state[5] += f;
    s->state[6] += g;
    s->state[7] += h;
}

static void sha256_update(struct sha256 *s, const uint8_t *data, size_t len) {
    s->bits += (uint64_t)len * 8;
    while (len > 0) {
        size_t take = 64 - s->used;
        if (take > len) take = len;
        memcpy(s->block + s->used, data, take);
        s->used += take;
        data += take;
        len -= take;
        if (s->used == 64) {
            sha256_block(s, s->block);
            s->used = 0;
        }
    }
}

static void sha256_final(struct sha256 *s, uint8_t out[32]) {
    uint64_t bits = s->bits;
    uint8_t pad = 0x80;
    sha256_update(s, &pad, 1);
    uint8_t zero = 0;
    while (s->used != 56) sha256_update(s, &zero, 1);
    uint8_t len[8];
    for (int i = 0; i < 8; i++) len[i] = (uint8_t)(bits >> (56 - i * 8));
    sha256_update(s, len, 8);
    for (int i = 0; i < 8; i++) {
        out[i * 4] = (uint8_t)(s->state[i] >> 24);
        out[i * 4 + 1] = (uint8_t)(s->state[i] >> 16);
        out[i * 4 + 2] = (uint8_t)(s->state[i] >> 8);
        out[i * 4 + 3] = (uint8_t)s->state[i];
    }
}

// ------------------------------------------------------------- the targets ---
// Ordinary global functions with the libssl signatures, so the collector can
// attach with its normal PATH:SYMBOL:SYMBOL form.  noinline keeps the probe
// points at real function entries instead of an inlined copy.
__attribute__((noinline)) int SSL_write(void *ssl, const void *buf, int num) {
    if (ssl == NULL || buf == NULL || num <= 0) return 0;
    volatile int sink = num + ((const unsigned char *)buf)[0];
    return sink > 0 ? num : 0;
}

__attribute__((noinline)) int SSL_read(void *ssl, void *buf, int num) {
    if (ssl == NULL || buf == NULL || num <= 0) return 0;
    volatile int sink = num;
    ((unsigned char *)buf)[0] = (unsigned char)(sink & 0xff);
    return sink > 0 ? 1 : 0;
}

// --------------------------------------------------------------- hashing ---
#define WINDOW_BYTES 256
#define MAX_WINDOW (2 * WINDOW_BYTES)

struct window {
    const unsigned char *lo;
    const unsigned char *hi;
    uintptr_t map_start;
    uintptr_t map_end;
    unsigned long map_offset;
    char map[160];
};

static unsigned char base_copy[MAX_WINDOW];

static int read_maps_window(struct window *w) {
    uintptr_t fn = (uintptr_t)(void *)&SSL_write;
    FILE *maps = fopen("/proc/self/maps", "re");
    if (maps == NULL) return -1;
    char line[512];
    int found = 0;
    while (fgets(line, sizeof(line), maps) != NULL) {
        unsigned long start = 0, end = 0, off = 0, ino = 0;
        char perms[8] = {0}, path[256] = {0};
        int fields = sscanf(line, "%lx-%lx %7s %lx %*s %lu %255s", &start, &end,
                            perms, &off, &ino, path);
        if (fields < 4) continue;
        if (strchr(perms, 'x') == NULL) continue;
        if (fn < start || fn >= end) continue;
        uintptr_t lo = fn - WINDOW_BYTES;
        uintptr_t hi = fn + WINDOW_BYTES;
        if (lo < start) lo = start;
        if (hi > end) hi = end;
        w->lo = (const unsigned char *)lo;
        w->hi = (const unsigned char *)hi;
        w->map_start = start;
        w->map_end = end;
        w->map_offset = off;
        snprintf(w->map, sizeof(w->map), "%s", path[0] ? path : "-");
        found = 1;
        break;
    }
    fclose(maps);
    return found ? 0 : -1;
}

// File offset of one of our own symbols: the mapping already tells us the
// offset it was mapped at, so no ELF parsing is needed here.  A static binary
// has no .dynsym, and this number is what lets the collector attach with its
// path@base:write:read form instead of a symbol name.
static unsigned long file_offset_of(const void *fn) {
    uintptr_t addr = (uintptr_t)fn;
    FILE *maps = fopen("/proc/self/maps", "re");
    if (maps == NULL) return 0;
    char line[512];
    unsigned long result = 0;
    while (fgets(line, sizeof(line), maps) != NULL) {
        unsigned long start = 0, end = 0, off = 0, ino = 0;
        char perms[8] = {0}, path[256] = {0};
        int fields = sscanf(line, "%lx-%lx %7s %lx %*s %lu %255s", &start, &end,
                            perms, &off, &ino, path);
        if (fields < 4) continue;
        if (addr < start || addr >= end) continue;
        result = (unsigned long)(addr - start) + off;
        break;
    }
    fclose(maps);
    return result;
}

static uint64_t fnv1a64(const unsigned char *p, size_t len) {
    uint64_t hash = 0xcbf29ce484222325ull;
    for (size_t i = 0; i < len; i++) {
        hash ^= p[i];
        hash *= 0x100000001b3ull;
    }
    return hash;
}

static void hash_window(const struct window *w, uint64_t *fnv, uint8_t sha[32]) {
    size_t len = (size_t)(w->hi - w->lo);
    *fnv = fnv1a64(w->lo, len);
    struct sha256 s;
    sha256_init(&s);
    sha256_update(&s, w->lo, len);
    sha256_final(&s, sha);
}

static void hex32(const uint8_t sha[32], char out[65]) {
    static const char digits[] = "0123456789abcdef";
    for (int i = 0; i < 32; i++) {
        out[i * 2] = digits[sha[i] >> 4];
        out[i * 2 + 1] = digits[sha[i] & 0xf];
    }
    out[64] = 0;
}

// ------------------------------------------------------------------ state ---
static volatile int g_signal = 0;
static void on_signal(int sig) { g_signal = sig; }

static void install_signals(void) {
    // Only catchable, non-fatal signals: a SEGV here would mean the fixture
    // itself is broken, and pretending to report through it would be a lie.
    static const int caught[] = {SIGHUP, SIGINT, SIGQUIT, SIGUSR1, SIGUSR2, SIGTERM};
    for (unsigned i = 0; i < sizeof(caught) / sizeof(caught[0]); i++) {
        signal(caught[i], on_signal);
    }
}

static uint64_t now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000ull + (uint64_t)(ts.tv_nsec / 1000000);
}

static void print_state(const char *record, long pid, uint64_t started,
                        unsigned long long calls, const struct window *w,
                        uint64_t base_fnv, const uint8_t base_sha[32],
                        int signal_number) {
    uint64_t fnv = 0;
    uint8_t sha[32];
    char hex[65];
    hash_window(w, &fnv, sha);
    hex32(sha, hex);
    int changed = (fnv != base_fnv) || (memcmp(sha, base_sha, 32) != 0);
    if (signal_number > 0) {
        printf("{\"record\":\"%s\",\"pid\":%ld,\"signal\":%d,\"elapsed_ms\":%llu,"
               "\"calls\":%llu,\"fnv1a64\":\"0x%016llx\",\"sha256\":\"%s\","
               "\"changed\":%s}\n",
               record, pid, signal_number,
               (unsigned long long)(now_ms() - started), calls,
               (unsigned long long)fnv, hex, changed ? "true" : "false");
    } else {
        printf("{\"record\":\"%s\",\"pid\":%ld,\"elapsed_ms\":%llu,"
               "\"calls\":%llu,\"fnv1a64\":\"0x%016llx\",\"sha256\":\"%s\"}\n",
               record, pid, (unsigned long long)(now_ms() - started), calls,
               (unsigned long long)fnv, hex);
    }
}

static void report_detected(long pid, uint64_t started, unsigned long long calls,
                            const struct window *w, uint64_t base_fnv,
                            const uint8_t base_sha[32]) {
    uint64_t fnv = 0;
    uint8_t sha[32];
    char before_hex[65];
    char after_hex[65];
    hash_window(w, &fnv, sha);
    hex32(base_sha, before_hex);
    hex32(sha, after_hex);
    size_t len = (size_t)(w->hi - w->lo);
    long first = -1;
    for (size_t i = 0; i < len; i++) {
        if (base_copy[i] != w->lo[i]) {
            first = (long)i - (long)((const unsigned char *)&SSL_write - w->lo);
            break;
        }
    }
    printf("{\"record\":\"detected\",\"pid\":%ld,\"elapsed_ms\":%llu,"
           "\"calls\":%llu,\"first_diff_offset\":%ld,"
           "\"before_fnv1a64\":\"0x%016llx\",\"after_fnv1a64\":\"0x%016llx\","
           "\"before_sha256\":\"%s\",\"after_sha256\":\"%s\","
           "\"window_bytes\":%zu}\n",
           pid, (unsigned long long)(now_ms() - started), calls, first,
           (unsigned long long)base_fnv, (unsigned long long)fnv, before_hex,
           after_hex, len);
}

// ------------------------------------------------------------------- main ---
int main(int argc, char **argv) {
    long interval_ms = 100;
    long timeout_ms = 60000;
    int call = 1;
    const char *pidfile = NULL;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--pidfile") == 0 && i + 1 < argc) {
            pidfile = argv[++i];
        } else if (strcmp(argv[i], "--no-call") == 0) {
            call = 0;
        } else if (strcmp(argv[i], "--interval-ms") == 0 && i + 1 < argc) {
            interval_ms = strtol(argv[++i], NULL, 10);
        } else if (strcmp(argv[i], "--timeout-ms") == 0 && i + 1 < argc) {
            timeout_ms = strtol(argv[++i], NULL, 10);
        } else {
            fprintf(stderr,
                    "usage: %s [--pidfile PATH] [--no-call] [--interval-ms N] "
                    "[--timeout-ms N]\n",
                    argv[0]);
            return 1;
        }
    }
    if (interval_ms < 1) interval_ms = 1;
    if (timeout_ms < interval_ms) timeout_ms = interval_ms;

    install_signals();
    long pid = (long)getpid();
    if (pidfile != NULL) {
        FILE *f = fopen(pidfile, "we");
        if (f != NULL) {
            fprintf(f, "%ld\n", pid);
            fclose(f);
        }
    }

    struct window w;
    if (read_maps_window(&w) != 0) {
        fprintf(stderr, "selfhash: own executable mapping not found\n");
        return 2;
    }
    size_t len = (size_t)(w.hi - w.lo);
    if (len > MAX_WINDOW) len = MAX_WINDOW;
    memcpy(base_copy, w.lo, len);

    uint64_t base_fnv = 0;
    uint8_t base_sha[32];
    hash_window(&w, &base_fnv, base_sha);
    char base_hex[65];
    hex32(base_sha, base_hex);
    printf("{\"record\":\"before\",\"pid\":%ld,\"map\":\"%s\",\"map_start\":%lu,"
           "\"map_end\":%lu,\"window_bytes\":%zu,\"fnv1a64\":\"0x%016llx\","
           "\"sha256\":\"%s\",\"mode\":\"%s\",\"interval_ms\":%ld,"
           "\"timeout_ms\":%ld,\"ssl_write_file_offset\":%lu,"
           "\"ssl_read_file_offset\":%lu}\n",
           pid, w.map, (unsigned long)w.map_start, (unsigned long)w.map_end, len,
           (unsigned long long)base_fnv, base_hex, call ? "call" : "no-call",
           interval_ms, timeout_ms, file_offset_of((const void *)&SSL_write),
           file_offset_of((const void *)&SSL_read));
    fflush(stdout);

    uint64_t started = now_ms();
    unsigned long long calls = 0;
    unsigned char scratch[8] = {0};
    for (;;) {
        if (g_signal != 0) {
            print_state("signal", pid, started, calls, &w, base_fnv, base_sha,
                        (int)g_signal);
            fflush(stdout);
            return 0;
        }
        if (call) {
            (void)SSL_write(NULL, "x", 1);
            (void)SSL_read(NULL, scratch, 1);
        }
        calls++;
        uint64_t fnv = 0;
        uint8_t sha[32];
        hash_window(&w, &fnv, sha);
        if (fnv != base_fnv || memcmp(sha, base_sha, 32) != 0) {
            report_detected(pid, started, calls, &w, base_fnv, base_sha);
            fflush(stdout);
            return 0;
        }
        if ((long)(now_ms() - started) >= timeout_ms) {
            print_state("clean", pid, started, calls, &w, base_fnv, base_sha, 0);
            fflush(stdout);
            return 0;
        }
        struct timespec ts = {interval_ms / 1000, (interval_ms % 1000) * 1000000L};
        nanosleep(&ts, NULL);
    }
}
