#!/bin/sh
# All rules match only SO_MARK 0xa150 of the voice client. Never change main.
set -eu
BASE=/data/opt/alisa-cli
PID=/tmp/alisa-vpn.pid
MARK=0xa150
TABLE=150
ENGINE=sing-box
if [ -r "$BASE/vpn-engine" ]; then read -r ENGINE < "$BASE/vpn-engine"; fi
case "$ENGINE" in sing-box|xray) ;; *) exit 2 ;; esac
owned() {
    [ -r "$PID" ] || return 1
    read -r task_pid < "$PID"
    case "$task_pid" in ''|*[!0-9]*) return 1 ;; esac
    [ -r "/proc/$task_pid/cmdline" ] || return 1
    task_cmd=$(tr '\000' ' ' < "/proc/$task_pid/cmdline")
    case "$task_cmd" in *"$BASE/sing-box run -c $BASE/vless.json"*|*"$BASE/xray run -c $BASE/vless.json"*) return 0 ;; *) return 1 ;; esac
}
tunnel_up() {
    # TUN appears before its creator sets IFF_UP; operstate may remain UNKNOWN.
    ip link show alisa-gpt 2>/dev/null | grep -q '[<,]UP[,>]'
}
ready() {
    owned && tunnel_up || return 1
    for task_family in -4 -6; do
        ip "$task_family" route show table "$TABLE" | grep -Eq '^default dev alisa-gpt( |$)' || return 1
    done
}
rules() {
    # Refuse a collision instead of modifying another tool's policy table.
    for task_family in -4 -6; do
        task_conflict=$(ip "$task_family" rule show | awk '$1 ~ /^151(0[0-4]|50):$/ && $0 !~ /fwmark 0xa150 / {print}')
        [ -z "$task_conflict" ] || { echo 'Reserved VPN rule priority is already used' >&2; return 1; }
        task_conflict=$(ip "$task_family" route show table "$TABLE" | awk '!/unreachable default/ && !/default dev alisa-gpt/ {print}')
        [ -z "$task_conflict" ] || { echo 'Reserved VPN route table is already used' >&2; return 1; }
    done
    # The unreachable default persists after the tunnel disappears: no fallback.
    for task_family in -4 -6; do
        ip "$task_family" route replace unreachable default table "$TABLE" metric 42760
        ip "$task_family" rule del priority 15150 fwmark "$MARK" lookup "$TABLE" 2>/dev/null || true
        ip "$task_family" rule add priority 15150 fwmark "$MARK" lookup "$TABLE"
    done
    task_priority=15100
    for task_network in 127.0.0.0/8 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16; do
        ip rule del priority "$task_priority" fwmark "$MARK" to "$task_network" lookup main 2>/dev/null || true
        ip rule add priority "$task_priority" fwmark "$MARK" to "$task_network" lookup main
        task_priority=$((task_priority+1))
    done
    task_priority=15100
    for task_network in ::1/128 fe80::/10 fc00::/7; do
        ip -6 rule del priority "$task_priority" fwmark "$MARK" to "$task_network" lookup main 2>/dev/null || true
        ip -6 rule add priority "$task_priority" fwmark "$MARK" to "$task_network" lookup main
        task_priority=$((task_priority+1))
    done
}
case "${1:-}" in
guard) rules ;;
start)
    if ! owned; then
        [ -c /dev/net/tun ] && [ -x "$BASE/$ENGINE" ] && [ -s "$BASE/vless.json" ] || exit 1
        if [ "$ENGINE" = xray ]; then "$BASE/xray" run -test -c "$BASE/vless.json"; else "$BASE/sing-box" check -c "$BASE/vless.json"; fi
    fi
    rules
    umask 077
    if ! owned; then
        GOGC=50 GOMEMLIMIT=32MiB nohup "$BASE/$ENGINE" run -c "$BASE/vless.json" > /tmp/alisa-vless.log 2>&1 </dev/null &
        echo $! > "$PID"
    fi
    task_wait=0
    while [ "$task_wait" -lt 50 ]; do
        # nohup can still be exec'ing when its PID is first observed.
        if ! owned; then
            read -r task_start_pid < "$PID"
            kill -0 "$task_start_pid" 2>/dev/null || exit 1
        elif tunnel_up; then
            # A link can also go down between the flag check and route setup.
            # Keep the blocking defaults and retry instead of exiting via -e.
            if ip -4 route replace default dev alisa-gpt table "$TABLE" metric 10 &&
               ip -6 route replace default dev alisa-gpt table "$TABLE" metric 10; then
                nohup "$BASE/vpn.sh" watch > /tmp/alisa-vpn-watch.log 2>&1 </dev/null &
                echo $! > /tmp/alisa-vpn-watch.pid
                exit 0
            fi
        fi
        sleep .2;task_wait=$((task_wait+1))
    done
    echo 'VPN tunnel is not ready; GPT remains blocked.' >&2
    exit 1 ;;
watch)
    exec 9>/tmp/alisa-vpn-watch.lock
    flock -n 9 || exit 0
    task_health=0
    while owned; do
        if [ "$task_health" -ge 20 ]; then
            task_health=0
            if ! env ALISA_VPN_REQUIRED=1 ALISA_PROXY_URL=socks5h://127.0.0.1:18081 LD_PRELOAD="$BASE/mark.so" /data/opt/alisa-standalone/bin/alisa-standalone vpn-health >/dev/null 2>&1; then
                /data/opt/alisa-standalone/bin/alisa-standalone close >/dev/null 2>&1 || true
            fi
        fi
        sleep 1;task_health=$((task_health+1))
    done
    /data/opt/alisa-standalone/bin/alisa-standalone close >/dev/null 2>&1 || true
    # Keep the unreachable route; disabling VPN in CLI explicitly restarts client.
    ;;
stop)
    /data/opt/alisa-standalone/bin/alisa-standalone close >/dev/null 2>&1 || true
    if owned; then kill "$task_pid"; fi
    rm -f "$PID"
    ip route del default dev alisa-gpt table "$TABLE" metric 10 2>/dev/null || true
    ip -6 route del default dev alisa-gpt table "$TABLE" metric 10 2>/dev/null || true
    ;;
# Existing callers use status to decide whether a core must be stopped before
# replacing/removing its files, even if its tunnel is broken.
status) owned ;;
ready) ready ;;
*) exit 2 ;;
esac
