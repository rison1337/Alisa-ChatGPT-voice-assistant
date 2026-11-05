#!/bin/sh
# Shared deterministic seven-segment frames (A..G for 0..3, DH/DM/DL/DEGREE).
display_pattern() {
    case "$1" in
        0) display_on=ABCDEF;; 1) display_on=BC;; 2) display_on=ABDEG;;
        3) display_on=ABCDG;; 4) display_on=BCFG;; 5) display_on=ACDFG;;
        6) display_on=ACDEFG;; 7) display_on=ABC;; 8) display_on=ABCDEFG;;
        9) display_on=ABCDFG;; ' ') display_on='';; *) return 1;;
    esac
}
display_build() {
    display_text=$1
    display_level=$2
    display_colon=${3:-0}
    display_values=''
    display_index=0
    while [ "$display_index" -lt 4 ]; do
        display_char=${display_text%"${display_text#?}"}
        display_text=${display_text#?}
        display_pattern "$display_char" || return 1
        for display_segment in A B C D E F G; do
            display_value=0
            case "$display_on" in *"$display_segment"*) display_value=$display_level;; esac
            display_values="$display_values $display_value"
        done
        display_index=$((display_index + 1))
    done
    display_dot=0
    [ "$display_colon" = 0 ] || display_dot=$display_level
    display_values="$display_values $display_dot 0 $display_dot 0"
}
display_volume() {
    case "$1" in ''|*[!0-9]*) return 1;; esac
    [ "$1" -le 100 ] || return 1
    if [ "$1" = 0 ] || [ "$2" = down ]; then
        display_padded=$(printf '%-4s' "$1")
    else display_padded=$(printf '%4s' "$1")
    fi
    display_build "$display_padded" "$3"
}
display_clock() {
    # Anchor is sampled from the real clock before taking ownership. This
    # preserves the device's displayed timezone, independent of system UTC.
    IFS=' ' read -r display_epoch display_minutes display_brightness display_blank < "$1" || return 1
    case "$display_epoch:$display_minutes:$display_brightness:$display_blank" in *[!0-9:]*) return 1;; esac
    [ "$display_minutes" -lt 1440 ] && [ "$display_brightness" -le 255 ] || return 1
    display_now=${ALISA_CLOCK_EPOCH_SECONDS:-$(date +%s)}
    case "$display_now" in ''|*[!0-9]*) return 1;; esac
    display_total=$(( (display_minutes + display_now / 60 - display_epoch) % 1440 ))
    [ "$display_total" -ge 0 ] || display_total=$((display_total + 1440))
    display_hour=$((display_total / 60))
    display_minute=$((display_total % 60))
    display_time=$(printf '%02d%02d' "$display_hour" "$display_minute")
    if [ "$display_blank" = 1 ] && [ "$display_hour" -lt 10 ]; then display_time=" ${display_time#?}"; fi
    display_build "$display_time" "$display_brightness" 1
}
display_apply_changes() {
    display_prefix=$2
    set -- $1
    [ "$#" -eq 32 ] || return 1
    for display_part in A0 B0 C0 D0 E0 F0 G0 A1 B1 C1 D1 E1 F1 G1 A2 B2 C2 D2 E2 F2 G2 A3 B3 C3 D3 E3 F3 G3 DH DM DL DEGREE; do
        display_current=''
        IFS= read -r display_current < "$display_prefix$display_part/brightness" || [ -n "$display_current" ] || return 1
        # A redundant sysfs write still reaches the LED driver. Avoid touching
        # already-correct segments during the clock ownership handoff.
        if [ "$display_current" != "$1" ]; then
            printf '%s\n' "$1" > "$display_prefix$display_part/brightness" || return 1
        fi
        shift
    done
}
