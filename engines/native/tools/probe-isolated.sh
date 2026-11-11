#!/bin/sh
# Run on the station. One new synthetic Voice session; no microphone, focus,
# speaker output or edits to the installed account/conversations/settings.
set -eu
umask 077
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
voice=$(sed -n 's/^[[:space:]]*"voice"[[:space:]]*:[[:space:]]*"\([a-z][a-z]*\)".*/\1/p' "$work/status.json" | head -n 1)
case "$voice" in ''|*[!a-z]*) echo 'Не удалось прочитать выбранный голос.' >&2; exit 2;; esac
mkdir "$work/state"
cp "$base/state/web-session.json" "$work/state/web-session.json"
chmod 600 "$work/state/web-session.json"
printf '{"voice":"%s","text":"Ответь только фразой: автономный режим готов.","expected":"режим готов"}\n' "$voice" > "$work/request.json"
if [ -f "$base/runtime.env" ]; then . "$base/runtime.env"; fi
# Match run.sh's IPv4 choice without changing the machine's routes or DNS.
if [ -z "${ALISA_ICE_BIND_ADDRESS:-}" ] && ip -4 route get 1.1.1.1 2>/dev/null | grep -q ' src '; then
    export ALISA_ICE_BIND_ADDRESS=0.0.0.0
fi
"$client" probe "$work/state" "$work/request.json"
