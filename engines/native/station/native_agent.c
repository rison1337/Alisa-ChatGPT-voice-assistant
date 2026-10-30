/* Native command handling runs on the station. Only validated command labels
 * cross SSH; unrelated recognized text, startup credentials and PCM are never
 * logged. Run as an opt-in preload constructor in an existing libc executable.
 */
typedef __SIZE_TYPE__ size_t;
typedef unsigned long long u64;
extern char *getenv(const char *);
extern int socket(int, int, int);
extern int connect(int, const void *, unsigned int);
extern long read(int, void *, size_t);
extern long write(int, const void *, size_t);
extern int close(int);
extern int open(const char *, int, ...);
extern int unlink(const char *);
extern int flock(int, int);
extern int getpid(void);
extern int *__errno_location(void);
extern void *memcpy(void *, const void *, size_t);
extern void *memmove(void *, const void *, size_t);
extern int memcmp(const void *, const void *, size_t);
extern size_t strlen(const char *);
extern int strcmp(const char *, const char *);
extern int snprintf(char *, size_t, const char *, ...);
extern void _exit(int) __attribute__((noreturn));
extern void (*signal(int, void (*)(int)))(int);
struct pollfd { int fd; short events, revents; };
struct timespec { long sec, nsec; };
struct local_address { unsigned short family, port; unsigned char address[4], pad[8]; };
extern int poll(struct pollfd *, unsigned long, int);
extern int clock_gettime(int, struct timespec *);
extern int pipe2(int *, int);
extern int fork(void);
extern int setsid(void);
extern int waitpid(int, int *, int);
extern int getsockopt(int, int, int, void *, unsigned int *);
extern int link(const char *, const char *);

struct value { const unsigned char *data; size_t size; u64 number; int wire; };
struct agent {
    int fd, chat, recognizing, output_active, native_state, pause_pending;
    char listening[37], current_request[37], seen[64][37];
    unsigned int seen_count, commands, recognition_starts, speech_stops, native_speech_events, wake_blocks;
    unsigned int queue_clear_requests, music_pause_requests;
    double heartbeat;
    int focus_enabled, focus_current, focus_pending, focus_owned[2], focus_ready;
    unsigned int focus_serial, focus_acquires, focus_releases;
    char focus_request[2][37];
    double focus_deadline;
    int focus_guard_fd, focus_guard_pid, focus_cleanup_failed;
    char focus_path[128];
    int focus_session_ready, focus_recovering, focus_wait_idle, focus_retries, focus_closing;
    unsigned int focus_yields, focus_reacquires;
    unsigned int native_inputs, focus_input_at_start;
    double focus_idle_since, focus_retry_after;
    const char *tts_gate_file;
};

/* A single looped native session retains its activity across replay. A
 * separate local guardian holds our lock, and stops only our file on pipe EOF
 * or10s without a heartbeat, even when this agent is killed or blocked. */
static const char *focus_paths[2] = {
    "/data/opt/alisa-standalone/focus-0.wav", "/data/opt/alisa-standalone/focus-1.wav"
};
enum alice_method_kind {
    STOP_CONVERSATION = 3,
    CANCEL_DIALOG_AND_CLEAR_QUEUE = 6,
    STOP_TTS = 14
};
static volatile int stopping;
static void stop_signal(int unused);

static double seconds(void) {
    struct timespec t;
    if (clock_gettime(1, &t) < 0) return 0;
    return (double)t.sec + (double)t.nsec / 1000000000.0;
}

#include "tts_lease.h"

static int number(const unsigned char *data, size_t length, size_t *offset, u64 *out) {
    *out = 0;
    for (unsigned int shift = 0; shift < 70; shift += 7) {
        if (*offset >= length) return 0;
        unsigned char b = data[(*offset)++];
        if (shift == 63 && b > 1) return 0;
        *out |= (u64)(b & 127) << shift;
        if (!(b & 128)) return 1;
    }
    return 0;
}

