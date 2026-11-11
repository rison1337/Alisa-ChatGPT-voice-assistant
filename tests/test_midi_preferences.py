"""Midi preferences apply without restarting audio; demo matches the renderer."""
import copy,os,select,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from alisa_cli import cli,display,menus,runtime,state,ui

ROOT=Path(__file__).resolve().parents[1]

class MidiPreferences(unittest.TestCase):
    def config(self):
        config=state.defaults('native');config.update(host='192.0.2.44',model='cucumber',installed=True)
        return config

    def test_display_selection_preserves_legacy_flags_and_is_available_in_menu(self):
        config=self.config();config['midi_matrix']=True
        self.assertEqual(display.preference(config),'matrix')
        self.assertIn(('settings','display'),[action for _,action in menus.entries('native','settings',config)])
        with patch.object(ui,'select',return_value='eyes'),patch.object(cli,'save'),patch.object(runtime,'update_preferences'):
            cli.settings(config,fields=('display',))
        self.assertTrue(config['midi_eyes']);self.assertFalse(config['midi_matrix'])

    def test_display_and_fire_update_only_their_files_without_stopping_audio(self):
        config=self.config();previous=copy.deepcopy(config)
        config.update(midi_eyes=True,midi_fire=True)
        with patch.object(runtime,'Station') as station,patch.object(runtime,'status') as status,patch.object(runtime,'stop') as stop:
            runtime.update_preferences(config,previous)
        self.assertEqual(dict(call.args for call in station.return_value.put.call_args_list),
            {runtime.NATIVE+'/midi-display':b'eyes\n',runtime.NATIVE+'/midi-fire':b'1\n'})
        status.assert_not_called();stop.assert_not_called()

    def test_failed_display_write_restores_previous_preference(self):
        config=self.config();previous=copy.deepcopy(config);config['midi_eyes']=True
        with patch.object(runtime,'Station') as station:
            station.return_value.put.side_effect=[OSError('synthetic write failure'),None]
            with self.assertRaises(OSError):runtime.update_preferences(config,previous)
        self.assertEqual(station.return_value.put.call_args.args,(runtime.NATIVE+'/midi-display',b'clock\n'))

    def test_fire_is_explicit_and_scoped_to_midi(self):
        config=self.config()
        self.assertEqual(runtime.midi_fire_preference(config),b'0\n')
        actions=[action for _,action in menus.entries('native','lighting',config)]
        self.assertIn(('settings','midi-fire-on'),actions)
        config['model']='lite2'
        self.assertNotIn(('settings','midi-fire-on'),[action for _,action in menus.entries('native','lighting',config)])

    @unittest.skipUnless(sys.platform=='linux','requires Bash and pipe select')
    def test_default_rainbow_and_optional_fire_match_their_preview(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);gpio=root/'gpio';gpio.write_text('gpio-505 ( |mute-switch ) in hi\n')
            uptime=root/'uptime';uptime.write_text('100.00 0\n')
            for fire,style in ((False,'rainbow'),(True,'rainbow'),(True,'red')):
                frames=[]
                for preview in (False,True):
                    ring=root/'ring';logo=root/'logo';ring.write_bytes(bytes(72));logo.write_bytes(bytes(18))
                    env={**os.environ,'ALICE_MIDI_FB':str(ring),'ALISA_MIDI_LOGO_FB':str(logo),
                        'ALICE_MUTE_GPIO_STATUS':str(gpio),'ALISA_RING_UPTIME_FILE':str(uptime),
                        'ALISA_LED_PREVIEW':str(int(preview)),'ALISA_LED_STYLE':'rainbow',
                        'ALISA_MIDI_FIRE':str(int(fire)),'ALISA_MIDI_FIRE_FILE':'',
                        'ALISA_MIDI_DISPLAY_FILE':str(root/'missing-display'),
                        'ALISA_MIDI_EYES':'0','ALISA_MIDI_MATRIX':'0'}
                    process=subprocess.Popen(['bash',str(ROOT/'assets/native/indicator.sh')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env)
                    try:
                        process.stdin.write(('STYLE '+style+'\nF 160 0 0 0 160 0 0 0 160\n').encode());process.stdin.flush()
                        self.assertTrue(select.select([process.stdout],[],[],3)[0])
                        self.assertEqual(process.stdout.readline(),b'FRAME\n');frames.append(ring.read_bytes())
                        _,error=process.communicate(b'0\n',timeout=5)
                        self.assertEqual(process.returncode,0,error)
                    finally:
                        if process.poll() is None:process.kill();process.communicate()
                self.assertEqual(frames[0],frames[1])
                if style=='rainbow':self.assertEqual(max(frames[0][0::3])>0,not fire)

if __name__=='__main__':unittest.main()
