"""Run the shipped ring script against temporary files, with no device access."""
import contextlib
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
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
    @contextlib.contextmanager
    def panel(self, gpio_line=' gpio-421 ( |MUTE_SWITCH ) in lo\n gpio-464 ( |MUTE_STATE ) in lo\n',
              listen='0', preview=False, initial=None):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);fb=root/'fb0';gpio=root/'gpio';flag=root/'listen-led'
            fb.write_bytes(bytes(356) if initial is None else initial);gpio.write_text(gpio_line)
            env={**os.environ,'ALICE_PANEL_FB':str(fb),'ALICE_MUTE_GPIO_STATUS':str(gpio),
                 'ALISA_LED_STYLE':'red','ALISA_LISTEN_LED':listen,'ALISA_LISTEN_LED_FILE':str(flag),
                 'ALISA_LED_PREVIEW':str(int(preview))}
            env.pop('ALICE_LED_ROOT',None)
            process=subprocess.Popen(['bash',str(ROOT/'assets/native/indicator.sh')],stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,bufsize=0)
            try:yield process,fb,gpio,flag
            finally:
                if not process.stdin.closed:process.stdin.close()
                try:process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait(timeout=2);self.fail('indicator did not release ownership')
                error=process.stderr.read().decode(errors='replace')
                process.stdout.close();process.stderr.close()
                self.assertEqual(process.returncode,0,error)

    def send(self,process,command,expected='FRAME'):
        process.stdin.write((command+'\n').encode());process.stdin.flush()
        self.assertTrue(select.select([process.stdout],[],[],2)[0],'no indicator acknowledgement')
        self.assertEqual(process.stdout.readline().decode().strip(),expected)

    def test_zone_frame_maps_to_rgbw_pixels_with_pad(self):
        with self.panel() as (process,fb,gpio,flag):
            self.send(process,'F 160 0 0 0 160 0 0 0 160')
            pixels=[fb.read_bytes()[i:i+4] for i in range(0,356,4)]
            self.assertEqual(pixels[0],bytes((0,0,160,0)))
            self.assertEqual(pixels[33],bytes((160,0,0,0)))
            self.assertEqual(pixels[84],bytes((160,0,0,0)))
            self.assertEqual(pixels[88],bytes(4));self.assertEqual(sum(bool(any(p)) for p in pixels),88)
            self.send(process,'0','STOCK');self.assertEqual(fb.read_bytes(),bytes(356))

    def test_default_pauses_are_dark_and_live_listening_toggle_is_optional(self):
        with self.panel() as (process,fb,gpio,flag):
            self.send(process,'F 0 0 0 0 0 0 0 0 0');self.assertEqual(fb.read_bytes(),bytes(356))
            flag.write_text('1\n');self.send(process,'F 0 0 0 0 0 0 0 0 0')
            first=fb.read_bytes();self.assertTrue(any(first))
            self.send(process,'F 0 0 0 0 0 0 0 0 0');self.assertNotEqual(fb.read_bytes(),first)
            flag.write_text('0\n');self.send(process,'F 0 0 0 0 0 0 0 0 0')
            self.assertEqual(fb.read_bytes(),bytes(356))

    def test_mute_after_acquisition_preserves_the_firmware_warning(self):
        for text in (' gpio-421 ( |MUTE_SWITCH ) in hi\n gpio-464 ( |MUTE_STATE ) in lo\n',
                     ' gpio-421 ( |MUTE_STATE ) in unknown\n', 'unreadable state\n'):
            with self.subTest(text=text),self.panel() as (process,fb,gpio,flag):
                self.send(process,'F 0 160 0 0 160 0 0 160 0')
                warning=bytes((80,0,0,0))*88+bytes(4);fb.write_bytes(warning);gpio.write_text(text)
                self.send(process,'F 0 0 160 0 0 160 0 0 160','STOCK')
                self.assertEqual(fb.read_bytes(),warning)
                process.stdin.close();process.wait(timeout=3);self.assertEqual(fb.read_bytes(),warning)

    def test_preexisting_warning_or_mute_is_never_owned(self):
        warning=bytes((40,0,0,0))*88+bytes(4)
        for text in (' gpio-1 ( |MUTE_SWITCH ) in hi\n',' gpio-1 ( |MUTE_SWITCH ) in lo hi\n',
                     ' gpio-1 ( |MUTE_STUFF ) in lo\n',' gpio-1 ( |MUTE_STATE ) in lo\n'):
            with self.subTest(text=text),self.panel(gpio_line=text,initial=warning) as (process,fb,gpio,flag):
                process.stdin.write(b'F 0 160 0 0 160 0 0 160 0\n0\n');process.stdin.close()
                process.wait(timeout=3);self.assertEqual(process.stdout.read(),b'')
                self.assertEqual(fb.read_bytes(),warning)

    def test_legacy_converted_gpio_is_supported(self):
        with self.panel(gpio_line=' gpio-0 ( |mute-switch ) in hi\n') as (process,fb,gpio,flag):
            self.send(process,'F 160 0 0 160 0 0 160 0 0');self.assertTrue(any(fb.read_bytes()))
            gpio.write_text(' gpio-0 ( |mute-switch ) in lo\n')
            self.send(process,'F 0 0 160 0 0 160 0 0 160','STOCK')

    def test_exit_release_preserves_a_new_native_owner(self):
        for release in ('0','eof','TERM','HUP'):
            with self.subTest(release=release),self.panel() as (process,fb,gpio,flag):
                self.send(process,'F 160 0 0 160 0 0 160 0 0')
                external=bytes((0,80,100,0))*88+bytes(4);fb.write_bytes(external)
                if release=='0':self.send(process,'0','STOCK')
                elif release=='eof':process.stdin.close();process.wait(timeout=3)
                else:
                    process.send_signal(getattr(signal,'SIG'+release));process.wait(timeout=3)
                self.assertEqual(fb.read_bytes(),external)

    def test_muting_before_exit_does_not_even_clear_our_old_frame(self):
        with self.panel() as (process,fb,gpio,flag):
            self.send(process,'F 0 0 160 0 0 160 0 0 160');own=fb.read_bytes()
            gpio.unlink();self.send(process,'0','STOCK');self.assertEqual(fb.read_bytes(),own)

    def test_solid_frame_recovers_from_an_ordinary_firmware_repaint(self):
        with self.panel() as (process,fb,gpio,flag):
            command='F 160 0 0 160 0 0 160 0 0';self.send(process,command);ours=fb.read_bytes()
            fb.write_bytes(bytes((0,0,80,0))*88+bytes(4))
            self.send(process,command);self.assertEqual(fb.read_bytes(),ours)
            # Our own red frame is not confused with an external warning.
            self.send(process,'F 0 160 0 0 160 0 0 160 0');self.assertNotEqual(fb.read_bytes(),ours)

    def test_external_red_warning_takes_priority_even_when_unmuted(self):
        with self.panel() as (process,fb,gpio,flag):
            self.send(process,'F 0 160 0 0 160 0 0 160 0')
            warning=bytes((40,0,0,0))*88+bytes(4);fb.write_bytes(warning)
            self.send(process,'F 0 0 160 0 0 160 0 0 160','STOCK');self.assertEqual(fb.read_bytes(),warning)

    def test_demo_restores_actual_initial_frame_and_normal_chat_restores_darkness(self):
        initial=bytes((0,25,50,0))*88+bytes(4)
        for preview in (True,False):
            with self.subTest(preview=preview),self.panel(preview=preview,initial=initial) as (process,fb,gpio,flag):
                self.send(process,'F 0 160 0 0 160 0 0 160 0')
                self.send(process,'0','STOCK');self.assertEqual(fb.read_bytes(),initial if preview else bytes(356))
                self.send(process,'F 0 160 0 0 160 0 0 160 0');self.send(process,'0','STOCK')
                self.assertEqual(fb.read_bytes(),initial if preview else bytes(356))

    def test_short_framebuffer_is_not_written(self):
        with self.panel(initial=b'too short') as (process,fb,gpio,flag):
            process.stdin.write(b'F 160 0 0 160 0 0 160 0 0\n');process.stdin.close();process.wait(timeout=3)
            self.assertEqual(process.stdout.read(),b'');self.assertEqual(fb.read_bytes(),b'too short')

    def test_invalid_commands_do_not_change_the_panel(self):
        with self.panel() as (process,fb,gpio,flag):
            self.send(process,'F 0 160 0 0 160 0 0 160 0');ours=fb.read_bytes()
            process.stdin.write(b'F 0 0 256 0 0 0 0 0 0\nF -1 2 3\nBLAH\n');process.stdin.close();process.wait(timeout=3)
            self.assertEqual(process.stdout.read(),b'STOCK\n')
            self.assertTrue(any(ours));self.assertEqual(fb.read_bytes(),bytes(356))


if __name__ == '__main__':unittest.main()
