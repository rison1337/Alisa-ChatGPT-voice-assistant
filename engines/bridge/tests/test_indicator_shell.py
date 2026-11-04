"""Run the shipped bridge ring script against file-backed LEDs, with no device access."""
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'station' / 'indicator.sh'


@unittest.skipUnless(sys.platform == 'linux', 'shell and file-backed LED fixture')
class BridgeRing(unittest.TestCase):
    def fixture(self, root, prefix, zone_count):
        paths = []
        for zone in range(1, zone_count + 1):
            for color in ('red', 'green', 'blue'):
                path = root / f'{prefix}:{color}:ring{zone}'
                path.mkdir()
                (path / 'brightness').write_text('0\n')
                (path / 'trigger').write_text('none [quasar]\n')
                paths.append(path)
        clock = root / f'{prefix}:white:clockA1'
        clock.mkdir()
        (clock / 'brightness').write_text('7\n')
        (clock / 'trigger').write_text('[none] quasar\n')
        gpio = root / 'gpio'
        gpio.write_text('gpio-0 ( |mute-switch ) in hi\n' if prefix == 'plum' else 'software mute; no switch\n')
        env = {**os.environ, 'ALICE_LED_ROOT': str(root / prefix), 'ALICE_MUTE_GPIO_STATUS': str(gpio)}
        process = subprocess.Popen(['bash', str(SCRIPT)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, env=env)
        return process, paths, clock

    def frame(self, paths):
        return [int((path / 'brightness').read_text()) for path in paths]

    def send(self, process, command, expected='FRAME'):
        process.stdin.write(command + '\n');process.stdin.flush()
        self.assertTrue(select.select([process.stdout], [], [], 2)[0], 'LED script did not acknowledge frame')
        self.assertEqual(process.stdout.readline().strip(), expected)

    def finish(self, process):
        process.stdin.close();process.wait(timeout=3)
        error = process.stderr.read()
        process.stdout.close();process.stderr.close()
        self.assertEqual(process.returncode, 0, error)

    def test_plum_three_zones_paint_and_release(self):
        with tempfile.TemporaryDirectory() as folder:
            process, paths, clock = self.fixture(Path(folder), 'plum', 3)
            try:
                self.send(process, 'F 10 20 30 40 50 60 70 80 90')
                self.assertEqual(self.frame(paths), [10, 20, 30, 40, 50, 60, 70, 80, 90])
                self.send(process, '0', 'STOCK')
                self.assertEqual(self.frame(paths), [0] * 9)
                self.assertTrue(all((path / 'trigger').read_text().strip() == 'quasar' for path in paths))
            finally:
                self.finish(process)

    def test_mini2_logical_zones_map_to_pairs_and_clock_is_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            process, paths, clock = self.fixture(Path(folder), 'mini2', 6)
            try:
                self.send(process, 'F 10 20 30 40 50 60 70 80 90')
                self.assertEqual(self.frame(paths), [10, 20, 30, 10, 20, 30, 40, 50, 60,
                                                     40, 50, 60, 70, 80, 90, 70, 80, 90])
                self.send(process, '0', 'STOCK')
                self.assertEqual(self.frame(paths), [0] * 18)
                self.assertTrue(all((path / 'trigger').read_text().strip() == 'quasar' for path in paths))
            finally:
                self.finish(process)
            self.assertEqual((clock / 'brightness').read_text(), '7\n')
            self.assertEqual((clock / 'trigger').read_text(), '[none] quasar\n')


if __name__ == '__main__':
    unittest.main()
