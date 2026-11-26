#!/bin/sh
# Runtime-only bind overlay for the stock Mango standby dispatcher.
# Missing/expired ownership always delegates to the byte-identical stock script.
D=/run/street-alisa-standby
B=/bkupgrade/data/street-alisa-port-v1
if [ "$#" = 2 ] && [ "$1" = -l ] && { [ "$2" = 0 ] || [ "$2" = 1 ]; } &&
   [ ! -e "$B/DISABLED" ] && [ ! -e "$B/run/STOP" ] &&
   read -r boot pid tick <"$D/owner" && [ -n "$tick" ] &&
   [ "$(cat /proc/sys/kernel/random/boot_id)" = "$boot" ] &&
   [ "$(awk '{sub(/^.*\) /, ""); print $20}' "/proc/$pid/stat" 2>/dev/null)" = "$tick" ] &&
   grep -qw street-alisa-port-v1 /sys/power/wake_lock; then
    caller=$(tr -cd '[:alnum:]_.-' <"/proc/$PPID/comm" 2>/dev/null)
    read -r uptime _ </proc/uptime
    printf 'uptime=%s standby=inhibited level=%s caller=%s\n' "${uptime%%.*}" "$2" "${caller:-unknown}" >>"$D/blocked.log" 2>/dev/null || :
    exit 0
fi
exec /bin/sh "$D/stock" "$@"
