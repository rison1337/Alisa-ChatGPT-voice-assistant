#!/bin/sh
# Cucumber-only four-digit display. Firmware writes continue into temporary shadows.
set -efu
BASE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
parts=''
for p in a b c d e f g; do for n in 1 2 3 4; do parts="$parts $p$n"; done; done
parts="$parts deg h1 h2 l1 l2 m1 m2 p1 p2 p3 p4 p5 q1 q2 q3 q4 q5"
target_for() { readlink -f "/sys/class/leds/cucumber:white:clock-$1/brightness"; }
physical_for() {
    phys_target=$(target_for "$1")
    case "$phys_target" in
        "$root0"/*) printf '%s/%s' "$dir/original0" "${phys_target#"$root0"/}";;
        "$root1"/*) printf '%s/%s' "$dir/original1" "${phys_target#"$root1"/}";;
        *) return 1;;
    esac
}
mounted() { awk -v p="$1" '$5==p {ok=1} END {exit !ok}' /proc/self/mountinfo; }
owned_mount() {
    awk -v root="$1" -v target="$2" '$5==target {count++; if($4==root)found=1} END {exit !(found && count==1)}' /proc/self/mountinfo
}
# All mount and physical-write transactions share this lock with recovery.
# A closing marker prevents a paused owner from resuming after the guardian.
transaction() (
    [ -d "$dir" ] || exit 1
    exec 9>"$dir/restore.lock" || exit 1
    flock -x 9 || exit 1
    [ -f "$dir/active" ] && [ ! -e "$dir/closing" ] || exit 1
    "$@"
)
restore() (
    dir=$1
    [ -d "$dir" ] || exit 0
    rm -f "$dir/active" || exit 1
    : > "$dir/closing"
    exec 9>"$dir/restore.lock"; flock -x 9
    [ -d "$dir" ] || exit 0
    IFS= read -r root0 < "$dir/root0"
    IFS= read -r root1 < "$dir/root1"
    for root in "$root0" "$root1"; do case "$root" in /sys/devices/*/leds) :;; *) exit 1;; esac; done
    # Refuse foreign/stacked mounts before writing any physical segment.
    for slot in 0 1; do
        eval 'root=$root'"$slot"
        if mounted "$dir/original$slot"; then
            owned_mount "${root#/sys}" "$dir/original$slot" || exit 1
        fi
    done
    overlays=0
    for part in $parts; do
        target=$(target_for "$part")
        if mounted "$target"; then
            owned_mount "${dir#/tmp}/shadow/$part" "$target" || exit 1
            overlays=1
        fi
    done
    if [ "$overlays" = 1 ]; then
        owned_mount "${root0#/sys}" "$dir/original0" || exit 1
        owned_mount "${root1#/sys}" "$dir/original1" || exit 1
    fi
    for part in $parts; do
        target=$(target_for "$part")
        if mounted "$target"; then
            # Replay the latest value supplied by the stock renderer.
            value=$(cat "$dir/shadow/$part")
            # Corrupt/partial stock data must not prevent releasing ownership.
            case "$value" in ''|*[!0-9]*) continue;; esac
            [ "$value" -le 255 ] || continue
            printf '%s\n' "$value" > "$(physical_for "$part")"
        fi
    done
    for part in $parts; do
        target=$(target_for "$part")
        if mounted "$target"; then
            owned_mount "${dir#/tmp}/shadow/$part" "$target" || exit 1
            umount "$target" || exit 1
        fi
    done
    for slot in 0 1; do if mounted "$dir/original$slot"; then umount "$dir/original$slot" || exit 1; fi; done
    : > "$dir/released"
    # Only exact regular shadow/state paths; never recursively delete sysfs views.
    for part in $parts; do rm -f "$dir/shadow/$part"; done
    for folder in shadow original0 original1; do
        [ ! -d "$dir/$folder" ] || rmdir "$dir/$folder" || exit 1
    done
    rm -f "$dir/root0" "$dir/root1" "$dir/lease" "$dir/lease.new" \
        "$dir/closing" "$dir/released" "$dir/restore.lock"
    rmdir "$dir"
)
touch_lease() { cut -d. -f1 /proc/uptime > "$dir/lease.new"; mv "$dir/lease.new" "$dir/lease"; }
acquire() {
    mount -t none --bind "$root0" "$dir/original0" || return 1
    mount -t none --bind "$root1" "$dir/original1" || return 1
    for part in $parts; do
        cat "$(physical_for "$part")" > "$dir/shadow/$part" || return 1
        mount -t none --bind "$dir/shadow/$part" "$(target_for "$part")" || return 1
        touch_lease || return 1
    done
    for part in $parts; do printf '0\n' > "$(physical_for "$part")"; done
}
paint() {
    touch_lease
    for n in 1 2 3 4; do
        ch=${text%"${text#?}"};text=${text#?};pattern "$ch"
        for part in a b c d e f g; do
            value=0;case "$on" in *"$part"*) value=100;; esac
            printf '%s\n' "$value" > "$(physical_for "$part$n")"
        done
    done
}
guard() (
    dir=$1; owner=$2
    trap - TERM INT HUP
    while [ -d "$dir" ]; do
        sleep 1
        now=$(cut -d. -f1 /proc/uptime); lease=0
        if [ -r "$dir/lease" ]; then
            IFS= read -r lease < "$dir/lease" || lease=0
        fi
        case "$lease" in ''|*[!0-9]*) lease=0;; esac
        if ! kill -0 "$owner" 2>/dev/null || [ $((now-lease)) -gt 4 ]; then restore "$dir" || true; fi
    done
    exit 0
)
# Tests source the same transactions with a file-backed mount implementation.
if [ "${1:-}" = library ]; then return 0; fi
if [ "${1:-}" = guard ]; then guard "$2" "$3"; exit $?; fi
tr '\000' '\n' < /proc/device-tree/compatible | grep -qx yandex,cucumber || exit 0
# Ownership checks below require these exact mount roots.
awk '$4=="/" && $5=="/tmp" && / - tmpfs / {ok=1} END {exit !ok}' /proc/self/mountinfo || exit 1
awk '$4=="/" && $5=="/sys" && / - sysfs / {ok=1} END {exit !ok}' /proc/self/mountinfo || exit 1
exec 8>/tmp/alisa-midi-clock.lock
flock -n 8 || exit 0
root0=$(readlink -f /sys/class/leds/cucumber:white:clock-a1);root0=${root0%/*}
root1=$(readlink -f /sys/class/leds/cucumber:white:clock-a4);root1=${root1%/*}
for root in "$root0" "$root1"; do case "$root" in /sys/devices/*/leds) :;; *) exit 1;; esac; done
for part in $parts; do
    target=$(target_for "$part");[ -e "$target" ] || exit 1
    if mounted "$target"; then exit 1; fi
