#!/bin/sh
# Temporary ChatGPT LED ownership; no firmware files are edited.
set -efu
LED=${ALICE_LED_ROOT:-/sys/class/leds/plum}
# Lite 1 (yandex,station-micro) has the same 3x3 ring under another name
# and only a software microphone mute, so there is no switch to report.
no_mute_switch=0
if [ -z "${ALICE_LED_ROOT:-}" ] && [ ! -e "$LED:red:ring1" ] && [ -e /sys/class/leds/micro:red:ring1 ]; then
    LED=/sys/class/leds/micro
fi
case "$LED" in */micro) no_mute_switch=1 ;; esac
# Station 2 (yandexmidi) drives its 88-LED panel through a framebuffer
# (/dev/fb0, 89x1 RGBW) instead of plum sysfs rings; render there instead.
panel_mode=0
if [ -z "${ALICE_LED_ROOT:-}" ]; then
    if [ -n "${ALICE_PANEL_FB:-}" ]; then
        # Explicit framebuffer paths are also used by isolated device tests.
        panel_mode=1
    elif [ ! -e "$LED:red:ring1" ] && [ ! -e /sys/class/leds/micro:red:ring1 ]; then
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
# speaks and while GPT thinks. A soft cyan wave is an explicit preference;
# the small flag is re-read so the CLI can change it without restarting audio.
listen=${ALISA_LISTEN_LED:-0}
listen_file=${ALISA_LISTEN_LED_FILE-/data/opt/alisa-standalone/listen-led}
listening_enabled() {
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
    tick=$(( (tick + 1) % 16 ))
    task_led_decoded=''
    for task_led_zone in 0 5 10; do
        task_led_step=$(( (tick + task_led_zone) % 16 ))
        [ "$task_led_step" -le 8 ] || task_led_step=$(( 16 - task_led_step ))
        task_led_level=$(( 20 + task_led_step * 13 ))
        task_led_decoded="$task_led_decoded 0 $(( task_led_level / 2 )) $task_led_level"
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
    task_panel_snapshot=$(od -An -v -t u1 -N 356 "$PANEL_FB" 2>/dev/null | awk '
        { for (i=1; i<=NF; i++) {
            n++; v=$i; out=out sprintf("\\0%03o", v)
            if (n<=352) {
                if (n%4==1 && v>0) red=1
                if (n%4!=1 && v>0) other=1
            }
        } }
        END { if (n!=356) exit 1; printf "%d %s", red && !other, out }
    ') || return 1
    task_panel_warning=${task_panel_snapshot%% *}
    task_panel_current=${task_panel_snapshot#* }
}
panel_encode() {
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
panel_release() {
    if [ "$panel_owned" = 1 ]; then
        # Firmware may already own a new warning/animation. Never clear it;
        # mute or an unknown GPIO state also forbids the final write.
        if panel_unmuted && panel_read && [ "$task_panel_current" = "$panel_painted" ]; then
            printf '%b' "$panel_saved" > "$PANEL_FB"
        fi
        panel_owned=0
        printf 'STOCK\n'
    fi
}
panel_owned=0
if [ "$panel_mode" = 1 ]; then
    trap 'panel_release; exit 0' TERM INT HUP
    trap panel_release EXIT
    panel_encode ' 0 0 0 0 0 0 0 0 0'; panel_dark=$task_panel_encoded
    panel_requested=''; panel_painted=''
    while IFS= read -r -t 2 task_led_command; do
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
    for task_led_zone in 1 2 3; do
        for task_led_color in red green blue; do
            active_trigger "$LED:$task_led_color:ring$task_led_zone"
            task_led_triggers="$task_led_triggers $task_led_trigger"
        done
    done
}
detach_triggers() {
    # Keep native wake/listening animations out of the entire GPT session,
    # including silent frames. The physical mute switch is checked separately.
    for task_led_zone in 1 2 3; do
        for task_led_color in red green blue; do
            printf 'none\n' > "$LED:$task_led_color:ring$task_led_zone/trigger"
        done
    done
}
restore_triggers() {
    set -- $saved_triggers
    for task_led_zone in 1 2 3; do
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
    for task_led_zone in 1 2 3; do
        for task_led_color in red green blue; do
            IFS= read -r task_led_value < "$LED:$task_led_color:ring$task_led_zone/brightness"
            task_led_frame="$task_led_frame $task_led_value"
        done
    done
}
paint() {
    set -- $1
    for task_led_zone in 1 2 3; do
        for task_led_color in red green blue; do
            printf '%s\n' "$1" > "$LED:$task_led_color:ring$task_led_zone/brightness"
            shift
        done
    done
}
restore_frame() {
    set -- $saved
    for task_led_zone in 1 2 3; do
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
        [ "$task_led_current" != "$painted" ] || restore_frame
        restore_triggers
        owned=0
        printf 'STOCK\n'
    fi
}
trap 'release; exit 0' TERM INT HUP
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
        saved=' 0 0 0 0 0 0 0 0 0'
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
