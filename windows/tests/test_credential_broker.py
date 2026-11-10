"""Real Windows named pipes and SSH; no passwords in persisted configuration."""
import contextlib,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from alisa_cli import winssh,windows_credentials

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'windows'))
from credential_broker import Broker,session

@unittest.skipUnless(sys.platform=='win32','actual Windows named pipes')
class CredentialBroker(unittest.TestCase):
    def setUp(self):
        self.broker=Broker();self.addCleanup(self.broker.close)
        self.environment=patch.dict(os.environ,{windows_credentials.ENVIRONMENT:self.broker.specification()})
        self.environment.start();self.addCleanup(self.environment.stop)

    def test_credentials_are_scoped_to_endpoint_user_and_profile(self):
        key=('speaker',2222,'root','profile-one/known_hosts')
        self.assertTrue(windows_credentials.request(key,'put','synthetic-only-password'))
        for other in [('other',2222,'root',key[3]),('speaker',22,'root',key[3]),
                      ('speaker',2222,'other',key[3]),('speaker',2222,'root','profile-two/known_hosts')]:
            self.assertIsNone(windows_credentials.request(other,'get')['password'])
        self.assertTrue(windows_credentials.request(key,'clear'))
        self.assertIsNone(windows_credentials.request(key,'get')['password'])

    def test_delegated_supervisor_reuses_parent_memory(self):
        child,specification=session(self.broker.specification())
        self.assertIsNone(child);self.assertEqual(specification,self.broker.specification())

    def test_fresh_process_uses_remembered_password_for_real_pinned_ssh(self):
        from test_native_host import Peer
        from alisa_cli.state import defaults
        peer=Peer();self.addCleanup(peer.close)
        with tempfile.TemporaryDirectory() as folder:
            known=Path(folder)/'known_hosts'
            config=defaults('native');config.update(host='127.0.0.1',port=peer.port,user='root',known_hosts=str(known))
            winssh.trust(config,lambda *args:True)
            with patch.dict(winssh._passwords,{},clear=True):
                winssh.use_password(config,'synthetic-only-password',persist=False)
            code="import json,sys; from alisa_cli import winssh; c=json.loads(sys.argv[1]); print(winssh.run(c,'id -u').stdout.decode().strip())"
            child=subprocess.run([sys.executable,'-B','-X','utf8','-c',code,json.dumps(config)],cwd=ROOT,capture_output=True,timeout=20)
            self.assertEqual(child.returncode,0,child.stdout+child.stderr);self.assertEqual(child.stdout.strip(),b'0')
            self.assertNotIn('synthetic-only-password',json.dumps(config));self.assertNotIn('synthetic-only-password',str(dict(os.environ)))

if __name__=='__main__':unittest.main()
