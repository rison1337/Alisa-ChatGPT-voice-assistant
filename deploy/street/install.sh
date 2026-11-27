#!/bin/sh
set -eu
umask 077
B=/bkupgrade/data/street-alisa-port-v1
cd "$B"
[ "$#" = 3 ]
[ -f DISABLED ] && [ ! -L DISABLED ]
[ "$(sha256sum MANIFEST | cut -d' ' -f1)" = fc0a5c4e0a620ace17f5dc646099c281fb0e0c9380d6ac4d9c1eb33d924fdc45 ]
sha256sum -c MANIFEST
./stack.sh stop
exec 9>run/service.lock
flock -xn 9
D=.standby-soak
mkdir "$D"
busybox tar -xf - -C "$D"
[ "$(sha256sum "$D/MANIFEST" | cut -d' ' -f1)" = "$1" ]
[ "$(sha256sum "$D/soak.sh" | cut -d' ' -f1)" = "$2" ]
(cd "$D"; awk '$2=="stack.sh"||$2=="standby-inhibit.sh"{print;n++} END{exit n!=2}' MANIFEST | sha256sum -c -)
[ "$(sha256sum "$D/warmup.sh" | cut -d' ' -f1)" = "$3" ]
for f in stack.sh soak.sh standby-inhibit.sh warmup.sh; do /bin/sh -n "$D/$f"; done
cp -p stack.sh "$D/stack.sh.before"
cp -p MANIFEST "$D/MANIFEST.before"
undo() {
    rc=$?; trap - EXIT
    cp -p "$D/stack.sh.before" stack.sh
    cp -p "$D/MANIFEST.before" MANIFEST
    rm -f standby-inhibit.sh
    exit "$rc"
}
[ ! -e standby-inhibit.sh ] && [ ! -L standby-inhibit.sh ]
trap undo EXIT
chown 0:0 "$D/stack.sh" "$D/standby-inhibit.sh" "$D/MANIFEST" "$D/soak.sh" "$D/warmup.sh"
chmod 700 "$D/stack.sh" "$D/standby-inhibit.sh"
chmod 600 "$D/MANIFEST" "$D/soak.sh" "$D/warmup.sh"
mv "$D/standby-inhibit.sh" standby-inhibit.sh
mv "$D/stack.sh" stack.sh
mv "$D/MANIFEST" MANIFEST
sha256sum -c MANIFEST
trap - EXIT
exec 9>&-
# Keep the lifecycle script separate from SSH/shell quoting.
nohup setsid /bin/sh "$B/$D/warmup.sh" </dev/null >run/health-soak.log 2>&1 &
printf 'soak_driver_pid=%s\n' "$!"
