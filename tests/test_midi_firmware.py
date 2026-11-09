"""Midi is a distinct, experimental native target, not Station 2."""
import unittest
from alisa_cli import firmware


class MidiFirmware(unittest.TestCase):
    def test_exact_base_marker_with_board_revision(self):
        result=firmware.classify('yandex,cucumber-dvt2\0yandex,cucumber\0amlogic, a5\0', ['a'*64,'b'*64])
        self.assertEqual(result['model'],'cucumber')
        self.assertEqual(result['firmware_status'],'unverified')
        self.assertFalse(result['firmware_abi_supported'])
        self.assertTrue(result['warnings'])

    def test_board_revision_or_soc_alone_does_not_identify_midi(self):
        for marker in ('yandex,cucumber-dvt2','cucumber','amlogic, a5','yandex,cucumber-pro'):
            self.assertIsNone(firmware.classify(marker,['a'*64,'b'*64])['model'])

    def test_station2_is_not_midi(self):
        self.assertEqual(firmware.classify('yandex,station-midi',['a'*64,'b'*64])['model'],'yandexmidi')

    def test_missing_firmware_still_fails(self):
        result=firmware.classify('yandex,cucumber',[])
        self.assertEqual(result['firmware_status'],'unavailable')
        self.assertFalse(result['firmware_files_present'])
