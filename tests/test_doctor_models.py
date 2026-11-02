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
    patch('alisa_cli.windows.missing',return_value=[]),patch.object(doctor.subprocess,'run',return_value=subprocess.CompletedProcess([],0)):
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

 def test_mini1_native_with_no_aplay_is_recognized_but_unverified(self):
  pair=('255d97e7199f05b5ef09cbd28dfedc35b2050e75db5b40d1546a9cb999602eda',
        '23eb591fe3397a2e3e9d58ff2bc846be541a1a2f0f8cfd957abbc2d674ea1da1')
  response=output('amlogic, axg',*pair,tools=('sha256sum',))
  c=state.defaults('native');c['host']='192.0.2.10'
  def reply(command):
   return '      "deviceType" : "yandexmini",' if 'deviceType' in command else response
  with patch.object(doctor.Station,'text',side_effect=reply),patch('alisa_cli.windows.missing',return_value=[]),patch('alisa_cli.doctor.shutil.which',return_value='/usr/bin/tool'):
   report=doctor.inspect(c)
  self.assertEqual(report['model'],'mini1')
  self.assertEqual(report['firmware_status'],'unverified')
  self.assertEqual(report['station_missing'],[])
  self.assertTrue(report['ok'])

 def test_generic_amlogic_without_mini_identity_is_rejected(self):
  from alisa_cli.firmware import classify
  pair=('a'*64,'b'*64)
  self.assertIsNone(classify('amlogic, axg',pair)['model'])
  self.assertIsNone(classify('amlogic, axg',pair,'otherdevice')['model'])

 def test_mini1_bridge_is_rejected(self):
  from alisa_cli.firmware import classify
  pair=('a'*64,'b'*64)
  report=classify('amlogic, axg',pair,'yandexmini')
  self.assertEqual(report['model'],'mini1')
  config=state.defaults('bridge');config['host']='192.0.2.10'
  with self.assertRaises(RuntimeError):
   doctor.require(config,{'root':True,'plum':True,'model':'mini1','mode_supported':False,
    'firmware_abi_supported':False,'firmware_files_present':True,'architecture':'aarch64',
    'free_data_kib':55000,'host_missing':[],'station_missing':[],'ok':False})

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
 def test_yandexmidi_bridge_is_rejected_before_installation(self):
  r=self.check('bridge',output('yandex,station-midi',*PINNED,tools=('aplay','base64','sha256sum','socat')))
  self.assertFalse(r['mode_supported']);self.assertFalse(r['ok'])
  with self.assertRaisesRegex(RuntimeError,'мост доступен для Лайт 2'):doctor.require(state.defaults('bridge'),r)
 def test_station_midi_synonyms_are_recognized(self):
  for compatible in ('yandexmidi','station-midi'):
   r=self.check('native',output(compatible,*LITE1))
   self.assertEqual(r['model'],'yandexmidi');self.assertTrue(r['ok'])

if __name__=='__main__':unittest.main()
