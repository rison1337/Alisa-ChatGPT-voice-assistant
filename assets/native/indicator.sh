#!/bin/sh
# Temporary ChatGPT LED ownership; no firmware files are edited.
set -efu
# An explicit opt-out acknowledges frames without acquiring LEDs.
if [ "${ALISA_DISABLE_LED:-0}" = 1 ]; then
    trap 'exit 0' TERM INT HUP
    while IFS= read -r task_led_command; do
        case "$task_led_command" in
            F\ *) printf 'FRAME\n' ;;
            B) printf 'BLUE\n' ;;
            0) printf 'STOCK\n' ;;
        esac
    done
    exit 0
fi
LED=${ALICE_LED_ROOT:-/sys/class/leds/plum}
# Lite 1 (yandex,station-micro) has the same 3x3 ring under another name
# and only a software microphone mute, so there is no switch to report.
# Mini 2 (yandex,station-mini2) has a 6-zone ring; its momentary mute button
# is handled by maind before VQE; its latched state is read separately below.
no_mute_switch=0
mini2_mute=0
if [ -z "${ALICE_LED_ROOT:-}" ] && [ ! -e "$LED:red:ring1" ]; then
    if [ -e /sys/class/leds/micro:red:ring1 ]; then
        LED=/sys/class/leds/micro
    elif [ -e /sys/class/leds/mini2:red:ring1 ]; then
        LED=/sys/class/leds/mini2
    fi
fi
# Frames carry 3 logical zones; zone_map gives the logical zone of each
# physical one. Mini 2 doubles every logical zone onto a physical pair; its
# clock LEDs (mini2:white:clock*) are never touched.
zones='1 2 3'
zone_map='1 2 3'
case "$LED" in
    */micro) no_mute_switch=1 ;;
    */mini2) mini2_mute=1; zones='1 2 3 4 5 6'; zone_map='1 1 2 2 3 3' ;;
esac
dark_frame=''
for task_led_zone in $zones; do dark_frame="$dark_frame 0 0 0"; done
# Station 2 (yandexmidi) drives its 88-LED panel through a framebuffer
# (/dev/fb0, 89x1 RGBW) instead of plum sysfs rings; render there instead.
panel_mode=0
mini3_mode=0
if [ -z "${ALICE_LED_ROOT:-}" ]; then
    # Explicit fixture paths must take precedence over physical device detection.
    if [ -n "${ALICE_MINI3_FB:-}" ]; then
        mini3_mode=1
        panel_mode=1
    elif [ -n "${ALICE_PANEL_FB:-}" ]; then
        panel_mode=1
    elif tr '\000' '\n' < /proc/device-tree/compatible 2>/dev/null | grep -Eq '^yandex,bergamot(-evt1)?$'; then
        # Mini 3 has six BGR pixels; it does not use Mini 2's sysfs LEDs.
        [ -c /dev/fb0 ] &&
        [ "$(cat /sys/class/graphics/fb0/virtual_size 2>/dev/null)" = 6,1 ] &&
        [ "$(cat /sys/class/graphics/fb0/bits_per_pixel 2>/dev/null)" = 24 ] || exit 0
        mini3_mode=1
        panel_mode=1
    elif [ ! -e "$LED:red:ring1" ] && [ ! -e /sys/class/leds/micro:red:ring1 ] && [ ! -e /sys/class/leds/mini2:red:ring1 ]; then
        # Do not send RGBW bytes to another model's framebuffer (Lite 2 is 3x1).
        [ -c /dev/fb0 ] &&
        [ "$(cat /sys/class/graphics/fb0/virtual_size 2>/dev/null)" = 89,1 ] &&
        [ "$(cat /sys/class/graphics/fb0/bits_per_pixel 2>/dev/null)" = 32 ] || exit 0
        panel_mode=1
    fi
