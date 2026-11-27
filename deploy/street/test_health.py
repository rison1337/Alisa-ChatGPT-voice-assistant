#!/usr/bin/env python3
from pathlib import Path
import re
import subprocess
import sys

src = Path(sys.argv[1] if len(sys.argv) > 1 else 'stack.sh').read_text()

def fixture(body):
    functions = []
    for name in ('health_reset', 'health_observe', 'health_measure', 'health_cycle'):
        match = re.search(r'^'+name+r'\(\) \{.*?^\}', src, re.M | re.S)
        if match:
            functions.append(match[0])
    script = '''
log() { printf '%s\\n' "$*"; }
paused() { return 1; }
integrity() { return "${bad:-0}"; }
uptime_s() { echo "$now"; }
relay_check() { probe=$rc; own=0; after=0; if [ "$1" = watch_retry ]; then probe=$retry; fi; now=$((now+${delay:-0})); [ "$probe" = 0 ]; }
relay_up() { return 0; }
relay_restart_soft() { log restart; }
health_reset
probe_ok=0 probe_fail=0
'''+'\n'  # functions must precede setup invocation
    p = subprocess.run(['/bin/sh','-u','-c','\n'.join(functions)+'\n'+script+body], text=True, capture_output=True)
    assert p.returncode == 0, (p.stdout, p.stderr)
    return p.stdout


out = fixture('now=0 rc=2 retry=0 delay=65; health_cycle; [ "$health_streak:$probe_ok:$probe_fail" = 0:0:0 ]')
assert 'health=skip' in out and 'restart' not in out, out
out = fixture('now=0 rc=124 retry=0; health_cycle; [ "$health_streak:$probe_ok:$probe_fail" = 0:0:0 ]')
assert 'health=skip' in out and 'restart' not in out, out
out = fixture('now=0 rc=2 retry=2; health_cycle; now=30; health_cycle; now=630; health_cycle || exit 99; [ "$health_streak:$probe_fail" = 4:4 ]')
assert 'health=outage' in out and 'health=rollback' not in out, out
out = fixture('now=0 rc=2 retry=2; health_cycle; for now in $(seq 30 30 570); do health_cycle || exit 99; done; now=600; health_cycle; [ "$?" = 78 ]')
assert 'health=rollback' in out, out
out = fixture('now=0 rc=2 retry=0; health_cycle; [ "$health_streak:$probe_ok:$probe_fail" = 0:1:1 ]')
assert out.splitlines().count('restart') == 1, out
print('health fixtures: delayed, deadline, interrupted outage, continuous outage, recovered retry')
