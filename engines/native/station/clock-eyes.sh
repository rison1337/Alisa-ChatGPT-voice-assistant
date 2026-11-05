#!/bin/sh
# Bergamot clock only. Protocol heartbeats come from the existing voice daemon.
set -efu
tr '\000' '\n' < "${ALISA_CLOCK_DEVICE_TREE:-/proc/device-tree/compatible}" 2>/dev/null | grep -Eq '^yandex,bergamot(-evt1)?$' || exit 0
BASE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
LED=${ALISA_CLOCK_LED_ROOT:-/sys/class/leds/bergamot:white:clock}
parts='A0 B0 C0 D0 E0 F0 G0 A1 B1 C1 D1 E1 F1 G1 A2 B2 C2 D2 E2 F2 G2 A3 B3 C3 D3 E3 F3 G3 DH DM DL DEGREE'
ISO_SESSION=''
STOCK_LED=$LED
. "$BASE/clock-isolation.sh"
owned=0
saved=''
painted=''
tick=0
active=0
started_cs=0
VOLUME_FILE=${ALISA_CLOCK_VOLUME_FILE:-/data/quasar/data/last_vendor_volume}
volume_previous=''
volume_until=0
volume_value=0
volume_direction=up
volume_update() {
    volume_step=''
    [ -r "$VOLUME_FILE" ] || return 0
    IFS= read -r volume_step < "$VOLUME_FILE" || [ -n "$volume_step" ] || return 0
    case "$volume_step" in ''|*[!0-9]*) return 0;; esac
    [ "$volume_step" -le 20 ] || return 0
    if [ -n "$volume_previous" ] && [ "$volume_step" != "$volume_previous" ]; then
        if [ "$volume_step" -gt "$volume_previous" ]; then volume_direction=up; else volume_direction=down; fi
        volume_value=$((volume_step * 5))
        volume_until=$((now_cs + 200))
    fi
    volume_previous=$volume_step
}
UPTIME=${ALISA_CLOCK_UPTIME_FILE:-/proc/uptime}
clock_now() {
    IFS=' ' read -r uptime remainder < "$UPTIME" || return 1
    seconds=${uptime%%.*}
    fraction=${uptime#*.}00
    fraction=${fraction%${fraction#??}}
    case "$seconds:$fraction" in *[!0-9:]*) return 1;; esac
    now_cs=$((seconds * 100 + 1$fraction - 100))
}
snapshot() {
    observed=''
    for part in $parts; do
        IFS= read -r value < "$LED$part/brightness" || return 1
        case "$value" in ''|*[!0-9]*) return 1;; esac
        [ "$value" -le 255 ] || return 1
        observed="$observed $value"
    done
}
write_frame() {
    display_apply_changes "$1" "$LED"
}
release() {
    if [ -n "$ISO_SESSION" ]; then
        # Generate current time before handing the physical display back to maind.
        iso_release || return 1
        LED=$STOCK_LED
    elif [ "$owned" = 1 ] && snapshot && [ "$observed" = "$painted" ]; then
        write_frame "$saved" || true
    fi
    owned=0
    painted=''
    volume_previous=''
    volume_until=0
}
trap release EXIT
trap 'exit 0' TERM INT HUP
is_clock() {
    # Reacquire after ordinary time/brightness updates, never overwrite a new
    # expression, temperature, or other non-clock frame from the stock renderer.
    set -- $observed
    level=0
    digit=0
    while [ "$digit" -lt 4 ]; do
        pattern=''
        for segment in A B C D E F G; do
            [ "$1" -eq 0 ] || pattern="$pattern$segment"
            [ "$1" -le "$level" ] || level=$1
            shift
        done
        case "$pattern" in ''|ABCDEF|BC|ABDEG|ABCDG|BCFG|ACDFG|ACDEFG|ABC|ABCDEFG|ABCDFG) :;; *) return 1;; esac
        digit=$((digit + 1))
    done
    # DH DM DL DEGREE: no extra symbols on a normal time frame.
    [ "$2" -eq 0 ] && [ "$4" -eq 0 ] && [ "$level" -gt 0 ]
}
draw() {
    # Microphone mute belongs to the audio path and ring. Retain the display
    # until the conversation ends, including volume changes while muted.
    if [ -n "$ISO_SESSION" ]; then
        # Exclusive ownership: no repeated framebuffer scan or corrective paint.
        [ -d "$ISO_SESSION/original" ] || exit 1
        observed=$painted
    else
        snapshot || return 0
        if [ "$owned" = 1 ] && [ "$observed" != "$painted" ]; then owned=0; fi
    fi
    if [ "$owned" = 0 ]; then
        is_clock || return 0
        saved=$observed
        # Respect stock automatic brightness, capped at the tested low level.
        [ "$level" -le 40 ] || level=40
        if [ "$LED" = /sys/class/leds/bergamot:white:clock ]; then
            # If isolation is unavailable, do not fall back to fighting maind.
            iso_acquire || return 0
            LED="$ISO_SESSION/original/bergamot:white:clock"
        fi
        owned=1
    fi
    clock_now || { release; return 0; }
    volume_update
    elapsed=$((now_cs - started_cs))
    phase_cs=$((elapsed % 3200))
    left=open
    right=open
    # Blink at 8 and 24 s (200 ms); wink at 16 s (300 ms), smile
    # at 17..18.5 s. The 125 ms daemon heartbeat bounds display precision.
    if { [ "$phase_cs" -ge 800 ] && [ "$phase_cs" -lt 820 ]; } ||
       { [ "$phase_cs" -ge 2400 ] && [ "$phase_cs" -lt 2420 ]; }; then
        left=closed; right=closed
    elif [ "$phase_cs" -ge 1600 ] && [ "$phase_cs" -lt 1630 ]; then
        right=closed
    elif [ "$phase_cs" -ge 1700 ] && [ "$phase_cs" -lt 1850 ]; then
        left=smile; right=smile
    fi
    target=''
    for part in $parts; do
        value=0
        case "$part" in
            G0) [ "$left" = smile ] || value=$level;;
            G3) [ "$right" = smile ] || value=$level;;
            A0|B0|F0) [ "$left" = closed ] || value=$level;;
            A3|B3|F3) [ "$right" = closed ] || value=$level;;
        esac
        target="$target $value"
    done
    if [ "$now_cs" -lt "$volume_until" ]; then
        display_volume "$volume_value" "$volume_direction" "$level" || return 0
        target=$display_values
    fi
    if [ "$target" != "$observed" ]; then
        painted=$target
        write_frame "$target" || exit 1
    else painted=$target
    fi
}
while IFS= read -r -t 2 command; do
    case "$command" in
        F\ *)
            # Query local state once per second, not the network or account.
            if [ $((tick % 8)) = 0 ]; then
                if [ -n "$ISO_SESSION" ]; then iso_touch || exit 1; fi
                clock_active=0
                if [ "${ALISA_CLOCK_PREVIEW:-0}" = 1 ]; then
                    # Preview uses the same isolation/guardian, but must yield if
                    # a real conversation starts while the chooser is open.
                    status_base=${ALISA_CLOCK_STATUS_BASE:-$BASE}
                    if [ -x "$status_base/bin/alisa-standalone" ]; then
                        preview_status=$("$status_base/bin/alisa-standalone" status "$status_base/state" 2>/dev/null) || exit 1
                        printf '%s' "$preview_status" | grep -Eq '"phase": "(active|opening|connecting)"' && exit 0
                    fi
                    clock_active=1
                elif "$BASE/bin/alisa-standalone" status "$BASE/state" 2>/dev/null | grep -q '"phase": "active"'; then
                    clock_active=1
                fi
                if [ "$clock_active" = 1 ]; then
                    if [ "$active" = 0 ]; then
                        clock_now || exit 1
                        started_cs=$now_cs
                    fi
                    active=1
                else active=0; release
                fi
            fi
            if [ "$active" = 1 ]; then draw; fi
            tick=$(( (tick + 1) % 64 ))
            printf 'FRAME\n'
            ;;
        B) printf 'BLUE\n';;
        0) release; printf 'STOCK\n'; exit 0;;
    esac
done
# EOF, stalled heartbeat, normal close and caught termination release ownership.
# The independent lease guard also recovers mounts after owner SIGKILL.
