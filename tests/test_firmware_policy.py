"""An unknown build is a warning; access, payload and model errors still block."""
import contextlib
import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from alisa_cli import doctor,firmware,install,state,storage,ui

ISSUE_PAIR=('5719ba6cbe756d2d60bc7755e13493340a7a85a9269f5a5b7845f13ee09e6986',
            '838e498b40508e2647633f6b5440759ead5a6e22b2acb33c739b39eb04a54f67')


class FirmwarePolicy(unittest.TestCase):
    def report(self,model='yandex,prune',mode='native',pair=ISSUE_PAIR):
        config=state.defaults(mode);config['host']='192.0.2.44'
        response='\n'.join(['0','aarch64',model+'\0amlogic,axg\0',
            pair[0]+'  /system/vendor/quasar/maind',pair[1]+'  /system/vendor/quasar/libs/libyandex_evil.so',
            '/dev/ubi1_0 89568 19408 65548 23% /data',
            '/bin/aplay','/bin/base64','/bin/sha256sum','/bin/socat',
            'ALISA_FIRMWARE_VERSION=0.372.1.13.4794666714.20261021.136'])
        host=patch.object(doctor.sys,'platform','linux') if mode=='bridge' else contextlib.nullcontext()
        with host,patch('alisa_cli.windows.missing',return_value=[]), \
             patch.object(doctor.Station,'text',return_value=response),patch.object(doctor.shutil,'which',return_value='/bin/tool'), \
             patch.object(doctor.subprocess,'run') as run:
            run.return_value.returncode=0
            return config,doctor.inspect(config)

    def test_issue2_pair_is_installable_but_is_not_claimed_verified(self):
        for model in ('yandex,prune','yandex,station-lite2','plum'):
            with self.subTest(model=model):
                config,report=self.report(model)
                self.assertTrue(report['ok']);self.assertFalse(report['firmware_abi_supported'])
                self.assertEqual(report['firmware_status'],'unverified')
                self.assertTrue(report['firmware_files_present'])
                self.assertEqual(report['firmware_version'],'0.372.1.13.4794666714.20261021.136')
                with patch.object(ui,'note') as note:doctor.require(config,report)
                note.assert_called_once_with(firmware.WARNING,'yellow')

    def test_unknown_lite1_and_lite2_bridge_builds_are_allowed_with_warning(self):
        for model,mode in [('yandex,station-micro','native'),('yandex,prune','bridge')]:
            with self.subTest(model=model,mode=mode):
                config,report=self.report(model,mode)
                self.assertTrue(report['ok']);self.assertTrue(report['warnings'])
                doctor.require(config,report,warn=False)

    def test_known_builds_have_no_warning(self):
        for model,(markers,pairs) in firmware.BUILDS.items():
            if not pairs:continue
            report=firmware.classify(markers[0],pairs[0])
            self.assertEqual(report['firmware_status'],'verified');self.assertEqual(report['warnings'],[])

    def test_hard_failures_remain_blocking_even_for_unknown_build(self):
        config,original=self.report()
        for changes in [{'root':False},{'plum':False},{'mode_supported':False},
                        {'firmware_files_present':False},{'architecture':'x86_64'},
                        {'free_data_kib':1000},{'host_missing':['ssh']},{'station_missing':['aplay']}]:
            with self.subTest(changes=changes),self.assertRaises(RuntimeError):
                doctor.require(config,{**original,**changes})
        for marker,mode in [('yandex,station-max','native'),('fake-plum-device','native'),('yandex,station-micro','bridge')]:
            config,report=self.report(marker,mode)
            with self.subTest(marker=marker,mode=mode),self.assertRaises(RuntimeError):doctor.require(config,report)

    def test_missing_or_invalid_sha_is_not_an_unknown_build_permission(self):
        for pair in [(None,ISSUE_PAIR[1]),('z'*64,ISSUE_PAIR[1]),('',ISSUE_PAIR[1]),()]:
            with self.subTest(pair=pair):
                # A malformed/missing file is different from a new valid hash.
                report=firmware.classify('yandex,prune',pair)
                self.assertFalse(report['firmware_files_present']);self.assertEqual(report['warnings'],[])

    def test_unverified_native_dry_run_reaches_preflight_without_writes(self):
        config,report=self.report()
        with patch.object(doctor,'inspect',return_value=report),patch.object(install,'Station') as station, \
             patch.object(install,'native_files',return_value={'/data/fake':b'payload'}), \
             patch.object(storage,'report',return_value={'enough':True}),patch.object(ui,'note'):
            result=install.install(config,dry_run=True)
        self.assertTrue(result['dry_run']);self.assertTrue(result['preflight']['warnings'])
        station.return_value.put.assert_not_called();station.return_value.run.assert_not_called()

    def test_unverified_firmware_does_not_bypass_bundle_integrity(self):
        config,report=self.report()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);assets=root/'assets/native';assets.mkdir(parents=True)
            (assets/'client').write_bytes(b'corrupt')
            (assets/'manifest.json').write_text(json.dumps({'files':{'client':hashlib.sha256(b'original').hexdigest()}}))
            with patch.object(doctor,'inspect',return_value=report),patch.object(install,'ROOT',root), \
                 patch.object(install,'Station') as station,patch.object(ui,'note'):
                with self.assertRaisesRegex(RuntimeError,'Повреждён файл пакета'):install.install(config,dry_run=True)
            station.return_value.put.assert_not_called();station.return_value.run.assert_not_called()

    def test_terminal_diagnostics_show_unverified_instead_of_incompatible(self):
        _,report=self.report()
        with patch.object(ui,'interactive',return_value=True),ui.feedback(quiet=True) as messages:ui.show(report)
        self.assertIn('Проверка прошивки: Совместимость не проверена',messages)
        self.assertTrue(any(firmware.WARNING in text for text in messages))
        self.assertFalse(any('Совместимая прошивка: Нет' in text for text in messages))

    def test_profiles_set_capture_gain_only_for_yandexmidi(self):
        import importlib.util
        path=Path(__file__).resolve().parents[1]/'engines/native/tools/deploy.py'
        spec=importlib.util.spec_from_file_location('deploy_under_test',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        def launcher():
            return {'nodes':[{'tasks':[{'arg':['--platformd'],'envs':[]},
                                       {'arg':['--mediad'],'envs':[]}]}]}
        for model,wanted in [('yandexmidi',True),(None,False),('lite2',False)]:
            launch,_=module.profiles(launcher(),{'audioclient':{'controller':{'playbackParams':{
                'OPUS_STREAM':{'gstPipeline':'{STREAM_PIPELINE}'},'PCM_STREAM':{'gstPipeline':'{STREAM_PIPELINE}'},
                'common':{'gstProperties':{'STREAM_PIPELINE':'appsrc name=stream-src ! {SINK}'}}}}}},model)
            envs=[e for task in launch['nodes'][0]['tasks'] for e in task.get('envs',[])]
            has=any(e=='ALICE_CAPTURE_GAIN=16' for e in envs)
            self.assertEqual(has,wanted,f'model={model}')

    def test_standalone_deployer_uses_the_same_policy(self):
        path=Path(__file__).resolve().parents[1]/'engines/native/tools/deploy.py'
        spec=importlib.util.spec_from_file_location('policy_deploy',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        def ssh(host,command):
            if command=='cat /proc/device-tree/compatible':return b'yandex,prune\0amlogic,axg\0'
            if command=='uname -m':return b'aarch64\n'
            return (ISSUE_PAIR[1 if command.endswith('libyandex_evil.so') else 0]+'  filename').encode()
        stderr=io.StringIO()
        with patch.object(module,'ssh',side_effect=ssh),contextlib.redirect_stderr(stderr):report=module.check_firmware('fixture')
        self.assertEqual(report['firmware_status'],'unverified');self.assertIn(firmware.WARNING,stderr.getvalue())

    def test_yandexmidi_model_is_supported_with_unverified_firmware_warning(self):
        config,report=self.report(model='yandex,station-midi')
        self.assertEqual(report['model'],'yandexmidi')
        self.assertTrue(report['plum']);self.assertTrue(report['ok'])
        self.assertFalse(report['firmware_abi_supported'])
        self.assertTrue(report['warnings'])

    def test_yandexmidi_pinned_build_is_verified(self):
        config,report=self.report(model='yandex,station-midi',pair=('9ad1476ee37df3e401a8426df7710f8f3d2d0695e01d0b949d99a5a8b562a4d2','e3f80b79cd5b7f9422b790b3fe81cc41883e1c07914e6b474f4fd9313e3b84af'))
        self.assertEqual(report['firmware_status'],'verified')
        self.assertTrue(report['firmware_abi_supported']);self.assertEqual(report['warnings'],[])

    @unittest.skipUnless(sys.platform=='linux','Linux audio bridge')
    def test_bridge_has_no_second_hash_allowlist_and_keeps_profile_checks(self):
        root=Path(__file__).resolve().parents[1]/'engines/bridge'
        with patch.object(sys,'path',[str(root),*sys.path]):
            spec=importlib.util.spec_from_file_location('policy_bridge',root/'bridge.py')
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        commands=[]
        def ssh(host,command):
            commands.append(command)
            if command=='cat /proc/device-tree/compatible':return b'yandex,prune\0'
            if command=='uname -m':return b'aarch64'
            if command.startswith('sha256sum '):return (ISSUE_PAIR[1 if command.endswith('libyandex_evil.so') else 0]+'  filename').encode()
            if command=='cat /etc/asound.conf':return b'unknown ALSA layout'
            return b'{}'
        with patch.object(module,'ssh',side_effect=ssh),patch.object(module,'speech_profile',return_value={}), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(module.check_firmware('fixture')['firmware_status'],'unverified')
            with self.assertRaisesRegex(RuntimeError,'Unexpected ALSA configuration'):module.stage_station('fixture')
        self.assertTrue(all(command.startswith(('cat ','sha256sum ','uname ')) for command in commands))
