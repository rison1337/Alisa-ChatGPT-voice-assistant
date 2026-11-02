import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock,patch
from alisa_cli import firmware,install


class Mini1Port(unittest.TestCase):
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
