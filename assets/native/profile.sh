#!/bin/sh
set -eu
BASE=/data/opt/alisa-standalone
RUNTIME=/tmp/alisa-standalone
Q=/system/vendor/quasar
umask 077
mkdir -p "$RUNTIME"

cleanup_receivers() {
    # A remote command without a PTY may survive a dropped SSH connection.
    # Match only this prototype's receiver and its earlier capture probes.
    for task_audio_pid in $(pidof arecord socat aplay true 2>/dev/null || true); do
        [ -r "/proc/$task_audio_pid/cmdline" ] || continue
        task_audio_command=$(tr '\000' ' ' < "/proc/$task_audio_pid/cmdline")
        case "$task_audio_command" in
            *"$RUNTIME/microphone.sock"*|*"$BASE/native_agent.so"*)
                kill -KILL "$task_audio_pid" 2>/dev/null || true
                ;;
        esac
    done
    rm -f "$BASE/microphone.sock" "$BASE/native-muted" "$BASE/tts.lease" "$BASE/tts.lease.new" "$BASE/tts-gate.status"
    rm -f "$RUNTIME/microphone.sock" "$RUNTIME/tts.lease" "$RUNTIME/tts.lease.new" "$RUNTIME/tts-gate.status"
}

cancel_guard() {
    rm -f "$RUNTIME/rollback.pending"
    if [ -s "$RUNTIME/rollback.pid" ]; then
        task_guard_pid=$(cat "$RUNTIME/rollback.pid")
        if [ -r "/proc/$task_guard_pid/cmdline" ]; then
            task_guard_command=$(tr '\000' ' ' < "/proc/$task_guard_pid/cmdline")
            case "$task_guard_command" in
                *rollback.pending*|*profile.sh\ guard*) kill "$task_guard_pid" 2>/dev/null || true ;;
            esac
        fi
        rm -f "$RUNTIME/rollback.pid"
    fi
}

case "${1:-}" in
    guard)
        sleep 180
        if [ -f "$RUNTIME/rollback.pending" ]; then
            cleanup_receivers
            unset ALSA_CONFIG_PATH
            "$Q/q" restart
            rm -f "$RUNTIME/rollback.pending" "$RUNTIME/rollback.pid"
        fi
        ;;
    bridge)
        cancel_guard
        cleanup_receivers
        touch "$RUNTIME/rollback.pending"
        nohup "$BASE/profile.sh" guard >"$RUNTIME/rollback.log" 2>&1 </dev/null &
        echo $! >"$RUNTIME/rollback.pid"
        # The stock stop command exits nonzero when an optional process is absent.
        "$Q/q" stop || true
        if pidof quasar_launcher2 >/dev/null; then
            echo 'The stock launcher is still running' >&2
            exit 1
        fi
        cd /data/quasar
        export CA_CERTIFICATES="$Q/ca-certificates.crt"
        export LD_LIBRARY_PATH="$Q/libs"
        ulimit -n 10240
        ulimit -s 1024
        nohup "$Q/quasar_launcher2" "$BASE/launch.bridge.json" >/dev/null 2>&1 </dev/null &
        sleep 1
        ;;
    commit)
        cancel_guard
        ;;
    stock)
        cancel_guard
        cleanup_receivers
        unset ALSA_CONFIG_PATH
        "$Q/q" restart
        ;;
    *)
        echo 'Usage: profile.sh bridge|commit|stock' >&2
        exit 2
        ;;
esac
