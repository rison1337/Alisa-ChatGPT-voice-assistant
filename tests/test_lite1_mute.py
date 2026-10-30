"""Execute the GPIO shim with a fake next libc; never access real debugfs."""
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


@unittest.skipUnless(sys.platform == 'linux' and shutil.which('clang'), 'host C compiler')
class Lite1MuteRedirect(unittest.TestCase):
    def test_opt_in_redirects_only_exact_read_only_opens_and_preserves_other_calls(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            harness = base / 'harness.c'
            harness.write_text('''#define dlsym fake_dlsym
#include "SOURCE_FILE"
#undef dlsym
static char captured[4096];
static unsigned int captured_mode;
static void record(const char *path) {
    int i=0; while(path && path[i] && i<4095) { captured[i]=path[i]; ++i; } captured[i]=0;
}
static int fake_open(const char *p, int f, unsigned int m) { (void)f; record(p); captured_mode=m; return 42; }
static int fake_openat(int d,const char *p,int f,unsigned int m) { (void)d; return fake_open(p,f,m); }
static void *fake_fopen(const char *p,const char *m) { (void)m; record(p); return (void *)1; }
void *fake_dlsym(void *h,const char *n) {
    (void)h;
    if(!strcmp(n,"openat")) return fake_openat;
    if(!strcmp(n,"fopen") || !strcmp(n,"fopen64")) return fake_fopen;
    return fake_open;
}
const char *last_path(void) { return captured; }
unsigned int last_mode(void) { return captured_mode; }
'''.replace('SOURCE_FILE', str(ROOT / 'engines/native/station/mute_gpio.c')))
            library = base / 'shim.so'
            subprocess.run(['clang', '-shared', '-fPIC', str(harness), '-o', str(library)], check=True, capture_output=True)
            lib = ctypes.CDLL(str(library))
            lib.last_path.restype = ctypes.c_char_p
            lib.last_mode.restype = ctypes.c_uint
            gpio = b'/sys/kernel/debug/gpio'
            replacement = b'/tmp/synthetic-mute-gpio'
            with patch.dict(os.environ, {'ALICE_MUTE_GPIO_FILE': replacement.decode()}):
                for name in ('open', 'open64'):
                    function = getattr(lib, name)
                    function.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_uint]
                    self.assertEqual(function(gpio, os.O_RDONLY, 0), 42)
                    self.assertEqual(lib.last_path(), replacement)
                    for flag in (os.O_WRONLY, os.O_RDWR, os.O_CREAT, os.O_TRUNC, os.O_APPEND, getattr(os, 'O_TMPFILE', 0o20000000)):
                        function(gpio, flag, 0o640)
                        self.assertEqual(lib.last_path(), gpio)
                    function(b'/tmp/ordinary', os.O_RDONLY, 0)
                    self.assertEqual(lib.last_path(), b'/tmp/ordinary')
                    function(gpio, os.O_CREAT, 0o640)
                    self.assertEqual(lib.last_mode(), 0o640)
                for name in ('fopen', 'fopen64'):
                    function = getattr(lib, name)
                    function.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
                    function(gpio, b'rb');self.assertEqual(lib.last_path(), replacement)
                    function(gpio, b'r+');self.assertEqual(lib.last_path(), gpio)
                lib.openat.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint]
                lib.openat(-100, gpio, os.O_RDONLY, 0)
                self.assertEqual(lib.last_path(), replacement)
            with patch.dict(os.environ, {'ALICE_MUTE_GPIO_FILE': 'relative-path'}):
                lib.open(gpio, os.O_RDONLY, 0)
                self.assertEqual(lib.last_path(), gpio)


if __name__ == '__main__':unittest.main()
