#!/bin/sh
# Street retail .376. Root-only service; logs contain fixed labels and numbers.
set -u
umask 077
export PATH=/usr/bin:/bin:/usr/sbin:/sbin LC_ALL=C
B=/bkupgrade/data/street-alisa-port-v1 R=/bkupgrade/data/street-alisa-port-v1/run
Q=/system/vendor/quasar
W=street-alisa-port-v1
SD=/run/street-alisa-standby
SP=/usr/bin/yandex-standby
SHASH=6b72cc550ae6e4acd0fb2bd5a2de8c58a1fe76d1f9e912133315f1f712958723
boot=$(cat /proc/sys/kernel/random/boot_id) || exit 77
[ "$(id -u)" = 0 ] && [ -d "$R" ] && [ ! -L "$R" ] || exit 77
ulimit -c 0
argv() { tr '\000' ' ' 2>/dev/null <"/proc/$1/cmdline"; }
exe() { [ -n "$1" ] && [ "$(readlink "/proc/$1/exe" 2>/dev/null)" = "$2" ]; }
parent_is() { awk -v p="$2" '$1=="PPid:"&&$2==p {ok=1} END{exit !ok}' "/proc/$1/status" 2>/dev/null; }
platform() {
    for p in $(pidof maind 2>/dev/null || :); do
        [ "$(argv "$p")" = "$Q/maind --platformd " ] && printf '%s\n' "$p"
    done
}
pair() {
    cfg=$1
    set -- $(pidof quasar_launcher2 2>/dev/null || :)
    [ "$#" = 1 ] && [ "$(argv "$1")" = "$Q/quasar_launcher2 $cfg " ] || return 1
    PL=$1; set -- $(platform)
    [ "$#" = 1 ] && parent_is "$1" "$PL" || return 1
    PM=$1
    grep -q 'libyandex_evil[.]so' "/proc/$PM/maps" 2>/dev/null || return 1
    awk '$2=="0100007F:2693" && $4=="0A"{ok=1} END{exit !ok}' /proc/net/tcp || return 1
    if [ "$cfg" = "$Q/launch.json" ]; then
        ! grep -Eq 'street-alisa-port|vqe_tap|native_agent' "/proc/$PM/maps" 2>/dev/null
    else
        awk -v f="$B/vqe_tap_mango_dsp.so" '$NF==f && $2~/^r.x/{ok=1} END{exit !ok}' "/proc/$PM/maps" 2>/dev/null
    fi
}
identity() {
    awk '$1=="Uid:"&&$2==48261&&$3==48261&&$4==48261&&$5==48261{u=1} $1=="Gid:"&&$2==29&&$3==29&&$4==29&&$5==29{g=1} $1=="Groups:"&&NF==3&&$2==29&&$3==104{s=1} $1=="CapEff:"&&$2~/^0+$/{c=1} $1=="NoNewPrivs:"&&$2==1{n=1} END{exit !(u&&g&&s&&c&&n)}' "/proc/$1/status" 2>/dev/null
}
ipc_allowed() {
    awk '$1==1028 && $5!=48261{bad=1} END{exit bad}' /proc/sysvipc/sem &&
    awk '($1==1028||$1==1029) && $8!=48261{bad=1} END{exit bad}' /proc/sysvipc/shm
}
holder_ok() {
    exe "$h" "$B/holder" && identity "$h" && grep -q '^rc=+0000000000 ' "$R/holder.state" &&
        awk '$1==1028{if($5==48261&&$6==29&&$3==600)ok=1;else bad=1} END{exit !(ok&&!bad)}' /proc/sysvipc/sem
}
socket_owner() {
    [ -S "$2" ] && [ ! -L "$2" ] || return 1
    # Accepted Unix stream sockets inherit the pathname, but are not listeners.
    ino=$(awk -v s="$2" '$8==s && $4=="00010000" && $5=="0001" && $6=="01" {i=$7;n++} END{if(n!=1)exit 1;print i}' /proc/net/unix) || return 1
    [ -n "$ino" ] || return 1
    for fd in /proc/"$1"/fd/*; do
        [ "$(readlink "$fd" 2>/dev/null)" = "socket:[$ino]" ] && return 0
    done
    return 1
}
control_free() { ! awk '$2 ~ /:46A0$/ && $4=="0A"{bad=1} END{exit !bad}' /proc/net/tcp /proc/net/tcp6; }
control_local() {
    awk '$2 ~ /:46A0$/ && $4=="0A"{n++; if($2=="0100007F:46A0")good++} END{exit !(good==1&&n==1)}' /proc/net/tcp /proc/net/tcp6 || return 1
    inode=$(awk '$2=="0100007F:46A0" && $4=="0A" {print $10}' /proc/net/tcp)
    [ -n "$inode" ] || return 1
    for fd in /proc/"$1"/fd/*; do
        [ "$(readlink "$fd" 2>/dev/null)" = "socket:[$inode]" ] && return 0
    done
    return 1
}
app() { setpriv --no-new-privs "$B/dispatch" "$@"; }
paused() { [ -e "$B/DISABLED" ] || [ -e "$R/STOP" ]; }
nap() { for j in $(seq 1 "$1"); do paused && return 1; wake_keep || exit 80; sleep 1; done; }
wake_active() { grep -qw "$W" /sys/power/wake_lock; }
wake_keep() {
    [ "${wake_owned:-0}" = 1 ] || return 0
    wu=$(uptime_s)
    [ "$wu" -ge "${wake_next:-0}" ] || return 0
    # Kernel lease, in ns: SIGKILL, stalled supervisor and watchdog loss cannot leak it.
    printf '%s 60000000000\n' "$W" >/sys/power/wake_lock && wake_active || { log 'wake=failed'; return 1; }
    wake_next=$((wu+10))
}
wake_release() {
    if wake_active; then
        printf '%s\n' "$W" >/sys/power/wake_unlock && ! wake_active || { log 'wake=release_failed'; return 1; }
    fi
    log 'wake=released'
}
standby_mounts() { awk -v p="$SP" '$5==p{print $4}' /proc/self/mountinfo; }
standby_ours() { [ "$(standby_mounts)" = /street-alisa-standby/inhibit ]; }
standby_acquire() {
    # Use only a transient bind over the dispatcher; do not touch rootfs or sysfs policy.
    [ -z "$(standby_mounts)" ] || return 1
    [ "$(sha256sum "$SP" | cut -d' ' -f1)" = "$SHASH" ] || return 1
    [ ! -L "$SD" ] && mkdir -p "$SD" && chmod 755 "$SD" || return 1
    cp "$SP" "$SD/stock" && [ "$(sha256sum "$SD/stock" | cut -d' ' -f1)" = "$SHASH" ] || return 1
    cp "$B/standby-inhibit.sh" "$SD/inhibit" && chmod 755 "$SD/inhibit" || return 1
    st=$(start_tick "$$"); [ -n "$st" ] || return 1
    printf '%s %s %s\n' "$boot" "$$" "$st" >"$SD/owner" && : >"$SD/blocked.log" || return 1
    mount -o bind "$SD/inhibit" "$SP" && standby_ours && cmp -s "$SD/inhibit" "$SP" || return 1
    log 'standby_overlay=active'
}
standby_release() {
    rm -f "$SD/owner"
    if standby_ours; then
        umount "$SP" && [ -z "$(standby_mounts)" ] || { log 'standby_overlay=passthrough_unmount_failed'; return 1; }
    fi
    [ "$(sha256sum "$SP" | cut -d' ' -f1)" = "$SHASH" ] || return 1
    log 'standby_overlay=released'
}
log() { printf 'uptime=%s %s\n' "$(cut -d. -f1 /proc/uptime)" "$*" >>"$R/stack.log"; }
uptime_s() { cut -d. -f1 /proc/uptime; }
relay_log() { printf 'uptime=%s %s\n' "$(uptime_s)" "$*" >>"$R/relay.log"; }
integrity() {
    [ "$(cat "$Q/version_full")" = 0.376.1.20.4861173338.20261116.100 ] || { log 'integrity=version rc=77'; return 77; }
    (cd "$B" && sha256sum -c MANIFEST >/dev/null 2>&1) || { log 'integrity=manifest rc=76'; return 76; }
}
prepare_client_runtime() {
    # Keep the native focus mechanism (which plays synthetic silence), but
    # ensure all of its runtime files and the mic socket live only in RAM.
    ram=/run/street-alisa-client
    [ "$(readlink "$R/client")" = "$ram" ] && [ ! -L "$ram" ] || return 77
    awk '$2=="/run" && $3=="tmpfs"{ok=1} END{exit !ok}' /proc/mounts || return 77
    awk 'NR>1{bad=1} END{exit bad}' /proc/swaps || return 77
    mkdir -p "$ram" && chown 48261:29 "$ram" && chmod 0700 "$ram"
}
relay_up() { exe "$r" "$B/relay" && socket_owner "$r" "$R/relay.sock"; }
relay_start() {
    if [ -e "$R/relay.sock" ] || [ -L "$R/relay.sock" ]; then
        [ -S "$R/relay.sock" ] && [ ! -L "$R/relay.sock" ] || { relay_log 'stage=socket_path rc=78'; return 78; }
        ! awk -v s="$R/relay.sock" '$8==s{found=1} END{exit !found}' /proc/net/unix || { relay_log 'stage=socket_busy rc=78'; return 78; }
        rm -f "$R/relay.sock" || return 78
    fi
    "$B/relay" serve 8>&- 9>&- </dev/null >>"$R/relay.log" 2>/dev/null & r=$!
    printf '%s\n' "$r" >"$R/relay.pid"
    relay_log "stage=spawn pid=$r rc=0"
}
relay_stop() {
    [ -n "$r" ] || return 0
    stop_one "$r" "$B/relay"
    if exe "$r" "$B/relay"; then
        relay_log 'stage=stop rc=78'; return 78
    fi
    wait "$r" 2>/dev/null; rr=$?
    relay_log "stage=$1 pid=$r rc=$rr"
    r=
    if [ -S "$R/relay.sock" ] && [ ! -L "$R/relay.sock" ] && ! awk -v s="$R/relay.sock" '$8==s{found=1} END{exit !found}' /proc/net/unix; then
        rm -f "$R/relay.sock"
    fi
}
relay_check() {
    # Always sample both checks; a failed ownership check with a good
    # end-to-end probe is diagnostic, never authorization to use that socket.
    relay_up; own=$?
    run_child "$2" setpriv --no-new-privs "$B/dispatch" vpn-health; probe=$?
    relay_up; after=$?
    relay_log "stage=$1 owner_rc=$own vpn_rc=$probe owner_after_rc=$after"
    [ "$own:$probe:$after" = 0:0:0 ]
}
relay_wait_owner() {
    for rw in 1 2 3 4 5; do
        relay_up && return 0
        [ "$(uptime_s)" -lt "$1" ] && kill -0 "$r" 2>/dev/null || return 78
        nap 1 || return 69
    done
    relay_up
}
relay_startup() {
    until [ "$(uptime_s)" -ge "$deadline" ]; do
        paused && return 69
        if [ -z "$r" ] || ! kill -0 "$r" 2>/dev/null; then
            relay_stop boot_exit || return 78
            relay_start || { nap 2 || return 69; continue; }
        fi
        if relay_wait_owner "$deadline"; then
            left=$((deadline-$(uptime_s)))
            [ "$left" -gt 0 ] || break
            [ "$left" -le 15 ] || left=15
            relay_check boot_check "$left" && return 0
        else
            relay_stop boot_unready || return 78
        fi
        nap 2 || return 69
    done
    relay_log 'stage=boot_deadline rc=78'
    return 78
}
health_reset() {
    health_streak=0 health_outage=0 health_since=0 health_last=0 health_penult=0 health_contiguous=0
}
health_observe() {
    observed=$(uptime_s)
    if [ "$2" = 0 ]; then
        probe_ok=$((probe_ok+1))
        if [ "$health_streak" -ne 0 ]; then log "health=recovered seconds=$((observed-health_since))"; fi
        health_reset
    else
        probe_fail=$((probe_fail+1))
        # A gap contributes no evidence of continuous outage, and does not
        # invent a failure or discard already observed negative responses.
        if [ "$health_contiguous" -eq 0 ] || [ "$((observed-health_last))" -gt 45 ]; then
            health_contiguous=0 health_outage=0 health_since=$observed
        fi
        if [ "$health_contiguous" -ge 2 ] && [ "$health_outage" -eq 0 ] && [ "$((observed-health_penult))" -le 90 ]; then
            health_outage=1
            log "health=outage window_s=$((observed-health_penult))"
        fi
        health_penult=$health_last health_last=$observed
        health_streak=$((health_streak+1))
        health_contiguous=$((health_contiguous+1))
    fi
    log "health=probe kind=$1 result=$2 vpn_rc=$probe owner_rc=$own owner_after_rc=$after probe_ok=$probe_ok probe_fail=$probe_fail streak=$health_streak outage=$health_outage"
}
health_measure() {
    probe_started=$(uptime_s)
    relay_check "$1" 8; outcome=$?
    probe_elapsed=$(($(uptime_s)-probe_started))
    # A killed local watchdog child did not return a negative answer.
    # A late negative can straddle suspension; do not consume it as evidence.
    if [ "$outcome" -ne 0 ] && { [ "$probe_elapsed" -gt 12 ] || { [ "$probe" = 124 ] && [ "$own:$after" = 0:0 ]; }; }; then
        log "health=skip kind=$2 elapsed_s=$probe_elapsed vpn_rc=$probe owner_rc=$own owner_after_rc=$after"
        return 1
    fi
    health_observe "$2" "$outcome"
}
relay_restart_soft() {
    log 'health=relay_restart'
    relay_stop restart_exit && relay_start && relay_wait_owner "$(( $(uptime_s)+5 ))"
}
health_cycle() {
    paused && return 69
    integrity || return "$?"
    health_measure watch scheduled || return 0
    [ "$outcome" -ne 0 ] || return 0
    if [ "$health_outage" -eq 1 ] && [ "$((observed-health_since))" -ge 600 ]; then
        log "health=rollback seconds=$((observed-health_since)) rc=78"
        return 78
    fi
    # Always restart on the first miss; on later misses repair a dead relay,
    # but do not keep tearing down a healthy listener for an upstream outage.
    if [ "$health_streak" -eq 1 ] || ! relay_up; then
        paused && return 69
        relay_restart_soft || log 'health=relay_restart_incomplete'
        paused && return 69
        health_measure watch_retry retry || :
    fi
    return 0
}
# Called for shell-owned children only; no trust in old persistent PID files.
stop_one() {
    sp=$1; sf=$2
    exe "$sp" "$sf" || return 0
    kill -TERM "$sp" 2>/dev/null || :
    for jj in 1 2 3 4; do exe "$sp" "$sf" || break; sleep 1; done
    if exe "$sp" "$sf"; then kill -KILL "$sp" 2>/dev/null || :; fi
}
run_child() {
    lim=$1; shift
    "$@" 8>&- 9>&- </dev/null >/dev/null 2>&1 & wp=$!
    for zz in $(seq 1 "$lim"); do kill -0 "$wp" 2>/dev/null || break; sleep 1; done
    if kill -0 "$wp" 2>/dev/null; then kill -KILL "$wp" 2>/dev/null || :; wait "$wp" 2>/dev/null || :; return 124; fi
    wait "$wp"
}
restore_stock() {
    run_child 18 env -i PATH="$PATH" "$Q/q" restart ; qr=$?
    log "q_restart_rc=$qr"
    for ix in $(seq 1 45); do
        if pair "$Q/launch.json"; then
            log "stock=ready launcher=$PL platformd=$PM maps_nonstock=0"
            rm -f "$R/TAKEOVER"
            return 0
        fi
        sleep 1
    done
    log 'stock=not_verified'
    return 71
}
start_tick() { awk '{sub(/^.*\) /, ""); print $20}' "/proc/$1/stat" 2>/dev/null; }
recover() {
    sup=$1; tick=$2
    while [ "$(start_tick "$sup")" = "$tick" ]; do sleep 2; done
    exec 9>"$R/service.lock"
    n=0
    until flock -xn 9; do n=$((n+1)); [ "$n" -lt 15 ] || return 0; sleep 1; done
    [ -f "$R/TAKEOVER" ] && [ "$(cat "$R/TAKEOVER")" = "$boot $sup $tick" ] || return 0
    log 'watchdog=supervisor_lost'
    rm -f "$R/READY"
    : >"$R/HOLDER_STOP"
    for role in client holder relay; do
        child=; read -r child <"$R/$role.pid" 2>/dev/null || :
        case "$child" in ''|*[!0-9]*) continue;; esac
        if [ "$role" = client ]; then sf="$B/client.armhf"; else sf="$B/$role"; fi
        parent_is "$child" 1 && stop_one "$child" "$sf"
    done
    if [ -S "$R/relay.sock" ] && [ ! -L "$R/relay.sock" ] && ! awk -v s="$R/relay.sock" '$8==s{found=1} END{exit !found}' /proc/net/unix; then rm -f "$R/relay.sock"; fi
    restore_stock || :
    standby_release || :
    wake_release || :
}
cleanup() {
    rc=$?; trap - EXIT
    # A caught signal resets on exec; SIG_IGN would leak into vendor q/launcher.
    trap ':' TERM INT HUP
    rm -f "$R/READY"
    log "end stage=$stage rc=$rc"
    stop_one "$c" "$B/client.armhf"
    : >"$R/HOLDER_STOP"
    # Holder normally exits on its flag, before the vendor launches again.
    for ix in 1 2 3; do exe "$h" "$B/holder" || break; sleep 1; done
    stop_one "$h" "$B/holder"
    relay_stop cleanup_exit || :
    if [ "$need_stock" = 1 ]; then
        restore_stock; rc_stock=$?
        standby_release || :
        wake_release || :
        exit "$rc_stock"
    fi
    [ "${standby_owned:-0}" != 1 ] || standby_release || :
    [ "${wake_owned:-0}" != 1 ] || wake_release || :
    exit 0
}
agent_ready() {
    a=
    exe "$c" "$B/client.armhf" && identity "$c" && control_local "$c" || return 1
    status_live=$(app status 8>&- 9>&- </dev/null 2>/dev/null | jq -ce --argjson c "$c" 'select(.pid==$c and .agent_connected==true and .login_saved==true) | {calls,phase,native:(.native|{commands,recognition_starts,wake_blocks,focus_ready}),microphone_frames:(.audio.microphone_frames // .last_call.microphone_frames // null)}' 2>/dev/null) || return 1
    for ap in $(pidof aplay 2>/dev/null || :); do
        if parent_is "$ap" "$c" && identity "$ap" && grep -Fq "$B/native_agent.so" "/proc/$ap/maps" 2>/dev/null; then a=$ap; break; fi
    done
    [ -n "$a" ]
}
serve() {
    exec 9>"$R/service.lock"
    flock -xn 9 || return 0
    [ ! -e "$B/DISABLED" ] || return 0
    if [ -f "$R/TAKEOVER" ]; then
        read -r oldboot previous rest <"$R/TAKEOVER" || return 0
        [ "$oldboot" != "$boot" ] || return 0
    fi
    rm -f "$R/TAKEOVER" "$R/STOP" "$R/HOLDER_STOP" "$R/READY" "$R/relay.pid" "$R/holder.pid" "$R/client.pid" "$R/agent.pid" "$R/launcher.pid" "$R/platformd.pid" "$R/watchdog.pid"
    : >"$R/stack.log"
    if [ -f "$R/relay.log" ]; then mv -f "$R/relay.log" "$R/relay.previous.log" || return 77; fi
    : >"$R/relay.log" || return 77
    c= h= r= a= need_stock=0 stage=stock
    deadline=$(( $(uptime_s)+180 ))
    trap cleanup EXIT
    trap 'stage=signal; exit 69' TERM INT HUP
    printf '%s\n' "$$" >"$R/supervisor.pid"
    while ! pair "$Q/launch.json"; do
        [ "$(uptime_s)" -lt "$deadline" ] || exit 72
        nap 1 || exit 0
    done
    log "baseline launcher=$PL platformd=$PM"
    stage=preflight
    # Lock out the separately installed native controller; never inherit its fd.
    exec 8</data/street-native-voice-v2/run-native
    flock -xn 8 && control_free || exit 75
    integrity || exit "$?"
    [ -s "$B/state/web-session.json" ] && [ ! -L "$B/state/web-session.json" ] || exit 77
    prepare_client_runtime || exit 77
    paused && exit 0
    stage=wake
    wake_owned=1
    wake_keep || exit 80
    log "wake=held lease_s=60"
    stage=standby_overlay
    [ -z "$(standby_mounts)" ] || exit 80
    standby_owned=1
    standby_acquire || exit 80
    stage=relay_boot
    relay_startup || exit "$?"
    paused && exit 0
    integrity || exit "$?"
    tick=$(start_tick "$$"); [ -n "$tick" ] || exit 77
    printf '%s %s %s\n' "$boot" "$$" "$tick" >"$R/TAKEOVER"
    nohup setsid /bin/sh "$B/stack.sh" recover "$$" "$tick" 8>&- 9>&- </dev/null >/dev/null 2>&1 &
    printf '%s\n' "$!" >"$R/watchdog.pid"
    need_stock=1
    stage=holder
    for ix in $(seq 1 35); do
        if ipc_allowed && [ "$(sed -n '1p' /proc/asound/card0/pcm1p/sub0/status)" = closed ]; then break; fi
        nap 1 || exit 0
    done
    ipc_allowed || exit 74
    "$B/holder" 3>"$R/holder.state" 8>&- 9>&- </dev/null >/dev/null 2>&1 & h=$!
    printf '%s\n' "$h" >"$R/holder.pid"
    for ix in $(seq 1 15); do holder_ok && break; nap 1 || exit 0; done
    holder_ok || exit 74
    log "holder_pid=$h $(cat "$R/holder.state")"
    stage=q_stop
    run_child 13 env -i PATH="$PATH" "$Q/q" stop; qs=$?
    log "q_stop_rc=$qs"
    [ "$qs" -le 1 ] || exit 73
    # Vendor q returns killall begemot status; absence is a valid stop.
    for ix in 1 2 3 4 5; do
        if [ -z "$(pidof quasar_launcher2 2>/dev/null || :)" ] && [ -z "$(platform)" ]; then break; fi
        nap 1 || exit 0
    done
    [ -z "$(pidof quasar_launcher2 2>/dev/null || :)" ] && [ -z "$(platform)" ] || exit 73
    paused && exit 0
    stage=launcher
    rm -f /run/street-alisa-vqe.stats || exit 77
    (cd /data/quasar && ulimit -n 10240 && ulimit -s 1024 && exec env -i PATH="$PATH" CA_CERTIFICATES="$Q/ca-certificates.crt" LD_LIBRARY_PATH="$Q/libs" "$Q/quasar_launcher2" "$B/launch.json") 8>&- 9>&- </dev/null >/dev/null 2>&1 & l=$!
    printf '%s\n' "$l" >"$R/launcher.pid"
    for ix in $(seq 1 35); do pair "$B/launch.json" && [ "$PL" = "$l" ] && break; nap 1 || exit 0; done
    pair "$B/launch.json" && [ "$PL" = "$l" ] && holder_ok || exit 73
    m=$PM; printf '%s\n' "$m" >"$R/platformd.pid"
    log "launcher=$l platformd=$m maps_tap=1 maps_vendor_dsp=1"
    health_reset
    probe_ok=0 probe_fail=0 next_health=0
    attempt=1
    while [ "$attempt" -le 4 ]; do
        stage=client_start
        paused && exit 0
        control_free || exit 75
        setpriv --no-new-privs "$B/dispatch" serve 8>&- 9>&- </dev/null >/dev/null 2>&1 & c=$!
        printf '%s\n' "$c" >"$R/client.pid"
        for ix in $(seq 1 18); do
            agent_ready && break
            kill -0 "$c" 2>/dev/null || break
            nap 1 || exit 0
        done
        if agent_ready; then
            printf '%s\n' "$a" >"$R/agent.pid"
            log "client=$c agent=$a attempt=$attempt agent_map=1 control=loopback"
            : >"$R/READY"
            stage=watch
            misses=0; holder_misses=0
            while ! paused; do
                wake_keep || exit 80
                pair "$B/launch.json" && [ "$PL" = "$l" ] && [ "$PM" = "$m" ] || exit 73
                if [ "$(uptime_s)" -ge "$next_health" ]; then
                    now_health=$(uptime_s)
                    if [ "$next_health" -eq 0 ]; then next_health=$now_health; fi
                    skipped=$(((now_health-next_health)/30))
                    [ "$skipped" -eq 0 ] || log "health=slots_skipped count=$skipped"
                    next_health=$((next_health+(skipped+1)*30))
                    stage=health
                    health_cycle || exit "$?"
                    stage=watch
                fi
                exe "$h" "$B/holder" || exit 74
                if holder_ok; then holder_misses=0; else holder_misses=$((holder_misses+1)); [ "$holder_misses" -lt 7 ] || exit 74; fi
                if agent_ready; then misses=0; else misses=$((misses+1)); [ "$misses" -lt 2 ] || break; fi
                nap 3 || exit 0
            done
        fi
        paused && exit 0
        rm -f "$R/READY"
        stop_one "$c" "$B/client.armhf"
        wait "$c" 2>/dev/null; cr=$?
        log "client_exit_rc=$cr attempt=$attempt"
        c=
        [ "$attempt" -lt 4 ] || break
        nap "$((1 << attempt))" || exit 0
        attempt=$((attempt+1))
    done
    stage=retry_exhausted; exit 79
}
case ${1:-} in
    start)
        [ ! -e "$B/DISABLED" ] || exit 0
        nohup setsid /bin/sh "$B/stack.sh" supervise </dev/null >/dev/null 2>&1 &
        exit 0 ;;
    supervise) serve ;;
    recover)
        case ${2:-}:${3:-} in *[!0-9:]*|:*|*:) exit 64;; esac
        recover "$2" "$3" ;;
    stop)
        : >"$R/STOP"
        for ix in $(seq 1 100); do
            if (exec 9>"$R/service.lock"; flock -xn 9) && pair "$Q/launch.json" && ! wake_active && [ -z "$(standby_mounts)" ]; then
                printf 'stock_launcher=%s stock_platformd=%s maps_nonstock=0 listener=ready\n' "$PL" "$PM"
                exit 0
            fi
            sleep 1
        done
        exit 71 ;;
    status)
        [ -f "$R/READY" ] && pair "$B/launch.json" || exit 69
        read -r h <"$R/holder.pid"; read -r c <"$R/client.pid"; read -r r <"$R/relay.pid"; read -r s <"$R/supervisor.pid"
        holder_ok && agent_ready && relay_up || exit 69
        printf 'supervisor=%s launcher=%s platformd=%s holder=%s client=%s agent=%s relay=%s tap_map=1 agent_map=1\n' "$s" "$PL" "$PM" "$h" "$c" "$a" "$r"
        cat "$R/holder.state"
        printf 'counters=%s\n' "$status_live" ;;
    *) exit 64 ;;
esac
