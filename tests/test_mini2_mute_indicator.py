"""Exercise both shipped Mini 2 indicators on files, including inherited preload."""
import contextlib
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ('assets/native/indicator.sh', 'engines/bridge/station/indicator.sh')
ENABLED = ' gpio-414 (GPIOZ_3 | ) in hi\n'
MUTED = ' gpio-414 (GPIOZ_3 | ) in lo\n'


@unittest.skipUnless(sys.platform == 'linux', 'shell and file-backed LED fixture')
class Mini2MuteIndicator(unittest.TestCase):
    @contextlib.contextmanager
    def indicator(self, script, initial=ENABLED, preload=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder);paths = []
            for zone in range(1, 7):
                for color in ('red', 'green', 'blue'):
                    path = root / f'mini2:{color}:ring{zone}';path.mkdir()
                    (path / 'brightness').write_text('0\n')
                    (path / 'trigger').write_text('none [quasar]\n');paths.append(path)
            clock = root / 'mini2:white:clockA1';clock.mkdir()
            (clock / 'brightness').write_text('7\n');(clock / 'trigger').write_text('[none] quasar\n')
            gpio = root / 'gpio';gpio.write_text(initial)
            env = {**os.environ, 'ALICE_LED_ROOT': str(root / 'mini2'),
                   'ALICE_MUTE_GPIO_STATUS': str(gpio), 'ALISA_LISTEN_LED_FILE': '', 'ALISA_LISTEN_LED': '0'}
            if preload:
                library = root / 'mute.so'
                subprocess.run(['clang', '-shared', '-fPIC', '-O2',
                                str(ROOT / 'engines/native/station/mute_gpio.c'), '-o', str(library)],
                               check=True, capture_output=True)
                replacement = root / 'always-enabled'
                replacement.write_text(' gpio-0 ( |mute-switch ) in hi\n')
                env.update(LD_PRELOAD=str(library), ALICE_MUTE_GPIO_PATH=str(gpio),
                           ALICE_MUTE_GPIO_FILE=str(replacement))
                # Prove that a normal inherited reader would see the fake hi.
                result = subprocess.run(['cat', str(gpio)], env=env, capture_output=True, check=True)
                self.assertEqual(result.stdout, replacement.read_bytes())
            process = subprocess.Popen(['bash', str(ROOT / script)], env=env, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
            try:
                yield process, gpio, paths
            finally:
                if not process.stdin.closed:process.stdin.close()
                try:process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait();self.fail('indicator did not release ownership')
                error = process.stderr.read().decode(errors='replace')
                process.stdout.close();process.stderr.close()
                self.assertEqual(process.returncode, 0, error)
                self.assertEqual((clock / 'brightness').read_text(), '7\n')
                self.assertEqual((clock / 'trigger').read_text(), '[none] quasar\n')

    def send(self, process, frame='F 0 160 0 0 160 0 0 160 0', expected='FRAME'):
        process.stdin.write((frame + '\n').encode());process.stdin.flush()
        self.assertTrue(select.select([process.stdout], [], [], 2)[0], 'no acknowledgement')
        self.assertEqual(process.stdout.readline().decode().strip(), expected)

    def brightness(self, paths):return [int((p / 'brightness').read_text()) for p in paths]

    def assert_native(self, paths):
        self.assertTrue(all((p / 'trigger').read_text().strip() == 'quasar' for p in paths))

    def test_muted_and_unknown_states_return_triggers_without_brightness_writes(self):
        for script in SCRIPTS:
            for text in (MUTED, '', None, ENABLED + MUTED, ENABLED.replace('hi', 'hi lo'),
                         ENABLED.replace('in', 'out'), ENABLED.replace('414', '415')):
                with self.subTest(script=script, text=text), self.indicator(script) as (process, gpio, paths):
                    self.send(process);ours = self.brightness(paths)
                    if text is None:gpio.unlink()
                    else:gpio.write_text(text)
                    self.send(process, expected='STOCK')
                    self.assertEqual(self.brightness(paths), ours)
                    self.assert_native(paths)

    def test_pre_muted_never_acquires_the_ring(self):
        for script in SCRIPTS:
            with self.subTest(script=script), self.indicator(script, MUTED) as (process, gpio, paths):
                before = [(p / 'trigger').read_text() for p in paths]
                process.stdin.write(b'F 0 160 0 0 160 0 0 160 0\n0\n');process.stdin.close()
                process.wait(timeout=3)
                self.assertEqual(process.stdout.read(), b'')
                self.assertEqual(self.brightness(paths), [0] * 18)
                self.assertEqual([(p / 'trigger').read_text() for p in paths], before)

    def test_unmute_allows_reacquisition_after_firmware_clears_its_warning(self):
        for script in SCRIPTS:
            with self.subTest(script=script), self.indicator(script) as (process, gpio, paths):
                self.send(process);gpio.write_text(MUTED);self.send(process, expected='STOCK')
                gpio.write_text(ENABLED)
                for path in paths:(path / 'brightness').write_text('0\n')
                self.send(process, 'F 0 0 160 0 0 160 0 0 160')
                self.assertEqual(self.brightness(paths), [0, 0, 160] * 6)
                self.send(process, '0', 'STOCK');self.assert_native(paths)

    def test_mute_before_exit_preserves_even_a_matching_red_warning(self):
        for script in SCRIPTS:
            for ending in ('0', 'eof', 'TERM', 'HUP'):
                with self.subTest(script=script, ending=ending), self.indicator(script) as (process, gpio, paths):
                    self.send(process, 'F 160 0 0 160 0 0 160 0 0')
                    warning = self.brightness(paths);gpio.write_text(MUTED)
                    if ending == '0':self.send(process, '0', 'STOCK')
                    elif ending == 'eof':process.stdin.close();process.wait(timeout=3)
                    else:process.send_signal(getattr(signal, 'SIG' + ending));process.wait(timeout=3)
                    self.assertEqual(self.brightness(paths), warning);self.assert_native(paths)

    def test_a_new_firmware_frame_and_trigger_are_preserved(self):
        for script in SCRIPTS:
            with self.subTest(script=script), self.indicator(script) as (process, gpio, paths):
                self.send(process)
                for path in paths:
                    (path / 'brightness').write_text('60\n' if ':red:' in path.name else '0\n')
                    (path / 'trigger').write_text('none [heartbeat]\n')
                gpio.write_text(MUTED);self.send(process, expected='STOCK')
                self.assertEqual(self.brightness(paths), [60, 0, 0] * 6)
                self.assertTrue(all((p / 'trigger').read_text() == 'none [heartbeat]\n' for p in paths))

    @unittest.skipUnless(shutil.which('clang'), 'host C compiler')
    def test_physical_state_is_read_despite_inherited_always_enabled_listing(self):
        with self.indicator(SCRIPTS[0], preload=True) as (process, gpio, paths):
            self.send(process);gpio.write_text(MUTED);self.send(process, expected='STOCK')
            self.assert_native(paths)


if __name__ == '__main__':unittest.main()
