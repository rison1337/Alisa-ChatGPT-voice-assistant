#!/bin/sh
# Read-only acceptance after a separately authorized cold boot. Never starts services.
set -eu
B=/bkupgrade/data/street-alisa-port-v1
S=/etc/init.d/S99alisa-port
[ "$#" = 1 ] || exit 64
before=$1
printf '%s\n' "$before" | grep -Eq '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' || exit 64
boot=$(cat /proc/sys/kernel/random/boot_id)
printf 'boot_before=%s boot_after=%s\n' "$before" "$boot"
[ "$boot" != "$before" ] || exit 75
cd "$B"
[ ! -e DISABLED ] && [ -x "$S" ] && cmp -s S99alisa-port "$S"
awk '$1=="ubi0:rootfs" && $2=="/" && $3=="ubifs" && $4~/^ro(,|$)/{ok=1} END{exit !ok}' /proc/mounts
sha256sum -c MANIFEST
status() { ./stack.sh status 2>/dev/null | awk '/^supervisor=[0-9]+ launcher=[0-9]+ platformd=[0-9]+ holder=[0-9]+ client=[0-9]+ agent=[0-9]+ relay=[0-9]+ tap_map=1 agent_map=1$/{n++; print} END{exit n!=1}'; }
voice() {
    busybox od -An -v -tu4 /run/street-alisa-vqe.stats 2>/dev/null | awk '{for(i=1;i<=NF;i++)a[++n]=$i} END{if(n!=20||a[1]!=1||a[2]!=1||!a[8])exit 1; print a[5],a[16]}'
}
deadline=$(($(cut -d. -f1 /proc/uptime)+220))
until profile=$(status) && baseline=$(voice); do
    [ "$(cut -d. -f1 /proc/uptime)" -lt "$deadline" ] || exit 69
    sleep 2
done
read -r takeover sup tick <run/TAKEOVER
[ "$takeover" = "$boot" ]
read -r owner owner_pid owner_tick </run/street-alisa-standby/owner
[ "$owner:$owner_pid:$owner_tick" = "$takeover:$sup:$tick" ]
grep -qw street-alisa-port-v1 /sys/power/wake_lock
awk '$4=="/street-alisa-standby/inhibit"&&$5=="/usr/bin/yandex-standby"{n++} END{exit n!=1}' /proc/self/mountinfo
sleep 3
final=$(voice)
[ "$(status)" = "$profile" ]
set -- $baseline $final
[ "$3" -gt "$1" ] && [ "$4" -gt "$2" ]
printf 'cold_stack=ready %s process_delta=%s eligible_delta=%s\n' "$profile" "$(($3-$1))" "$(($4-$2))"
