"""Exercise the actual CLI preview and shipped script with a file-backed panel."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from alisa_cli import lighting,state


@unittest.skipUnless(sys.platform=='linux','shell and framebuffer fixture')
class LightingPreview(unittest.TestCase):
    def fixture(self,folder,geometry='89,1'):
        root=Path(folder);fb=root/'fb0';gpio=root/'gpio'
        original=bytes((0,20,60,0))*88+bytes(4);fb.write_bytes(original)
        gpio.write_text(' gpio-421 ( |MUTE_SWITCH ) in lo\n gpio-464 ( |MUTE_STATE ) in lo\n')
        graphics=root/'graphics/fb0';graphics.mkdir(parents=True)
        (graphics/'virtual_size').write_text(geometry+'\n');(graphics/'bits_per_pixel').write_text('32\n')
        class FixtureStation:
            def __init__(self,config):pass
            def run(self,command,**kwargs):
                # Ordinary files stand in for the framebuffer character device.
                command=command.replace('/sys/class/leds',str(root/'leds')).replace('/sys/class/graphics',str(root/'graphics'))
                command=command.replace('/dev/fb0',str(fb)).replace('test -c ','test -f ')
                subprocess.run(['sh','-c',command],check=True,capture_output=True)
            def open_input(self,command):
                env={**os.environ,'ALICE_PANEL_FB':str(fb),'ALICE_MUTE_GPIO_STATUS':str(gpio)}
                env.pop('ALICE_LED_ROOT',None)
                # The host fixture uses bash; target BusyBox ash is checked separately.
                if len(command)>8192:raise RuntimeError('SSH exec request exceeds device limit')
                command=command.replace(' sh \"$task_led_demo\"',' bash \"$task_led_demo\"')
                return subprocess.Popen(['sh','-c',command],env=env,stdin=subprocess.PIPE,
                                        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        return FixtureStation,fb,original

    def wait_frame(self,fb,pixel):
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            if fb.read_bytes()[:4]==pixel:return
            time.sleep(.02)
        self.fail('preview did not render the selected color')

    def test_arrow_selection_renders_on_panel_and_close_restores_original(self):
        with tempfile.TemporaryDirectory() as folder:
            station,fb,original=self.fixture(folder);config=state.defaults('native');config['model']='yandexmidi'
            with patch.object(lighting,'Station',station):
                demo=lighting.Preview(config)
                try:
                    self.assertIn('Демо на колонке',demo.select('red'));self.wait_frame(fb,bytes((160,0,0,0)))
                    demo.select('blue');self.wait_frame(fb,bytes((0,0,160,0)))
                    demo.select('off');self.wait_frame(fb,bytes(4))
                finally:demo.close()
                self.assertEqual(fb.read_bytes(),original)

    def test_other_framebuffer_geometry_is_not_opened_or_written(self):
        with tempfile.TemporaryDirectory() as folder:
            station,fb,original=self.fixture(folder,geometry='3,1');config=state.defaults('native')
            with patch.object(lighting,'Station',station):
                demo=lighting.Preview(config)
                try:
                    self.assertIn('Демо недоступно',demo.select('red'));self.assertIsNone(demo.process)
                finally:demo.close()
                self.assertEqual(fb.read_bytes(),original)

    def test_active_conversation_is_not_interrupted_by_preview(self):
        config=state.defaults('native');config.update(model='yandexmidi',installed=True)
        with patch.object(lighting.runtime,'status',return_value={'phase':'active'}),patch.object(lighting,'Station') as station:
            demo=lighting.Preview(config)
            try:self.assertIn('Сейчас идёт разговор',demo.select('red'))
            finally:demo.close()
            station.assert_not_called()