fi
MUTE_GPIO=${ALICE_MUTE_GPIO_STATUS:-/sys/kernel/debug/gpio}
owned=0
saved=''
saved_triggers=''
painted=''
blue=' 0 0 160 0 0 160 0 0 160'
# The client sends dark frames whenever GPT is silent, i.e. while the owner
# speaks and while GPT thinks. Both the legacy cyan wave and the selected
# effect during pauses are opt-in; preferences reload without restarting audio.
listen=${ALISA_LISTEN_LED:-0}
listen_file=${ALISA_LISTEN_LED_FILE-/data/opt/alisa-standalone/listen-led}
scope_file=${ALISA_LED_SCOPE_FILE-/data/opt/alisa-standalone/led-scope}
listening_enabled() {
    task_led_style=''
    if [ -n "$scope_file" ] && [ -r "$scope_file" ]; then
        IFS=' ' read -r task_led_scope task_led_style task_led_extra < "$scope_file" || return 1
        [ -z "$task_led_extra" ] || return 1
        case "$task_led_scope" in
            active)
                case "$task_led_style" in
                    rainbow|red|green|blue|purple|white|pulse-red|pulse-green|pulse-blue|pulse-purple|pulse-white) return 0 ;;
                    *) return 1 ;;
                esac ;;
            speaking) task_led_style='' ;;
            *) return 1 ;;
        esac
    fi
    task_led_listen=$listen
    if [ -n "$listen_file" ] && [ -r "$listen_file" ]; then
        IFS= read -r task_led_listen < "$listen_file" || return 1
    else
        [ "${ALISA_LED_STYLE:-}" != off ] || return 1
    fi
    [ "$task_led_listen" = 1 ]
}
tick=0
listening_frame() {
    tick=$(( (tick + 1) % 48 ))
    task_led_decoded=''
    if [ -n "$task_led_style" ]; then
        selected_listening_frame
        return
    fi
    for task_led_zone in 0 5 10; do
        task_led_step=$(( (tick + task_led_zone) % 16 ))
        [ "$task_led_step" -le 8 ] || task_led_step=$(( 16 - task_led_step ))
        task_led_level=$(( 20 + task_led_step * 13 ))
        task_led_decoded="$task_led_decoded 0 $(( task_led_level / 2 )) $task_led_level"
    done
}
# Integer-only animation at the client's 8 fps; no helper process per frame.
selected_listening_frame() {
    if [ "$task_led_style" = rainbow ]; then
        for task_led_zone in 0 16 32; do
            task_led_hue=$(( (tick + task_led_zone) % 48 ))
            task_led_up=$(( (task_led_hue % 8) * 20 ))
            task_led_down=$(( 160 - task_led_up ))
            case $(( task_led_hue / 8 )) in
                0) task_led_rgb="160 $task_led_up 0" ;;
                1) task_led_rgb="$task_led_down 160 0" ;;
                2) task_led_rgb="0 160 $task_led_up" ;;
                3) task_led_rgb="0 $task_led_down 160" ;;
                4) task_led_rgb="$task_led_up 0 160" ;;
                5) task_led_rgb="160 0 $task_led_down" ;;
            esac
            task_led_decoded="$task_led_decoded $task_led_rgb"
        done
        return
    fi
    case "${task_led_style#pulse-}" in
        red) task_led_rgb='160 0 0' ;;
        green) task_led_rgb='0 160 0' ;;
        blue) task_led_rgb='0 0 160' ;;
        purple) task_led_rgb='120 0 120' ;;
        white) task_led_rgb='100 100 100' ;;
    esac
    task_led_scale=16
    case "$task_led_style" in
        pulse-*) task_led_scale=$(( tick % 16 ))
                 [ "$task_led_scale" -le 8 ] || task_led_scale=$(( 16 - task_led_scale ))
                 task_led_scale=$(( 8 + task_led_scale )) ;;
    esac
    for task_led_zone in 1 2 3; do
        for task_led_value in $task_led_rgb; do
            task_led_decoded="$task_led_decoded $(( task_led_value * task_led_scale / 16 ))"
        done
    done
}
decode_frame() {
    set -- $1
    [ "$#" = 9 ] || return 1
    task_led_decoded=''
    for task_led_value; do
        case "$task_led_value" in
            0|[1-9]|[1-9][0-9]|[1-9][0-9][0-9]) ;;
            *) return 1 ;;
        esac
        [ "$task_led_value" -le 255 ] || return 1
        task_led_decoded="$task_led_decoded $task_led_value"
    done
}
mute_status() {
    if [ "$mini2_mute" = 1 ]; then
        # Mini 2 exposes its latched state on GPIOZ_3 (gpio-414), not a
        # mute-switch label. The native client inherits a compatibility
        # listing; this separate reader must see the physical debugfs file.
        LD_PRELOAD= awk '
            /^[[:space:]]*gpio-414[[:space:]]+\(/ {
                count++
                tail=$0
                sub(/^.*\)[[:space:]]*/, "", tail)
                n=split(tail, bits, /[[:space:]]+/)
                if (n < 2 || bits[1] != "in" || (bits[2] != "hi" && bits[2] != "lo")) bad=1
                for (i=3; i<=n; i++) if (bits[i] == "hi" || bits[i] == "lo") bad=1
                state=(bits[2] == "hi" ? 1 : 0)
            }
            END { if (count != 1 || bad) exit 2; exit state }
        ' "$MUTE_GPIO" 2>/dev/null
        return $?
    fi
    # Verified Plum device tree: gpio-keys/mute-switch is active-low.
    # Reading the labelled GPIO also works while LED triggers are detached.
    [ -r "$MUTE_GPIO" ] || return 2
    while IFS= read -r task_led_gpio; do
        case "$task_led_gpio" in
            *'|mute-switch '*)
                set -- $task_led_gpio
                for task_led_bit; do
                    case "$task_led_bit" in hi) return 1 ;; lo) return 0 ;; esac
                done
                return 2 ;;
        esac
    done < "$MUTE_GPIO"
    [ "$no_mute_switch" = 1 ] && return 1
    return 2
}
# --- Station 2 panel backend (88 LEDs, zigzag map, /dev/fb0 RGBW) ---
PANEL_FB=${ALICE_PANEL_FB:-/dev/fb0}
[ "$mini3_mode" = 0 ] || PANEL_FB=${ALICE_MINI3_FB:-/dev/fb0}
panel_bytes=356
[ "$mini3_mode" = 0 ] || panel_bytes=18
# led x y zone: zone 1..3 by distance from the center (ring1 = inner),
# map transcribed from the experimental index map for the Station 2 panel.
panel_map='0 1 0 3
1 3 0 3
2 5 0 2
3 7 0 2
4 9 0 2
5 11 0 3
6 13 0 3
7 0 1 3
8 2 1 3
9 4 1 2
10 6 1 1
11 8 1 1
12 10 1 2
13 12 1 3
14 14 1 3
15 1 2 3
16 3 2 2
17 5 2 1
18 7 2 1
19 9 2 1
20 11 2 2
21 13 2 3
22 0 3 3
23 2 3 2
24 4 3 1
25 6 3 1
26 8 3 1
27 10 3 1
28 12 3 2
29 14 3 3
30 1 4 2
31 3 4 1
32 5 4 1
33 7 4 1
34 9 4 1
35 11 4 1
36 13 4 2
37 0 5 3
38 2 5 1
39 12 5 1
40 14 5 3
41 1 6 2
42 13 6 2
43 0 7 3
44 2 7 1
45 12 7 1
46 14 7 3
47 1 8 2
48 3 8 1
49 5 8 1
50 7 8 1
51 9 8 1
52 11 8 1
53 13 8 2
54 0 9 3
55 2 9 2
56 4 9 1
57 6 9 1
58 8 9 1
59 10 9 1
60 12 9 2
61 14 9 3
62 1 10 3
63 3 10 2
64 5 10 1
65 7 10 1
66 9 10 1
67 11 10 2
68 13 10 3
69 0 11 3
70 2 11 3
71 4 11 2
72 6 11 1
73 8 11 1
74 10 11 2
75 12 11 3
76 14 11 3
77 1 12 3
78 3 12 3
79 5 12 2
80 7 12 2
81 9 12 2
82 11 12 3
83 13 12 3
84 8 7 1
85 6 7 1
86 6 5 1
87 8 5 1'
# Read just one frame and escape NUL bytes before storing it in a shell variable.
# The prefix flags a native red warning. A short/unreadable frame is not owned.
panel_read() {
    task_panel_snapshot=$(od -An -v -t u1 -N "$panel_bytes" "$PANEL_FB" 2>/dev/null | awk -v bytes="$panel_bytes" -v mini3="$mini3_mode" '
        { for (i=1; i<=NF; i++) {
            n++; v=$i; out=out sprintf("\\0%03o", v)
            if (mini3 || n<=352) {
                if ((mini3 ? n%3==0 : n%4==1) && v>0) red=1
                if ((mini3 ? n%3!=0 : n%4!=1) && v>0) other=1
            }
        } }
        END { if (n!=bytes) exit 1; printf "%d %s", red && !other, out }
    ') || return 1
    task_panel_warning=${task_panel_snapshot%% *}
    task_panel_current=${task_panel_snapshot#* }
}
panel_encode() {
    if [ "$mini3_mode" = 1 ]; then
        # Match Mini 2's pairs: logical zones 1/2/3 -> pixels 0-1/2-3/4-5.
        task_panel_encoded=$(awk -v v="$1" 'BEGIN {
            split(v,a," "); for(z=0;z<3;z++) for(p=0;p<2;p++)
                printf "\\0%03o\\0%03o\\0%03o",a[z*3+3],a[z*3+2],a[z*3+1]
        }')
        return
    fi
    task_panel_encoded=$(awk -v v="$1" -v map="$panel_map" '
        BEGIN {
            split(v, a, " "); lines=split(map, rows, "\n")
            for (i=1; i<=lines; i++) {
                split(rows[i], fields, " "); z=fields[4]
                printf "\\0%03o\\0%03o\\0%03o\\0000", a[(z-1)*3+1], a[(z-1)*3+2], a[(z-1)*3+3]
            }
            printf "\\0000\\0000\\0000\\0000"
        }
    ')
}
panel_unmuted() {
    # CLI demos do not inherit run.sh's mute_gpio.so. Accept both the converted
    # listing and the real active-low Station 2 labels, on every frame/release.
    [ -r "$MUTE_GPIO" ] || return 1
    awk '
        /\|mute-switch[[:space:]]|\|MUTE_SWITCH[[:space:]]|\|MUTE_STATE[[:space:]]/ {
            found=1; upper=($0 ~ /\|MUTE_(SWITCH|STATE)[[:space:]]/)
            if (length($0)>512 || !index($0, ")")) { bad=1; next }
            text=substr($0,index($0,")")+1); n=split(text, words, " "); hi=0; lo=0
            for (i=1;i<=n;i++) { if (words[i]=="hi") hi=1; if (words[i]=="lo") lo=1 }
            if (hi==lo) bad=1
            if ((upper && hi) || (!upper && lo)) muted=1
        }
        END { exit (!found || bad || muted) }
    ' "$MUTE_GPIO"
}
ring_waiting=0
ring_external=''
ring_until=0
ring_uptime_file=${ALISA_RING_UPTIME_FILE:-/proc/uptime}
ring_now() {
    IFS=' ' read -r ring_uptime ring_unused < "$ring_uptime_file" || return 1
    ring_seconds=${ring_uptime%%.*}
    ring_fraction=${ring_uptime#*.}00
    ring_fraction=${ring_fraction%${ring_fraction#??}}
    case "$ring_seconds:$ring_fraction" in ''|*[!0-9:]*) return 1 ;; esac
    ring_now=$((ring_seconds * 100 + 1$ring_fraction - 100))
}
mini3_ring_ready() {
    [ "$mini3_mode" = 1 ] || return 0
    # A firmware repaint is ownership, not a frame we should fight. Wait for
    # two quiet seconds before resuming; every new external frame renews it.
    if ! ring_now; then ring_waiting=1; panel_release; return 1; fi
    if [ "$ring_waiting" = 0 ] && [ "$panel_owned" = 1 ] &&
       [ "$task_panel_current" != "$panel_painted" ]; then
        ring_waiting=1
        ring_external=$task_panel_current
        ring_until=$((ring_now + 200))
        panel_release
    fi
    if [ "$ring_waiting" = 1 ]; then
        if [ "$task_panel_current" != "$ring_external" ]; then
            ring_external=$task_panel_current
            ring_until=$((ring_now + 200))
        fi
        [ "$ring_now" -ge "$ring_until" ] || return 1
        ring_waiting=0
    fi
}
panel_release() {
    if [ "$panel_owned" = 1 ]; then
        # Firmware may already own a new warning/animation. Never clear it;
        # mute or an unknown GPIO state also forbids the final write.
        if { [ "$mini3_mode" = 0 ] || [ "$ring_waiting" = 0 ]; } &&
           panel_unmuted && panel_read && [ "$task_panel_current" = "$panel_painted" ]; then
            printf '%b' "$panel_saved" > "$PANEL_FB"
        fi
        panel_owned=0
        printf 'STOCK\n'
    fi
}
panel_owned=0
if [ "$panel_mode" = 1 ]; then
    # Feed the independent clock even when the ring yields to firmware. Preview
    # never starts it. Opening the FIFO read/write avoids a deadlock if the
    # optional helper exits before its first read. Give the child a separate
    # read-only FD so it still receives EOF when the parent's writer closes.
    clock_pid=''; clock_dir=''
    clock_stop() {
        if [ -n "$clock_pid" ]; then
            exec 3>&-
            clock_wait=0
            # Clock handoff includes a 1.25 s settling interval plus unmounts.
            while kill -0 "$clock_pid" 2>/dev/null && [ "$clock_wait" -lt 80 ]; do
                sleep .05; clock_wait=$((clock_wait + 1))
            done
            kill -TERM "$clock_pid" 2>/dev/null || true
            wait "$clock_pid" 2>/dev/null || true
            clock_pid=''
        fi
        if [ -n "$clock_dir" ]; then
            rm -f "$clock_dir/input"; rmdir "$clock_dir"; clock_dir=''
        fi
    }
    panel_finish() { panel_release; clock_stop; }
    trap 'exit 0' TERM INT HUP
    trap panel_finish EXIT
    clock_mode=eyes
    clock_mode_file=${ALISA_CLOCK_MODE_FILE:-$(dirname -- "$0")/display-mode}
    if [ -e "$clock_mode_file" ]; then
        clock_mode=$(cat "$clock_mode_file" 2>/dev/null) || clock_mode=clock
    fi
    if [ "$mini3_mode" = 1 ] && [ "${ALISA_MINI3_CLOCK_EYES:-0}" = 1 ] &&
       [ "$clock_mode" = eyes ] && [ "${ALISA_LED_PREVIEW:-0}" != 1 ]; then
        clock_dir=$(mktemp -d /tmp/alisa-ring-clock-XXXXXX)
        mkfifo "$clock_dir/input"
        exec 3<>"$clock_dir/input"
        exec 4<"$clock_dir/input"
        sh "$(dirname -- "$0")/clock-eyes.sh" <&4 3>&- 4>&- > /dev/null &
        clock_pid=$!
        exec 4<&-
        trap '' PIPE
    fi
    panel_encode ' 0 0 0 0 0 0 0 0 0'; panel_dark=$task_panel_encoded
    panel_requested=''; panel_painted=''
    while IFS= read -r -t 2 task_led_command; do
        if [ -n "$clock_pid" ]; then
            if ! kill -0 "$clock_pid" 2>/dev/null ||
               ! printf '%s\n' "$task_led_command" >&3; then clock_stop; fi
        fi
        case "$task_led_command" in
            B) task_led_requested=$blue ;;
            F\ *) decode_frame "${task_led_command#F }" || continue
                  if [ "$task_led_decoded" = ' 0 0 0 0 0 0 0 0 0' ] && listening_enabled; then
                      listening_frame
                  fi
                  task_led_requested=$task_led_decoded ;;
            0) panel_release; continue ;;
            *) continue ;;
        esac
        if ! panel_unmuted || ! panel_read; then
            panel_release
            continue
        fi
        if ! mini3_ring_ready; then
            case "$task_led_command" in F\ *) printf 'FRAME\n' ;; B) printf 'BLUE\n' ;; esac
            continue
        fi
        if { [ "$panel_owned" = 0 ] || [ "$task_panel_current" != "$panel_painted" ]; } &&
           [ "$task_panel_warning" = 1 ]; then
            panel_release
            continue
        fi
        task_led_acquired=0
        if [ "$panel_owned" = 0 ]; then
            panel_saved=$panel_dark
            [ "${ALISA_LED_PREVIEW:-0}" != 1 ] || panel_saved=$task_panel_current
            panel_owned=1
            task_led_acquired=1
        fi
        if [ "$panel_requested" != "$task_led_requested" ]; then
            panel_encode "$task_led_requested"
            panel_painted=$task_panel_encoded
            panel_requested=$task_led_requested
        fi
        # Readback, rather than the cached requested color, detects firmware
        # repainting a solid GPT frame mid-session.
        if [ "$task_panel_current" != "$panel_painted" ]; then
            printf '%b' "$panel_painted" > "$PANEL_FB"
        fi
        case "$task_led_command" in
            F\ *) printf 'FRAME\n' ;;
            B) [ "$task_led_acquired" = 0 ] || printf 'BLUE\n' ;;
        esac
    done
    exit 0
