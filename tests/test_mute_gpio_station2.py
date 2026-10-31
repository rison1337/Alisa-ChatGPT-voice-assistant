"""Convert the Station 2 mute listing through the real hook; never touch debugfs."""
import ctypes
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
if sys.platform == 'linux':import fcntl

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

    def test_unknown_or_unreadable_state_never_enables_the_microphone(self):
        cases = [None, '', 'gpiochip0: no mute label\n',
                 ' gpio-1 ( |MUTE_SWITCH ) in  unknown\n',
                 ' gpio-1 ( |MUTE_STUFF ) in  lo\n',
                 ' gpio-1 ( |MUTE_SWITCH ) in  hi lo\n',
                 'x' * 600 + '\n' + STATION2,
                 'x' * 140000 + '\n' + STATION2,
                 'bad\0data\n' + STATION2]
        with tempfile.TemporaryDirectory() as folder:
            lib = self.build(folder)
            fixture = Path(folder) / 'gpio-fixture'
            with patch.dict(os.environ, {'ALICE_MUTE_GPIO_PATH': str(fixture)}):
                os.environ.pop('ALICE_MUTE_GPIO_FILE', None)
                for text in cases:
                    with self.subTest(text=None if text is None else text[:60]):
                        if text is None:fixture.unlink(missing_ok=True)
                        else:fixture.write_text(text)
                        served = self.read_back(lib.open(str(fixture).encode(), os.O_RDONLY, 0))
                        self.assertIn(' lo', served)
                        self.assertNotIn(' hi', served)

    def test_long_listing_flags_and_live_changes_are_read_on_every_open(self):
        with tempfile.TemporaryDirectory() as folder:
            lib = self.build(folder)
            fixture = Path(folder) / 'gpio-fixture'
            prefix = ' gpio-1 ( |unrelated ) in  hi\n' * 160
            with patch.dict(os.environ, {'ALICE_MUTE_GPIO_PATH': str(fixture)}):
                os.environ.pop('ALICE_MUTE_GPIO_FILE', None)
                for listing, allowed in [(STATION2, True), (STATION2_MUTED, False),
                                         (' gpio-1 ( |MUTE_STATE ) in hi IRQ\n', False),
                                         (' gpio-1 ( |MUTE_SWITCH ) in lo ACTIVE LOW\n', True),
                                         (STATION2.rstrip(), True)]:
                    fixture.write_text(prefix + listing)
                    served = self.read_back(lib.open(str(fixture).encode(), os.O_RDONLY, 0))
                    self.assertEqual(' hi' in served, allowed)

    def test_readers_have_independent_snapshots_and_read_only_descriptors(self):
        with tempfile.TemporaryDirectory() as folder:
            lib = self.build(folder)
            fixture = self.listing(folder, STATION2)
            with patch.dict(os.environ, {'ALICE_MUTE_GPIO_PATH': str(fixture)}):
                os.environ.pop('ALICE_MUTE_GPIO_FILE', None)
                first = lib.open(str(fixture).encode(), os.O_RDONLY, 0)
                self.assertEqual(fcntl.fcntl(first, fcntl.F_GETFL) & os.O_ACCMODE, os.O_RDONLY)
                self.assertTrue(fcntl.fcntl(first, fcntl.F_GETFD) & fcntl.FD_CLOEXEC)
                fixture.write_text(STATION2_MUTED)
                second = lib.open64(str(fixture).encode(), os.O_RDONLY, 0)
                self.assertIn(' lo', self.read_back(second))
                self.assertIn(' hi', self.read_back(first))
                def read_one(_):
                    return self.read_back(lib.open(str(fixture).encode(), os.O_RDONLY, 0))
                with ThreadPoolExecutor(max_workers=8) as pool:
                    results = list(pool.map(read_one, range(80)))
                self.assertTrue(all(' lo' in served for served in results))


if __name__ == '__main__':
    unittest.main()
