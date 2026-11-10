"""Exercise real helper transactions with simulated mounts, never host sysfs."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
HARNESS=r'''
set -efu
. "$HELPER" library
dir="$FIXTURE/session"
mkdir "$dir" "$dir/shadow" "$dir/original0" "$dir/original1" "$FIXTURE/mounts" "$FIXTURE/physical"
: > "$dir/active"
root0=/sys/devices/controller0/leds; root1=/sys/devices/controller1/leds
printf '%s\n' "$root0" > "$dir/root0"; printf '%s\n' "$root1" > "$dir/root1"
for part in $parts; do printf '17\n' > "$FIXTURE/physical/$part"; done
target_for() {
    case "$1" in *4) printf '%s/clock-%s/brightness\n' "$root1" "$1";;
        *) printf '%s/clock-%s/brightness\n' "$root0" "$1";; esac
}
physical_for() { printf '%s/physical/%s\n' "$FIXTURE" "$1"; }
record_for() { printf '%s/mounts/%s\n' "$FIXTURE" "$(printf '%s' "$1" | cksum | cut -d' ' -f1)"; }
mounted() { [ -f "$(record_for "$1")" ]; }
owned_mount() { [ "$(cat "$(record_for "$2")" 2>/dev/null)" = "$1" ]; }
mount() {
    count=$(cat "$FIXTURE/count" 2>/dev/null || printf 0); count=$((count+1))
    printf '%s' "$count" > "$FIXTURE/count"
    [ "$count" != "${FAIL_AT:-0}" ] || return 1
    source=$4; target=$5
    case "$source" in /sys/*) source=${source#/sys};; *) source=${source#/tmp};; esac
    printf '%s' "$source" > "$(record_for "$target")"
}
umount() { rm "$(record_for "$1")"; }
'''

@unittest.skipUnless(sys.platform=='linux','requires Bash/flock')
class MidiRecovery(unittest.TestCase):
    def test_guard_recovers_dead_owner(self):
        self.run_case('''
transaction acquire
sleep 30 & owner=$!
kill "$owner"; wait "$owner" || true
guard "$dir" "$owner"
[ ! -d "$dir" ]
[ -z "$(ls -A "$FIXTURE/mounts")" ]
''')

    def test_expired_lease_blocks_still_living_owner(self):
        self.run_case('''
transaction acquire
printf '0\\n' > "$dir/lease"
guard "$dir" "$$"
if transaction acquire; then exit 93; fi
[ ! -d "$dir" ]
''')

    def test_truncated_lease_still_recovers(self):
        self.run_case('''
transaction acquire
: > "$dir/lease"
guard "$dir" "$$"
[ ! -d "$dir" ]
''')

    def test_invalid_shadow_does_not_leave_mounts(self):
        self.run_case('''
transaction acquire
printf invalid > "$dir/shadow/a1"
restore "$dir"
[ ! -d "$dir" ]
[ -z "$(ls -A "$FIXTURE/mounts")" ]
''')

    def run_case(self,body,fail_at=0):
        with tempfile.TemporaryDirectory() as folder:
            env={**os.environ,'FIXTURE':folder,'HELPER':str(ROOT/'assets/native/midi-clock.sh'),'FAIL_AT':str(fail_at)}
            result=subprocess.run(['bash','-c',HARNESS+'\n'+body],env=env,capture_output=True,timeout=12)
            self.assertEqual(result.returncode,0,result.stderr.decode())

    def test_full_release_replays_stock_values_and_removes_directory(self):
        self.run_case('''
transaction acquire
for part in $parts; do printf '42\\n' > "$dir/shadow/$part"; done
restore "$dir"
[ ! -d "$dir" ]
[ -z "$(ls -A "$FIXTURE/mounts")" ]
for part in $parts; do [ "$(cat "$FIXTURE/physical/$part")" = 42 ]; done
restore "$dir"
''')

    def test_every_partial_mount_failure_releases_only_acquired_mounts(self):
        # Two original views plus all 45 segment overlays.
        for step in range(1,48):
            with self.subTest(step=step):
                self.run_case('''
if transaction acquire; then exit 90; fi
restore "$dir"
[ ! -d "$dir" ]
[ -z "$(ls -A "$FIXTURE/mounts")" ]
''',step)

    def test_foreign_mount_is_not_unmounted_or_painted(self):
        self.run_case('''
transaction acquire
printf foreign > "$(record_for "$(target_for a4)")"
if restore "$dir"; then exit 91; fi
[ "$(cat "$(record_for "$(target_for a4)")")" = foreign ]
[ "$(cat "$FIXTURE/physical/a1")" = 0 ]
[ -f "$dir/closing" ]
''')

    def test_recovery_waits_for_writer_and_rejects_late_owner(self):
        self.run_case('''
transaction acquire
slow_frame() { : > "$FIXTURE/writing"; sleep .2; printf '99\\n' > "$FIXTURE/physical/a1"; }
transaction slow_frame & writer=$!
while [ ! -e "$FIXTURE/writing" ]; do sleep .01; done
restore "$dir" & recovery=$!
wait "$writer"; wait "$recovery"
[ "$(cat "$FIXTURE/physical/a1")" = 17 ]
if transaction slow_frame; then exit 92; fi
[ ! -d "$dir" ]
''')
