#!/bin/sh
set -eu
base=/data/opt/alisa-standalone
"$base/run.sh" start
attempt=0
while [ "$attempt" -lt 30 ]; do
    if "$base/bin/alisa-standalone" status "$base/state" 2>/dev/null | grep -q '"agent_connected": true'; then
        exec "$base/bin/alisa-standalone" open "$base/state"
    fi
    sleep 1
    attempt=$((attempt + 1))
done
echo 'Voice client did not become ready' >&2
exit 1
