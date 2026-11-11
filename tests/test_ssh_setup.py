import contextlib,hashlib,json,os,subprocess,tempfile,unittest
import copy
from pathlib import Path
from unittest.mock import patch
from alisa_cli import state,ssh_setup,ui
from alisa_cli.ssh import Station
from alisa_cli.errors import PublicError

class Setup(unittest.TestCase):
 def check_install_failure(self,result,expected_code,use_password=True):
  with tempfile.TemporaryDirectory() as folder:
   home=Path(folder);(home/'ssh').mkdir()
   config=state.defaults('native');config['host']='192.0.2.10';before=copy.deepcopy(config)
   suffix=hashlib.sha256(b'root@192.0.2.10:2222').hexdigest()[:16]
   key=home/'ssh'/('id_ed25519_'+suffix);key.touch();key.with_suffix('.pub').write_text('ssh-ed25519 AAAAtest')
   secret='test-only-password-never-publish'
   kwargs={'side_effect':result} if isinstance(result,Exception) else {'return_value':result}
   with patch.object(ssh_setup,'HOME',home),patch.object(ssh_setup,'password_channel',return_value=contextlib.nullcontext({})), \
        patch.object(ssh_setup.subprocess,'run',**kwargs) as run,patch.object(ssh_setup,'save') as save:
    with self.assertRaises(PublicError) as caught:ssh_setup.install_key(config,secret if use_password else None)
    self.assertEqual(caught.exception.code,expected_code)
    self.assertNotIn(secret,str(caught.exception));self.assertNotIn('private-remote-detail',str(caught.exception))
    self.assertNotIn(secret,str(run.call_args));save.assert_not_called()
   self.assertEqual(config,before)
 def test_remote_key_write_error_is_not_reported_as_wrong_password(self):
  self.check_install_failure(subprocess.CompletedProcess([],1,stdout=b'',stderr=b'Read-only file system private-remote-detail'),'ssh_key_install')
 def test_existing_key_login_reports_remote_write_failure_separately(self):
  self.check_install_failure(subprocess.CompletedProcess([],1,stdout=b'',stderr=b'Read-only file system private-remote-detail'),'ssh_key_install',False)
 def test_password_rejection_keeps_authentication_diagnostic(self):
  self.check_install_failure(subprocess.CompletedProcess([],255,stdout=b'',stderr=b'Permission denied private-remote-detail'),'ssh_auth')
 def test_key_install_timeout_has_a_safe_network_diagnostic(self):
  self.check_install_failure(subprocess.TimeoutExpired(['ssh'],60),'ssh_timeout')
 def test_reusing_saved_connection_does_not_ask_password_again(self):
  with tempfile.TemporaryDirectory() as folder:
   identity=Path(folder)/'key';identity.write_text('test-key')
   c=state.defaults('native');c.update(host='192.0.2.10',ssh_explicit=True,identity=str(identity))
   from unittest.mock import Mock
   form=Mock();form.select.return_value='reuse'
   with patch('shutil.which',return_value='/usr/bin/ssh'),patch.object(Station,'text',return_value='0\n'):
    self.assertTrue(ssh_setup.form(c,form,allow_saved=True)['ssh'])
   form.secret.assert_not_called();form.text.assert_not_called()
 def test_form_always_asks_connection_fields_and_does_not_reuse_another_column_state(self):
  c=state.defaults('native');c.update(host='192.0.2.10',installed=True,autostart=True,identity='/tmp/old_key')
  c['vpn'].update(enabled=True,required=True,autostart=True)
  class Form:
   def __init__(self):self.questions=[]
   def text(self,title,default='',validate=None):
    self.questions.append(title);return {'IP-адрес колонки':'192.0.2.11','Порт SSH':'2222','Пользователь SSH':'root'}[title]
   def secret(self,title,optional=False):self.questions.append(title);return 'test-only-password'
   def note(self,*args):pass
  form=Form();captured={}
  def install(candidate,password):captured.update(copy.deepcopy(candidate));self.assertEqual(password,'test-only-password');return {'ssh':True}
  with patch('alisa_cli.ssh_setup.trust_host'),patch('alisa_cli.ssh_setup.install_key',side_effect=install),patch('shutil.which',return_value='/usr/bin/ssh'):
   ssh_setup.form(c,form)
  self.assertEqual(len(form.questions),4);self.assertEqual(c['host'],'192.0.2.11');self.assertFalse(c['installed'])
  self.assertFalse(c['vpn']['enabled']);self.assertIsNone(c['identity']);self.assertNotIn('password',captured)
 def test_fresh_install_has_no_personal_address(self):
  c=state.defaults('native');self.assertEqual(c['host'],'');self.assertEqual(c['user'],'root');self.assertEqual(c['port'],2222)
  with self.assertRaises(ValueError):Station(c).args()
 def test_address_and_port_validation(self):
  for address in ['192.0.2.10','2001:db8::10']:self.assertIs(ssh_setup.ip_valid(address),True)
  for address in ['alice','-oProxyCommand=id','192.168.999.1']:self.assertIsNot(ssh_setup.ip_valid(address),True)
  for port in ['0','65536','22x','-1']:self.assertIsNot(ssh_setup.port_valid(port),True)
 def test_password_never_enters_config(self):
  c=state.defaults('native');c['host']='192.0.2.10';c['password']='do-not-save'
  with self.assertRaises(ValueError):state.validate(c)
 def test_askpass_uses_private_ipc_and_removes_it(self):
  secret='example password !$ with spaces'
  with ssh_setup.password_channel(secret) as env:
   self.assertNotIn(secret,json.dumps(env));self.assertEqual(Path(env['ALISA_ASKPASS_SOCKET']).stat().st_mode&0o777,0o600)
   helper=Path(env['SSH_ASKPASS']);self.assertNotIn(secret,helper.read_text())
   result=subprocess.run([str(helper),'password:'],env=env,capture_output=True,check=True)
   self.assertEqual(result.stdout,secret.encode()+b'\n')
  self.assertFalse(helper.exists())
 def test_existing_host_key_cannot_be_silently_replaced(self):
  scan=subprocess.CompletedProcess([],0,stdout=b'[192.0.2.10]:2222 ssh-ed25519 AAAAnew\n')
  known=subprocess.CompletedProcess([],0,stdout=b'[192.0.2.10]:2222 ssh-ed25519 AAAAold\n')
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'known_hosts';path.write_text('original\n')
   c=state.defaults('native');c.update(host='192.0.2.10',known_hosts=str(path))
   with patch('alisa_cli.ssh_setup.subprocess.run',side_effect=lambda args,**kw:scan if args[0]=='ssh-keyscan' else known):
    with self.assertRaisesRegex(RuntimeError,'изменился'):ssh_setup.trust_host(c,lambda *args:True)
   self.assertEqual(path.read_text(),'original\n')
 def test_unverified_new_host_leaves_no_pin_file(self):
  scan=subprocess.CompletedProcess([],0,stdout=b'[192.0.2.10]:2222 ssh-ed25519 AAAAnew\n')
  other=subprocess.CompletedProcess([],0,stdout=b'')
  with tempfile.TemporaryDirectory() as folder,patch('alisa_cli.ssh_setup.HOME',Path(folder)):
   c=state.defaults('native');c['host']='192.0.2.10'
   with patch('alisa_cli.ssh_setup.subprocess.run',side_effect=lambda args,**kw:scan if args[0]=='ssh-keyscan' else other):
    with self.assertRaisesRegex(RuntimeError,'не подтверждён'):ssh_setup.trust_host(c,lambda *args:False)
   self.assertFalse((Path(folder)/'ssh/known_hosts').exists())
 def test_explicit_connection_ignores_personal_ssh_aliases(self):
  c=state.defaults('native');c.update(host='192.0.2.10',ssh_explicit=True,known_hosts='/tmp/alisa/known_hosts')
  args=Station(c).args();self.assertIn(os.devnull,args);self.assertIn('2222',args);self.assertIn('root',args)
