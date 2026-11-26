/* Street/Mango opt-in alternative to engines/native/station/vqe_tap.c.
 * Observe the stock GetChannelLayout call, never issue an extra DSP RPC.
 * Stock owns the buffers, mute controls and DSP lifecycle. Only this tap's
 * private, nonblocking AF_UNIX datagrams are optional side effects.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <elf.h>
#include <errno.h>
#include <fcntl.h>
#include <link.h>
#include <linux/input.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#define API __attribute__((visibility("default")))
#define DSP(Name) YandexVqeC_DspClient_##Name
#define CHANNELS 16u
#define FRAMES 256u
#define PACKET_FRAMES 160u
#define VQE_KIND 6u
#define VQE_INDEX 0u
#define SWBIT (1u << (SW_MUTE_DEVICE % 8))
#define SWBYTE (SW_MUTE_DEVICE / 8)
_Static_assert(sizeof(void *) == 4 && sizeof(float) == 4 &&
               __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__ && SW_MUTE_DEVICE == 14,
               "pinned ARMhf/SW14 ABI only");

static void *(*stock_process)(const float *, uint32_t, float *, void *);
static void *(*stock_layout)(uint32_t, uint32_t *, uint32_t *, void *);
static void *(*stock_set)(const char *, void *);
static void *(*stock_apply)(const char *const *, uint32_t, void *);
static void (*stock_free)(void *);
static int enabled, counters_only;
static int tap_socket = -1, mute_fd = -1;
static struct sockaddr_un address;
static socklen_t address_size;
static float scale = 32768.0f;

/* Versioned U32 LE, mmap'ed only in diagnostic mode. No PCM in this mapping. */
struct counters {
    uint32_t version, pinned, process_missing, layout_missing;
    uint32_t process_hits, process_errors, layout_hits, layout_ok, layout_rejected;
    uint32_t no_layout, geometry_rejected, invalidated, busy, muted, mute_unknown;
    uint32_t eligible_blocks, eligible_packets, sent, send_failed, socket_failed;
};
static struct counters *stats;
#define INC(field) do { if (stats) __atomic_add_fetch(&stats->field, 1, __ATOMIC_RELAXED); } while (0)

/* The pinned maind serializes these DSP calls with its own mutex. Never wait
 * for our own state lock if somebody else calls the public ABI concurrently. */
static uint32_t observer_busy, epoch;
static void *layout_client;
static uint32_t layout_epoch;
static int slot = -1;
static int lock(void) { return !__atomic_exchange_n(&observer_busy, 1, __ATOMIC_ACQUIRE); }
static void unlock(void) { __atomic_store_n(&observer_busy, 0, __ATOMIC_RELEASE); }
static uint32_t generation(void) { return __atomic_load_n(&epoch, __ATOMIC_ACQUIRE); }
static uint32_t invalidate(void) { return __atomic_add_fetch(&epoch, 1, __ATOMIC_ACQ_REL); }

/* Only inspect private geometry for these exact loaded images; no file opens,
 * text patches, or VMA assumptions. Check callable providers, not filenames. */
static const unsigned char main_id[20] = {
    0xb8,0xb6,0x74,0x09,0x90,0xb3,0xb8,0x25,0x50,0x25,0xa0,0xc0,0x42,0x15,0x07,0xb2,0x23,0x7f,0x40,0x3e};
static const unsigned char vendor_id[20] = {
    0xe6,0x95,0x61,0x58,0x6f,0xc9,0xd1,0xe8,0x3e,0x7f,0xcf,0x7c,0xee,0x9a,0xe1,0x57,0x96,0x2f,0xae,0xf4};
struct pins { unsigned int main, vendor; };
static int image_id(const struct dl_phdr_info *obj, const unsigned char *id) {
    for (unsigned int i = 0; i < obj->dlpi_phnum; ++i) {
        const ElfW(Phdr) *p = &obj->dlpi_phdr[i];
        if (p->p_type != PT_NOTE || p->p_filesz > 4096 || p->p_filesz < sizeof(ElfW(Nhdr))) continue;
        /* PT_NOTE is not necessarily mapped. Confirm it is in a readable LOAD. */
        unsigned int mapped = 0;
        for (unsigned int j = 0; j < obj->dlpi_phnum; ++j) {
            const ElfW(Phdr) *load = &obj->dlpi_phdr[j];
            if (load->p_type == PT_LOAD && (load->p_flags & PF_R) && p->p_vaddr >= load->p_vaddr &&
                p->p_filesz <= load->p_filesz && p->p_vaddr - load->p_vaddr <= load->p_filesz - p->p_filesz) {
                mapped = 1; break;
            }
        }
        if (!mapped) continue;
        const unsigned char *note = (const void *)(obj->dlpi_addr + p->p_vaddr);
        size_t remain = p->p_filesz;
        while (remain >= sizeof(ElfW(Nhdr))) {
            ElfW(Nhdr) h;
            memcpy(&h, note, sizeof(h));
            note += sizeof(h); remain -= sizeof(h);
            if (h.n_namesz > remain || h.n_descsz > remain) break;
            size_t name_size = (h.n_namesz + 3u) & ~3u, desc_size = (h.n_descsz + 3u) & ~3u;
            if (name_size + desc_size > remain) break;
            if (h.n_type == NT_GNU_BUILD_ID && h.n_namesz == 4 && h.n_descsz == 20 &&
                !memcmp(note, "GNU", 4) && !memcmp(note + name_size, id, 20)) return 1;
            note += name_size + desc_size; remain -= name_size + desc_size;
        }
    }
    return 0;
}

