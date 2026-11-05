#!/bin/sh
# Scoped ownership of Bergamot's clock sysfs. No firmware/settings edits.
BASE=${BASE:-$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)}
. "$BASE/clock-display.sh"
iso_parts='A0 B0 C0 D0 E0 F0 G0 A1 B1 C1 D1 E1 F1 G1 A2 B2 C2 D2 E2 F2 G2 A3 B3 C3 D3 E3 F3 G3 DH DM DL DEGREE'
iso_now() {
    IFS=' ' read -r iso_uptime iso_rest < /proc/uptime || return 1
    iso_seconds=${iso_uptime%%.*}
}
iso_mounted() {
    awk -v root="$1" -v target="$2" '$5==target {count++; if($4==root)found=1} END {exit !(found && count==1)}' /proc/self/mountinfo
}
iso_read_shadow_frame() {
    iso_frame=''
    for iso_part in $iso_parts; do
        iso_value=''
        IFS= read -r iso_value < "$iso_dir/shadow/$iso_part" || [ -n "$iso_value" ] || return 1
        case "$iso_value" in ''|*[!0-9]*) return 1;; esac
        [ "$iso_value" -le 255 ] || return 1
        iso_frame="$iso_frame $iso_value"
    done
}
iso_frame_is_clock() {
    set -- $iso_frame
    [ "$#" -eq 32 ] || return 1
    iso_digits=''
    iso_brightness=0
    iso_digit=0
    iso_leading_blank=0
    while [ "$iso_digit" -lt 4 ]; do
        iso_pattern=''
        for iso_segment in A B C D E F G; do
            if [ "$1" -gt 0 ]; then
                iso_pattern="$iso_pattern$iso_segment"
                if [ "$iso_brightness" -eq 0 ]; then iso_brightness=$1; fi
                [ "$1" -eq "$iso_brightness" ] || return 1
            fi
            shift
        done
        case "$iso_pattern" in
            '') [ "$iso_digit" -eq 0 ] || return 1; iso_number=0; iso_leading_blank=1;;
            ABCDEF) iso_number=0;; BC) iso_number=1;; ABDEG) iso_number=2;;
            ABCDG) iso_number=3;; BCFG) iso_number=4;; ACDFG) iso_number=5;;
            ACDEFG) iso_number=6;; ABC) iso_number=7;; ABCDEFG) iso_number=8;;
            ABCDFG) iso_number=9;; *) return 1;;
        esac
        iso_digits="$iso_digits$iso_number"
        iso_digit=$((iso_digit + 1))
    done
    [ "$iso_brightness" -gt 0 ] && [ "$1" -eq "$iso_brightness" ] &&
        [ "$2" -eq 0 ] && [ "$3" -eq "$iso_brightness" ] && [ "$4" -eq 0 ] || return 1
    iso_hours=${iso_digits%??}
    iso_minutes=${iso_digits#??}
    [ $((1$iso_hours - 100)) -le 23 ] && [ $((1$iso_minutes - 100)) -le 59 ]
}
iso_restore() (
    iso_dir=$1
    [ -d "$iso_dir" ] || exit 0
    exec 9>"$iso_dir/restore.lock" || exit 1
    flock -x 9 || exit 1
    [ -f "$iso_dir/released" ] && exit 0
    IFS= read -r iso_real < "$iso_dir/real" || exit 1
    case "$iso_real" in /sys/devices/*/leds) :;; *) exit 1;; esac
    # Paint one validated, frozen clock frame WHILE all stock writes are
    # still isolated. Replaying segment files after unmount races maind.
    if [ -f "$iso_dir/ready" ] && [ ! -f "$iso_dir/frame-applied" ]; then
        # Let the native close transition settle behind the isolation, then
        # synthesize current time. Never replay independent shadow segments.
        sleep .25
        display_clock "$iso_dir/clock.anchor" || exit 1
        iso_frame=$display_values
        iso_restore_kind=generated_clock
        # Check all 32 overlays before writing even the first physical segment.
        for iso_part in $iso_parts; do
            iso_mounted "${iso_dir#/tmp}/shadow/$iso_part" "$iso_real/bergamot:white:clock$iso_part/brightness" || exit 1
        done
        display_apply_changes "$iso_frame" "$iso_dir/original/bergamot:white:clock" || exit 1
        : > "$iso_dir/frame-applied"
        # Keep stock writes isolated briefly after the full clock frame is
        # visible, so native close transitions settle before ownership returns.
        sleep 1
        # Clock diagnostics only: no voice, account or conversation data.
        printf '%s|%s\n' "$iso_restore_kind" "$iso_frame" > /tmp/alisa-clock-eyes-last-restore.new
        mv /tmp/alisa-clock-eyes-last-restore.new /tmp/alisa-clock-eyes-last-restore
    fi
    for iso_part in $iso_parts; do
        iso_target="$iso_real/bergamot:white:clock$iso_part/brightness"
        if awk -v p="$iso_target" '$5==p {found=1} END {exit !found}' /proc/self/mountinfo; then
            iso_mounted "${iso_dir#/tmp}/shadow/$iso_part" "$iso_target" || exit 1
            umount "$iso_target" || exit 1
        fi
    done
    # No physical writes after exposing the stock paths again.
    if iso_mounted "${iso_real#/sys}" "$iso_dir/original"; then
        umount "$iso_dir/original" || exit 1
    fi
    : > "$iso_dir/released"
    # Explicit paths only; never recursively remove a mounted sysfs view.
    for iso_part in $iso_parts; do rm -f "$iso_dir/shadow/$iso_part"; done
    for iso_subdir in shadow original; do
        [ ! -d "$iso_dir/$iso_subdir" ] || rmdir "$iso_dir/$iso_subdir" || exit 1
    done
    rm -f "$iso_dir/clock.anchor" "$iso_dir/ready" "$iso_dir/frame-applied" "$iso_dir/restore.started" "$iso_dir/real" "$iso_dir/lease" "$iso_dir/lease.new" "$iso_dir/restore.lock" "$iso_dir/released"
    rmdir "$iso_dir"
)
iso_guard() {
    iso_guard_dir=$1
    iso_owner=$2
    # Do not inherit the daemon's signal dispositions. FD 8 deliberately keeps
    # the ownership lock until recovery finishes, even after owner SIGKILL.
    trap - HUP INT TERM
    while [ -d "$iso_guard_dir" ]; do
        sleep 1
        [ -d "$iso_guard_dir" ] || break
        iso_now || continue
        iso_lease=0
        if [ -r "$iso_guard_dir/lease" ]; then
            IFS= read -r iso_lease < "$iso_guard_dir/lease" || iso_lease=0
        fi
        case "$iso_lease" in ''|*[!0-9]*) iso_lease=0;; esac
        if ! kill -0 "$iso_owner" 2>/dev/null || [ $((iso_seconds - iso_lease)) -ge 5 ]; then
            # Retry partial cleanup; never leave mounts behind merely because
            # the first unmount was briefly busy.
            iso_restore "$iso_guard_dir" || true
        fi
    done
}
iso_acquire() {
    ISO_SESSION=''
    command -v flock >/dev/null 2>&1 || return 1
    # The mountinfo root mapping below is valid for this verified /tmp layout.
    awk '$4=="/" && $5=="/tmp" && / - tmpfs / {ok=1} END {exit !ok}' /proc/self/mountinfo || return 1
    exec 8>/tmp/alisa-clock-eyes.lock
    if ! flock -n 8; then exec 8>&-; return 1; fi
    iso_real=$(readlink -f /sys/class/leds/bergamot:white:clockA0) || { exec 8>&-; return 1; }
    iso_real=${iso_real%/*}
    case "$iso_real" in /sys/devices/*/leds) :;; *) exec 8>&-; return 1;; esac
    # Refuse pre-existing overlays instead of stacking over another owner.
    for iso_part in $iso_parts; do
        if awk -v p="$iso_real/bergamot:white:clock$iso_part/brightness" '$5==p {found=1} END {exit !found}' /proc/self/mountinfo; then
            exec 8>&-; return 1
        fi
    done
    ISO_SESSION=$(mktemp -d /tmp/alisa-clock-eyes.XXXXXX) || { exec 8>&-; return 1; }
    chmod 700 "$ISO_SESSION"
    printf '%s\n' "$iso_real" > "$ISO_SESSION/real"
    mkdir "$ISO_SESSION/original" "$ISO_SESSION/shadow" || { iso_release; return 1; }
    iso_touch || { iso_release; return 1; }
    # Guardian is armed before the first mount, including partial acquisition.
    sh "$BASE/clock-isolation.sh" guard "$ISO_SESSION" "$$" </dev/null >/dev/null 2>&1 &
    if ! mount -t none --bind "$iso_real" "$ISO_SESSION/original"; then iso_release; return 1; fi
    iso_entry_epoch=$(date +%s)
    for iso_part in $iso_parts; do
        if ! cat "$ISO_SESSION/original/bergamot:white:clock$iso_part/brightness" > "$ISO_SESSION/shadow/$iso_part"; then iso_release; return 1; fi
    done
    iso_after_epoch=$(date +%s)
    # Retry acquisition if a minute changed during the initial capture.
    if [ $((iso_entry_epoch / 60)) != $((iso_after_epoch / 60)) ]; then iso_release; return 1; fi
    iso_dir=$ISO_SESSION
    if ! iso_read_shadow_frame || ! iso_frame_is_clock; then iso_release; return 1; fi
    printf '%s %s %s %s\n' "$((iso_entry_epoch / 60))" "$(((1$iso_hours - 100) * 60 + 1$iso_minutes - 100))" "$iso_brightness" "$iso_leading_blank" > "$ISO_SESSION/clock.anchor"
    for iso_part in $iso_parts; do
        if ! mount -t none --bind "$ISO_SESSION/shadow/$iso_part" "$iso_real/bergamot:white:clock$iso_part/brightness"; then iso_release; return 1; fi
        iso_touch || { iso_release; return 1; }
    done
    : > "$ISO_SESSION/ready"
}
iso_touch() {
    [ -n "${ISO_SESSION:-}" ] && [ -d "$ISO_SESSION" ] || return 1
    iso_now || return 1
    printf '%s\n' "$iso_seconds" > "$ISO_SESSION/lease.new" &&
        mv "$ISO_SESSION/lease.new" "$ISO_SESSION/lease"
}
iso_release() {
    if [ -n "${ISO_SESSION:-}" ]; then
        iso_restore "$ISO_SESSION" || return 1
        ISO_SESSION=''
    fi
    exec 8>&-
}
if [ "${1:-}" = guard ]; then
    iso_guard "$2" "$3"
    exit 0
fi
