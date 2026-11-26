#!/bin/sh
# Street retail .376. Disable first; change no other init entries.
set -eu
export PATH=/usr/bin:/bin:/usr/sbin:/sbin LC_ALL=C
umask 077
B=/bkupgrade/data/street-alisa-port-v1
Q=/system/vendor/quasar
S=/etc/init.d/S99alisa-port
[ "$(id -u)" = 0 ]
[ -d "$B" ] && [ ! -L "$B" ]
mount_is() { awk -v mode="$1" '$1=="ubi0:rootfs" && $2=="/" && $3=="ubifs" && $4~("^"mode"(,|$)"){ok=1} END{exit !ok}' /proc/mounts; }
: >"$B/DISABLED"
# Ask the permanent supervisor to drain and verify stock before removing its hook.
if [ -x "$B/stack.sh" ] && [ -d "$B/run" ]; then
    "$B/stack.sh" stop >/dev/null || :
fi
exec 9>"$B/run/service.lock"
n=0
until flock -xn 9; do
    n=$((n+1)); [ "$n" -lt 15 ] || exit 75
    sleep 1
done
mount_is ro
if [ -e "$S" ] || [ -L "$S" ]; then
    trap 'sync; mount -o remount,ro /; mount_is ro' EXIT
    trap 'exit 1' HUP INT TERM
    mount -o remount,rw /
    mount_is rw
    rm -f "$S"
    sync
    mount -o remount,ro /
    mount_is ro
    trap - EXIT HUP INT TERM
fi
[ ! -e "$S" ] && [ ! -L "$S" ]
# On BusyBox q restart uses the stock launch.json, not our copy.
env -i PATH="$PATH" "$Q/q" restart 9>&- </dev/null >/dev/null 2>&1 &
r=$!
n=0
while kill -0 "$r" 2>/dev/null; do
    n=$((n+1))
    if [ "$n" -gt 20 ]; then kill -KILL "$r" 2>/dev/null || :; exit 70; fi
    sleep 1
done
wait "$r" || exit 70
argv() { tr '\000' ' ' <"/proc/$1/cmdline" 2>/dev/null; }
for n in $(seq 1 50); do
    set -- $(pidof quasar_launcher2 2>/dev/null || :)
    if [ "$#" = 1 ] && [ "$(argv "$1")" = "$Q/quasar_launcher2 $Q/launch.json " ]; then
        l=$1
        for m in $(pidof maind 2>/dev/null || :); do
            [ "$(argv "$m")" = "$Q/maind --platformd " ] || continue
            awk -v l="$l" '$1=="PPid:"&&$2==l{ok=1} END{exit !ok}' "/proc/$m/status" || continue
            awk '/libyandex_evil[.]so/{native=1} /street-alisa-port|vqe_tap|native_agent/{foreign=1} END{exit !(native&&!foreign)}' "/proc/$m/maps" || continue
            awk '$2=="0100007F:2693" && $4=="0A"{ok=1} END{exit !ok}' /proc/net/tcp || continue
            mount_is ro
            printf 'uninstall=ok S99=absent rootfs=ro stock_launcher=%s platformd=%s nonstock_mappings=0 quasar_listener=ready\n' "$l" "$m"
            exit 0
        done
    fi
    sleep 1
done
printf '%s\n' 'uninstall=stock_not_verified' >&2
exit 71