static int provider_contains(const struct dl_phdr_info *obj, const void *address_to_check) {
    if (!address_to_check) return 0;
    uintptr_t addr = (uintptr_t)address_to_check & ~(uintptr_t)1u;
    for (unsigned int i = 0; i < obj->dlpi_phnum; ++i) {
        const ElfW(Phdr) *p = &obj->dlpi_phdr[i];
        uintptr_t base = obj->dlpi_addr + p->p_vaddr;
        if (p->p_type == PT_LOAD && (p->p_flags & PF_X) && addr >= base && addr - base < p->p_memsz) return 1;
    }
    return 0;
}

static int find_pins(struct dl_phdr_info *obj, size_t length, void *arg) {
    (void)length;
    struct pins *pins = arg;
    if (obj->dlpi_name && !*obj->dlpi_name && image_id(obj, main_id)) pins->main = 1;
    if (provider_contains(obj, stock_process) && provider_contains(obj, stock_layout) &&
        provider_contains(obj, stock_set) && provider_contains(obj, stock_apply) &&
        provider_contains(obj, stock_free) && image_id(obj, vendor_id)) pins->vendor = 1;
    return pins->main && pins->vendor;
}

static int from_fallback;
static void *resolve(const char *name) {
    void *sym = dlsym(RTLD_NEXT, name);
    if (sym) return sym;
    from_fallback = 1;
    /* Recover stock forwarding if scope lookup failed. Never load a new DSO. */
    void *vendor = dlopen("libyandex_evil.so", RTLD_LAZY | RTLD_NOLOAD);
    if (vendor) {
        sym = dlsym(vendor, name);
        dlclose(vendor);
    }
    return sym;
}