fi
active_trigger() {
    IFS= read -r task_led_available < "$1/trigger"
    task_led_trigger=${task_led_available#*\[}
    task_led_trigger=${task_led_trigger%%\]*}
    case "$task_led_trigger" in
        ''|*[!a-zA-Z0-9_:-]*) return 1 ;;
    esac
}
triggers() {
    task_led_triggers=''
    for task_led_zone in $zones; do
        for task_led_color in red green blue; do
            active_trigger "$LED:$task_led_color:ring$task_led_zone"
            task_led_triggers="$task_led_triggers $task_led_trigger"
        done
    done
}
detach_triggers() {
    # Keep native wake/listening animations out of the entire GPT session,
    # including silent frames. The physical mute switch is checked separately.
    for task_led_zone in $zones; do
        for task_led_color in red green blue; do
            printf 'none\n' > "$LED:$task_led_color:ring$task_led_zone/trigger"
        done
    done
}
restore_triggers() {
    set -- $saved_triggers
    for task_led_zone in $zones; do
        for task_led_color in red green blue; do
            task_led_path="$LED:$task_led_color:ring$task_led_zone"
            active_trigger "$task_led_path"
            task_led_current_trigger=$task_led_trigger
            # Writing brightness=0 detaches a Linux LED trigger. Restore it
            # after the last brightness write, but respect a new trigger owner.
            if [ "$task_led_current_trigger" = none ] && [ "$1" != none ]; then
                printf '%s\n' "$1" > "$task_led_path/trigger"
            fi
            shift
        done
    done
}
frame() {
    task_led_frame=''
    for task_led_zone in $zones; do
        for task_led_color in red green blue; do
            IFS= read -r task_led_value < "$LED:$task_led_color:ring$task_led_zone/brightness"
            task_led_frame="$task_led_frame $task_led_value"
        done
    done
}
paint() {
    set -- $1
    for task_led_zone in $zones; do
        for task_led_color in red green blue; do
            printf '%s\n' "$1" > "$LED:$task_led_color:ring$task_led_zone/brightness"
            shift
        done
    done
}
# Expand a logical frame to the physical channels following zone_map.
# Logical zones repeat in pairs on Mini 2 and map 1:1 elsewhere.
map_frame() {
    set -- $1
    task_led_logical=1
    task_led_mapped=''
    for task_led_ref in $zone_map; do
        while [ "$task_led_logical" -lt "$task_led_ref" ]; do
            shift 3
            task_led_logical=$((task_led_logical + 1))
        done
        task_led_mapped="$task_led_mapped $1 $2 $3"
    done
}
restore_frame() {
    set -- $saved
    for task_led_zone in $zones; do
        for task_led_color in red green blue; do
            task_led_path="$LED:$task_led_color:ring$task_led_zone"
            # A new owner must not lose its trigger to a saved zero brightness.
            active_trigger "$task_led_path"
            if [ "$task_led_trigger" = none ]; then
                printf '%s\n' "$1" > "$task_led_path/brightness"
            fi
            shift
        done
    done
}
release() {
    if [ "$owned" = 1 ]; then
        frame; task_led_current=$task_led_frame
        if [ "$mini2_mute" = 1 ]; then
            # Return triggers without brightness writes while muted/unknown.
            # Firmware owns the warning, even if it matches our last frame.
            if mute_status; then :; else
                task_led_release_mute=$?
                if [ "$task_led_release_mute" = 1 ]; then
                    [ "$task_led_current" != "$painted" ] || restore_frame
                fi
            fi
        else
            [ "$task_led_current" != "$painted" ] || restore_frame
        fi
        restore_triggers
        owned=0
        printf 'STOCK\n'
    fi
}
# A signal can interrupt IFS= read with an empty temporary IFS. Restore
# splitting before walking the physical zones and saved trigger list.
trap 'IFS=" "; release; exit 0' TERM INT HUP
trap release EXIT
while IFS= read -r -t 2 task_led_command; do
    case "$task_led_command" in
        B) task_led_requested=$blue ;;
        F\ *) decode_frame "${task_led_command#F }" || continue
              if [ "$task_led_decoded" = ' 0 0 0 0 0 0 0 0 0' ] && listening_enabled; then
                  listening_frame
              fi
              task_led_requested=$task_led_decoded ;;
        0) release; continue ;;
        *) continue ;;
    esac
    # Comparisons and painting below work on physical channels.
    map_frame "$task_led_requested"
    task_led_requested=$task_led_mapped
    if mute_status; then
        release
        continue
    else
        task_led_mute_status=$?
        # With no reliable switch state, leave native warning LEDs in charge.
        if [ "$task_led_mute_status" != 1 ]; then
            release
            continue
        fi
    fi
    frame; task_led_current=$task_led_frame
    # A rainbow frame may itself have a red first zone. Only an external
    # frame can be a red warning; never treat our last painted frame as one.
    if [ "$owned" = 0 ] || [ "$task_led_current" != "$painted" ]; then
        set -- $task_led_current
        if [ "$1" -gt 0 ] && [ "$2" = 0 ] && [ "$3" = 0 ]; then
            release
            continue
        fi
    fi
    task_led_acquired=0
    if [ "$owned" = 0 ]; then
        # A wake/listening color at mode entry is transient. The ordinary
        # idle state after closing chat is dark; native triggers are restored.
        saved=$dark_frame
        [ "${ALISA_LED_PREVIEW:-0}" != 1 ] || saved=$task_led_current
        triggers; saved_triggers=$task_led_triggers
        owned=1
        task_led_acquired=1
        detach_triggers
    elif [ "$task_led_current" != "$painted" ]; then
        # A new native wake animation can reattach triggers mid-session.
        detach_triggers
    fi
    if [ "$task_led_acquired" = 1 ] || [ "$task_led_current" != "$task_led_requested" ]; then
        paint "$task_led_requested"
    fi
    painted=$task_led_requested
    case "$task_led_command" in
        F\ *) printf 'FRAME\n' ;;
        B) [ "$task_led_acquired" = 0 ] || printf 'BLUE\n' ;;
    esac
done