done
dir=$(mktemp -d /tmp/alisa-midi-clock-XXXXXX)
chmod 700 "$dir"; printf '%s\n' "$root0" > "$dir/root0"; printf '%s\n' "$root1" > "$dir/root1"
mkdir "$dir/original0" "$dir/original1" "$dir/shadow"
: > "$dir/active"
finish() { restore "$dir" || true; }
trap finish EXIT
trap 'exit 0' TERM INT HUP
touch_lease
sh "$BASE/midi-clock.sh" guard "$dir" "$$" </dev/null >/dev/null 2>&1 &
transaction acquire
pattern() {
 case "$1" in
 0) on=abcdef;; 1) on=bc;; 2) on=abdeg;; 3) on=abcdg;;
 4) on=bcfg;; 5) on=acdfg;; 6) on=acdefg;; 7) on=abc;;
 8) on=abcdefg;; 9) on=abcdfg;; C) on=adef;; h) on=cefg;; a) on=abcdeg;; t) on=defg;; G) on=acdefg;; P) on=abefg;; T) on=defg;; ' ') on='';; *) return 1;; esac
}
previous='';tick=0;matrix_seed=7391;matrix_stream=7391
while IFS= read -r -t 2 command; do
    transaction touch_lease
    case "$command" in 0) exit 0;; B|F\ *) :;; *) continue;; esac
    # Continuous four-character stream; no blank frame between cycles.
    tick=$((tick+1))
    [ $((tick % 2)) = 0 ] || continue
    matrix_seed=$(( (matrix_seed * 25173 + 13849) % 65536 ))
    matrix_digit=$((matrix_seed % 10))
    matrix_stream="${matrix_stream#?}$matrix_digit"
    text=$matrix_stream
    case $((tick / 2 % 28)) in
        20|21|22|23) text="GPT$matrix_digit";;
    esac
    [ "$text" != "$previous" ] || continue
    previous=$text
    transaction paint
done
