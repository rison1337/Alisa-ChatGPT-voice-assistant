#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

long snd_pcm_writei(void *, const void *, unsigned long);

static int expected;
static int reject;

static long backend(void *pcm, const void *buffer, unsigned long frames) {
    (void)pcm;
    const int16_t *samples = buffer;
    assert(frames <= 1024);
    for (unsigned long i = 0; i < frames * 2; ++i)
        assert(samples[i] == (i % 2 == 0 ? expected : -expected));
    return reject ? -EPIPE : (long)frames;
}

void *__wrap_dlopen(const char *name, int flags) {
    (void)name;
    (void)flags;
    return &expected;
}

void *__wrap_dlsym(void *handle, const char *name) {
    assert(handle == &expected && strcmp(name, "snd_pcm_writei") == 0);
    return (void *)backend;
}

int __wrap_dlclose(void *handle) {
    assert(handle == &expected);
    return 0;
}

void *__wrap_malloc(size_t size) {
    (void)size;
    return NULL;
}

int main(int argc, char **argv) {
    assert(argc == 2);
    expected = atoi(argv[1]);
    enum { FRAMES = 9000 };
    int16_t samples[FRAMES * 2];
    for (unsigned long i = 0; i < FRAMES * 2; ++i)
        samples[i] = i % 2 == 0 ? 12000 : -12000;
    for (unsigned long offset = 0; offset < FRAMES;) {
        long written = snd_pcm_writei(NULL, samples + offset * 2, FRAMES - offset);
        assert(written > 0 && (unsigned long)written <= FRAMES - offset);
        offset += (unsigned long)written;
    }
    reject = 1;
    assert(snd_pcm_writei(NULL, samples, 4) == -EPIPE);
    return 0;
}
