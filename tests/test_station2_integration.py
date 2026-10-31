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

    def test_model_disables_custom_lighting_without_changing_voice_commands(self):
        value=self.config();before=copy.deepcopy(value)
        firmware.apply_model(value,'yandexmidi')
        self.assertEqual(value['model'],'yandexmidi');self.assertEqual(value['led'],'off')
        self.assertFalse(value['listen_led']);self.assertEqual(value['phrases'],before['phrases'])
        entries=menus.entries('native','settings',value)
        self.assertIn(('Голос GPT',('voice',None)),entries)
        self.assertFalse(any('Подсветка' in label for label,_ in entries))
        with patch.object(ui,'select') as select,patch.object(ui,'note') as note:
            self.assertEqual(lighting.choose(value),'off')
        select.assert_not_called();note.assert_called_once_with(firmware.LIGHTING_UNAVAILABLE)
        # Older/manually edited profiles must not activate a Lite ring driver.
        value.update(led='rainbow',listen_led=True)
        self.assertIn(b'ALISA_LED_STYLE=off',runtime.native_environment(value))
        self.assertEqual(runtime.listening_preference(value),b'0\n')

    def test_cli_rejects_unsupported_lighting_before_contacting_station(self):
        value=self.config();firmware.apply_model(value,'yandexmidi')
        for args in (['native','settings','listen-on'],['native','settings','--led','red']):
            with self.subTest(args=args),patch.object(cli,'load',return_value=value),\
                 patch.object(runtime,'update_preferences') as update,patch.object(ui,'note'):
                self.assertEqual(cli.main(args),1)
                update.assert_not_called()

    def test_other_models_keep_their_lighting_preferences(self):
        for model in ('lite1','lite2'):
            value=self.config();value['listen_led']=True
            firmware.apply_model(value,model)
            self.assertEqual(value['led'],'rainbow');self.assertTrue(value['listen_led'])
