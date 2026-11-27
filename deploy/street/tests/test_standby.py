#!/usr/bin/env python3
"""Exercise the exact deployed guard with test paths, never actual suspend."""
from pathlib import Path
import os
import subprocess
import tempfile

scratch = Path.home()/'.hermes/profiles/shattefashion/cache/scratch'
with tempfile.TemporaryDirectory(dir=scratch if scratch.is_dir() else os.environ.get('TMPDIR')) as tmp:
    root = Path(tmp)
    data, runtime = root/'data', root/'runtime'
    (data/'run').mkdir(parents=True)
    runtime.mkdir()
    (root/'proc/77').mkdir(parents=True)
    (root/'boot').write_text('fixture-boot\n')
    (root/'proc/77/stat').write_text('77 (fixture) '+ '0 '*19+'456\n')
    (root/'uptime').write_text('100.75 123.45\n')
    (runtime/'stock').write_text('printf "stock:%s\\n" "$*"\n')
    (runtime/'owner').write_text('fixture-boot 77 456\n')
    (root/'wake_lock').write_text('street-alisa-port-v1\n')
    src = Path(__file__).with_name('standby-inhibit.sh').read_text()
    subs = {'/run/street-alisa-standby': runtime, '/bkupgrade/data/street-alisa-port-v1': data,
            '/proc/sys/kernel/random/boot_id': root/'boot', '/proc/$pid/stat': root/'proc/$pid/stat',
            '/proc/$PPID/comm': root/'proc/77/comm', '/proc/uptime': root/'uptime',
            '/sys/power/wake_lock': root/'wake_lock'}
    for old, new in subs.items():
        src = src.replace(old, str(new))
    (root/'proc/77/comm').write_text('fixture\n')
    script = root/'guard'
    script.write_text(src)
    def invoke(*args):
        return subprocess.check_output(['/bin/sh', script, *args], text=True, stderr=subprocess.PIPE)
    assert invoke('-l', '0') == ''
    assert invoke('-l', '1') == ''
    assert invoke('-h') == 'stock:-h\n'
    assert invoke('-l', '2') == 'stock:-l 2\n'
    for disabled in (data/'DISABLED', data/'run/STOP'):
        disabled.touch()
        assert invoke('-l', '0') == 'stock:-l 0\n'
        disabled.unlink()
    (runtime/'owner').write_text('different-boot 77 456\n')
    assert invoke('-l', '0') == 'stock:-l 0\n'
    (runtime/'owner').write_text('fixture-boot 77 999\n')
    assert invoke('-l', '0') == 'stock:-l 0\n'
    (runtime/'owner').write_text('fixture-boot 77 456\n')
    (root/'wake_lock').write_text('other_service\n')
    assert invoke('-l', '0') == 'stock:-l 0\n'
    (runtime/'owner').unlink()
    assert invoke('-l', '0') == 'stock:-l 0\n'
    assert (runtime/'blocked.log').read_text().splitlines() == [f'uptime=100 standby=inhibited level={n} caller=fixture' for n in (0, 1)]
print('standby fixtures: owned calls only; stock on disabled, stop, stale boot/PID, no lease or no owner')
