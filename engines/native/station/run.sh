#!/bin/sh
# Manual, reversible runtime. Nothing is added to firmware or boot scripts.
set -eu
BASE=/data/opt/alisa-standalone
RUNTIME=/tmp/alisa-standalone
umask 077
mkdir -p "$RUNTIME"

owned_monitor() {
    [ -r "$RUNTIME/monitor.pid" ] || return 1
    task_monitor_pid=$(cat "$RUNTIME/monitor.pid")
    case "$task_monitor_pid" in ''|*[!0-9]*) return 1 ;; esac
    [ -r "/proc/$task_monitor_pid/cmdline" ] || return 1
    task_monitor_command=$(tr '\000' ' ' < "/proc/$task_monitor_pid/cmdline")
    case "$task_monitor_command" in *"$BASE/run.sh monitor"*) return 0 ;; *) return 1 ;; esac
}

case "${1:-}" in
    start)
        if owned_monitor; then echo 'Standalone runtime is already started'; exit 0; fi
        # Avoid stealing the working PC bridge's Quasar profile.
        if pidof quasar_launcher2 >/dev/null; then
            for task_launcher_pid in $(pidof quasar_launcher2); do
                task_launcher_command=$(tr '\000' ' ' < "/proc/$task_launcher_pid/cmdline")
                case "$task_launcher_command" in
                    *'/data/opt/alice-voice/'*) echo 'Stop the PC bridge first' >&2; exit 1 ;;
                esac
            done
        fi
        nohup sh "$BASE/run.sh" monitor >"$RUNTIME/daemon.log" 2>&1 </dev/null &
        echo $! > "$RUNTIME/monitor.pid"
        echo 'Standalone runtime is starting'
        ;;
    monitor)
        task_child_pid=''
        cleanup() {
            # A caught signal resets in executed programs; an ignored signal
            # would leak into the restored launcher and prevent its next stop.
            trap ':' HUP INT TERM
            if [ -n "$task_child_pid" ] && [ -r "/proc/$task_child_pid/cmdline" ]; then
                task_child_command=$(tr '\000' ' ' < "/proc/$task_child_pid/cmdline")
                case "$task_child_command" in
                    *"$BASE/bin/alisa-standalone serve $BASE/state"*)
                        kill -TERM "$task_child_pid" 2>/dev/null || true
                        task_wait=0
                        while kill -0 "$task_child_pid" 2>/dev/null && [ "$task_wait" -lt 30 ]; do
                            sleep .2; task_wait=$((task_wait + 1))
                        done
                        kill -KILL "$task_child_pid" 2>/dev/null || true
                        wait "$task_child_pid" 2>/dev/null || true
                        ;;
                esac
            fi
            "$BASE/profile.sh" stock || true
            rm -f "$RUNTIME/monitor.pid" "$RUNTIME/client.pid"
        }
        trap cleanup EXIT
        trap 'exit 0' HUP INT TERM
        "$BASE/profile.sh" bridge
        (
            if [ -r "$BASE/runtime.env" ]; then . "$BASE/runtime.env"; fi
            # Stations whose debugfs listing lacks the client's "|mute-switch "
            # line would otherwise silence the microphone. Lite 1 has no switch
            # at all and gets a static always-on listing. Yandex Station 2
            # (yandexmidi) labels it MUTE_SWITCH/MUTE_STATE with active-low
            # polarity; mute_gpio.so converts that listing on each read so the
            # physical mute switch keeps working.
            if [ -r "$BASE/mute_gpio.so" ]; then
                if tr '\000' '\n' < /proc/device-tree/compatible | grep -Eq '^(yandex,station-midi|yandexmidi|station-midi)$'; then
                    export LD_PRELOAD="${LD_PRELOAD:+$LD_PRELOAD }$BASE/mute_gpio.so"
                elif grep -Fq 'yandex,station-micro' /proc/device-tree/compatible &&
                     ! grep -q '|mute-switch ' /sys/kernel/debug/gpio 2>/dev/null; then
                    printf ' gpio-0 (                    |mute-switch         ) in  hi\n' > "$RUNTIME/mute-gpio"
                    export ALICE_MUTE_GPIO_FILE="$RUNTIME/mute-gpio"
                    export LD_PRELOAD="${LD_PRELOAD:+$LD_PRELOAD }$BASE/mute_gpio.so"
                fi
            fi
            exec "$BASE/bin/alisa-standalone" serve "$BASE/state"
        ) &
        task_child_pid=$!
        echo "$task_child_pid" > "$RUNTIME/client.pid"
        task_wait=0
        while [ "$task_wait" -lt 40 ]; do
            kill -0 "$task_child_pid" 2>/dev/null || exit 1
            if "$BASE/bin/alisa-standalone" status "$BASE/state" 2>/dev/null | grep -q '"agent_connected": true'; then
                "$BASE/profile.sh" commit
                echo 'Standalone runtime ready'
                wait "$task_child_pid" || true
                exit 0
            fi
            sleep .5; task_wait=$((task_wait + 1))
        done
        echo 'Standalone startup timed out' >&2
        exit 1
        ;;
    stop)
        if owned_monitor; then
            kill -TERM "$task_monitor_pid"
            task_wait=0
            while owned_monitor && [ "$task_wait" -lt 100 ]; do sleep .2; task_wait=$((task_wait + 1)); done
            if owned_monitor; then echo 'Runtime is still stopping' >&2; exit 1; fi
        fi
        echo 'Standalone runtime stopped'
        ;;
    status)
        exec "$BASE/bin/alisa-standalone" status "$BASE/state"
        ;;
    *) echo 'Usage: run.sh start|stop|status' >&2; exit 2 ;;
esac
