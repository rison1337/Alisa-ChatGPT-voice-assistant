import hashlib,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from alisa_cli import storage,uninstall,state,runtime

class Space(unittest.TestCase):
 def test_update_needs_temporary_full_copy_even_when_final_size_is_same(self):
  value=storage.calculate([('/app',bytes(8192),False)],{'/app':(8192,'')},12288,reserve=8192)
  self.assertEqual(value['additional_after_bytes'],0);self.assertEqual(value['required_free_bytes'],16384)
  self.assertFalse(value['enough'])
 def test_matching_vpn_binary_is_not_uploaded_or_counted_twice(self):
  data=b'binary'*2000;digest=hashlib.sha256(data).hexdigest()
  value=storage.calculate([('/vpn',data,True)],{'/vpn':(len(data),digest)},8192,reserve=4096)
  self.assertEqual(value['transfer_bytes'],0);self.assertEqual(value['required_free_bytes'],4096);self.assertTrue(value['enough'])
 def test_fresh_package_peak_includes_previous_new_files(self):
  value=storage.calculate([('/a',bytes(8192),False),('/b',bytes(16384),False)],{},32768,reserve=4096)
  self.assertEqual(value['peak_additional_bytes'],24576);self.assertEqual(value['free_after_bytes'],8192)
 def test_insufficient_space_is_rejected(self):
  with self.assertRaisesRegex(RuntimeError,'Недостаточно места'):storage.require({'enough':False})

class Removal(unittest.TestCase):
 def fixture(self):
  app=runtime.NATIVE+'/bin/alisa-standalone';profile=runtime.NATIVE+'/run.sh';account=runtime.NATIVE+'/state/web-session.json'
  files={app:b'program',profile:b'profile',account:b'private-login','/data/opt/etc/dropbear/authorized_keys':b'owner-key'}
  value={'paths':[app,profile],'hashes':{p:hashlib.sha256(files[p]).hexdigest() for p in [app,profile]},'reclaim_bytes':14}
  return files,value,account
 def test_stop_precedes_deletion_and_private_state_survives(self):
  self.perform(False)
 def test_partial_deletion_rolls_back_program_files(self):
  self.perform(True)
 def perform(self,fail):
  files,value,account=self.fixture();original=dict(files);events=[];failed=[False]
  class Station:
   def run(self,command,**kwargs):
    if command.startswith('cat '):return subprocess.CompletedProcess([],0,stdout=files[command[4:]],stderr=b'')
    if command.endswith('/vpn.sh status'):return subprocess.CompletedProcess([],1,stdout=b'',stderr=b'')
    if ' && rm -f ' in command:
     path=command.split(' && rm -f ',1)[1];events.append('delete')
     if fail and path.endswith('run.sh') and not failed[0]:failed[0]=True;raise RuntimeError('simulated interruption')
     files.pop(path,None)
    return subprocess.CompletedProcess([],0,stdout=b'',stderr=b'')
   def put(self,path,data,mode=0o600):files[path]=data
  config=state.defaults('native');config.update(host='192.0.2.2',installed=True)
  with tempfile.TemporaryDirectory() as folder,patch.object(uninstall,'HOME',Path(folder)),patch.object(uninstall,'plan',return_value=value),patch.object(uninstall,'Station',return_value=Station()),patch.object(uninstall,'save'),patch.object(uninstall.runtime,'status',return_value={'agent_connected':False}),patch.object(uninstall.runtime,'stop',side_effect=lambda c:events.append('stop')),patch.object(uninstall.autostart,'set_enabled'),patch.object(uninstall.autostart,'station_flag'),patch.object(uninstall.autostart,'remove_owned_boot'):
   if fail:
    with self.assertRaisesRegex(RuntimeError,'simulated'):uninstall.remove(config,value)
    for p in value['paths']:self.assertEqual(files[p],original[p])
   else:
    result=uninstall.remove(config,value);self.assertTrue(result['removed']);self.assertFalse(config['installed'])
    for p in value['paths']:self.assertNotIn(p,files)
   self.assertEqual(events[0],'stop');self.assertEqual(files[account],original[account])
   self.assertEqual(files['/data/opt/etc/dropbear/authorized_keys'],b'owner-key')
 def test_uninstall_plan_does_not_include_account_directory_or_ssh_keys(self):
  c=state.defaults('native');c['host']='192.0.2.2';seen=[]
  def sizes(s,paths):seen.extend(paths);return {}
  with patch('alisa_cli.install.native_paths',return_value=[runtime.NATIVE+'/run.sh']),patch.object(uninstall.storage,'remote_sizes',side_effect=sizes):uninstall.plan(c)
  self.assertTrue(seen);self.assertTrue(all('/state/' not in p and '/dropbear/' not in p and '/.ssh/' not in p for p in seen))
  self.assertIn(runtime.NATIVE+'/led-scope',seen)
