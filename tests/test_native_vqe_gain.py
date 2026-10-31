"""Run the native VQE hook against float samples, not only generated settings."""
import array
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform=='linux' and shutil.which('clang'),'Linux host C compiler')
class NativeCaptureGain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='alisa-native-gain-')
        cls.folder=Path(cls.temp.name)
        (cls.folder/'fixture.c').write_text('''
#include <stddef.h>
#include <errno.h>
static float samples[]={.03125f,-.03125f,.125f,-.125f,0,
                       __builtin_nanf(""),__builtin_inff(),-__builtin_inff()};
void *YandexVqeC_GetOutputChannelData(void *engine,unsigned int kind,
    unsigned int index,const float **data,size_t *frames) {
    *data=samples;*frames=8;errno=37;return 0;
}
''')
        (cls.folder/'driver.c').write_text('''
#include <stddef.h>
#include <errno.h>
extern void *YandexVqeC_GetOutputChannelData(void *,unsigned int,unsigned int,const float **,size_t *);
int main(void) {
    const float *data=0;size_t frames=0;
    void *result=YandexVqeC_GetOutputChannelData(0,6,0,&data,&frames);
    return result || frames!=8 || !data || data[0]!=.03125f || data[1]!=-.03125f || errno!=37;
}
''')
        for source,target in [(cls.folder/'fixture.c',cls.folder/'libfixture.so'),
                              (ROOT/'engines/native/station/vqe_tap.c',cls.folder/'tap.so')]:
            subprocess.run(['clang','-shared','-fPIC','-O2',str(source),'-o',str(target)],check=True,capture_output=True)
        subprocess.run(['clang',str(cls.folder/'driver.c'),'-L'+str(cls.folder),'-lfixture',
                        '-Wl,-rpath,'+str(cls.folder),'-o',str(cls.folder/'driver')],check=True,capture_output=True)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def capture(self,gain=None):
        path=self.folder/'microphone.sock'
        path.unlink(missing_ok=True)
        with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as receiver:
            receiver.bind(str(path));receiver.settimeout(1)
            env={**os.environ,'LD_PRELOAD':str(self.folder/'tap.so'),'ALICE_CAPTURE_SOCKET':str(path)}
            env.pop('ALICE_CAPTURE_GAIN',None)
            if gain is not None:env['ALICE_CAPTURE_GAIN']=gain
            result=subprocess.run([str(self.folder/'driver')],env=env,capture_output=True,timeout=3)
            self.assertEqual(result.returncode,0,result.stderr.decode())
            samples=array.array('h');samples.frombytes(receiver.recv(4096))
            return list(samples)

    def test_default_preserves_levels_and_original_float_buffer(self):
        self.assertEqual(self.capture(),[1024,-1024,4096,-4096,0,0,32767,-32768])

    def test_station2_gain_amplifies_quiet_speech_and_saturates_peaks(self):
        self.assertEqual(self.capture('16'),[16384,-16384,32767,-32768,0,0,32767,-32768])

    def test_invalid_gain_falls_back_without_silence_or_full_scale_noise(self):
        wanted=[1024,-1024,4096,-4096,0,0,32767,-32768]
        for gain in ('nan','inf','-inf','0','-1','1e100','1e-100','65','garbage','16oops',''):
            with self.subTest(gain=gain):self.assertEqual(self.capture(gain),wanted)
