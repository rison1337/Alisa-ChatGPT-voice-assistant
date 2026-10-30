"""Actual alisa.cmd, CLI handoff and Windows worker; file-backed station only."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]

FIXTURE = '''import json,os,sys
from pathlib import Path
from alisa_cli import __version__,updates,ui
def main():
 args=sys.argv[1:];device=Path(os.environ['ALISA_FIXTURE_DEVICE'])
 if args==['--version']:
  print('99.0.0' if os.environ.get('ALISA_FIXTURE_FAIL')=='version' and __version__=='2.0.0' else __version__);return 0
 if not args:
  (device/'relaunch.txt').write_text(__version__);return 0
 if args[0]=='echo':print(json.dumps(args[1:],ensure_ascii=False));return 7
 state=json.loads((device/'runtime.json').read_bytes())
 if args[0]=='update':
  updates.mode_plan=lambda:[{'mode':'native','running':state['agent_connected']}]
  ui.interactive=lambda:os.environ.get('ALISA_FIXTURE_RELAUNCH')=='1'
  return updates.command(args[1:])
 mode,action=args[:2]
 if action=='status':print(json.dumps(state));return 0
 if action=='install':
  (device/'components.txt').write_text(__version__)
  if os.environ.get('ALISA_FIXTURE_FAIL') in ('candidate','rollback') and (__version__=='2.0.0' or os.environ['ALISA_FIXTURE_FAIL']=='rollback'):return 1
 elif action in ('start','stop'):
  state['agent_connected']=action=='start';(device/'runtime.json').write_text(json.dumps(state))
 return 0
'''


@unittest.skipUnless(sys.platform == 'win32', 'actual Windows cmd.exe and embedded Python')
class WindowsLauncher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='av-exit-')
        cls.folder = Path(cls.temporary.name)
        cls.old = cls.package(cls.folder / 'old', '1.0.0')
        cls.new = cls.package(cls.folder / 'new', '2.0.0')
        cls.archive = cls.folder / 'candidate.zip'
        with zipfile.ZipFile(cls.archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for file in cls.new.rglob('*'):
                if file.is_file():archive.write(file, 'alisa-voice-windows/' + file.relative_to(cls.new).as_posix())
        Path(str(cls.archive) + '.sha256').write_text(hashlib.sha256(cls.archive.read_bytes()).hexdigest())

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @classmethod
    def package(cls, root, version):
        root.mkdir()
        shutil.copytree(ROOT / 'windows/runtime', root / 'windows/runtime')
        names = ['alisa_cli/' + name + '.py' for name in ('app_install', 'windows', 'state', 'updates', 'ui')]
        names += ['windows/install.py', 'windows/launcher.py', 'scripts/update_worker.py']
        for name in names:
            target = root / name;target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        (root / 'alisa_cli/__init__.py').write_text('__version__=' + repr(version), encoding='utf-8')
        (root / 'fixture_main.py').write_text(FIXTURE, encoding='utf-8')
        # Keep the real entry point and its UpdateScheduled boundary. Only the
        # device operations/menu body are replaced with deterministic fixtures.
        entry = (ROOT / 'alisa.py').read_text(encoding='utf-8').replace('from alisa_cli.cli import main', 'from fixture_main import main')
        (root / 'alisa.py').write_text(entry, encoding='utf-8')
        hashes = {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
        (root / 'windows-package.json').write_text(json.dumps({'schema':1, 'package':'alisa-voice-windows-native', 'platform':'windows-amd64', 'version':version, 'files':hashes}), encoding='utf-8')
        return root

    def profile(self, label):
        folder = self.folder / label;folder.mkdir()
        device = folder / 'device';device.mkdir()
        (device / 'runtime.json').write_text(json.dumps({'phase':'idle','agent_connected':True}))
        (device / 'components.txt').write_text('1.0.0')
        state = folder / 'state';state.mkdir()
        (state / 'session.json').write_bytes(b'synthetic owner account')
        env = {**os.environ, 'LOCALAPPDATA':str(folder/'local'), 'ALISA_HOME':str(state),
               'ALISA_FIXTURE_DEVICE':str(device), 'PYTHONDONTWRITEBYTECODE':'1'}
        env.pop('ALISA_WINDOWS_HANDOFF', None)
        result = self.install(env, folder)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        base = folder / 'local/AlisaVoice'
        (base / 'app/unknown.txt').write_bytes(b'owner notes')
        return folder, base, state, device, env

    def install(self, env, folder):
        return subprocess.run([str(self.old/'windows/runtime/python.exe'), '-B', '-X', 'utf8', str(self.old/'windows/install.py'), '--no-run'], env=env, cwd=folder, capture_output=True, timeout=90)

    def launch(self, base, arguments, env, folder):
        command = '"' + str(base/'alisa.cmd') + '" ' + subprocess.list2cmdline(list(map(str,arguments)))
        return subprocess.run(command, shell=True, env=env, cwd=folder, capture_output=True, timeout=120)

    def test_cmd_returns_update_result_after_replace_rollback_and_menu_relaunch(self):
        for failure in ('', 'version', 'candidate', 'rollback'):
            with self.subTest(failure=failure):
                folder, base, state, device, env = self.profile('transaction-' + (failure or 'success'))
                env['ALISA_FIXTURE_FAIL'] = failure
                env['ALISA_FIXTURE_RELAUNCH'] = '1' if failure else '0'
                result = self.launch(base, ['update','--archive',self.archive,'--version','2.0.0','--yes'], env, folder)
                deadline = time.monotonic() + 30
                while not (state/'update-status.json').is_file() and time.monotonic() < deadline:time.sleep(.05)
                receipt = json.loads((state/'update-status.json').read_bytes())
                expected = 'recovery_needed' if failure == 'rollback' else 'rolled_back' if failure else 'complete'
                self.assertEqual(receipt['phase'], expected, result.stdout + result.stderr)
                self.assertEqual(result.returncode, 1 if failure else 0, result.stdout + result.stderr)
                self.assertEqual(json.loads((base/'app/windows-package.json').read_bytes())['version'], '1.0.0' if failure else '2.0.0')
                self.assertEqual((state/'session.json').read_bytes(), b'synthetic owner account')
                self.assertTrue(any((p/'unknown.txt').is_file() for p in [base/'app', *base.glob('app.previous*')]))
                self.assertTrue(json.loads((device/'runtime.json').read_bytes())['agent_connected'])
                if failure:self.assertEqual((device/'relaunch.txt').read_text(), '1.0.0')
                self.assertNotIn(b'Traceback', result.stdout + result.stderr)
                self.assertEqual(list(base.glob('.launcher-run-*')), [])

    def test_cmd_preserves_unicode_arguments_spaces_quotes_and_plain_exit_code(self):
        folder, base, state, device, env = self.profile('папка с пробелами')
        arguments = ['слова с пробелом', 'a"b', 'ещё']
        result = self.launch(base, ['echo', *arguments], env, folder)
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout.decode('utf-8')), arguments)

    def test_reinstall_keeps_modified_supervisor_and_unknown_files(self):
        folder, base, state, device, env = self.profile('modified-supervisor')
        old = next((base/'launchers').iterdir())
        (old/'launcher.py').write_bytes(b'owner modified file')
        (old/'unknown.txt').write_bytes(b'owner unknown file')
        self.assertEqual(self.install(env, folder).returncode, 0)
        self.assertEqual((old/'launcher.py').read_bytes(), b'owner modified file')
        self.assertEqual((old/'unknown.txt').read_bytes(), b'owner unknown file')
        result = self.launch(base, ['--version'], env, folder)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), b'1.0.0')

    def test_direct_python_update_refuses_before_replacing_anything(self):
        folder, base, state, device, env = self.profile('direct-python')
        before = (base/'app/windows-package.json').read_bytes()
        result = subprocess.run([str(base/'app/windows/runtime/python.exe'), '-B', '-X', 'utf8', str(base/'app/alisa.py'), 'update', '--archive', str(self.archive), '--version', '2.0.0', '--yes'], env=env, cwd=folder, capture_output=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'alisa.cmd', result.stdout + result.stderr)
        self.assertEqual((base/'app/windows-package.json').read_bytes(), before)
        self.assertFalse((state/'update-status.json').exists())


if __name__ == '__main__':unittest.main()