static void init_stats(const char *path) {
    if (!*path) return;
    int fd = open(path, O_RDWR | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (fd < 0) return;
    if (!ftruncate(fd, sizeof(*stats))) {
        void *p = mmap(NULL, sizeof(*stats), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        if (p != MAP_FAILED) stats = p;
    }
    close(fd);
    if (stats) stats->version = 1;
}

__attribute__((constructor)) static void initialize(void) {
    int saved = errno;
    stock_process = resolve("YandexVqeC_DspClient_Process");
    stock_layout = resolve("YandexVqeC_DspClient_GetChannelLayout");
    stock_set = resolve("YandexVqeC_DspClient_SetConfig");
    stock_apply = resolve("YandexVqeC_DspClient_ApplyConfig");
    stock_free = resolve("YandexVqeC_DspClient_Free");
    const char *path = getenv("ALICE_VQE_STATS");
    counters_only = path != NULL;
    if (counters_only) init_stats(path);
    if (!stock_process) INC(process_missing);
    if (!stock_layout) INC(layout_missing);
    struct pins pins = {0,0};
    if (!from_fallback) dl_iterate_phdr(find_pins, &pins);
    if (!pins.main || !pins.vendor) goto done;
    INC(pinned);
    if (counters_only) { enabled = stats != NULL; goto done; }
    path = getenv("ALICE_CAPTURE_SOCKET");
    if (!path || !*path) goto done;
    size_t n = 0;
    while (n < sizeof(address.sun_path) && path[n]) ++n;
    if (n == sizeof(address.sun_path)) goto done;
    memcpy(address.sun_path, path, n + 1);
    address.sun_family = AF_UNIX;
    address_size = (socklen_t)(offsetof(struct sockaddr_un, sun_path) + n + 1);
    const char *gain = getenv("ALICE_CAPTURE_GAIN");
    char *end = NULL;
    double value = gain && *gain ? strtod(gain, &end) : 0.0;
    if (end && end != gain && !*end && value >= 0.015625 && value <= 64.0) scale *= (float)value;
    enabled = 1;
done:
    errno = saved;
}

/* Same read-only Street identity as the native client's SW14 gate. Snapshot
 * before touching the selected samples and again immediately before sendto. */
static int mute_state(void) {
    if (mute_fd < 0) {
        int fd = open("/dev/input/event0", O_RDONLY | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW);
        if (fd < 0) return -1;
        char name[32] = {0}, phys[64] = {0};
        struct input_id id = {0};
        unsigned char caps[8] = {0};
        if (ioctl(fd, EVIOCGNAME(sizeof(name)-1), name) <= 0 || strcmp(name, "gpio_keypad") ||
            ioctl(fd, EVIOCGPHYS(sizeof(phys)-1), phys) <= 0 || strcmp(phys, "gpio_keypad/input0") ||
            ioctl(fd, EVIOCGID, &id) != 0 || id.bustype != 0x10 || id.vendor != 1 ||
            id.product != 1 || id.version != 0x100 ||
            ioctl(fd, EVIOCGBIT(EV_SW,sizeof(caps)), caps) <= SWBYTE || !(caps[SWBYTE] & SWBIT)) {
            close(fd); return -1;
        }
        mute_fd = fd;
    }
    unsigned char bits[8] = {0};
    if (ioctl(mute_fd, EVIOCGSW(sizeof(bits)), bits) <= SWBYTE) {
        close(mute_fd); mute_fd = -1;
        return -1;
    }
    return (bits[SWBYTE] & SWBIT) != 0;
}

static int mute_open(void) {
    int state = mute_state();
    if (state < 0) INC(mute_unknown);
    if (state > 0) INC(muted);
    return state == 0;
}

/* Observed in 0x272f34/0x272f3c: the C client owns, at +12, a
 * pointer to the implementation whose first word is the layout count. The
 * public GetChannelLayout ABI does not return the count to the caller. */
static int geometry_known(const void *client) {
    if (!client) return 0;
    const uint32_t *impl;
    memcpy(&impl, (const unsigned char *)client + 12, sizeof(impl));
    return impl && *impl == CHANNELS;
}

API void *DSP(GetChannelLayout)(uint32_t staged, uint32_t *kinds, uint32_t *indexes, void *client) {
    INC(layout_hits);
    if (!stock_layout) return NULL; /* Only relevant when no stock implementation exists. */
    uint32_t gen = enabled ? invalidate() : 0;
    void *error = stock_layout(staged, kinds, indexes, client);
    int saved = errno;
    if (!enabled) return error;
    if (lock()) {
        layout_client = client;
        layout_epoch = gen;
        slot = -1;
        if (!error && !staged && kinds && indexes && geometry_known(client)) {
            for (unsigned int i = 0; i < CHANNELS; ++i) {
                if (kinds[i] != VQE_KIND || indexes[i] != VQE_INDEX) continue;
                if (slot != -1) { slot = -1; break; }
                slot = (int)i;
            }
        }
        if (slot >= 0) INC(layout_ok); else INC(layout_rejected);
        unlock();
    } else INC(busy);
    errno = saved;
    return error;
}

/* No reuse of a previous device/configuration's label map. Only a fresh,
 * stock active-layout observation can rearm following these calls. */
static void changed(void) {
    if (enabled) { invalidate(); INC(invalidated); }
}

API void *DSP(SetConfig)(const char *config, void *client) {
    changed();
    return stock_set ? stock_set(config, client) : NULL;
}
API void *DSP(ApplyConfig)(const char *const *pairs, uint32_t count, void *client) {
    changed();
    return stock_apply ? stock_apply(pairs, count, client) : NULL;
}
API void DSP(Free)(void *client) {
    changed();
    if (stock_free) stock_free(client);
}

static void capture(const float *output, uint32_t gen) {
    if (!mute_open() || generation() != gen) return;
    INC(eligible_blocks);
    if (!counters_only && tap_socket < 0) {
        tap_socket = socket(AF_UNIX, SOCK_DGRAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
        if (tap_socket < 0) { INC(socket_failed); return; }
    }
    for (size_t offset = 0; offset < FRAMES; offset += PACKET_FRAMES) {
        const size_t n = FRAMES - offset > PACKET_FRAMES ? PACKET_FRAMES : FRAMES - offset;
        if ((offset && !mute_open()) || generation() != gen) return;
        if (counters_only) { INC(eligible_packets); continue; }
        int16_t packet[PACKET_FRAMES];
        for (size_t i = 0; i < n; ++i) {
            float scaled = output[(offset + i) * CHANNELS + (unsigned int)slot] * scale;
            packet[i] = scaled != scaled ? 0 : scaled >= 32767.0f ? 32767 :
                        scaled <= -32768.0f ? -32768 : (int16_t)scaled;
        }
        if (mute_open() && generation() == gen) {
            const size_t bytes = n * sizeof(packet[0]);
            if (sendto(tap_socket, packet, bytes, MSG_DONTWAIT | MSG_NOSIGNAL,
                       (const struct sockaddr *)&address, address_size) == (ssize_t)bytes) INC(sent);
            else INC(send_failed);
        } else return;
    }
}

API void *DSP(Process)(const float *input, uint32_t words, float *output, void *client) {
    INC(process_hits);
    /* A firmware without this required symbol cannot run stock maind's call.
     * No exit, fabricated PCM, or output mutation if invoked without it. */
    if (!stock_process) return NULL;
    void *error = stock_process(input, words, output, client);
    int saved = errno;
    if (!enabled) return error;
    if (error) INC(process_errors);
    else if (!output || ((uintptr_t)output & 3u) || words != FRAMES * CHANNELS || !geometry_known(client)) INC(geometry_rejected);
    else if (!lock()) INC(busy);
    else {
        uint32_t gen = generation();
        if (layout_client == client && layout_epoch == gen && slot >= 0) capture(output, gen);
        else INC(no_layout);
        unlock();
    }
    errno = saved;
    return error;
}
