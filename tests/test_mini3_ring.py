"""Real ring script on temporary framebuffers; never access station hardware."""
import contextlib
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from alisa_cli import lighting,state

ROOT=Path(__file__).resolve().parents[1]
SHELL=shutil.which('bash') if sys.platform!='win32' else None
if sys.platform=='win32' and Path('C:/Program Files/Git/bin/bash.exe').is_file():
    SHELL='C:/Program Files/Git/bin/bash.exe'


@unittest.skipUnless(SHELL,'Bash required for file-backed ring fixtures')
class Mini3Ring(unittest.TestCase):
    @contextlib.contextmanager
    def ring(self,initial=None,preview=False,gpio_text=' gpio-505 ( |mute-switch ) in hi\n'):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);fb=root/'fb0';gpio=root/'gpio';flag=root/'listen-led'
            fb.write_bytes(bytes(18) if initial is None else initial)
            gpio.write_bytes(gpio_text.encode());clock=root/'clockA0';clock.write_bytes(b'17\n')
            uptime=root/'uptime';uptime.write_bytes(b'100.00 0.00\n')
            env={**os.environ,'ALICE_MINI3_FB':fb.as_posix(),'ALICE_MUTE_GPIO_STATUS':gpio.as_posix(),
                 'ALISA_LISTEN_LED_FILE':flag.as_posix(),'ALISA_LED_STYLE':'rainbow',
                 'ALISA_LISTEN_LED':'0','ALISA_LED_PREVIEW':str(int(preview)),
                 'ALISA_RING_UPTIME_FILE':uptime.as_posix()}
            for key in ('ALICE_LED_ROOT','ALICE_PANEL_FB','ALISA_DISABLE_LED','ALISA_MINI3_CLOCK_EYES'):env.pop(key,None)
            process=subprocess.Popen([SHELL,(ROOT/'assets/native/indicator.sh').as_posix()],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env)
            lines=queue.Queue()
            def read_lines():
                for line in iter(process.stdout.readline,b''):lines.put(line)
            reader=threading.Thread(target=read_lines,daemon=True)
            reader.start();process.responses=lines
            try:yield process,fb,gpio,flag,env
            finally:
                if not process.stdin.closed:process.stdin.close()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait();self.fail('Ring did not release ownership')
                reader.join(timeout=2)
                error=process.stderr.read().decode(errors='replace')
                process.stdout.close();process.stderr.close()
                self.assertEqual(process.returncode,0,error)
                self.assertEqual(clock.read_bytes(),b'17\n')

    def send(self,process,command,expected='FRAME'):
        process.stdin.write((command+'\n').encode());process.stdin.flush()
        try:line=process.responses.get(timeout=3)
        except queue.Empty:self.fail('No ring acknowledgement')
        if expected=='FRAME' and line.strip()==b'STOCK':
            try:line=process.responses.get(timeout=3)
            except queue.Empty:self.fail('No heartbeat acknowledgement after yielding')
        self.assertEqual(line.strip().decode(),expected)

    def test_three_zones_map_to_six_bgr_pixels(self):
        with self.ring() as (p,fb,gpio,flag,env):
            self.send(p,'F 10 20 30 40 50 60 70 80 90')
            self.assertEqual(fb.read_bytes(),bytes((30,20,10))*2+bytes((60,50,40))*2+bytes((90,80,70))*2)
            self.send(p,'0','STOCK');self.assertEqual(fb.read_bytes(),bytes(18))

    def test_listening_preference_and_color_off(self):
        with self.ring() as (p,fb,gpio,flag,env):
            self.send(p,'F 0 0 0 0 0 0 0 0 0');self.assertEqual(fb.read_bytes(),bytes(18))
            flag.write_bytes(b'1\n');self.send(p,'F 0 0 0 0 0 0 0 0 0')
            first=fb.read_bytes();self.assertTrue(any(first))
            self.send(p,'F 0 0 0 0 0 0 0 0 0');self.assertNotEqual(fb.read_bytes(),first)
            flag.write_bytes(b'0\n');self.send(p,'F 0 0 0 0 0 0 0 0 0')
            self.assertEqual(fb.read_bytes(),bytes(18))

    def test_demo_restores_initial_frame_and_chat_returns_dark(self):
        original=bytes((10,20,30))*6
        for preview in (False,True):
            with self.subTest(preview=preview),self.ring(initial=original,preview=preview) as (p,fb,gpio,flag,env):
                self.send(p,'F 0 160 0 0 160 0 0 160 0')
                self.send(p,'0','STOCK')
                self.assertEqual(fb.read_bytes(),original if preview else bytes(18))

    def test_mute_or_unknown_gpio_forbids_writes_including_release(self):
        for text in (' gpio-505 ( |mute-switch ) in lo\n',' gpio-505 ( |mute-switch ) in hi lo\n','missing label\n',None):
            with self.subTest(gpio=text),self.ring() as (p,fb,gpio,flag,env):
                self.send(p,'F 0 160 0 0 160 0 0 160 0');own=fb.read_bytes()
                if text is None:gpio.unlink()
                else:gpio.write_bytes(text.encode())
                self.send(p,'F 0 0 160 0 0 160 0 0 160','STOCK')
                self.assertEqual(fb.read_bytes(),own)
                p.stdin.close();p.wait(timeout=3);self.assertEqual(fb.read_bytes(),own)

    def test_external_frame_and_red_warning_are_preserved(self):
        with self.ring() as (p,fb,gpio,flag,env):
            self.send(p,'F 0 160 0 0 160 0 0 160 0')
            warning=bytes((0,0,40))*6;fb.write_bytes(warning)
            self.send(p,'F 0 0 160 0 0 160 0 0 160')
            self.assertEqual(fb.read_bytes(),warning)
        with self.ring() as (p,fb,gpio,flag,env):
            self.send(p,'F 0 160 0 0 160 0 0 160 0')
            external=bytes((50,30,10))*6;fb.write_bytes(external)
            self.send(p,'0','STOCK');self.assertEqual(fb.read_bytes(),external)

    def test_firmware_frames_renew_quiet_period_before_gpt_resumes(self):
        with self.ring() as (p,fb,gpio,flag,env):
            command='F 0 160 0 0 160 0 0 160 0'
            self.send(p,command);ours=fb.read_bytes()
            external=bytes((50,20,10))*6;fb.write_bytes(external)
            self.send(p,command);self.assertEqual(fb.read_bytes(),external)
            uptime=fb.parent/'uptime';uptime.write_bytes(b'101.99 0.00\n')
            self.send(p,command);self.assertEqual(fb.read_bytes(),external)
            external=bytes((30,40,50))*6;fb.write_bytes(external)
            self.send(p,command);self.assertEqual(fb.read_bytes(),external)
            uptime.write_bytes(b'103.98 0.00\n')
            self.send(p,command);self.assertEqual(fb.read_bytes(),external)
            uptime.write_bytes(b'104.00 0.00\n')
            self.send(p,command);self.assertEqual(fb.read_bytes(),ours)

    def test_exit_while_yielding_does_not_clear_a_frame_matching_our_old_color(self):
        with self.ring() as (p,fb,gpio,flag,env):
            command='F 0 160 0 0 160 0 0 160 0';self.send(p,command);ours=fb.read_bytes()
            fb.write_bytes(bytes((50,20,10))*6);self.send(p,command)
            fb.write_bytes(ours)
            p.stdin.close();p.wait(timeout=3)
            self.assertEqual(fb.read_bytes(),ours)

    def test_short_framebuffer_and_invalid_input_are_not_painted(self):
        with self.ring(initial=b'short') as (p,fb,gpio,flag,env):
            p.stdin.write(b'F 0 160 0 0 160 0 0 160 0\n');p.stdin.close();p.wait(timeout=3)
            self.assertEqual(fb.read_bytes(),b'short')
        with self.ring() as (p,fb,gpio,flag,env):
            p.stdin.write(b'F 256 0 0 0 0 0 0 0 0\nF -1 0 0\n');p.stdin.close();p.wait(timeout=3)
            self.assertEqual(fb.read_bytes(),bytes(18))

    def test_actual_cli_preview_streams_script_and_restores_frame(self):
        original=bytes((10,20,30))*6
        with self.ring(initial=original) as (unused,fb,gpio,flag,env):
            class FakeStation:
                def __init__(self,config):pass
                def run(self,command,**kwargs):
                    if '6,1' not in command:raise AssertionError('Mini 3 geometry missing')
                def open_input(self,command):
                    return subprocess.Popen([SHELL,'-c',command],env=env,stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            value=state.defaults('native');value['model']='bergamot'
            with patch.object(lighting,'Station',FakeStation):
                preview=lighting.Preview(value)
                try:
                    preview.select('green');deadline=time.monotonic()+3
                    while fb.read_bytes()==original and time.monotonic()<deadline:time.sleep(.02)
                    self.assertEqual(fb.read_bytes(),bytes((0,160,0))*6)
                finally:preview.close()
            self.assertEqual(fb.read_bytes(),original)


if __name__=='__main__':unittest.main()
