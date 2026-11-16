"""Detached startup must report actual readiness, without station or audio access."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from alisa_cli import doctor, state

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'posix' and Path('/proc/self/cmdline').exists()
                     and shutil.which('setsid'), 'Linux detached process fixture')
class NativeStartup(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='native-startup-')
        self.root = Path(self.temp.name)
        self.base = self.root / 'native'
        self.runtime = self.root / 'runtime'
        self.tools = self.root / 'tools'
        (self.base / 'bin').mkdir(parents=True)
        self.tools.mkdir()
        # Deliberately omit setsid, unless a test installs a real or failed one.
        for name in ('mkdir', 'pidof', 'cat', 'tr', 'nohup', 'sh', 'sleep', 'rm', 'grep', 'awk'):
            found = shutil.which(name)
            self.assertIsNotNone(found)
            (self.tools / name).symlink_to(found)
        self.script = self.base / 'run.sh'
        source = (ROOT / 'engines/native/station/run.sh').read_text()
        self.script.write_text(source.replace('BASE=/data/opt/alisa-standalone', 'BASE=' + str(self.base))
                               .replace('RUNTIME=/tmp/alisa-standalone', 'RUNTIME=' + str(self.runtime)))
        profile = self.base / 'profile.sh'
        profile.write_text('''#!/bin/sh
printf '%s\n' "$1" >> "$FIXTURE/profile-events"
if [ "$1" = bridge ] && [ "${FAIL_PROFILE:-0}" = 1 ]; then exit 9; fi
if [ "$1" = commit ] && [ "${FAIL_COMMIT:-0}" = 1 ]; then exit 9; fi
''')
        profile.chmod(0o700)
        binary = self.base / 'bin/alisa-standalone'
        binary.write_text('''#!/bin/sh
case "$1" in
serve)
    [ "${FAIL_CLIENT:-0}" = 0 ] || exit 7
    trap 'exit 0' TERM INT
    while [ ! -f "$FIXTURE/allow-ready" ]; do sleep .05; done
    printf '%s\n' "$$" > "$FIXTURE/served"
    while :; do sleep .05; done
    ;;
status)
    [ -f "$FIXTURE/served" ] && echo '{"agent_connected": true}' || echo '{"agent_connected": false}'
    ;;
esac
''')
        binary.chmod(0o700)
        self.env = {**os.environ, 'PATH': str(self.tools), 'FIXTURE': str(self.root)}
        for key in ('LD_PRELOAD', 'ALISA_ICE_BIND_ADDRESS', 'FAIL_PROFILE', 'FAIL_CLIENT', 'FAIL_COMMIT'):
            self.env.pop(key, None)
        self.processes = []

    def tearDown(self):
        # Kill only processes whose command line identifies this unique fixture.
        for _ in range(2):
            for path in Path('/proc').iterdir():
                if not path.name.isdigit():
                    continue
                try:
                    command = (path / 'cmdline').read_bytes()
                    if os.fsencode(self.base) in command:
                        os.kill(int(path.name), signal.SIGTERM)
                except (FileNotFoundError, ProcessLookupError, PermissionError):
                    pass
            time.sleep(.1)
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=5)
        self.temp.cleanup()

    def enable_setsid(self):
        (self.tools / 'setsid').symlink_to(shutil.which('setsid'))

    def launch(self, action='start'):
        process = subprocess.Popen(['/bin/sh', str(self.script), action], env=self.env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.processes.append(process)
        return process

    def wait_file(self, path):
        deadline = time.monotonic() + 5
        while not path.exists():
            if time.monotonic() >= deadline:
                self.fail('fixture did not create ' + path.name)
            time.sleep(.02)

    def assert_failed(self, process):
        output, error = process.communicate(timeout=8)
        self.assertNotEqual(process.returncode, 0, (output, error))
        self.assertNotIn(b'runtime ready', output)
        self.assertFalse((self.runtime / 'monitor.pid').exists())
        self.assertFalse((self.runtime / 'monitor.ready').exists())

    def test_missing_setsid_fails_before_pid_file_or_profile_change(self):
        process = self.launch()
        output, error = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn(b'requires setsid', error)
        self.assertFalse((self.runtime / 'monitor.pid').exists())
        self.assertFalse((self.root / 'profile-events').exists())

    def test_setsid_execution_failure_is_not_reported_as_started(self):
        failed = self.tools / 'setsid'
        failed.write_text('#!/bin/sh\nexit 127\n')
        failed.chmod(0o700)
        self.assert_failed(self.launch())

    def test_profile_switch_failure_is_not_reported_as_started(self):
        self.enable_setsid()
        self.env['FAIL_PROFILE'] = '1'
        self.assert_failed(self.launch())
        self.assertEqual((self.root / 'profile-events').read_text().splitlines(), ['bridge', 'stock'])

    def test_early_client_failure_is_not_reported_as_started(self):
        self.enable_setsid()
        self.env['FAIL_CLIENT'] = '1'
        self.assert_failed(self.launch())
        self.assertIn('stock', (self.root / 'profile-events').read_text().splitlines())

    def test_failed_profile_commit_cannot_acknowledge_ready(self):
        self.enable_setsid()
        self.env['FAIL_COMMIT'] = '1'
        (self.root / 'allow-ready').touch()
        self.assert_failed(self.launch())
        self.assertEqual((self.root / 'profile-events').read_text().splitlines(),
                         ['bridge', 'commit', 'stock'])

    def test_start_waits_for_agent_and_commit_then_is_idempotent(self):
        self.enable_setsid()
        process = self.launch()
        self.wait_file(self.runtime / 'client.pid')
        time.sleep(.1)
        self.assertIsNone(process.poll(), 'start returned before the agent was ready')
        (self.root / 'allow-ready').touch()
        output, error = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, error)
        self.assertIn(b'runtime ready', output)
        pid = (self.runtime / 'monitor.pid').read_text()
        self.assertEqual((self.runtime / 'monitor.ready').read_text(), pid)
        again = self.launch()
        output, error = again.communicate(timeout=5)
        self.assertEqual(again.returncode, 0, error)
        self.assertIn(b'already started', output)
        self.assertEqual((self.runtime / 'monitor.pid').read_text(), pid)
        self.assertEqual((self.root / 'profile-events').read_text().splitlines(), ['bridge', 'commit'])
        stopped = self.launch('stop')
        _, error = stopped.communicate(timeout=5)
        self.assertEqual(stopped.returncode, 0, error)
        self.assertFalse((self.runtime / 'monitor.ready').exists())
        self.assertFalse((self.runtime / 'monitor.pid').exists())

    def test_stale_ready_file_cannot_hide_failed_new_launch(self):
        self.runtime.mkdir()
        (self.runtime / 'monitor.ready').write_text('1\n')
        failed = self.tools / 'setsid'
        failed.write_text('#!/bin/sh\nexit 127\n')
        failed.chmod(0o700)
        self.assert_failed(self.launch())


class NativeSessionDependency(unittest.TestCase):
    def inspect(self, mode, tools):
        config = state.defaults(mode)
        config['host'] = '192.0.2.10'
        response = '\n'.join(['0', 'armv7l', 'yandex,prune\x00amlogic, axg\x00',
                              doctor.MAIND + '  /system/vendor/quasar/maind',
                              doctor.VQE + '  /system/vendor/quasar/libs/libyandex_evil.so',
                              '/dev/data 200000 10000 190000 5% /data',
                              *['/bin/' + name for name in tools]])
        with patch.object(doctor.Station, 'text', return_value=response), \
             patch.object(doctor.shutil, 'which', return_value='/bin/tool'), \
             patch.object(doctor, 'login_missing', return_value=[]), \
             patch.object(doctor.sys, 'platform', 'linux'), \
             patch.object(doctor.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)):
            return doctor.inspect(config)

    def test_native_preflight_requires_setsid(self):
        report = self.inspect('native', ['aplay', 'base64', 'sha256sum'])
        self.assertFalse(report['ok'])
        self.assertEqual(report['station_missing'], ['setsid'])
        config = state.defaults('native')
        with self.assertRaisesRegex(RuntimeError, 'setsid'):
            doctor.require(config, report)

    def test_bridge_does_not_require_native_launcher_dependency(self):
        report = self.inspect('bridge', ['aplay', 'base64', 'sha256sum', 'socat'])
        self.assertTrue(report['ok'])
        self.assertEqual(report['station_missing'], [])
