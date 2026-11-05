#!/bin/sh
# Temporary ChatGPT LED ownership; no firmware files are edited.
set -efu
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
MUTE_GPIO=${ALICE_MUTE_GPIO_STATUS:-/sys/kernel/debug/gpio}
owned=0
saved=''
saved_triggers=''
painted=''
blue=' 0 0 160 0 0 160 0 0 160'
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
    printf '%s' "$task_led_decoded"
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
active_trigger() {
    IFS= read -r task_led_available < "$1/trigger"
    task_led_trigger=${task_led_available#*\[}
    task_led_trigger=${task_led_trigger%%\]*}
    case "$task_led_trigger" in
        ''|*[!a-zA-Z0-9_:-]*) return 1 ;;
    esac
    printf '%s' "$task_led_trigger"
}
triggers() {
    task_led_triggers=''
    for task_led_zone in $zones; do
        for task_led_color in red green blue; do
            task_led_trigger=$(active_trigger "$LED:$task_led_color:ring$task_led_zone")
            task_led_triggers="$task_led_triggers $task_led_trigger"
        done
    done
    printf '%s' "$task_led_triggers"
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
            task_led_current_trigger=$(active_trigger "$task_led_path")
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
    printf '%s' "$task_led_frame"
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
            if [ "$(active_trigger "$task_led_path")" = none ]; then
                printf '%s\n' "$1" > "$task_led_path/brightness"
            fi
            shift
        done
    done
}
release() {
    if [ "$owned" = 1 ]; then
        task_led_current=$(frame)
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
        F\ *) task_led_requested=$(decode_frame "${task_led_command#F }") || continue ;;
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
    task_led_current=$(frame)
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
        saved="$task_led_current"
        saved_triggers=$(triggers)
        owned=1
        task_led_acquired=1
        detach_triggers
    elif [ "$task_led_current" != "$painted" ]; then
        saved="$task_led_current"
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
