/* Stations without a hardware mute-switch GPIO (Lite 1, yandex,station-micro)
 * expose no "|mute-switch" line in debugfs, and the client then treats the
 * microphone as muted. Opt-in through ALICE_MUTE_GPIO_FILE: only read-only
 * opens of the exact debugfs GPIO listing are redirected to that file; every
 * other path and every write goes to the original libc function unchanged.
 */
typedef __builtin_va_list va_list;
extern void *dlsym(void *, const char *);
extern char *getenv(const char *);
extern int strcmp(const char *, const char *);
extern void _exit(int);

typedef void *(*fopen_fn)(const char *, const char *);
typedef int (*open_fn)(const char *, int, unsigned int);
typedef int (*openat_fn)(int, const char *, int, unsigned int);

static void *next(const char *name) {
    void *symbol = dlsym((void *)(long)-1, name);
    if (!symbol) _exit(2); /* The original libc symbol must exist. */
    return symbol;
}

static const char *redirect(const char *path, int read_only) {
    const char *replacement = getenv("ALICE_MUTE_GPIO_FILE");
    if (!path || !read_only || !replacement || replacement[0] != '/') return path;
    return strcmp(path, "/sys/kernel/debug/gpio") ? path : replacement;
}

static int read_mode(const char *mode) {
    if (!mode || mode[0] != 'r') return 0;
    for (const char *p = mode; *p; ++p) if (*p == '+') return 0;
    return 1;
}

static int read_flags(int flags) {
    /* A read descriptor with O_TRUNC/O_CREAT still mutates the target. */
    return (flags & 3) == 0 && !(flags & (0100 | 01000 | 02000 | 020000000));
}

/* O_CREAT and O_TMPFILE carry a mode argument. */
static unsigned int create_mode(int flags, va_list args) {
    return flags & (0100 | 020000000) ? __builtin_va_arg(args, unsigned int) : 0;
}

void *fopen(const char *path, const char *mode) {
    static fopen_fn original;
    if (!original) original = (fopen_fn)next("fopen");
    return original(redirect(path, read_mode(mode)), mode);
}

void *fopen64(const char *path, const char *mode) {
    static fopen_fn original;
    if (!original) original = (fopen_fn)next("fopen64");
    return original(redirect(path, read_mode(mode)), mode);
}

int open(const char *path, int flags, ...) {
    static open_fn original;
    va_list args; __builtin_va_start(args, flags);
    unsigned int mode = create_mode(flags, args);
    __builtin_va_end(args);
    if (!original) original = (open_fn)next("open");
    return original(redirect(path, read_flags(flags)), flags, mode);
}

int open64(const char *path, int flags, ...) {
    static open_fn original;
    va_list args; __builtin_va_start(args, flags);
    unsigned int mode = create_mode(flags, args);
    __builtin_va_end(args);
    if (!original) original = (open_fn)next("open64");
    return original(redirect(path, read_flags(flags)), flags, mode);
}

int openat(int directory, const char *path, int flags, ...) {
    static openat_fn original;
    va_list args; __builtin_va_start(args, flags);
    unsigned int mode = create_mode(flags, args);
    __builtin_va_end(args);
    if (!original) original = (openat_fn)next("openat");
    return original(directory, redirect(path, read_flags(flags)), flags, mode);
}
