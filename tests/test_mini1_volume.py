import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('cc'),
                     'requires a Linux C compiler and linker')
class Mini1Volume(unittest.TestCase):
    def test_muted_quiet_and_missing_levels_never_bypass_scaling(self):
        native = Path(__file__).resolve().parents[1]/'engines/native'
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder)/'test-volume'
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            str(native/'station/mini1_volume.c'), str(native/'tests/mini1_volume.c'),
                            '-Wl,--wrap=dlopen,--wrap=dlsym,--wrap=dlclose,--wrap=malloc',
                            '-ldl', '-o', str(binary)], check=True)
            for index, (contents, expected) in enumerate(((None, 0), ('0\n', 0), ('1250\n', 1500),
                                                          ('10000invalid', 0))):
                with self.subTest(contents=contents):
                    level = Path(folder)/f'level-{index}'
                    if contents is not None:
                        level.write_text(contents)
                    subprocess.run([str(binary), str(expected)],
                                   env={**os.environ, 'ALISA_VOLUME_FILE': str(level)}, check=True)
