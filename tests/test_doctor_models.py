import subprocess,unittest
from unittest.mock import patch
from alisa_cli import doctor,state

LITE1=('2dad0ffc61ba73d15ea813b754d7a56866d0477c321dcad9138b8d06e14ddbf1',
       'df67c7ad9fb9f2f9c473ba2aaed7663a0e898a2c6622993e65d3b8834a0b3646')

PINNED=('9ad1476ee37df3e401a8426df7710f8f3d2d0695e01d0b949d99a5a8b562a4d2',
       'e3f80b79cd5b7f9422b790b3fe81cc41883e1c07914e6b474f4fd9313e3b84af')

MINI2=('7ff114e08d46d48394f9d794f89dc941f3b217bdcd3ff1ef46f1cd80fada6ec1',
      'fa7ad1544fe9ddfccedcf15acc031768345b0b8c7333bc1c0b47bc476dd4152f')

def output(compatible,maind,vqe,tools=('aplay','base64','sha256sum')):
 return '\n'.join(['0','armv7l',compatible+'\x00amlogic, axg\x00',
  maind+'  /system/vendor/quasar/maind',vqe+'  /system/vendor/quasar/libs/libyandex_evil.so',
  'Filesystem 1024-blocks Used Available Capacity Mounted on','/dev/ubi1_0 89568 19408 65548 23% /bkupgrade',
  *['/usr/bin/'+t for t in tools]])+'\n'

