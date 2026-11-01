"""Run the shipped ring script against temporary files, with no device access."""
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'linux', 'shell and file-backed LED fixture')
class NativeRing(unittest.TestCase):
    def test_listening_wave_off_demo_and_physical_mute_restore(self):
        for model, style, demo in [('micro', 'rainbow', False), ('plum', 'rainbow', False),
                                   ('micro', 'off', False), ('micro', 'rainbow', True)]:
            with self.subTest(model=model, style=style, demo=demo), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                prefix = root / model
                paths = []
                for zone in range(1, 4):
                    for color in ('red', 'green', 'blue'):
                        path = root / f'{model}:{color}:ring{zone}'
                        path.mkdir()
                        (path / 'brightness').write_text('0\n')
                        (path / 'trigger').write_text('none [quasar]\n')
                        paths.append(path)
                gpio = root / 'gpio'
                gpio.write_text('gpio-0 ( |mute-switch ) in hi\n' if model == 'plum' else 'software mute; no switch\n')
                env = {**os.environ, 'ALICE_LED_ROOT': str(prefix), 'ALICE_MUTE_GPIO_STATUS': str(gpio),
                       'ALISA_LED_STYLE': style, 'ALISA_LISTEN_LED': '0' if demo else '1','ALISA_LISTEN_LED_FILE':''}
                process = subprocess.Popen(['bash', str(ROOT / 'assets/native/indicator.sh')],
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           text=True, env=env)
                def send(command):
                    process.stdin.write(command + '\n');process.stdin.flush()
                    self.assertTrue(select.select([process.stdout], [], [], 2)[0], 'LED script did not acknowledge frame')
                    return process.stdout.readline().strip()
                def frame():return [int((path / 'brightness').read_text()) for path in paths]
                try:
                    self.assertEqual(send('F 0 0 0 0 0 0 0 0 0'), 'FRAME')
                    first = frame()
                    if style == 'off' or demo:
                        self.assertEqual(first, [0] * 9)
                    else:
                        self.assertGreater(sum(first), 0)
                        self.assertEqual(first[::3], [0, 0, 0])
                    self.assertEqual(send('F 0 0 0 0 0 0 0 0 0'), 'FRAME')
                    if style != 'off' and not demo:self.assertNotEqual(frame(), first)
                    if model == 'plum':
                        gpio.write_text('gpio-0 ( |mute-switch ) in lo\n')
                        self.assertEqual(send('F 0 0 160 0 0 160 0 0 160'), 'STOCK')
                    else:
                        self.assertEqual(send('0'), 'STOCK')
                    self.assertEqual(frame(), [0] * 9)
                    self.assertTrue(all((path / 'trigger').read_text().strip() == 'quasar' for path in paths))
                finally:
                    process.stdin.close();process.wait(timeout=3)
                    error = process.stderr.read()
                    process.stdout.close();process.stderr.close()
                self.assertEqual(process.returncode, 0, error)

    def test_default_pauses_are_dark_and_file_toggle_applies_without_restarting(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);paths=[]
            for zone in range(1,4):
                for color in ('red','green','blue'):
                    path=root/f'micro:{color}:ring{zone}';path.mkdir()
                    (path/'brightness').write_text('0\n');(path/'trigger').write_text('none [quasar]\n');paths.append(path)
            gpio=root/'gpio';gpio.write_text('software mute\n');flag=root/'listen-led'
            env={**os.environ,'ALICE_LED_ROOT':str(root/'micro'),'ALICE_MUTE_GPIO_STATUS':str(gpio),
                 'ALISA_LED_STYLE':'rainbow','ALISA_LISTEN_LED_FILE':str(flag)}
            env.pop('ALISA_LISTEN_LED',None)
            process=subprocess.Popen(['bash',str(ROOT/'assets/native/indicator.sh')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
            def send(values):
                process.stdin.write('F '+' '.join(map(str,values))+'\n');process.stdin.flush()
                self.assertTrue(select.select([process.stdout],[],[],2)[0]);self.assertEqual(process.stdout.readline().strip(),'FRAME')
                return [int((p/'brightness').read_text()) for p in paths]
            try:
                self.assertEqual(send([0]*9),[0]*9)
                flag.write_text('1\n');self.assertGreater(sum(send([0]*9)),0)
                flag.write_text('0\n');self.assertEqual(send([0]*9),[0]*9)
                speaking=[0,120,80]*3;self.assertEqual(send(speaking),speaking)
                self.assertIsNone(process.poll())
            finally:
                process.stdin.close();process.wait(timeout=3);process.stdout.close();process.stderr.close()


@unittest.skipUnless(sys.platform == 'linux', 'shell and framebuffer fixture')
class Station2Panel(unittest.TestCase):
    def launch(self, folder, gpio_line=' gpio-0 ( |mute-switch ) in hi\n', listen='0'):
        root = Path(folder)
        fb = root / 'fb0'
        fb.write_bytes(b'')
        gpio = root / 'gpio'
        gpio.write_text(gpio_line)
        env = {**os.environ, 'ALICE_PANEL_FB': str(fb), 'ALICE_MUTE_GPIO_STATUS': str(gpio),
               'ALISA_LED_STYLE': 'red', 'ALISA_LISTEN_LED': listen, 'ALISA_LISTEN_LED_FILE': ''}
        env.pop('ALICE_LED_ROOT', None)
        process = subprocess.Popen(['bash', str(ROOT / 'assets/native/indicator.sh')],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=env)
        return process, fb

    @staticmethod
    def snapshot(process, fb):
        time.sleep(1)
        return fb.read_bytes()

    def test_zone_frame_maps_to_rgbw_pixels_with_pad(self):
        with tempfile.TemporaryDirectory() as folder:
            process, fb = self.launch(folder)
            process.stdin.write(b'F 160 0 0 0 160 0 0 0 160\n')
            process.stdin.flush()
            frame = self.snapshot(process, fb)
            self.assertEqual(len(frame), 356)
            pixels = [frame[i:i + 4] for i in range(0, len(frame), 4)]
            self.assertEqual(pixels[0], bytes((0, 0, 160, 0)))
            self.assertEqual(pixels[33], bytes((160, 0, 0, 0)))
            self.assertEqual(pixels[84], bytes((160, 0, 0, 0)))
            self.assertEqual(pixels[88], bytes((0, 0, 0, 0)))
            self.assertEqual(sum(1 for p in pixels if any(p)), 88)
            process.stdin.write(b'0\n')
            process.stdin.close()
            process.wait(timeout=10)
            self.assertIn(b'STOCK', process.stdout.read())
            self.assertEqual(fb.read_bytes(), bytes(356))

    def test_physical_mute_leaves_the_panel_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            process, fb = self.launch(folder, gpio_line=' gpio-0 ( |mute-switch ) in lo\n')
            process.stdin.write(b'F 160 0 0 0 160 0 0 0 160\n')
            process.stdin.flush()
            time.sleep(1)
            # Muted: the script must not touch the framebuffer at all, so the
            # firmware keeps the panel (same contract as the ring backend).
            self.assertEqual(fb.read_bytes(), b'')
            process.stdin.close()
            process.wait(timeout=10)
            self.assertIn(b'STOCK', process.stdout.read())

    def test_listening_wave_lights_the_panel_on_dark_frames(self):
        with tempfile.TemporaryDirectory() as folder:
            process, fb = self.launch(folder, listen='1')
            process.stdin.write(b'F 0 0 0 0 0 0 0 0 0\n')
            process.stdin.flush()
            frame = self.snapshot(process, fb)
            self.assertEqual(len(frame), 356)
            self.assertTrue(any(frame))
            process.stdin.close()
            process.wait(timeout=10)


if __name__ == '__main__':unittest.main()
