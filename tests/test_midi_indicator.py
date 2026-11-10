"""File-backed Midi framebuffer checks; no station or sysfs writes."""
import os
from pathlib import Path
import select
import subprocess
import sys
import time
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform=='linux','requires Bash and pipe select')
class MidiIndicator(unittest.TestCase):
    def test_clock_helper_restarts_after_startup_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);ring=root/'ring';logo=root/'logo';gpio=root/'gpio'
            ring.write_bytes(bytes(72));logo.write_bytes(bytes(18));gpio.write_text('gpio-505 ( |mute-switch ) in hi\n')
            (root/'indicator.sh').write_bytes((ROOT/'assets/native/indicator.sh').read_bytes())
            (root/'midi-clock.sh').write_text('''#!/bin/sh
if [ ! -e "$ATTEMPTS" ]; then echo 1 > "$ATTEMPTS"; exit 1; fi
echo 2 > "$ATTEMPTS"
while IFS= read -r command; do [ "$command" != 0 ] || exit 0; done
''')
            env={**os.environ,'ALICE_MIDI_FB':str(ring),'ALISA_MIDI_LOGO_FB':str(logo),
                 'ALICE_MUTE_GPIO_STATUS':str(gpio),'ALISA_LED_PREVIEW':'0','ALISA_MIDI_EYES':'1',
                 'ALISA_LED_STYLE':'rainbow','ATTEMPTS':str(root/'attempts')}
            process=subprocess.Popen(['bash',str(root/'indicator.sh')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env)
            try:
                deadline=time.monotonic()+7
                while time.monotonic()<deadline:
                    process.stdin.write(b'F 160 0 0 0 160 0 0 0 160\n');process.stdin.flush()
                    if (root/'attempts').exists() and (root/'attempts').read_text().strip()=='2':break
                    time.sleep(.1)
                self.assertEqual((root/'attempts').read_text().strip(),'2')
                _,err=process.communicate(b'0\n',timeout=6);self.assertEqual(process.returncode,0,err)
            finally:
                if process.poll() is None:process.kill();process.communicate()

    def test_fast_animation_survives_delayed_first_frame(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);ring=root/'ring';logo=root/'logo';gpio=root/'gpio'
            ring.write_bytes(bytes(72));logo.write_bytes(bytes(18))
            gpio.write_text('gpio-505 ( |mute-switch ) in hi\n')
            env={**os.environ,'ALICE_MIDI_FB':str(ring),'ALISA_MIDI_LOGO_FB':str(logo),
                 'ALICE_MUTE_GPIO_STATUS':str(gpio),'ALISA_LED_PREVIEW':'0',
                 'ALISA_MIDI_MATRIX':'0','ALISA_LED_STYLE':'rainbow'}
            process=subprocess.Popen(['bash',str(ROOT/'assets/native/indicator.sh')],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env)
            try:
                time.sleep(.2)
                self.assertIsNone(process.poll(),'Animation exited before first heartbeat')
                process.stdin.write(b'F 160 0 0 0 160 0 0 0 160\n');process.stdin.flush()
                self.assertTrue(select.select([process.stdout],[],[],3)[0])
                self.assertEqual(process.stdout.readline(),b'FRAME\n')
                first=ring.read_bytes();time.sleep(.1)
                self.assertNotEqual(first,ring.read_bytes())
                self.assertFalse(select.select([process.stdout],[],[],.03)[0],'Intermediate frame must not send an acknowledgement')
                _,err=process.communicate(b'0\n',timeout=5)
                self.assertEqual(process.returncode,0,err)
                self.assertEqual(ring.read_bytes(),bytes(72));self.assertEqual(logo.read_bytes(),bytes(18))
            finally:
                if process.poll() is None:process.kill();process.communicate()

    def test_preview_restores_ring_and_logo_without_matrix(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);ring=root/'ring';logo=root/'logo';gpio=root/'gpio'
            original=bytes(range(72));badge=bytes(range(18))
            ring.write_bytes(original);logo.write_bytes(badge)
            gpio.write_text('gpio-505 (GPIOZ_10 |mute-switch ) in hi ACTIVE LOW\n')
            env={**os.environ,'ALICE_MIDI_FB':str(ring),'ALISA_MIDI_LOGO_FB':str(logo),
                 'ALICE_MUTE_GPIO_STATUS':str(gpio),'ALISA_LED_PREVIEW':'1',
                 'ALISA_MIDI_MATRIX':'0','ALISA_LED_STYLE':'rainbow'}
            process=subprocess.Popen(['bash',str(ROOT/'assets/native/indicator.sh')],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env)
            try:
                process.stdin.write(b'F 160 0 0 0 160 0 0 0 160\n');process.stdin.flush()
                self.assertTrue(select.select([process.stdout],[],[],3)[0])
                self.assertEqual(process.stdout.readline(),b'FRAME\n')
                self.assertEqual(len(ring.read_bytes()),72);self.assertEqual(len(logo.read_bytes()),18)
                self.assertNotEqual(ring.read_bytes(),original);self.assertNotEqual(logo.read_bytes(),badge)
                out,err=process.communicate(b'0\n',timeout=5)
                self.assertEqual(process.returncode,0,err)
                self.assertEqual(ring.read_bytes(),original);self.assertEqual(logo.read_bytes(),badge)
            finally:
                if process.poll() is None:process.kill();process.communicate()