class Models(unittest.TestCase):
 def test_cucumber_native_is_experimental_and_bridge_is_rejected(self):
  text=output('yandex,cucumber-dvt2\x00yandex,cucumber',*LITE1).replace('armv7l','aarch64')
  native=self.check('native',text)
  self.assertEqual(native['model'],'cucumber');self.assertTrue(native['ok'])
  self.assertFalse(native['firmware_abi_supported']);self.assertTrue(native['warnings'])
  bridge=self.check('bridge',text)
  self.assertFalse(bridge['mode_supported']);self.assertFalse(bridge['ok'])
 def check(self,mode,text,platform='linux'):
  c=state.defaults(mode);c['host']='192.0.2.10'
  with patch.object(doctor.Station,'text',return_value=text),patch.object(doctor.shutil,'which',return_value='/usr/bin/x'),\
    patch.object(doctor.subprocess,'run',return_value=subprocess.CompletedProcess([],0)),patch.object(doctor.sys,'platform',platform):
   return doctor.inspect(c)
 def test_lite1_verified_build_passes_native_without_socat(self):
  r=self.check('native',output('yandex,station-micro',*LITE1))
  self.assertEqual(r['model'],'lite1');self.assertTrue(r['plum']);self.assertTrue(r['firmware_abi_supported'])
  self.assertEqual(r['station_missing'],[]);self.assertTrue(r['ok'])
 def test_native_doctor_rejects_missing_session_import_dependencies(self):
  import tempfile
  from pathlib import Path
  from alisa_cli import dependencies
  c=state.defaults('native');c['host']='192.0.2.10'
  with tempfile.TemporaryDirectory() as folder,patch.object(dependencies,'ROOT',Path(folder)),patch.object(doctor.sys,'platform','linux'),patch.object(doctor.Station,'text',return_value=output('yandex,station-mini2',*MINI2)),patch.object(doctor.shutil,'which',side_effect=lambda tool:None if tool in ('Xvfb','xauth') else '/usr/bin/'+tool):
   report=doctor.inspect(c)
  self.assertEqual(report['host_missing'],['Xvfb','xauth']);self.assertFalse(report['ok'])
  from alisa_cli.errors import PublicError
  with self.assertRaisesRegex(PublicError,'Xvfb, xauth') as caught:doctor.require(c,report)
  self.assertEqual(caught.exception.code,'login_display_missing')
 def test_lite1_with_other_hashes_is_unverified_but_installable(self):
  r=self.check('native',output('yandex,station-micro',doctor.MAIND,doctor.VQE))
  self.assertFalse(r['firmware_abi_supported']);self.assertTrue(r['ok']);self.assertTrue(r['warnings'])
 def test_lite2_keeps_its_pinned_build(self):
  r=self.check('native',output('yandex,prune',doctor.MAIND,doctor.VQE))
  self.assertEqual(r['model'],'lite2');self.assertTrue(r['firmware_abi_supported'])
 def test_lite2_plum_and_prune_board_revisions_on_linux_and_windows(self):
  # Root compatible properties from the four Lite 2 DTBs. Every board has a
  # stable vendor-qualified fallback; CPU/SoC and revision alone are not models.
  for board,base in [('plum-evt1','plum'),('plum-evt2','plum'),
                     ('plum-evt2-aw21036','plum'),('prune-evt2','prune')]:
   for platform in ('linux','win32'):
    with self.subTest(board=board,platform=platform),patch('alisa_cli.windows.missing',return_value=[]):
     text=output('yandex,'+board+'\0yandex,'+base,'a'*64,'b'*64)
     text+='ALISA_FIRMWARE_VERSION=0.373.1.16.4836123286.20261106.137\n'
     r=self.check('native',text,platform=platform)
     self.assertEqual(r['model'],'lite2');self.assertTrue(r['mode_supported'])
     self.assertTrue(r['ok']);self.assertEqual(r['firmware_status'],'unverified')
     self.assertEqual(r['firmware_version'],'0.373.1.16.4836123286.20261106.137')
     doctor.require(state.defaults('native'),r,warn=False)
 def test_lite2_plum_bridge_is_supported_with_socat(self):
  r=self.check('bridge',output('yandex,plum-evt2\0yandex,plum',doctor.MAIND,doctor.VQE,
                             tools=('aplay','base64','sha256sum','socat')))
  self.assertEqual(r['model'],'lite2');self.assertTrue(r['ok'])
 def test_unknown_board_is_not_reported_as_native_only(self):
  for marker in ('fake-yandex,plum','yandex,plum-other','amlogic, axg'):
   with self.subTest(marker=marker):
    r=self.check('native',output(marker,doctor.MAIND,doctor.VQE))
    self.assertIsNone(r['model']);self.assertFalse(r['ok'])
    with self.assertRaisesRegex(RuntimeError,'устройство не распознано') as caught:
     doctor.require(state.defaults('native'),r)
    self.assertNotIn('только в нативном режиме',str(caught.exception))
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
   return '{"other":"value","deviceType":"yandexmini"}' if 'quasar.cfg' in command else response
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
 def test_mini2_pinned_build_is_verified(self):
  r=self.check('native',output('yandex,station-mini2',*MINI2))
  self.assertEqual(r['model'],'mini2');self.assertTrue(r['plum']);self.assertTrue(r['firmware_abi_supported'])
  self.assertEqual(r['warnings'],[]);self.assertTrue(r['ok'])
 def test_mini2_with_other_hashes_is_unverified_but_installable(self):
  r=self.check('native',output('yandex,station-mini2',*LITE1))
  self.assertEqual(r['model'],'mini2');self.assertFalse(r['firmware_abi_supported'])
  self.assertTrue(r['ok']);self.assertTrue(r['warnings'])
 def test_mini2_bridge_is_supported_with_socat(self):
  r=self.check('bridge',output('yandex,station-mini2',*MINI2,tools=('aplay','base64','sha256sum','socat')))
  self.assertEqual(r['model'],'mini2');self.assertTrue(r['mode_supported']);self.assertTrue(r['ok'])
 def test_entware_socat_path_counts_as_installed(self):
  r=self.check('bridge',output('yandex,station-mini2',*MINI2)+'/data/opt/entware/bin/socat\n')
  self.assertEqual(r['station_missing'],[]);self.assertTrue(r['ok'])

 def test_windows_accepts_mini2_native_but_requires_a_linux_audio_backend_for_bridge(self):
  text=output('yandex,station-mini2',*MINI2,tools=('aplay','base64','sha256sum','socat'))
  with patch('alisa_cli.windows.missing',return_value=[]):
   self.assertTrue(self.check('native',text,platform='win32')['ok'])
   bridge=self.check('bridge',text,platform='win32')
  self.assertFalse(bridge['ok'])
  self.assertIn('Windows audio backend (следующий этап)',bridge['host_missing'])

if __name__=='__main__':unittest.main()
