#!/bin/sh
# Read-only, after (or during) the detached test. Emit only fixed diagnostics.
set -eu
B=/bkupgrade/data/street-alisa-port-v1
Q=/system/vendor/quasar
cd "$B"
printf 'readback_boot_id='; cat /proc/sys/kernel/random/boot_id
printf 'readback_uptime='; cut -d' ' -f1 /proc/uptime
if [ -e DISABLED ]; then echo 'readback_disabled=1'; else echo 'readback_disabled=0'; fi
printf 'readback_suspend_success='; cat /sys/power/suspend_stats/success
if grep -qw street-alisa-port-v1 /sys/power/wake_lock; then echo 'readback_wake_active=1'; else echo 'readback_wake_active=0'; fi
printf 'readback_autosleep='; cat /sys/power/autosleep
printf 'readback_mem_sleep='; cat /sys/power/mem_sleep
awk '$2=="/" && $4~/^ro(,|$)/{print "rootfs_ro=1"}' /proc/mounts
awk '$4=="/street-alisa-standby/inhibit" && $5=="/usr/bin/yandex-standby"{seen++} END{print "readback_standby_overlay=" (seen==1)}' /proc/self/mountinfo
if [ -r /run/street-alisa-standby/blocked.log ]; then
    awk '/^uptime=[0-9]+ standby=inhibited level=[01] caller=[A-Za-z0-9_.-]+$/' /run/street-alisa-standby/blocked.log
fi
if cmp -s S99alisa-port /etc/init.d/S99alisa-port && [ -x /etc/init.d/S99alisa-port ]; then echo 'readback_hook_installed=1'; fi
printf 'manifest_digest='; sha256sum MANIFEST | cut -d' ' -f1
sha256sum -c MANIFEST
awk '/^(soak=|sample=|voice=|power=|warmup=|standby=|stock_launcher=|disabled=)/' run/health-soak.log
awk '/^uptime=[0-9]+ (baseline|holder_pid|q_stop_rc|launcher|client|wake=|standby_overlay=|health=|integrity=|end stage=|watchdog=|stock=|q_restart_rc=)/' run/stack.log
awk '/^discard_only=1 packet_320=[0-9]+ packet_192=[0-9]+ packet_other=[0-9]+ payload_bytes_copied=0$/' run/health-packet-proof.log 2>/dev/null || :
if [ ! -e /run/street-packet-count-proof/observer ] && [ ! -e run/client/microphone.sock ]; then echo 'observer_and_idle_socket_removed=1'; fi
if [ ! -e DISABLED ]; then
    ./stack.sh status 2>/dev/null | awk '/^supervisor=[0-9]+ launcher=[0-9]+ platformd=[0-9]+ holder=[0-9]+ client=[0-9]+ agent=[0-9]+ relay=[0-9]+ tap_map=1 agent_map=1$/{print}'
    ./stack.sh status 2>/dev/null | awk '/^counters=/{sub(/^counters=/, "");print}' | jq -c '{phase,calls,microphone_frames}'
    awk '$2 ~ /:46A0$/ && $4=="0A"{n++; if($2=="0100007F:46A0")m++} END{if(n==1&&m==1)print "unique_tcp_control_loopback=1"}' /proc/net/tcp /proc/net/tcp6
    awk '$2=="/run" && $3=="tmpfs"{print "runtime_tmpfs=1"}' /proc/mounts
    awk 'END{if(NR==1)print "swap_devices=0"}' /proc/swaps
    [ -f run/client/focus-0.wav ] && cmp -s focus-0.wav run/client/focus-0.wav && echo 'runtime_focus_equals_packaged_silence=1'
else
    set -- $(pidof quasar_launcher2)
    [ "$#" = 1 ] || exit 71
    stock_l=$1
    [ "$(tr '\000' ' ' </proc/$stock_l/cmdline)" = "$Q/quasar_launcher2 $Q/launch.json " ] || exit 71
    stock_m=
    for p in $(pidof maind); do
        if [ "$(tr '\000' ' ' </proc/$p/cmdline)" = "$Q/maind --platformd " ] && awk -v p="$stock_l" '$1=="PPid:"&&$2==p{ok=1} END{exit !ok}' /proc/$p/status; then stock_m=$p; break; fi
    done
    [ -n "$stock_m" ] && grep -q 'libyandex_evil[.]so' /proc/$stock_m/maps
    ! grep -Eq 'street-alisa-port|vqe_tap|native_agent' /proc/$stock_m/maps
    awk '$2=="0100007F:2693" && $4=="0A"{ok=1} END{exit !ok}' /proc/net/tcp
    [ ! -e run/READY ] && [ ! -e run/TAKEOVER ] && [ ! -e run/relay.sock ]
    printf 'stock_readback launcher=%s platformd=%s maps_nonstock=0 listener=ready\n' "$stock_l" "$stock_m"
fi
