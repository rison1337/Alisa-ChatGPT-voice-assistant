"""The station diagnostic isolates auth refresh and never touches real chats."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'engines/native/tools/probe-isolated.sh'


@unittest.skipUnless(sys.platform=='linux' and shutil.which('sh'),'POSIX station shell')
class IsolatedVoiceProbe(unittest.TestCase):
    def run_probe(self,phase='idle',failure=False):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        base=Path(folder.name)/'station with spaces';(base/'bin').mkdir(parents=True);(base/'state').mkdir()
        (base/'state/web-session.json').write_text('{"test_only_secret":"never print this"}')
        (base/'state/settings.json').write_text('{"private_chats":"stay here"}')
        (base/'runtime.env').write_text('export ALISA_ICE_BIND_ADDRESS=192.0.2.42\n')
        before={p.relative_to(base).as_posix():p.read_bytes() for p in base.rglob('*') if p.is_file()}
        client=base/'bin/alisa-standalone'
        client.write_text('#!'+sys.executable+'''\nimport json,os,sys
from pathlib import Path
if sys.argv[1]=='status':
 print(json.dumps({'phase':os.environ['TEST_PHASE'],'settings':{'voice':'juniper'}},indent=2));raise SystemExit(0)
assert sys.argv[1]=='probe'
state=Path(sys.argv[2]);request=json.loads(Path(sys.argv[3]).read_text())
assert state.parent!=Path(os.environ['TEST_BASE'])
assert request['voice']=='juniper'
assert (state/'web-session.json').stat().st_mode&0o777==0o600
assert state.parent.stat().st_mode&0o777==0o700
assert os.environ['ALISA_ICE_BIND_ADDRESS']=='192.0.2.42'
assert 'test_only_secret' in (state/'web-session.json').read_text()
(state/'web-session.json').write_text('synthetic refresh changes only the private copy')
(state/'probe-conversation.json').write_text('synthetic identifiers')
Path(os.environ['TEST_RECORD']).write_text(str(state.parent))
print(json.dumps({'success':not bool(os.environ.get('TEST_FAIL')),'microphone_frames':0}))
raise SystemExit(2 if os.environ.get('TEST_FAIL') else 0)
''');client.chmod(0o700)
        record=Path(folder.name)/'record'
        env={**os.environ,'TEST_PHASE':phase,'TEST_BASE':str(base),'TEST_RECORD':str(record),'TEST_FAIL':'1' if failure else ''}
        result=subprocess.run(['sh',str(SCRIPT),str(base)],env=env,capture_output=True,timeout=8)
        self.assertNotIn(b'never print this',result.stdout+result.stderr)
        for name,data in before.items():self.assertEqual((base/name).read_bytes(),data)
        if record.exists():self.assertFalse(Path(record.read_text()).exists(),'private copied credentials not cleaned up')
        return result,record.exists()

    def test_idle_probe_preserves_installed_auth_settings_and_route_bind(self):
        result,ran=self.run_probe();self.assertTrue(ran);self.assertEqual(result.returncode,0)
        self.assertEqual(json.loads(result.stdout),{'success':True,'microphone_frames':0})

    def test_early_failure_is_returned_and_private_credentials_are_removed(self):
        result,ran=self.run_probe(failure=True);self.assertTrue(ran);self.assertEqual(result.returncode,2)
        self.assertFalse(json.loads(result.stdout)['success'])

    def test_active_or_connecting_or_stopped_client_does_not_start_a_probe(self):
        for phase in ('active','connecting','stopped','stopping'):
            with self.subTest(phase=phase):
                result,ran=self.run_probe(phase=phase);self.assertFalse(ran);self.assertEqual(result.returncode,2)
                self.assertEqual(result.stdout,b'')
