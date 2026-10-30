"""Execute the Windows worker with file-backed component failures, no SSH."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from alisa_cli import app_install


@unittest.skipUnless(sys.platform=='win32','real Windows embedded Python worker')
class WindowsWorkerTransaction(unittest.TestCase):
    def package(self,root,version):
        root.mkdir(parents=True)
        shutil.copytree(ROOT/'windows/runtime',root/'windows/runtime')
        for name in ('alisa_cli/app_install.py','alisa_cli/windows.py','windows/install.py','windows/launcher.py','scripts/update_worker.py'):
            target=root/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,target)
        (root/'alisa_cli/__init__.py').write_text('__version__='+repr(version)+'\n',encoding='utf-8')
        script='''import json,os,sys
from pathlib import Path
VERSION=VERSION_VALUE
device=Path(os.environ['ALISA_FIXTURE_DEVICE']);args=sys.argv[1:]
if args==['--version']:print(VERSION);raise SystemExit()
if not args:raise SystemExit()
mode,action=args[:2];state=json.loads((device/'runtime.json').read_bytes())
if action=='status':print(json.dumps(state));raise SystemExit()
if action=='install':
 (device/'components.txt').write_text(VERSION)
 if os.environ.get('ALISA_FIXTURE_FAIL') and (VERSION=='2.0.0' or os.environ['ALISA_FIXTURE_FAIL']=='rollback'):raise SystemExit(1)
elif action in ('start','stop'):
 state['agent_connected']=action=='start';(device/'runtime.json').write_text(json.dumps(state))
'''.replace('VERSION_VALUE',repr(version))
        (root/'alisa.py').write_text(script,encoding='utf-8')
        hashes={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
        (root/'windows-package.json').write_text(json.dumps({'schema':1,'package':'alisa-voice-windows-native','platform':'windows-amd64','version':version,'files':hashes}),encoding='utf-8')
        return root

    def test_real_worker_preserves_components_data_and_running_state_on_success_or_failure(self):
        for failure,running in (('',True),('candidate',True),('rollback',True),('',False)):
            with self.subTest(failure=failure,running=running),tempfile.TemporaryDirectory(prefix='avu-worker-') as folder:
                base=Path(folder);local=base/'l';old=self.package(local/'AlisaVoice/app','1.0.0');candidate=self.package(base/'payload','2.0.0')
                (old/'unknown.txt').write_bytes(b'synthetic owner notes')
                state=base/'state';device=base/'device';state.mkdir();device.mkdir()
                (state/'session.json').write_bytes(b'synthetic account data')
                (device/'runtime.json').write_text(json.dumps({'phase':'idle','agent_connected':running}),encoding='utf-8')
                (device/'components.txt').write_text('1.0.0',encoding='utf-8')
                job=base/'job.json';job.write_text(json.dumps({'schema':1,'current_root':str(old),'payload':str(candidate),'platform':'windows-amd64','version':'2.0.0','parent_pid':0,'modes':[{'mode':'native','running':running}],'state_directory':str(state),'relaunch':bool(failure)}),encoding='utf-8')
                env={**os.environ,'LOCALAPPDATA':str(local),'ALISA_HOME':str(state),'ALISA_FIXTURE_DEVICE':str(device),'ALISA_FIXTURE_FAIL':failure,'PYTHONDONTWRITEBYTECODE':'1'}
                result=subprocess.run([str(candidate/'windows/runtime/python.exe'),'-B','-X','utf8',str(candidate/'scripts/update_worker.py'),str(job)],cwd=base,env=env,capture_output=True,timeout=90)
                self.assertEqual(result.returncode,1 if failure else 0,result.stdout+result.stderr)
                expected='1.0.0' if failure else '2.0.0'
                self.assertEqual(app_install.validate(old,'windows-amd64')['version'],expected)
                self.assertEqual((device/'components.txt').read_text(),expected)
                self.assertEqual(json.loads((device/'runtime.json').read_bytes())['agent_connected'],running)
                self.assertEqual((state/'session.json').read_bytes(),b'synthetic account data')
                receipt=json.loads((state/'update-status.json').read_bytes())
                self.assertEqual(receipt['phase'],'recovery_needed' if failure=='rollback' else 'rolled_back' if failure else 'complete')
                if failure=='rollback':self.assertTrue(receipt['rollback_failures'])
                if failure:self.assertEqual((old/'unknown.txt').read_bytes(),b'synthetic owner notes')


if __name__=='__main__':unittest.main()
