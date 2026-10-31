/* Stations without a hardware mute-switch GPIO (Lite 1, yandex,station-micro)
 * expose no "|mute-switch" line in debugfs, and the client then treats the
 * microphone as muted. Opt-in through ALICE_MUTE_GPIO_FILE: only read-only
 * opens of the exact debugfs GPIO listing are redirected to that file; every
 * other path and every write goes to the original libc function unchanged.
 *
 * Yandex Station 2 (yandexmidi) exposes the switch with different labels and
 * active-low polarity: "MUTE_SWITCH"/"MUTE_STATE" lines where "lo" means the
 * microphone is enabled. When no ALICE_MUTE_GPIO_FILE is set and the listing
 * uses that format, a converted view is served instead: the physical switch
 * keeps working while the client sees its expected "|mute-switch ... hi".
 */
typedef __builtin_va_list va_list;
extern void *dlsym(void *, const char *);
extern char *getenv(const char *);
extern int strcmp(const char *, const char *);
extern void _exit(int);
typedef __SIZE_TYPE__ size_t;
extern long read(int, void *, size_t);
extern long write(int, const void *, size_t);
extern int close(int);
extern int unlink(const char *);
extern void *fdopen(int, const char *);

typedef void *(*fopen_fn)(const char *, const char *);
typedef int (*open_fn)(const char *, int, unsigned int);
typedef int (*openat_fn)(int, const char *, int, unsigned int);

static void *next(const char *name) {
    void *symbol = dlsym((void *)(long)-1, name);
    if (!symbol) _exit(2); /* The original libc symbol must exist. */
    return symbol;
}

static const char *source(void) {
    const char *path = getenv("ALICE_MUTE_GPIO_PATH");
    return path && path[0] ? path : "/sys/kernel/debug/gpio";
}

static int contains(const char *text, const char *needle) {
    if (!text || !needle) return 0;
    for (; *text; ++text) {
        const char *a = text, *b = needle;
        while (*a && *b && *a == *b) { ++a; ++b; }
        if (!*b) return 1;
    }
    return 0;
}

/* Active-low mute lines: a trailing "hi" means the microphone is muted. */
static int hardware_muted(const char *listing) {
    const char *line = listing;
    int muted = 0;
    while (*line) {
        const char *end = line;
        while (*end && *end != '\n') ++end;
        const char *name = line;
        int named = 0;
        while (name + 10 <= end) {
            if ((name[0] == 'M' && name[1] == 'U' && name[2] == 'T' && name[3] == 'E' &&
                 name[4] == '_' && name[5] == 'S') &&
                (name[6] == 'W' || name[6] == 'T')) { named = 1; break; }
            ++name;
        }
        if (named) {
            const char *tail = end;
            while (tail > line && (tail[-1] == ' ' || tail[-1] == '\t')) --tail;
            if (tail - line >= 2 && tail[-2] == 'h' && tail[-1] == 'i') muted = 1;
        }
        line = *end ? end + 1 : end;
    }
    return muted;
}

static unsigned int length_of(const char *text) {
    unsigned int n = 0;
    while (text[n]) ++n;
    return n;
}

/* Serve the converted listing through a short-lived temp file so the client
 * re-reads the live switch state on each of its periodic opens. Returns -1
 * when the listing already speaks the client's format (Lite 1/2): pass the
 * original open through untouched. */
static int converted_gpio(void) {
    open_fn original_open = (open_fn)next("open");
    char listing[2048];
    long used = 0;
    int muted = 0;
    int fd = original_open(source(), 0, 0);
    if (fd >= 0) {
        while (used < (long)sizeof(listing) - 1) {
            long n = read(fd, listing + used, sizeof(listing) - 1 - used);
            if (n <= 0) break;
            used += n;
        }
        close(fd);
        listing[used] = 0;
        if (contains(listing, "|mute-switch ")) return -1;
        muted = hardware_muted(listing);
    }
    const char *state = muted ? "lo" : "hi";
    const char *head = " gpio-0 (                    |mute-switch               ) in  ";
    const char *tail = "    \n";
    const char *tmp = "/tmp/alisa-mute-gpio-auto";
    fd = original_open(tmp, 0100 | 01000 | 01, 0600); /* O_WRONLY|O_CREAT|O_TRUNC */
    if (fd >= 0) {
        write(fd, head, length_of(head));
        write(fd, state, 2);
        write(fd, tail, length_of(tail));
        close(fd);
    }
    fd = original_open(tmp, 0, 0);
    unlink(tmp);
    return fd;
}

static const char *redirect(const char *path, int read_only) {
    const char *replacement = getenv("ALICE_MUTE_GPIO_FILE");
    if (!path || !read_only || strcmp(path, source())) return path;
    return replacement && replacement[0] == '/' ? replacement : path;
}

static int auto_convert(const char *path, int read_only) {
    const char *replacement = getenv("ALICE_MUTE_GPIO_FILE");
    return path && read_only && !strcmp(path, source()) &&
           (!replacement || !replacement[0]);
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
    int read_only = read_mode(mode);
    if (auto_convert(path, read_only)) {
        int fd = converted_gpio();
        if (fd >= 0) return fdopen(fd, "r");
    }
    return original(redirect(path, read_only), mode);
}

void *fopen64(const char *path, const char *mode) {
    static fopen_fn original;
    if (!original) original = (fopen_fn)next("fopen64");
    int read_only = read_mode(mode);
    if (auto_convert(path, read_only)) {
        int fd = converted_gpio();
        if (fd >= 0) return fdopen(fd, "r");
    }
    return original(redirect(path, read_only), mode);
}

int open(const char *path, int flags, ...) {
    static open_fn original;
    va_list args; __builtin_va_start(args, flags);
    unsigned int mode = create_mode(flags, args);
    __builtin_va_end(args);
    if (!original) original = (open_fn)next("open");
    int read_only = read_flags(flags);
    if (auto_convert(path, read_only)) {
        int fd = converted_gpio();
        if (fd >= 0) return fd;
    }
    return original(redirect(path, read_only), flags, mode);
}

int open64(const char *path, int flags, ...) {
    static open_fn original;
    va_list args; __builtin_va_start(args, flags);
    unsigned int mode = create_mode(flags, args);
    __builtin_va_end(args);
    if (!original) original = (open_fn)next("open64");
    int read_only = read_flags(flags);
    if (auto_convert(path, read_only)) {
        int fd = converted_gpio();
        if (fd >= 0) return fd;
    }
    return original(redirect(path, read_only), flags, mode);
}

int openat(int directory, const char *path, int flags, ...) {
    static openat_fn original;
    va_list args; __builtin_va_start(args, flags);
    unsigned int mode = create_mode(flags, args);
    __builtin_va_end(args);
    if (!original) original = (openat_fn)next("openat");
    int read_only = read_flags(flags);
    if (auto_convert(path, read_only)) {
        int fd = converted_gpio();
        if (fd >= 0) return fd;
    }
    return original(directory, redirect(path, read_only), flags, mode);
}
