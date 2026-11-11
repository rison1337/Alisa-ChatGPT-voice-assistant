"""Real clock scripts against temporary segments, without sysfs or mounts."""
import contextlib
import os
from pathlib import Path
import queue
import signal
import time
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]
SHELL=shutil.which('bash') if sys.platform!='win32' else None
if sys.platform=='win32' and Path('C:/Program Files/Git/bin/bash.exe').is_file():
    SHELL='C:/Program Files/Git/bin/bash.exe'
PARTS=[s+str(d) for d in range(4) for s in 'ABCDEFG']+['DH','DM','DL','DEGREE']
GLYPHS={'1':'BC','2':'ABDEG','3':'ABCDG','4':'BCFG'}
INITIAL=[14 if s in GLYPHS[d] else 0 for d in '1234' for s in 'ABCDEFG']+[14,0,14,0]


@unittest.skipUnless(SHELL,'Bash required for clock fixtures')
class Mini3Display(unittest.TestCase):
    def snapshot(self,root):
        return [int((root/('led'+p)/'brightness').read_text()) for p in PARTS]

    def write_frame(self,root,values):
        for p,v in zip(PARTS,values):(root/('led'+p)/'brightness').write_bytes((str(v)+'\n').encode())

    @contextlib.contextmanager
    def clock(self,phase='active',marker='yandex,bergamot-evt1',preview=False):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in ('clock-eyes.sh','clock-display.sh','clock-isolation.sh'):
                (root/name).write_bytes((ROOT/'assets/native'/name).read_bytes())
            for p in PARTS:(root/('led'+p)).mkdir()
            self.write_frame(root,INITIAL)
            for name,data in {'phase':phase+'\n','volume':'1\n','uptime':'0.00 0.00\n',
                              'gpio':' gpio-505 ( |mute-switch ) in hi\n','compatible':marker+'\0amlogic,axg\0'}.items():
                (root/name).write_bytes(data.encode())
            (root/'bin').mkdir()
            binary=root/'bin/alisa-standalone'
            binary.write_bytes(b'#!/bin/sh\nprintf \'{"phase": "%s"}\\n\' "$(cat "$CLOCK_PHASE")"\n')
            binary.chmod(0o700)
            env={**os.environ,'CLOCK_PHASE':(root/'phase').as_posix(),
                 'ALISA_CLOCK_LED_ROOT':(root/'led').as_posix(),'ALISA_CLOCK_GPIO_STATUS':(root/'gpio').as_posix(),
                 'ALISA_CLOCK_DEVICE_TREE':(root/'compatible').as_posix(),
                 'ALISA_CLOCK_UPTIME_FILE':(root/'uptime').as_posix(),'ALISA_CLOCK_VOLUME_FILE':(root/'volume').as_posix()}
            if preview:env['ALISA_CLOCK_PREVIEW']='1'
            process=subprocess.Popen([SHELL,(root/'clock-eyes.sh').as_posix()],env=env,
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            lines=queue.Queue()
            def read_lines():
                for line in iter(process.stdout.readline,b''):lines.put(line)
            reader=threading.Thread(target=read_lines,daemon=True);reader.start();process.responses=lines
            try:yield process,root
            finally:
                if not process.stdin.closed:process.stdin.close()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait();self.fail('Clock failed to stop')
                reader.join(timeout=2);error=process.stderr.read().decode(errors='replace')
                process.stdout.close();process.stderr.close();self.assertEqual(process.returncode,0,error)

    def beat(self,process,count=1,command='F 0 0 0 0 0 0 0 0 0',expected='FRAME'):
        for _ in range(count):
            process.stdin.write((command+'\n').encode());process.stdin.flush()
            try:line=process.responses.get(timeout=3)
            except queue.Empty:self.fail('Clock did not acknowledge heartbeat')
            self.assertEqual(line.strip().decode(),expected)

    def test_eyes_blink_wink_smile_and_close_restore(self):
        with self.clock() as (p,root):
            self.beat(p);opened=self.snapshot(root);self.assertEqual(sum(v>0 for v in opened),8)
            for stamp,lit in (('8.05',2),('8.30',8),('16.05',5),('17.20',6),('18.60',8),('24.05',2)):
                (root/'uptime').write_bytes((stamp+' 0.00\n').encode());self.beat(p)
                self.assertEqual(sum(v>0 for v in self.snapshot(root)),lit,stamp)
            for stamp in ('16.05','48.05'):
                (root/'uptime').write_bytes((stamp+' 0.00\n').encode());self.beat(p)
                frame=self.snapshot(root)
                self.assertEqual(sum(v>0 for v in frame[:7]),4,stamp)
                self.assertEqual(sum(v>0 for v in frame[21:28]),1,stamp)
            self.beat(p,command='0',expected='STOCK');p.wait(timeout=3)
            self.assertEqual(self.snapshot(root),INITIAL)

    def test_preview_draws_while_idle_and_restores_on_close(self):
        with self.clock(phase='idle',preview=True) as (p,root):
            self.beat(p);self.assertEqual(sum(v>0 for v in self.snapshot(root)),8)
            self.beat(p,command='0',expected='STOCK');p.wait(timeout=3)
            self.assertEqual(self.snapshot(root),INITIAL)

    def test_preview_yields_when_a_real_conversation_starts(self):
        with self.clock(phase='idle',preview=True) as (p,root):
            self.beat(p)
            (root/'phase').write_bytes(b'connecting\n')
            for _ in range(7):self.beat(p)
            p.stdin.write(b'F 0 0 0 0 0 0 0 0 0\n');p.stdin.flush();p.wait(timeout=3)
            self.assertEqual(self.snapshot(root),INITIAL)

    def test_volume_left_right_zero_and_return_to_eyes(self):
        with self.clock() as (p,root):
            self.beat(p);opened=self.snapshot(root)
            (root/'volume').write_bytes(b'2\n');self.beat(p)
            self.assertEqual({x for x,v in zip(PARTS,self.snapshot(root)) if v},
                             {'B2','C2','A3','B3','C3','D3','E3','F3'})
            (root/'uptime').write_bytes(b'2.01 0.00\n');self.beat(p)
            self.assertEqual(self.snapshot(root),opened)
            (root/'volume').write_bytes(b'0\n');self.beat(p)
            self.assertEqual({x for x,v in zip(PARTS,self.snapshot(root)) if v},{'A0','B0','C0','D0','E0','F0'})

    def test_display_and_clock_restore_are_independent_of_microphone_gpio(self):
        for text in (' gpio-505 ( |mute-switch ) in lo\n','unknown\n',
                     ' gpio-505 ( |mute-switch ) in hi lo\n',None):
            with self.subTest(text=text),self.clock() as (p,root):
                self.beat(p);opened=self.snapshot(root)
                if text is None:(root/'gpio').unlink()
                else:(root/'gpio').write_bytes(text.encode())
                (root/'volume').write_bytes(b'2\n');self.beat(p)
                expected=[0]*14+[14 if s in 'BC' else 0 for s in 'ABCDEFG']+[14 if s in 'ABCDEF' else 0 for s in 'ABCDEFG']+[0]*4
                self.assertEqual(self.snapshot(root),expected)
                (root/'uptime').write_bytes(b'2.01 0.00\n');self.beat(p)
                self.assertEqual(self.snapshot(root),opened)
                self.beat(p,command='0',expected='STOCK');p.wait(timeout=3)
                self.assertEqual(self.snapshot(root),INITIAL)

    def test_idle_and_external_non_clock_frame_are_preserved(self):
        with self.clock(phase='idle') as (p,root):
            self.beat(p,2);self.assertEqual(self.snapshot(root),INITIAL)
        with self.clock() as (p,root):
            self.beat(p);external=[9]*32;self.write_frame(root,external)
            self.beat(p,2);self.assertEqual(self.snapshot(root),external)
            self.beat(p,command='0',expected='STOCK');p.wait(timeout=3)
            self.assertEqual(self.snapshot(root),external)

    def test_eof_restores_the_saved_clock(self):
        with self.clock() as (p,root):
            self.beat(p);p.stdin.close();p.wait(timeout=3)
            self.assertEqual(self.snapshot(root),INITIAL)

    def test_caught_signals_restore_the_saved_frame(self):
        for caught in (signal.SIGTERM,signal.SIGHUP):
            with self.subTest(signal=caught),self.clock() as (process,root):
                self.beat(process)
                time.sleep(.03)
                process.send_signal(caught);process.wait(timeout=4)
                self.assertEqual(self.snapshot(root),INITIAL)

    def test_other_device_tree_marker_never_acquires_the_display(self):
        with self.clock(marker='yandex,bergamot-other') as (p,root):
            p.stdin.close();p.wait(timeout=3)
            self.assertEqual(self.snapshot(root),INITIAL)

    def test_clock_generation_rolls_over_midnight(self):
        with tempfile.TemporaryDirectory() as folder:
            anchor=Path(folder)/'anchor';anchor.write_bytes(b'1000 1439 14 0\n')
            env={**os.environ,'DISPLAY_SOURCE':(ROOT/'assets/native/clock-display.sh').as_posix(),
                 'CLOCK_ANCHOR':anchor.as_posix(),'ALISA_CLOCK_EPOCH_SECONDS':'60060'}
            r=subprocess.run([SHELL,'-c','. "$DISPLAY_SOURCE"; display_clock "$CLOCK_ANCHOR"; printf "%s\\n" "$display_values"'],
                env=env,capture_output=True,check=True,timeout=3)
            expected=[14 if s in 'ABCDEF' else 0 for _ in range(4) for s in 'ABCDEFG']+[14,0,14,0]
            self.assertEqual(list(map(int,r.stdout.split())),expected)

    def test_ring_does_not_start_clock_helper_when_stock_clock_is_selected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);script=root/'indicator.sh';script.write_bytes((ROOT/'assets/native/indicator.sh').read_bytes())
            helper=root/'clock-eyes.sh';marker=root/'started';mode=root/'display-mode'
            helper.write_bytes(b'printf started > "$CLOCK_STARTED"\n')
            fb=root/'fb';gpio=root/'gpio';gpio.write_bytes(b' gpio-505 ( |mute-switch ) in hi\n')
            env={**os.environ,'ALICE_MINI3_FB':fb.as_posix(),'ALICE_MUTE_GPIO_STATUS':gpio.as_posix(),
                 'ALISA_MINI3_CLOCK_EYES':'1','ALISA_LED_PREVIEW':'0','ALISA_LISTEN_LED_FILE':'',
                 'ALISA_CLOCK_MODE_FILE':mode.as_posix(),'CLOCK_STARTED':marker.as_posix()}
            for key in ('ALICE_LED_ROOT','ALICE_PANEL_FB','ALISA_DISABLE_LED'):env.pop(key,None)
            for value,expected in (('clock',False),('invalid',False),('eyes',True)):
                mode.write_bytes((value+'\n').encode());fb.write_bytes(bytes(18))
                r=subprocess.run([SHELL,script.as_posix()],env=env,input=b'F 0 160 0 0 160 0 0 160 0\n0\n',capture_output=True,timeout=6)
                self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(marker.exists(),expected,value)

    def test_optional_clock_failure_does_not_block_ring_or_preview(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);script=root/'indicator.sh';script.write_bytes((ROOT/'assets/native/indicator.sh').read_bytes())
            helper=root/'clock-eyes.sh';helper.write_bytes(b'exit 0\n')
            fb=root/'fb';gpio=root/'gpio';gpio.write_bytes(b' gpio-505 ( |mute-switch ) in hi\n')
            for preview in ('0','1'):
                fb.write_bytes(bytes(18))
                env={**os.environ,'ALICE_MINI3_FB':fb.as_posix(),'ALICE_MUTE_GPIO_STATUS':gpio.as_posix(),
                     'ALISA_MINI3_CLOCK_EYES':'1','ALISA_LED_PREVIEW':preview,'ALISA_LISTEN_LED_FILE':''}
                for key in ('ALICE_LED_ROOT','ALICE_PANEL_FB','ALISA_DISABLE_LED'):env.pop(key,None)
                r=subprocess.run([SHELL,script.as_posix()],env=env,input=b'F 0 160 0 0 160 0 0 160 0\n0\n',
                    capture_output=True,timeout=5)
                self.assertEqual(r.returncode,0,r.stderr);self.assertIn(b'FRAME',r.stdout)
                self.assertEqual(fb.read_bytes(),bytes(18))


if __name__=='__main__':unittest.main()
