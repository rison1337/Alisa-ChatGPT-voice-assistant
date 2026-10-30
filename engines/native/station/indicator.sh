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
