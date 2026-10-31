"""Convert the Station 2 mute listing through the real hook; never touch debugfs."""
import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

STATION2 = (
    'gpiochip1: GPIOs 411-496, parent: platform/pinctrl@ff634480, periphs-banks:\n'
    ' gpio-421 (                    |MUTE_SWITCH         ) in  lo    \n'
    ' gpio-464 (                    |MUTE_STATE          ) in  lo    \n'
)

STATION2_MUTED = (
    'gpiochip1: GPIOs 411-496, parent: platform/pinctrl@ff634480, periphs-banks:\n'
    ' gpio-421 (                    |MUTE_SWITCH         ) in  hi    \n'
    ' gpio-464 (                    |MUTE_STATE          ) in  lo    \n'
)

LITE2 = (
    'gpiochip0: GPIOs 480-511:\n'
    ' gpio-504 (                    |mute-switch         ) in  hi    \n'
)


@unittest.skipUnless(sys.platform == 'linux' and shutil.which('clang'), 'host C compiler')
class Station2MuteConversion(unittest.TestCase):
    def build(self, folder):
        library = Path(folder) / 'shim.so'
        subprocess.run(['clang', '-shared', '-fPIC', '-O2',
                        str(ROOT / 'engines/native/station/mute_gpio.c'), '-o', str(library)],
                       check=True, capture_output=True)
        lib = ctypes.CDLL(str(library))
        for name in ('open', 'open64'):
            function = getattr(lib, name)
            function.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_uint]
            function.restype = ctypes.c_int
        return lib

    @staticmethod
    def listing(folder, text):
        fixture = Path(folder) / 'gpio-fixture'
        fixture.write_text(text)
        return fixture

    def read_back(self, fd):
        with os.fdopen(fd, 'rb') as handle:
            return handle.read().decode()

    def test_active_low_listing_is_converted_to_client_format(self):
        with tempfile.TemporaryDirectory() as folder:
            lib = self.build(folder)
            fixture = self.listing(folder, STATION2)
            with patch.dict(os.environ, {'ALICE_MUTE_GPIO_PATH': str(fixture)}):
                os.environ.pop('ALICE_MUTE_GPIO_FILE', None)
                served = self.read_back(lib.open(str(fixture).encode(), os.O_RDONLY, 0))
                self.assertIn('|mute-switch ', served)
                self.assertIn(' hi', served)

    def test_engaged_hardware_mute_reports_muted(self):
        with tempfile.TemporaryDirectory() as folder:
            lib = self.build(folder)
            fixture = self.listing(folder, STATION2_MUTED)
            with patch.dict(os.environ, {'ALICE_MUTE_GPIO_PATH': str(fixture)}):
                os.environ.pop('ALICE_MUTE_GPIO_FILE', None)
                served = self.read_back(lib.open(str(fixture).encode(), os.O_RDONLY, 0))
                self.assertIn('|mute-switch ', served)
                self.assertNotIn(' hi', served)

    def test_client_format_listing_passes_through_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            lib = self.build(folder)
            fixture = self.listing(folder, LITE2)
            with patch.dict(os.environ, {'ALICE_MUTE_GPIO_PATH': str(fixture)}):
                os.environ.pop('ALICE_MUTE_GPIO_FILE', None)
                served = self.read_back(lib.open(str(fixture).encode(), os.O_RDONLY, 0))
                self.assertEqual(served, LITE2)

    def test_explicit_replacement_file_takes_precedence(self):
        with tempfile.TemporaryDirectory() as folder:
            lib = self.build(folder)
            fixture = self.listing(folder, STATION2)
            replacement = Path(folder) / 'static-mute-gpio'
            replacement.write_text(' gpio-0 ( |mute-switch ) in  hi\n')
            with patch.dict(os.environ, {'ALICE_MUTE_GPIO_PATH': str(fixture),
                                         'ALICE_MUTE_GPIO_FILE': str(replacement)}):
                served = self.read_back(lib.open(str(fixture).encode(), os.O_RDONLY, 0))
                self.assertEqual(served, ' gpio-0 ( |mute-switch ) in  hi\n')


if __name__ == '__main__':
    unittest.main()
