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
restore() (
    dir=$1
    [ -d "$dir" ] || exit 0
    exec 9>"$dir/restore.lock"; flock -x 9
    [ ! -f "$dir/released" ] || exit 0
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
    for part in $parts; do
        target=$(target_for "$part")
        if mounted "$target"; then
            owned_mount "${dir#/tmp}/shadow/$part" "$target" || exit 1
        fi
    done
    for part in $parts; do
        target=$(target_for "$part")
        if mounted "$target"; then
            # Replay the latest value supplied by the stock renderer.
            value=$(cat "$dir/shadow/$part")
            case "$value" in ''|*[!0-9]*) exit 1;; esac
            [ "$value" -le 255 ] || exit 1
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
)
if [ "${1:-}" = guard ]; then
    dir=$2; owner=$3
    trap - TERM INT HUP
    while [ -d "$dir" ] && [ ! -f "$dir/released" ]; do
        sleep 1
        now=$(cut -d. -f1 /proc/uptime); lease=0
        [ ! -r "$dir/lease" ] || read -r lease < "$dir/lease"
        case "$lease" in ''|*[!0-9]*) lease=0;; esac
        if ! kill -0 "$owner" 2>/dev/null || [ $((now-lease)) -gt 4 ]; then restore "$dir" || true; fi
    done
    exit 0
fi
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
finish() { restore "$dir" || true; }
trap finish EXIT
trap 'exit 0' TERM INT HUP
touch_lease() { cut -d. -f1 /proc/uptime > "$dir/lease.new"; mv "$dir/lease.new" "$dir/lease"; }
touch_lease
sh "$BASE/midi-clock.sh" guard "$dir" "$$" </dev/null >/dev/null 2>&1 &
mount -t none --bind "$root0" "$dir/original0"
mount -t none --bind "$root1" "$dir/original1"
for part in $parts; do
    cat "$(physical_for "$part")" > "$dir/shadow/$part"
    mount -t none --bind "$dir/shadow/$part" "$(target_for "$part")"
    touch_lease
done
# Write only through the preserved physical sysfs view.

for part in $parts; do printf '0\n' > "$(physical_for "$part")"; done
pattern() {
 case "$1" in
 0) on=abcdef;; 1) on=bc;; 2) on=abdeg;; 3) on=abcdg;;
 4) on=bcfg;; 5) on=acdfg;; 6) on=acdefg;; 7) on=abc;;
 8) on=abcdefg;; 9) on=abcdfg;; C) on=adef;; h) on=cefg;; a) on=abcdeg;; t) on=defg;; G) on=acdefg;; P) on=abefg;; T) on=defg;; ' ') on='';; *) return 1;; esac
}
previous='';tick=0;matrix_seed=7391;matrix_stream=7391
while IFS= read -r -t 2 command; do
    touch_lease
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
    for n in 1 2 3 4; do
        ch=${text%"${text#?}"};text=${text#?};pattern "$ch"
        for part in a b c d e f g; do
            value=0;case "$on" in *"$part"*) value=100;; esac
            printf '%s\n' "$value" > "$(physical_for "$part$n")"
        done
    done
done
