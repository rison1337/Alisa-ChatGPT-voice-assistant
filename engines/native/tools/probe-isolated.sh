#!/bin/sh
# Run on the station. One synthetic text or audio Voice session, without a
# room microphone, native focus or acoustic output.
set -eu
umask 077
pcm=
if [ "${1:-}" = "--audio" ]; then
    if [ "$#" -lt 2 ] || [ ! -f "$2" ] || [ ! -r "$2" ]; then
        echo 'Укажите читаемый файл с тестовым PCM, 16 кГц mono s16le.' >&2
        exit 2
    fi
    pcm=$2
    shift 2
fi
if [ "$#" -gt 1 ]; then
    echo 'Использование: probe-isolated.sh [--audio файл.pcm] [каталог клиента]' >&2
    exit 2
fi
base=${1:-/data/opt/alisa-standalone}
client=$base/bin/alisa-standalone
work=$(mktemp -d /tmp/alisa-voice-probe.XXXXXX)
trap 'rm -rf "$work"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
"$client" status "$base/state" > "$work/status.json"
if ! grep -Eq '^[[:space:]]*"phase"[[:space:]]*:[[:space:]]*"idle"' "$work/status.json"; then
    echo 'Сначала закройте разговор GPT. Рабочее состояние не изменено.' >&2
    exit 2
fi
if [ -n "$pcm" ]; then
    "$client" info > "$work/info.json"
    if ! grep -Eq '"audio_probe_revision"[[:space:]]*:[[:space:]]*1[[:space:]]*[,}]' "$work/info.json"; then
        echo 'Для проверки аудио нужен клиент с audio_probe_revision 1.' >&2
        exit 2
    fi
fi
voice=$(sed -n 's/^[[:space:]]*"voice"[[:space:]]*:[[:space:]]*"\([a-z][a-z]*\)".*/\1/p' "$work/status.json" | head -n 1)
case "$voice" in ''|*[!a-z]*) echo 'Не удалось прочитать выбранный голос.' >&2; exit 2;; esac
mkdir "$work/state"
cp "$base/state/web-session.json" "$work/state/web-session.json"
chmod 600 "$work/state/web-session.json"
if [ -n "$pcm" ]; then
    printf '{"voice":"%s","expected":"готов"}\n' "$voice" > "$work/request.json"
else
    printf '{"voice":"%s","text":"Ответь только фразой: автономный режим готов.","expected":"режим готов"}\n' "$voice" > "$work/request.json"
fi
if [ -f "$base/runtime.env" ]; then . "$base/runtime.env"; fi
tools=$(dirname "$base")/alisa-cli
if [ "${ALISA_VPN_REQUIRED:-0}" = 1 ] || [ -f "$tools/vpn-required" ]; then
    if [ "${ALISA_VPN_REQUIRED:-0}" != 1 ] || [ ! -r "$tools/mark.so" ] ||
       [ "${LD_PRELOAD:-}" != "$tools/mark.so" ] || [ -z "${ALISA_PROXY_URL:-}" ] ||
       [ -z "${ALISA_ICE_BIND_ADDRESS:-}" ] || ! "$tools/vpn.sh" ready; then
        echo 'Настроенный VPN не готов. Проверка Voice остановлена.' >&2
        exit 2
    fi
fi
# Match run.sh's IPv4 choice without changing the machine's routes or DNS.
if [ -z "${ALISA_ICE_BIND_ADDRESS:-}" ] && ip -4 route get 1.1.1.1 2>/dev/null | grep -q ' src '; then
    export ALISA_ICE_BIND_ADDRESS=0.0.0.0
fi
if [ -n "$pcm" ]; then
    "$client" probe "$work/state" "$work/request.json" --audio "$pcm"
else
    "$client" probe "$work/state" "$work/request.json"
fi
