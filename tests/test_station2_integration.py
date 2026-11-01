"""Exercise the shipped native payload and model limits before any SSH writes."""
import copy
import json
import unittest
from unittest.mock import patch

from alisa_cli import cli,doctor,firmware,install,lighting,menus,runtime,state,storage,ui


def stock(path):
    if path.endswith('launch.json'):
        return json.dumps({'nodes':[{'tasks':[{'arg':['--platformd'],'envs':[]},
                                              {'arg':['--mediad'],'envs':[]}]}]})
    if path.endswith('quasar.cfg'):
        return json.dumps({'audioclient':{'controller':{'playbackParams':{
            'OPUS_STREAM':{'gstPipeline':'{STREAM_PIPELINE}'},
            'PCM_STREAM':{'gstPipeline':'{STREAM_PIPELINE}'},
            'common':{'gstProperties':{'STREAM_PIPELINE':'appsrc name=stream-src ! {SINK}'}}}}}})
    raise AssertionError('Unexpected station read: '+path)


class Station2Integration(unittest.TestCase):
    def config(self):
        value=state.defaults('native');value['host']='192.0.2.44'
        return value

    def test_shipped_bundle_loads_and_gain_is_isolated_to_station2(self):
        for model in ('lite1','lite2','yandexmidi'):
            with self.subTest(model=model),patch.object(install,'Station') as station:
                station.return_value.text.side_effect=stock
                files=install.native_files(station.return_value,model)
                launch=json.loads(files[runtime.NATIVE+'/launch.bridge.json'])
                envs=[env for node in launch['nodes'] for task in node['tasks'] for env in task['envs']]
                self.assertEqual('ALICE_CAPTURE_GAIN=16' in envs,model=='yandexmidi')
                self.assertEqual(files[runtime.NATIVE+'/run.sh'],(state.ROOT/'engines/native/station/run.sh').read_bytes())
                self.assertTrue(files[runtime.NATIVE+'/mute_gpio.so'])
                station.return_value.put.assert_not_called()

    def test_dry_run_validates_real_payload_without_writes_or_changing_config(self):
        value=self.config();before=copy.deepcopy(value)
        with patch.object(install,'require',return_value={'model':'yandexmidi'}),\
             patch.object(install,'Station') as station,\
             patch.object(storage,'report',return_value={'enough':True}),patch.object(install,'save') as save:
            station.return_value.text.side_effect=stock
            result=install.install(value,dry_run=True)
        self.assertTrue(result['dry_run']);self.assertEqual(value,before)
        station.return_value.put.assert_not_called();station.return_value.run.assert_not_called();save.assert_not_called()

    def test_station2_lighting_is_available_and_existing_preferences_are_preserved(self):
        for style in ('rainbow','off','pulse-red'):
            value=self.config();value['led']=style;before=copy.deepcopy(value)
            firmware.apply_model(value,'yandexmidi')
            self.assertEqual(value['model'],'yandexmidi');self.assertEqual(value['led'],style)
            self.assertFalse(value['listen_led']);self.assertEqual(value['phrases'],before['phrases'])
            entries=menus.entries('native','settings',value)
            self.assertIn(('Подсветка',('settings','led')),entries)
            self.assertIn(('[ ] Подсветка в паузах GPT',('settings','listen-on')),entries)
            self.assertIn(('ALISA_LED_STYLE='+style).encode(),runtime.native_environment(value))
            value['listen_led']=True
            self.assertEqual(runtime.listening_preference(value),b'0\n' if style=='off' else b'1\n')

    def test_cli_accepts_station2_colors_and_optional_listening(self):
        for args in (['native','settings','listen-on'],['native','settings','--led','red']):
            value=self.config();firmware.apply_model(value,'yandexmidi')
            with self.subTest(args=args),patch.object(cli,'load',return_value=value),\
                 patch.object(runtime,'update_preferences') as update,patch.object(cli,'save'),patch.object(ui,'note'):
                self.assertEqual(cli.main(args),0)
                update.assert_called_once()
            if args[-1]=='listen-on':self.assertTrue(value['listen_led'])
            else:self.assertEqual(value['led'],'red')

    def test_other_models_keep_their_lighting_preferences(self):
        for model in ('lite1','lite2'):
            value=self.config();value['listen_led']=True
            firmware.apply_model(value,model)
            self.assertEqual(value['led'],'rainbow');self.assertTrue(value['listen_led'])
