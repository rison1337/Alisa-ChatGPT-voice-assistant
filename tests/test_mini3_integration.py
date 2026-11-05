"""Bergamot native gates, ring preferences and real payload without writes."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

from alisa_cli import cli,doctor,firmware,install,lighting,menus,runtime,state,storage

PAIR=('a'*64,'b'*64)


def stock(path):
    if path.endswith('launch.json'):
        return json.dumps({'nodes':[{'tasks':[{'arg':['--platformd'],'envs':[]},
                                              {'arg':['--mediad'],'envs':[]}]}]})
    if path.endswith('quasar.cfg'):
        return json.dumps({'audioclient':{'controller':{'playbackParams':{
            'OPUS_STREAM':{'gstPipeline':'appsrc name=stream-src ! {SINK}'},
            'PCM_STREAM':{'gstPipeline':'{STREAM_PIPELINE}'},
            'common':{'gstProperties':{'STREAM_PIPELINE':'appsrc name=stream-src ! {SINK}'}}}}}})
    raise AssertionError(path)


class Mini3(unittest.TestCase):
    def config(self,mode='native'):
        value=state.defaults(mode);value['host']='192.0.2.44';return value

    def report(self,mode='native',arch='aarch64',pair=PAIR):
        value=self.config(mode)
        text='\n'.join(['0',arch,'yandex,bergamot-evt1\0yandex,bergamot\0amlogic, axg\0',
            pair[0]+'  /system/vendor/quasar/maind',pair[1]+'  /system/vendor/quasar/libs/libyandex_evil.so',
            '/dev/data 200000 10000 190000 5% /data','/usr/bin/aplay','/bin/base64','/bin/sha256sum','/bin/socat'])
        with patch.object(doctor.Station,'text',return_value=text),\
             patch.object(doctor.shutil,'which',return_value='/bin/tool'),\
             patch('alisa_cli.windows.missing',return_value=[]):
            return value,doctor.inspect(value)

    def test_exact_model_markers_and_hash_pair_scoping(self):
        known=firmware.BUILDS['bergamot'][1][0]
        for marker in ('yandex,bergamot','yandex,bergamot-evt1'):
            self.assertEqual(firmware.classify(marker,known)['firmware_status'],'verified')
            report=firmware.classify(marker,PAIR)
            self.assertEqual(report['model'],'bergamot')
            self.assertEqual(report['firmware_status'],'unverified');self.assertTrue(report['warnings'])
        self.assertEqual(firmware.classify('yandex,station-mini2',known)['firmware_status'],'unverified')
        for marker in ('bergamot','fake-yandex,bergamot','yandex,bergamot-other','amlogic, axg','yandexmini'):
            self.assertIsNone(firmware.classify(marker,PAIR)['model'])

    def test_native_only_and_mandatory_preflight_checks(self):
        value,report=self.report();self.assertTrue(report['ok']);doctor.require(value,report,warn=False)
        for args in ({'mode':'bridge'},{'arch':'x86_64'},{'pair':('missing',PAIR[1])}):
            with self.subTest(args=args):
                value,report=self.report(**args);self.assertFalse(report['ok'])
                with self.assertRaises(RuntimeError):doctor.require(value,report)

    def test_lighting_is_available_without_display_helpers_or_preference_reset(self):
        value=self.config();value.update(model='bergamot',led='red',listen_led=True)
        self.assertEqual(runtime.native_environment(value),b'export ALISA_LED_STYLE=red\nexport ALISA_MINI3_CLOCK_EYES=1\n')
        self.assertEqual(runtime.listening_preference(value),b'1\n')
        for args in (['native','settings','--led','green'],['native','settings','listen-on']):
            value['led']='red';value['listen_led']=False
            with patch.object(cli,'load',return_value=value),patch.object(cli,'save') as save,\
                 patch.object(runtime,'update_preferences') as update,patch('alisa_cli.ui.note'):
                self.assertEqual(cli.main(args),0)
            save.assert_called_once();update.assert_called_once()
        before=copy.deepcopy(value);firmware.apply_model(value,'bergamot')
        self.assertEqual(value['led'],'red');self.assertTrue(value['listen_led'])
        self.assertEqual(value['phrases'],before['phrases']);self.assertEqual(value['vpn'],before['vpn'])
        entries=menus.entries('native','settings',value)
        self.assertIn(('Подсветка',('settings','led')),entries)
        value['led']='off';firmware.apply_model(value,'bergamot')
        self.assertEqual(value['led'],'off');self.assertEqual(runtime.listening_preference(value),b'0\n')
        self.assertIn(b'ALISA_MINI3_CLOCK_EYES=1',runtime.native_environment(value))

    def test_preview_does_not_interfere_with_a_connecting_voice_session(self):
        value=self.config();value.update(model='bergamot',installed=True)
        with patch.object(lighting.runtime,'status',return_value={'phase':'connecting'}),\
             patch.object(lighting,'Station') as station:
            preview=lighting.Preview(value)
            try:
                preview.select('green')
                self.assertTrue(preview.error);self.assertIsNone(preview.process)
                station.assert_not_called()
            finally:preview.close()

    def test_real_bundle_has_standard_audio_and_scoped_display_helpers(self):
        station=Mock();station.text.side_effect=stock
        mini3=install.native_files(station,'bergamot');mini2=install.native_files(station,'mini2')
        self.assertEqual(mini3,mini2)
        launch=json.loads(mini3[runtime.NATIVE+'/launch.bridge.json'])
        envs=[env for node in launch['nodes'] for task in node['tasks'] for env in task['envs']]
        self.assertFalse(any(env.startswith('ALICE_CAPTURE_GAIN=') for env in envs))
        for name in ('clock-eyes.sh','clock-display.sh','clock-isolation.sh'):
            self.assertEqual(mini3[runtime.NATIVE+'/'+name],(state.ROOT/'engines/native/station'/name).read_bytes())
        other=self.config();other['model']='mini2'
        self.assertNotIn(b'CLOCK',runtime.native_environment(other))
        self.assertIn(b'ALISA_DISABLE_LED',mini3[runtime.NATIVE+'/indicator.sh'])
        self.assertNotIn(b'bergamot',mini3[runtime.NATIVE+'/run.sh'])
        station2=install.native_files(station,'yandexmidi')
        self.assertIn(b'ALICE_CAPTURE_GAIN=16',station2[runtime.NATIVE+'/launch.bridge.json'])

    def test_dry_run_does_not_mutate_config_or_station(self):
        value,report=self.report();before=copy.deepcopy(value);station=Mock();station.text.side_effect=stock
        with patch.object(install,'require',return_value=report),patch.object(install,'Station',return_value=station),\
             patch.object(storage,'report',return_value={'enough':True}),patch.object(install,'save') as save:
            result=install.install(value,dry_run=True)
        self.assertTrue(result['dry_run']);self.assertGreater(result['verified_files'],10)
        self.assertEqual(value,before)
        station.put.assert_not_called();station.run.assert_not_called();save.assert_not_called()

    def test_bad_payload_fails_before_reading_remote_configuration(self):
        station=Mock()
        with patch.object(Path,'read_bytes',return_value=b'corrupt'):
            with self.assertRaisesRegex(RuntimeError,'Повреждён файл пакета'):
                install.native_files(station,'bergamot')
        station.text.assert_not_called();station.put.assert_not_called()

    def test_stun_bypass_is_opt_in_and_requires_capable_binary_before_writes(self):
        value=self.config();value['installed']=True
        self.assertFalse(value['disable_stun'])
        value.pop('disable_stun')
        self.assertNotIn(b'ALISA_DISABLE_STUN',runtime.native_environment(value))
        value['disable_stun']=True
        self.assertIn(b'export ALISA_DISABLE_STUN=1\n',runtime.native_environment(value))
        with patch.object(runtime,'Station') as station,patch.object(runtime,'apply_settings') as settings:
            station.return_value.text.side_effect=['','{}']
            with self.assertRaisesRegex(RuntimeError,'пересобранный клиент'):runtime.start(value)
            settings.assert_not_called();station.return_value.run.assert_not_called()
        for invalid in ('true',1,None):
            value['disable_stun']=invalid
            with self.subTest(value=invalid),self.assertRaises(ValueError):state.validate(value)

    def test_stun_capability_allows_normal_start_without_changing_audio_settings(self):
        value=self.config();value.update(installed=True,model='bergamot',disable_stun=True)
        with patch.object(runtime,'Station') as station,patch.object(runtime,'apply_settings') as settings,\
             patch.object(runtime,'status',return_value={'agent_connected':True}):
            station.return_value.text.side_effect=['','{"ice_stun_toggle_revision":1}']
            self.assertTrue(runtime.start(value)['agent_connected'])
            station.return_value.run.assert_called_once_with(runtime.NATIVE+'/run.sh start')
            settings.assert_called_once_with(value)
        self.assertNotIn('capture_gain',value)
        self.assertNotIn(b'ALISA_DISABLE_LED',runtime.native_environment(value))
        self.assertIn(b'ALISA_MINI3_CLOCK_EYES=1',runtime.native_environment(value))


if __name__=='__main__':unittest.main()