static int field(const unsigned char *data, size_t length, unsigned int wanted, struct value *out) {
    size_t offset = 0;
    while (offset < length) {
        u64 key, n = 0;
        if (!number(data, length, &offset, &key) || !(key >> 3)) return 0;
        unsigned int wire = key & 7;
        const unsigned char *bytes = 0;
        if (wire == 0) {
            if (!number(data, length, &offset, &n)) return 0;
        } else if (wire == 1 || wire == 2 || wire == 5) {
            if (wire == 2) {
                if (!number(data, length, &offset, &n)) return 0;
            } else n = wire == 1 ? 8 : 4;
            if (n > length - offset) return 0;
            bytes = data + offset;
            offset += (size_t)n;
        } else return 0;
        if ((key >> 3) == wanted) {
            out->data = bytes;
            out->size = bytes ? (size_t)n : 0;
            out->number = wire == 0 ? n : 0;
            out->wire = wire;
            return 1;
        }
    }
    return wanted == 0;
}

static int bytes_field(const unsigned char *data, size_t size, unsigned int key, struct value *out) {
    return field(data, size, key, out) && out->wire == 2;
}

static u64 int_field(const unsigned char *data, size_t size, unsigned int key) {
    struct value v;
    return field(data, size, key, &v) && v.wire == 0 ? v.number : 0;
}

static size_t put_number(unsigned char *out, u64 value) {
    size_t size = 0;
    while (value >= 128) { out[size++] = (unsigned char)value | 128; value >>= 7; }
    out[size++] = (unsigned char)value;
    return size;
}

static size_t put_bytes(unsigned char *out, unsigned int key, const void *data, size_t length) {
    size_t n = put_number(out, key * 8 + 2);
    n += put_number(out + n, length);
    memcpy(out + n, data, length);
    return n + length;
}

static int output(int fd, const void *data, size_t size) {
    const unsigned char *p = data;
    while (size) {
        long n = write(fd, p, size);
        if (n < 0 && *__errno_location() == 4) continue;
        if (n <= 0) return 0;
        p += n; size -= (size_t)n;
    }
    return 1;
}

static int rpc(struct agent *a, const char *capability, unsigned int branch,
               const unsigned char *method, size_t size) {
    unsigned char remote[512], envelope[560], packet[564];
    size_t n = put_bytes(remote, 7, capability, strlen(capability));
    n += put_bytes(remote + n, branch, method, size);
    size_t length = put_bytes(envelope, 252, remote, n);
    length += put_number(envelope + length, 253 * 8);
    length += put_number(envelope + length, 210);
    packet[0] = length; packet[1] = length >> 8;
    packet[2] = length >> 16; packet[3] = length >> 24;
    memcpy(packet + 4, envelope, length);
    return output(a->fd, packet, length + 4);
}

static int alice_method(struct agent *a, unsigned char kind) {
    unsigned char method[] = {8, kind};
    return rpc(a, "AliceCapability", 8, method, sizeof(method));
}

static int clear_dialog_queue(struct agent *a, const char *request) {
    /* Field 10 scopes both clear_queue and cancelDialog to the completed
     * native request. Call only after BUSY: LISTENING still owns voice input. */
    unsigned char method[40] = {8, CANCEL_DIALOG_AND_CLEAR_QUEUE};
    size_t length = 2 + put_bytes(method + 2, 10, request, 36);
    if (!rpc(a, "AliceCapability", 8, method, length)) return 0;
    a->queue_clear_requests++;
    return 1;
}

static int pause_player(struct agent *a) {
    const char source_id[] = "00000000-0000-4000-8000-000000000001";
    unsigned char source[100] = {8,14,16,3,24,1}, method[130] = {8,1};
    size_t n = 6;
    n += put_bytes(source + n, 4, source_id, sizeof(source_id) - 1);
    n += put_bytes(source + n, 6, "ChatGPTVoice", 12);
    size_t length = 2 + put_bytes(method + 2, 5, source, n);
    method[length++] = 56; method[length++] = 0;
    if (!rpc(a, "PlaybackControlCapability", 13, method, length)) return 0;
    a->music_pause_requests++;
    return 1;
}

