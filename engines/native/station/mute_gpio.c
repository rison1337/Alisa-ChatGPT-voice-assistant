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
extern int mkstemp(char *);
extern int *__errno_location(void);

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

static unsigned int length_of(const char *text) {
    unsigned int n = 0;
    while (text[n]) ++n;
    return n;
}

static int space(char c) { return c == ' ' || c == '\t' || c == '\r'; }

/* Exact consumer labels only; GPIO flags can follow the hi/lo token. */
static const char *label_end(const char *line, const char *label) {
    for (; *line; ++line) {
        if (*line != '|') continue;
        const char *p = line + 1, *name = label;
        while (*p && *p == *name) { ++p; ++name; }
        if (!*name && (space(*p) || *p == ')')) return p;
    }
    return 0;
}

/* 0: unrelated, 1: enabled, 2: muted, 3: native format, -1: unknown. */
static int line_state(const char *line) {
    if (label_end(line, "mute-switch")) return 3;
    const char *p = label_end(line, "MUTE_SWITCH");
    if (!p) p = label_end(line, "MUTE_STATE");
    if (!p) return 0;
    while (*p && *p != ')') ++p;
    if (!*p) return -1;
    ++p;
    int state = 0;
    while (*p) {
        while (space(*p)) ++p;
        const char *word = p;
        while (*p && !space(*p)) ++p;
        if (p - word == 2) {
            int value = word[0] == 'h' && word[1] == 'i' ? 2 :
                        word[0] == 'l' && word[1] == 'o' ? 1 : 0;
            if (value && state && value != state) return -1;
            if (value) state = value;
        }
    }
    return state ? state : -1;
}

static int write_all(int fd, const char *text) {
    size_t left = length_of(text);
    while (left) {
        long n = write(fd, text, left);
        if (n < 0 && *__errno_location() == 4) continue; /* EINTR */
        if (n <= 0) return 0;
        text += n; left -= (size_t)n;
    }
    return 1;
}

/* Re-read the live switch on each open. Stream the entire GPIO listing with
 * bounded memory; missing, unreadable, malformed or oversized input stays
 * muted. Each caller owns an unlinked file, so concurrent reads cannot race. */
static int converted_gpio(void) {
    open_fn original_open = (open_fn)next("open");
    char chunk[512], line[512];
    size_t used = 0, total = 0;
    int states = 0, invalid = 0, overflow = 0, legacy = 0;
    int fd = original_open(source(), 0, 0);
    if (fd >= 0) {
        for (;;) {
            long n = read(fd, chunk, sizeof(chunk));
            if (n < 0 && *__errno_location() == 4) continue;
            if (n < 0) { invalid = 1; break; }
            if (!n) break;
            total += (size_t)n;
            if (total > 131072) { invalid = 1; break; }
            for (long i = 0; i < n; ++i) {
                if (chunk[i] == '\n') {
                    line[used] = 0;
                    int state = overflow ? -1 : line_state(line);
                    if (state == 3) legacy = 1;
                    else if (state < 0) invalid = 1;
                    else states |= state;
                    used = 0; overflow = 0;
                } else if (chunk[i] == 0) invalid = 1;
                else if (used < sizeof(line) - 1) line[used++] = chunk[i];
                else overflow = 1;
            }
        }
        close(fd);
        if (used || overflow) {
            line[used] = 0;
            int state = overflow ? -1 : line_state(line);
            if (state == 3) legacy = 1;
            else if (state < 0) invalid = 1;
            else states |= state;
        }
        if (legacy) return -1; /* Preserve Lite 2's original listing. */
    } else invalid = 1;
    char tmp[] = "/tmp/alisa-mute-gpio-XXXXXX";
    fd = mkstemp(tmp);
    if (fd < 0) return -1;
    const char *listing = !invalid && states == 1 ?
        " gpio-0 ( |mute-switch ) in  hi\n" : " gpio-0 ( |mute-switch ) in  lo\n";
    int reader = write_all(fd, listing) ? original_open(tmp, 02000000, 0) : -1;
    unlink(tmp);
    close(fd);
    return reader; /* Read-only, close-on-exec, independent file offset. */
}

static void *converted_stream(int fd) {
    void *stream = fdopen(fd, "r");
    if (!stream) close(fd);
    return stream;
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
        if (fd >= 0) return converted_stream(fd);
    }
    return original(redirect(path, read_only), mode);
}

void *fopen64(const char *path, const char *mode) {
    static fopen_fn original;
    if (!original) original = (fopen_fn)next("fopen64");
    int read_only = read_mode(mode);
    if (auto_convert(path, read_only)) {
        int fd = converted_gpio();
        if (fd >= 0) return converted_stream(fd);
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
