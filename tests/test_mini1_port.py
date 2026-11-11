import hashlib
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock,patch
from alisa_cli import firmware,install
from engines.native.tools import deploy


class Mini1Port(unittest.TestCase):
    def test_known_model_does_not_require_quasar_config(self):
        read_text = Mock(side_effect=AssertionError('unexpected config read'))
        report = firmware.inspect_identity('yandex,station-micro\x00amlogic, axg',
                                           ('a'*64, 'b'*64), read_text)
        self.assertEqual(report['model'], 'lite1')
        read_text.assert_not_called()

    def test_axg_without_device_type_remains_unsupported(self):
        for config in ('', '{}', '{"other":"value"}', 'not JSON', '[]'):
            with self.subTest(config=config):
                report = firmware.inspect_identity('amlogic,axg', ('a'*64, 'b'*64),
                                                   Mock(return_value=config))
                self.assertIsNone(report['model'])

    def test_direct_deploy_detects_mini1_with_compact_json(self):
        def ssh(host, command):
            if 'compatible' in command:
                return b'amlogic,axg\x00'
            if command.startswith('sha256sum '):
                return b'a'*64 + b'  file\n'
            if 'quasar.cfg' in command:
                return b'{"other":"value","deviceType":"yandexmini"}'
            if command == 'uname -m':
                return b'aarch64\n'
            raise AssertionError(command)
        with patch.object(deploy, 'ssh', side_effect=ssh):
            self.assertEqual(deploy.check_firmware('station')['model'], 'mini1')

    def test_direct_deploy_checks_bundle_before_any_station_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            ssh = Mock()
            with patch.object(deploy, 'ROOT', root/'engines/native'), \
                 patch.object(deploy, 'ssh', ssh), \
                 patch.object(deploy, 'check_firmware', return_value={'model':'mini1'}), \
                 patch('sys.argv', ['deploy.py', '--host', 'station']), \
                 self.assertRaisesRegex(RuntimeError, 'glibc 2.23'):
                deploy.main()
            ssh.assert_not_called()

    def test_direct_deploy_requires_matching_client_and_adapter(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package = root/'assets/native'
            files = {'bin/alisa-standalone': b'client', 'volume-libs/libasound.so.2': b'adapter'}
            for name, data in files.items():
                path = package/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            manifest = {'model': 'mini1', 'files': {
                name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
            (package/'manifest.json').write_text(json.dumps(manifest))
            binary = package/'bin/alisa-standalone'
            with patch.object(deploy, 'ROOT', root/'engines/native'):
                self.assertEqual(deploy.mini1_payload(binary), files)
                (package/'volume-libs/libasound.so.2').write_bytes(b'mismatched')
                with self.assertRaisesRegex(RuntimeError, 'matching volume adapter'):
                    deploy.mini1_payload(binary)

    def test_model_is_saved_without_offering_unsupported_lighting(self):
        config={}
        firmware.apply_model(config,'mini1')
        self.assertEqual(config['model'],'mini1')
        self.assertFalse(firmware.supports_lighting('mini1'))
        self.assertTrue(firmware.supports_lighting('yandexmidi'))

    def test_original_bundle_cannot_silently_install_mini1(self):
        station=Mock()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            assets=root/'assets/native'
            assets.mkdir(parents=True)
            (assets/'manifest.json').write_text('{"files":{}}')
            with patch.object(install,'ROOT',root),self.assertRaisesRegex(RuntimeError,'glibc 2.23'):
                install.native_files(station,'mini1')
        station.text.assert_not_called()


if __name__=='__main__':unittest.main()
