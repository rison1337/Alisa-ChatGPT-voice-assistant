import subprocess,unittest
from unittest.mock import patch
from alisa_cli import doctor,state

LITE1=('2dad0ffc61ba73d15ea813b754d7a56866d0477c321dcad9138b8d06e14ddbf1',
       'df67c7ad9fb9f2f9c473ba2aaed7663a0e898a2c6622993e65d3b8834a0b3646')

PINNED=('9ad1476ee37df3e401a8426df7710f8f3d2d0695e01d0b949d99a5a8b562a4d2',
       'e3f80b79cd5b7f9422b790b3fe81cc41883e1c07914e6b474f4fd9313e3b84af')

def output(compatible,maind,vqe,tools=('aplay','base64','sha256sum')):
 return '\n'.join(['0','armv7l',compatible+'\x00amlogic, axg\x00',
  maind+'  /system/vendor/quasar/maind',vqe+'  /system/vendor/quasar/libs/libyandex_evil.so',
  'Filesystem 1024-blocks Used Available Capacity Mounted on','/dev/ubi1_0 89568 19408 65548 23% /bkupgrade',
  *['/usr/bin/'+t for t in tools]])+'\n'

class Models(unittest.TestCase):
 def check(self,mode,text):
  c=state.defaults(mode);c['host']='192.0.2.10'
  with patch.object(doctor.Station,'text',return_value=text),patch.object(doctor.shutil,'which',return_value='/usr/bin/x'),\
    patch.object(doctor.subprocess,'run',return_value=subprocess.CompletedProcess([],0)):
   return doctor.inspect(c)
 def test_lite1_verified_build_passes_native_without_socat(self):
  r=self.check('native',output('yandex,station-micro',*LITE1))
  self.assertEqual(r['model'],'lite1');self.assertTrue(r['plum']);self.assertTrue(r['firmware_abi_supported'])
  self.assertEqual(r['station_missing'],[]);self.assertTrue(r['ok'])
 def test_lite1_with_other_hashes_is_unverified_but_installable(self):
  r=self.check('native',output('yandex,station-micro',doctor.MAIND,doctor.VQE))
  self.assertFalse(r['firmware_abi_supported']);self.assertTrue(r['ok']);self.assertTrue(r['warnings'])
 def test_lite2_keeps_its_pinned_build(self):
  r=self.check('native',output('yandex,prune',doctor.MAIND,doctor.VQE))
  self.assertEqual(r['model'],'lite2');self.assertTrue(r['firmware_abi_supported'])
 def test_unknown_model_is_rejected(self):
  r=self.check('native',output('yandex,station-max',*LITE1))
  self.assertIsNone(r['model']);self.assertFalse(r['firmware_abi_supported']);self.assertFalse(r['ok'])
 def test_bridge_still_requires_socat_on_station(self):
  r=self.check('bridge',output('yandex,station-micro',*LITE1))
  self.assertEqual(r['station_missing'],['socat'])

 def test_windows_chromium_outside_path_counts_as_installed(self):
  import os,tempfile
  from pathlib import Path
  with tempfile.TemporaryDirectory() as folder:
   exe=Path(folder)/'Chromium/Application/chrome.exe';exe.parent.mkdir(parents=True);exe.write_bytes(b'')
   isolated={'LOCALAPPDATA':folder,'PROGRAMFILES':str(Path(folder)/'program-files'),'PROGRAMFILES(X86)':str(Path(folder)/'program-files-x86')}
   with patch.object(doctor.sys,'platform','win32'),patch.dict(os.environ,isolated),patch('alisa_cli.windows.shutil.which',return_value=None):
    self.assertEqual(doctor.windows_chromium(),exe)
   with patch.object(doctor.sys,'platform','linux'):self.assertIsNone(doctor.windows_chromium())

 def test_lite1_bridge_is_rejected_even_if_socat_is_installed(self):
  r=self.check('bridge',output('yandex,station-micro',*LITE1,tools=('aplay','base64','sha256sum','socat')))
  self.assertFalse(r['mode_supported']);self.assertFalse(r['ok'])
  with self.assertRaisesRegex(RuntimeError,'только в нативном режиме'):doctor.require(state.defaults('bridge'),r)

 def test_swapping_the_hash_paths_does_not_pass_the_build_check(self):
  r=self.check('native',output('yandex,prune',doctor.VQE,doctor.MAIND))
  self.assertFalse(r['firmware_abi_supported'])
 def test_yandexmidi_is_recognized_and_installable_with_warning(self):
  r=self.check('native',output('yandex,station-midi',doctor.MAIND,doctor.VQE))
  self.assertEqual(r['model'],'yandexmidi');self.assertTrue(r['plum'])
  self.assertFalse(r['firmware_abi_supported']);self.assertTrue(r['ok']);self.assertTrue(r['warnings'])
 def test_yandexmidi_pinned_build_is_verified(self):
  r=self.check('native',output('yandex,station-midi',*PINNED))
  self.assertEqual(r['model'],'yandexmidi');self.assertTrue(r['firmware_abi_supported'])
  self.assertEqual(r['warnings'],[]);self.assertTrue(r['ok'])
 def test_station_midi_synonyms_are_recognized(self):
  for compatible in ('yandexmidi','station-midi'):
   r=self.check('native',output(compatible,*LITE1))
   self.assertEqual(r['model'],'yandexmidi');self.assertTrue(r['ok'])

if __name__=='__main__':unittest.main()
