/* Mini 1 only: scale direct ChatGPT PCM by Quasar's gesture volume.
 * Link with -Wl,-z,nodelete so later dlopen calls keep finding this wrapper,
 * rather than the real libasound that remains resident after the first call.
 */
#define _POSIX_C_SOURCE 200809L
#include <ctype.h>
#include <dlfcn.h>
#include <errno.h>
#include <stdint.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
#include <fcntl.h>

static void *real_alsa;
__attribute__((destructor)) static void release_real_alsa(void) {
    if (real_alsa) { dlclose(real_alsa); real_alsa = 0; }
}
static void *symbol(const char *name) {
    if (!real_alsa) real_alsa = dlopen("/system/vendor/quasar/libs/libasound.so.2", RTLD_NOW | RTLD_LOCAL);
    return real_alsa ? dlsym(real_alsa, name) : 0;
}

#define FORWARD_INT(name, signature, args) \
int name signature { \
    static int (*fn) signature; \
    if (!fn) fn = symbol(#name); \
    return fn ? fn args : -ENOSYS; \
}

FORWARD_INT(snd_pcm_open, (void **p, const char *n, int s, int m), (p,n,s,m))
FORWARD_INT(snd_pcm_set_params, (void *p, int f, int a, unsigned int c, unsigned int r, int z, unsigned int u), (p,f,a,c,r,z,u))
FORWARD_INT(snd_pcm_recover, (void *p, int e, int s), (p,e,s))
FORWARD_INT(snd_pcm_drop, (void *p), (p))
FORWARD_INT(snd_pcm_close, (void *p), (p))
FORWARD_INT(snd_pcm_drain, (void *p), (p))
FORWARD_INT(snd_pcm_get_params, (void *p, unsigned long *b, unsigned long *r), (p,b,r))
FORWARD_INT(snd_pcm_sw_params_malloc, (void **p), (p))
FORWARD_INT(snd_pcm_sw_params_current, (void *p, void *s), (p,s))
FORWARD_INT(snd_pcm_sw_params_set_start_threshold, (void *p, void *s, unsigned long t), (p,s,t))
FORWARD_INT(snd_pcm_sw_params, (void *p, void *s), (p,s))

void snd_pcm_sw_params_free(void *p) {
    static void (*fn)(void *);
    if (!fn) fn = symbol("snd_pcm_sw_params_free");
    if (fn) fn(p);
}

static int volume_level(void) {
    /* Until a valid level is read, keep output silent. A failed refresh
     * preserves the last valid level. */
    static int cached = 0;
    static struct timespec last;
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return cached;
    if (last.tv_sec && (now.tv_sec - last.tv_sec) * 1000 +
        (now.tv_nsec - last.tv_nsec) / 1000000 < 200) return cached;
    last = now;
    const char *path = getenv("ALISA_VOLUME_FILE");
    if (!path || !*path) return cached;
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return cached;
    char buf[32]; ssize_t len = read(fd, buf, sizeof(buf) - 1); close(fd);
    if (len <= 0 || len == sizeof(buf) - 1) return cached;
    buf[len] = 0;
    char *end; long level = strtol(buf, &end, 10);
    if (end == buf || level < 0 || level > 10000) return cached;
    while (isspace((unsigned char)*end)) ++end;
    if (!*end) cached = (int)level;
    return cached;
}

long snd_pcm_writei(void *pcm, const void *data, unsigned long frames) {
    static long (*fn)(void *, const void *, unsigned long);
    if (!fn) fn = symbol("snd_pcm_writei");
    if (!fn) return -ENOSYS;
    int level = volume_level();
    if (level == 10000 || frames == 0) return fn(pcm, data, frames);
    /* A short write is valid ALSA behavior. The caller advances by the
     * number of frames returned and retries the rest. */
    enum { BLOCK_FRAMES = 1024 };
    int16_t scaled[BLOCK_FRAMES * 2];
    unsigned long count = frames < BLOCK_FRAMES ? frames : BLOCK_FRAMES;
    const int16_t *input = data;
    for (unsigned long i = 0; i < count * 2; ++i)
        scaled[i] = (int16_t)((int32_t)input[i] * level / 10000);
    return fn(pcm, scaled, count);
}
