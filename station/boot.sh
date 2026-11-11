#!/bin/sh
# This process is started once at boot and exits; runtime supervisors own restart.
set -eu
BASE=/data/opt/alisa-cli
case "${1:-}" in
launch) nohup "$BASE/boot.sh" wait > /tmp/alisa-cli-boot.log 2>&1 </dev/null & exit 0 ;;
wait)
    count=0
    while [ "$count" -lt 120 ]; do
        if pidof quasar_launcher2 >/dev/null; then break; fi
        sleep 1;count=$((count+1))
    done
    sleep 5
    if [ -f "$BASE/vpn-autostart" ]; then
        task_vpn_attempt=0
        until "$BASE/vpn.sh" start; do
            task_vpn_attempt=$((task_vpn_attempt+1))
            if [ "$task_vpn_attempt" -ge 3 ]; then
                echo 'VPN is not ready; native autostart was not run.' >&2
                exit 1
            fi
            sleep 2
        done
    fi
    if [ -f "$BASE/native-autostart" ]; then
        if [ -f "$BASE/vpn-required" ]; then "$BASE/vpn.sh" ready || exit 1; fi
        /data/opt/alisa-standalone/run.sh start
    fi
    ;;
*) exit 2 ;;
esac