static int request_music_pause(struct agent *a) {
    /* A playback directive during recognition can cancel its input. Remember
     * this one entry pause until IDLE instead of running a periodic guard. */
    if (a->native_state == 1 || a->native_state == 3) {
        a->pause_pending = 1;
        return 1;
    }
    return pause_player(a);
}

#include "custom_commands.h"

int alice_native_command(const unsigned char *text, size_t length) {
    unsigned char normalized[1025];
    size_t used = 0;
    int space = 1;
    for (size_t i = 0; i < length;) {
        unsigned int c = text[i++];
        if (c >= 0xc2 && c <= 0xdf && i < length && (text[i] & 0xc0) == 0x80)
            c = ((c & 31) << 6) | (text[i++] & 63);
        else if (c >= 128) return 0;
        if (c >= 0x410 && c <= 0x42f) c += 32;
        if (c == 0x401 || c == 0x451) c = 0x435;
        if (c >= 0x430 && c <= 0x44f) {
            if (used > sizeof(normalized) - 3) return 0;
            normalized[used++] = 0xc0 | (c >> 6);
            normalized[used++] = 0x80 | (c & 63);
            space = 0;
        } else if ((c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9')) {
            if(used>=sizeof(normalized)-1)return 0;
            normalized[used++]=(c>='A'&&c<='Z')?c+32:c;space=0;
        } else if (!space && used < sizeof(normalized) - 1) {
            normalized[used++] = ' '; space = 1;
        }
    }
    if (space && used) --used;
    normalized[used] = 0;
    char *value = (char *)normalized;
    const char marker[] = "алиса ", please[] = " пожалуйста";
    if (used >= sizeof(marker) - 1 && !memcmp(value, marker, sizeof(marker) - 1)) {
        value += sizeof(marker) - 1; used -= sizeof(marker) - 1;
    }
    if (used >= sizeof(please) - 1 && !memcmp(value + used - sizeof(please) + 1,
                                             please, sizeof(please) - 1))
        value[used - sizeof(please) + 1] = 0;
    int custom=configured_command(value);if(custom>=0)return custom;
    if (!strcmp(value, "открой предыдущий чат") || !strcmp(value, "верни предыдущий чат")) return 4;
    if (!strcmp(value, "открой чат")) return 1;
    if (!strcmp(value, "создай чат") || !strcmp(value, "создай новый чат")) return 2;
    if (!strcmp(value, "закрой чат") || !strcmp(value, "выйди из чата") ||
        !strcmp(value, "закончи разговор")) return 3;
    return 0;
}

static void status(struct agent *a, const char *event) {
    char text[768];
    int n = snprintf(text, sizeof(text),
        "{\"event\":\"%s\",\"pid\":%d,\"mode\":\"%s\",\"recognition_starts\":%u,"
        "\"commands\":%u,\"speech_stops\":%u,\"recognition_active\":%d,"
        "\"native_state\":%d,\"native_speech_events\":%u,\"output_active\":%d,\"wake_blocks\":%u,"
        "\"queue_clear_requests\":%u,\"music_pause_requests\":%u,\"pause_pending\":%d,\"focus_enabled\":%d,\"focus_ready\":%d,"
        "\"focus_acquires\":%u,\"focus_releases\":%u,"
        "\"focus_guard_pid\":%d,\"focus_recovering\":%d,"
        "\"focus_yields\":%u,\"focus_reacquires\":%u,\"tts_gate_enabled\":%d}\n", event, getpid(),
        a->chat ? "chat" : "idle", a->recognition_starts, a->commands, a->speech_stops,
        a->recognizing, a->native_state, a->native_speech_events, a->output_active, a->wake_blocks,
        a->queue_clear_requests, a->music_pause_requests, a->pause_pending, a->focus_enabled, a->focus_ready,
        a->focus_acquires, a->focus_releases, a->focus_guard_pid,
        a->focus_recovering, a->focus_yields, a->focus_reacquires, !!a->tts_gate_file);
    if (n > 0 && (size_t)n < sizeof(text) && !output(1, text, (size_t)n)) stopping = 1;
}

static int focus_stop(struct agent *a, int slot) {
    unsigned char method[192] = {8, 1};
    size_t n = 2 + put_bytes(method + 2, 2, a->focus_path, strlen(a->focus_path));
    a->focus_owned[slot] = 0;
    a->focus_request[slot][0] = 0; /* Ignore late completion/focus events. */
    a->focus_releases++;
    int good = rpc(a, "AudioFilePlayerCapability", 12, method, n);
    if (!good) a->focus_cleanup_failed = 1;
    return good;
}

static int focus_release(struct agent *a) {
    int good = 1;
    for (int i = 0; i < 2; ++i)
        if (a->focus_owned[i] && !focus_stop(a, i)) good = 0;
    a->focus_current = a->focus_pending = -1;
    a->focus_ready = 0;
    return good;
}

static int focus_start(struct agent *a, int slot) {
    unsigned char method[220] = {8, 0}, params[48] = {8,1,16,1};
    /* Unique per acquisition, including replacements in the same slot. */
    snprintf(a->focus_request[slot], 37, "47505446-0000-4000-%04x-%012x",
             (unsigned int)getpid() & 65535, ++a->focus_serial);
    size_t p = 4 + put_bytes(params + 4, 4, a->focus_request[slot], 36);
    size_t n = 2 + put_bytes(method + 2, 2, a->focus_path, strlen(a->focus_path));
    method[n++] = 24; method[n++] = 1; /* DIALOG channel. */
    n += put_bytes(method + n, 4, params, p);
    method[n++] = 40; method[n++] = 1; /* send_events, presence + value. */
    a->focus_owned[slot] = 1;
    a->focus_input_at_start = a->native_inputs;
    a->focus_pending = slot;
    a->focus_deadline = seconds() + 5;
    return rpc(a, "AudioFilePlayerCapability", 12, method, n);
}

static void focus_fail(struct agent *a) {
    focus_release(a);
    if (a->tts_gate_file) unlink(a->tts_gate_file);
    a->chat = a->pause_pending = a->focus_session_ready = a->focus_recovering = 0;
    status(a, "focus_error");
}

static void focus_yield(struct agent *a) {
    /* FilePlayer may complete BEFORE the Alice LISTENING event arrives. Keep
     * user chat intent independent of focus and wait for a fresh IDLE event;
     * issuing another local directive now can cancel native recognition. */
    a->focus_ready = 0;
    a->focus_recovering = a->focus_wait_idle = 1;
    a->focus_idle_since = 0;
    /* Completion can also arrive AFTER the IDLE of the intervening native
     * input. That IDLE is already fresh; waiting for another would strand
     * recovery. A cached IDLE from before the wake must not qualify. */
    if (a->native_state == 0 && a->native_inputs != a->focus_input_at_start) {
        a->focus_wait_idle = 0;
        a->focus_idle_since = seconds();
    }
    a->focus_retry_after = seconds() + .4;
    a->focus_yields++;
    status(a, "focus_yielded");
}

static int handle_focus(struct agent *a, const unsigned char *data, size_t size) {
    struct value event, directive_event, directive, request, audio, flag, completed;
    if (!a->focus_enabled || !bytes_field(data, size, 17, &event) ||
        !bytes_field(event.data, event.size, 1, &directive_event) ||
        !bytes_field(directive_event.data, directive_event.size, 1, &directive) ||
        !bytes_field(directive.data, directive.size, 4, &request) || request.size != 36)
        return 1;
    int slot = -1;
    for (int i = 0; i < 2; ++i)
        if (a->focus_owned[i] && !memcmp(request.data, a->focus_request[i], 36)) slot = i;
    if (slot < 0) return 1;
    if (field(directive_event.data, directive_event.size, 4, &completed)) {
        if (!a->focus_session_ready) { focus_fail(a); return 1; }
        int was_pending = slot == a->focus_pending;
        a->focus_owned[slot] = 0;
        a->focus_request[slot][0] = 0;
        a->focus_current = a->focus_pending = -1;
        if (was_pending && ++a->focus_retries >= 3) { focus_fail(a); return 1; }
        focus_yield(a);
        return 1;
    }
    if (!bytes_field(event.data, event.size, 3, &audio) ||
        int_field(audio.data, audio.size, 1) != 6 ||
        !field(audio.data, audio.size, 17, &flag) || flag.wire != 0) return 1;
    if (slot == a->focus_pending && flag.number == 1) {
        a->focus_current = slot; a->focus_pending = -1; a->focus_ready = 1;
        a->focus_session_ready = 1;
        a->focus_recovering = a->focus_wait_idle = a->focus_retries = 0;
        a->focus_acquires++;
        status(a, "focus");
    } else if (slot == a->focus_current && flag.number == 1 && !a->focus_ready) {
        a->focus_ready = 1;
        a->focus_recovering = a->focus_wait_idle = 0;
        a->focus_input_at_start = a->native_inputs;
        status(a, "focus");
    } else if (slot == a->focus_current && flag.number != 1) {
        /* The activity still exists while backgrounded; allow native focus
         * restoration instead of stopping/recreating it during voice input. */
        if (a->focus_ready) focus_yield(a);
    }
    return 1;
}

static int focus_tick(struct agent *a) {
    if (!a->focus_enabled || !a->chat) return 1;
    if (a->focus_pending >= 0) {
        if (seconds() >= a->focus_deadline) {
            if (!a->focus_session_ready) focus_fail(a);
            else if (a->native_state == 0) {
                focus_release(a);
                if (++a->focus_retries >= 3) focus_fail(a);
                else {
                    focus_yield(a);
                    a->focus_wait_idle = 0;
                    a->focus_idle_since = seconds();
                }
            }
        }
    } else if (a->focus_recovering && !a->focus_closing && !a->focus_wait_idle &&
               a->focus_current < 0 && a->native_state == 0 &&
               seconds() >= a->focus_retry_after && seconds() - a->focus_idle_since >= .4) {
        a->focus_reacquires++;
        return focus_start(a, 0);
    }
    return 1;
}

static int start_focus_guard(struct agent *a, int lock, const struct local_address *address) {
    int pipe[2];
    if (pipe2(pipe, 02000000 | 04000) < 0) return 0;
    int child = fork();
    if (child < 0) { close(pipe[0]); close(pipe[1]); return 0; }
    if (child) {
        close(pipe[0]); a->focus_guard_fd = pipe[1]; a->focus_guard_pid = child;
        return 1;
    }
    /* Do not keep SSH stdio or the parent's Remoting socket alive. The lock
     * stays inherited until cleanup, so a replacement agent cannot race us. */
    close(pipe[1]); close(a->fd); close(0); close(1); close(2);
    setsid();
    signal(1, stop_signal); signal(15, stop_signal); signal(2, stop_signal);
    int disarmed = 0;
    while (!stopping) {
        struct pollfd watched = {pipe[0], 1, 0};
        int ready = poll(&watched, 1, 10000);
        if (ready < 0 && *__errno_location() == 4) continue;
        if (ready <= 0) break;
        char input[128]; long n = read(pipe[0], input, sizeof(input));
        if (n <= 0) break;
        for (long i = 0; i < n; ++i) if (input[i] == 'D') disarmed = 1;
        if (disarmed) break;
    }
    if (!disarmed) {
        /* Bounded nonblocking connect; the tiny stop frame fits the empty
         * send buffer. Retain the lock until this attempt has finished. */
        int fd = socket(2, 1 | 04000, 0);
        if (fd >= 0) {
            int connected = connect(fd, address, sizeof(*address)) == 0;
            if (!connected && *__errno_location() == 115) {
                struct pollfd writable = {fd, 4, 0};
                int error = -1; unsigned int length = sizeof(error);
                if (poll(&writable, 1, 2000) > 0 &&
                    getsockopt(fd, 1, 4, &error, &length) == 0 && error == 0) connected = 1;
            }
            if (connected) {
                a->fd = fd;
                focus_stop(a, 0);
            }
            close(fd);
        }
    }
    unlink(a->focus_path);
    if (a->tts_gate_file) unlink(a->tts_gate_file);
    close(pipe[0]); close(lock); _exit(0);
}

static void command_event(struct agent *a, int command) {
    const char *label = command == 1 ? "open" : command == 2 ? "new" : command == 4 ? "previous" : "close";
    char text[100];
    int n = snprintf(text, sizeof(text), "{\"event\":\"command\",\"action\":\"%s\"}\n", label);
    if (n > 0 && !output(1, text, (size_t)n)) stopping = 1;
    a->commands++;
}

static int handle_state(struct agent *a, const unsigned char *data, size_t size) {
    struct value request, identifier, text;
    int state = (int)int_field(data, size, 1);
    if (state == 1 && a->native_state != 1) a->native_inputs++;
    if (state == 2 && a->native_state != 2) a->native_speech_events++;
    a->native_state = state;
    if (a->focus_recovering) {
        if (state == 0) {
            if (a->focus_wait_idle || a->focus_idle_since == 0) a->focus_idle_since = seconds();
            a->focus_wait_idle = 0;
        } else {
            a->focus_wait_idle = 1;
            a->focus_idle_since = 0;
        }
    }
    if (state == 0 && a->pause_pending) {
        a->pause_pending = 0;
        if (!pause_player(a)) return 0;
    }
    char id[37] = {0};
    if (bytes_field(data, size, 7, &identifier) && identifier.size == 36)
        memcpy(id, identifier.data, 36);
    memcpy(a->current_request, id, sizeof(id));
    /* Keep the wake-triggered recognizer and its buffered speech intact.
     * Replacing it with START_RECOGNIZE can drop speech immediately after
     * the wake word and adds an unnecessary SDK session transition. */
    if (a->chat && state == 1 && id[0] && memcmp(id, a->listening, 36)) {
        memcpy(a->listening, id, sizeof(id));
        if (a->output_active) {
            /* Let the client interrupt its own output and keep the stock
             * recognizer alive, so "закрой чат" also works during a reply. */
            const char event[] = "{\"event\":\"wake\"}\n";
            write(1, event, sizeof(event) - 1);
        }
        a->recognizing = 1;
        a->recognition_starts++;
    }
    if (a->chat && state == 2) {
        a->speech_stops++;
        if (!alice_method(a, STOP_TTS)) return 0;
    }
    if (state == 0) a->recognizing = 0;
    if (!bytes_field(data, size, 15, &request) ||
        !field(request.data, request.size, 0, &text) ||
        int_field(request.data, request.size, 2) != 1 ||
        int_field(request.data, request.size, 4) != 1 || !id[0]) return 1;
    /* Wait for BUSY, as the stock request has then finished its voice input.
     * Cancelling an earlier LISTENING/final notification can strand the
     * spotter while the SDK is still completing that input. */
    if (state != 3) return 1;
    if (!bytes_field(request.data, request.size, 1, &text) || text.size > 1024) return 1;
    int action = alice_native_command(text.data, text.size);
    if (!action && !a->chat) return 1;
    for (unsigned int i = 0; i < a->seen_count && i < 64; ++i)
        if (!memcmp(a->seen[i], id, 36)) return 1;
    memcpy(a->seen[a->seen_count++ % 64], id, sizeof(id));
    /* Every request consumed by chat must discard its queued native reply,
     * not just opening commands. STOP_CONVERSATION alone can leave that reply
     * queued until SPEAKING, when STOP_TTS is already audibly late. */
    if (a->chat || action == 1 || action == 2 || action == 4) {
        if (!clear_dialog_queue(a, id)) return 0;
    } else if (!alice_method(a, STOP_CONVERSATION)) return 0;
    a->recognizing = 0;
    if (action == 3) a->focus_closing = 1;
    if (action) command_event(a, action);
    return 1;
}

static int handle_frame(struct agent *a, const unsigned char *data, size_t size) {
    struct value state;
    if (!field(data, size, 0, &state)) return 1;
    if (int_field(data, size, 253) == 210 && bytes_field(data, size, 252, &state)) {
        struct value object;
        const char name[] = "AudioFilePlayerCapability";
        if (!field(state.data, state.size, 0, &object)) return 1;
        if (bytes_field(state.data, state.size, 7, &object) &&
            object.size == sizeof(name) - 1 && !memcmp(object.data, name, object.size))
            return handle_focus(a, state.data, state.size);
        return 1;
    }
    if (int_field(data, size, 253) != 3 || !bytes_field(data, size, 252, &state)) return 1;
    struct value ignored;
    if (!field(state.data, state.size, 0, &ignored)) return 1;
    return handle_state(a, state.data, state.size);
}

static void restore(struct agent *a) {
    if (a->tts_gate_file) unlink(a->tts_gate_file);
    if (a->focus_enabled) focus_release(a);
    if (a->recognizing) alice_method(a, STOP_CONVERSATION);
    a->chat = a->recognizing = 0;
    a->pause_pending = 0;
    a->focus_session_ready = a->focus_recovering = a->focus_wait_idle = 0;
    a->focus_retries = a->focus_closing = 0;
    a->listening[0] = 0;
    unlink("/data/opt/alisa-standalone/native-muted");
}

static int control(struct agent *a, const char *line) {
    a->heartbeat = seconds();
    if (a->chat && !tts_lease_refresh(a->tts_gate_file)) return 0;
    if (a->focus_enabled && write(a->focus_guard_fd, "P", 1) != 1) return 0;
    if (!strcmp(line, "PING")) status(a, "status");
    else if (!strcmp(line, "CHAT")) {
        if (!a->chat) {
            a->focus_session_ready = 0;
            /* Focus may only duck MEDIA; make the underlying music stay
             * paused, including after the GPT dialog releases its focus. */
            if (!request_music_pause(a)) return 0;
        }
        a->chat = 1;
        if (!tts_lease_refresh(a->tts_gate_file)) return 0;
        a->focus_closing = 0;
        if (a->focus_enabled && !a->focus_recovering && a->focus_current < 0 && a->focus_pending < 0 &&
            !focus_start(a, 0)) return 0;
        status(a, "mode");
    }
    else if (!strcmp(line, "IDLE")) { restore(a); status(a, "mode"); }
    else if (!strcmp(line, "SPEAKING")) a->output_active = 1;
    else if (!strcmp(line, "QUIET")) a->output_active = 0;
    else if (!strcmp(line, "CANCEL")) return alice_method(a, STOP_CONVERSATION);
    else if (!strcmp(line, "CLEAR_QUEUE")) {
        if (a->native_state == 3 && a->current_request[0])
            return clear_dialog_queue(a, a->current_request);
    }
    else if (!strcmp(line, "PAUSE")) {
        /* Native playback controls share SDK focus with speech input. Let
         * an in-flight request finish before enforcing the music pause. */
        if (a->native_state != 1 && a->native_state != 3) return pause_player(a);
        /* Native focus replaces the periodic pause guard. Preserve the single
         * requested pause until this voice input finishes, without a timer. */
        if (a->focus_enabled) a->pause_pending = 1;
    }
    else if (!strcmp(line, "STOP")) stopping = 1;
    return 1;
}

static void stop_signal(int unused) { (void)unused; stopping = 1; }

__attribute__((constructor)) static void run_agent(void) {
    const char *enabled = getenv("ALICE_NATIVE_AGENT");
    if (!enabled || enabled[0] != '1') return;
    signal(13, (void (*)(int))1);
    signal(15, stop_signal); signal(2, stop_signal);
    const char *lock_path = getenv("ALICE_NATIVE_LOCK");
    if (!lock_path) lock_path = "/data/opt/alisa-standalone/native-agent.lock";
    int lock = open(lock_path, 2 | 64, 0600);
    if (lock < 0 || flock(lock, 2 | 4) < 0) _exit(2);
    unsigned int port = 9875;
    const char *port_text = getenv("ALICE_NATIVE_PORT");
    if (port_text) {
        port = 0;
        while (*port_text >= '0' && *port_text <= '9') port = port * 10 + *port_text++ - '0';
        if (*port_text || !port || port > 65535) _exit(2);
    }
    struct local_address address =
        {2, (unsigned short)((port >> 8) | (port << 8)), {127,0,0,1}, {0}};
    struct agent a = {0}; a.fd = -1; a.native_state = -1;
    a.tts_gate_file = getenv("ALICE_TTS_GATE_FILE");
    a.focus_current = a.focus_pending = -1;
    a.focus_guard_fd = -1;
    const char *native_focus = getenv("ALICE_NATIVE_FOCUS");
    a.focus_enabled = native_focus && !strcmp(native_focus, "1");
    for (int attempt = 0; attempt < 40 && !stopping; ++attempt) {
        a.fd = socket(2, 1, 0);
        if (a.fd >= 0 && connect(a.fd, &address, sizeof(address)) == 0) break;
        if (a.fd >= 0) close(a.fd);
        a.fd = -1; poll(0, 0, 250);
    }
    if (a.fd < 0) _exit(2);
    if (a.focus_enabled) {
        struct timespec stamp;
        if (clock_gettime(1, &stamp) < 0) _exit(2);
        int written = snprintf(a.focus_path, sizeof(a.focus_path),
                 "/data/opt/alisa-standalone/focus-%08x-%08lx.wav", (unsigned int)getpid(), stamp.nsec);
        /* Tests may point the template at an ordinary temporary WAV. */
        const char *source = getenv("ALICE_FOCUS_TEMPLATE");
        const char *directory = getenv("ALICE_FOCUS_DIRECTORY");
        if (directory) written = snprintf(a.focus_path, sizeof(a.focus_path),
                              "%s/focus-%08x-%08lx.wav", directory, (unsigned int)getpid(), stamp.nsec);
        if (written <= 0 || (size_t)written >= sizeof(a.focus_path) || a.focus_path[0] != '/') _exit(2);
        if (link(source ? source : focus_paths[0], a.focus_path) < 0) _exit(2);
        if (!start_focus_guard(&a, lock, &address)) { unlink(a.focus_path); _exit(2); }
    }
    a.heartbeat = seconds(); status(&a, "ready");
    static unsigned char input[512 * 1024 + 4];
    size_t used = 0, control_used = 0;
    char line[64];
    int good = 1;
    while (!stopping && good) {
        if (!focus_tick(&a)) break;
        if (seconds() - a.heartbeat > 8) {
            restore(&a); status(&a, "heartbeat_timeout"); break;
        }
        struct pollfd fds[2] = {{0,1,0}, {a.fd,1,0}};
        int ready = poll(fds, 2, 200);
        if (ready < 0) { if (*__errno_location() == 4) continue; break; }
        if (fds[0].revents & (1 | 8 | 16)) {
            char data[128]; long n = read(0, data, sizeof(data));
            if (n <= 0) break;
            for (long i = 0; i < n && good; ++i) {
                if (data[i] == '\n') {
                    line[control_used] = 0; good = control(&a, line); control_used = 0;
                } else if (control_used < sizeof(line) - 1) line[control_used++] = data[i];
                else { good = 0; break; }
            }
        }
        if (fds[1].revents & (1 | 8 | 16)) {
            long n = read(a.fd, input + used, sizeof(input) - used);
            if (n <= 0) break;
            used += (size_t)n;
            while (used >= 4 && good) {
                size_t length = (size_t)input[0] | (size_t)input[1] << 8 |
                                (size_t)input[2] << 16 | (size_t)input[3] << 24;
                if (!length || length > sizeof(input) - 4) { good = 0; break; }
                if (used < length + 4) break;
                good = handle_frame(&a, input + 4, length);
                used -= length + 4;
                memmove(input, input + length + 4, used);
            }
        }
    }
    restore(&a);
    if (a.focus_guard_fd >= 0) {
        if (!a.focus_cleanup_failed) write(a.focus_guard_fd, "D", 1);
        close(a.focus_guard_fd);
        for (int i = 0; i < 100; ++i) {
            if (waitpid(a.focus_guard_pid, 0, 1) != 0) break;
            poll(0, 0, 10);
        }
    }
    close(a.fd); close(lock);
    _exit(good ? 0 : 2);
}
