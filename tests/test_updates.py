"""Release validation and real filesystem rollback, without user state or SSH."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from alisa_cli import app_install, updates

ROOT = Path(__file__).resolve().parents[1]


def package(root, version, target='linux-x86_64', contents=None):
    root.mkdir(parents=True, exist_ok=True)
    contents = contents or {'alisa.py': ('VERSION ' + version).encode()}
    hashes = {}
    for name, data in contents.items():
        output = root / name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    filename, identity = app_install.FORMATS[target]
    (root / filename).write_text(json.dumps({'package': identity, 'files': hashes, 'version': version, 'platform': target}), encoding='utf-8')
    return root


class AppTransaction(unittest.TestCase):
    def test_upgrade_and_rollback_preserve_all_old_unknown_files_and_backups(self):
        for target in app_install.FORMATS:
            with self.subTest(target=target), tempfile.TemporaryDirectory() as folder:
                base = Path(folder)
                old = package(base / 'app', '1.0.0', target)
                (old / 'unknown.txt').write_bytes(b'user data')
                previous = base / 'app.previous'
                previous.mkdir()
                (previous / 'keep').write_bytes(b'unknown prior backup')
                state = base / 'state'
                state.mkdir()
                (state / 'session.json').write_bytes(b'synthetic account')
                new = package(base / 'candidate', '2.0.0', target)
                record = app_install.replace(new, old, target)
                self.assertEqual((Path(record['backup']) / 'unknown.txt').read_bytes(), b'user data')
                self.assertEqual((previous / 'keep').read_bytes(), b'unknown prior backup')
                self.assertEqual(app_install.validate(old, target)['version'], '2.0.0')
                app_install.rollback(record)
                self.assertEqual(app_install.validate(old, target)['version'], '1.0.0')
                self.assertEqual((old / 'unknown.txt').read_bytes(), b'user data')
                self.assertEqual((state / 'session.json').read_bytes(), b'synthetic account')

    def test_failed_rename_restores_the_original_application(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            old, new = package(base / 'app', '1.0.0'), package(base / 'new', '2.0.0')
            real = Path.rename
            def rename(path, destination):
                if '.alisa-install-' in str(path):
                    raise PermissionError('synthetic Windows image lock')
                return real(path, destination)
            with patch.object(Path, 'rename', rename):
                with self.assertRaises(PermissionError):app_install.replace(new, old, 'linux-x86_64')
            self.assertEqual(app_install.validate(old, 'linux-x86_64')['version'], '1.0.0')

    def test_corrupt_candidate_foreign_destination_and_wrong_version_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            root = package(base / 'new', '2.0.0')
            with self.assertRaises(RuntimeError):app_install.validate(root, 'linux-x86_64', '3.0.0')
            foreign = base / 'foreign'
            foreign.mkdir()
            (foreign / 'private.txt').write_bytes(b'keep me')
            with self.assertRaises(RuntimeError):app_install.replace(root, foreign, 'linux-x86_64')
            (root / 'alisa.py').write_bytes(b'corrupt')
            with self.assertRaises(RuntimeError):app_install.replace(root, base / 'app', 'linux-x86_64')
            self.assertEqual((foreign / 'private.txt').read_bytes(), b'keep me')
            self.assertFalse((base / 'app').exists())


class ReleaseOffer(unittest.TestCase):
    def release(self, version='99.0.0'):
        name = updates.ASSETS['linux-x86_64'][0]
        return {'tag_name': 'v' + version, 'assets': [
            {'name': x, 'browser_download_url': 'https://github.com/' + updates.REPOSITORY + '/releases/download/v' + version + '/' + x}
            for x in (name, name + '.sha256')]}

    def test_version_order_including_prereleases(self):
        values = ['0.2.0', '0.3.0.dev2', '0.3.0.dev10', '0.3.0-beta.1', '0.3.0-rc.1', '0.3.0', '1.0.0']
        self.assertEqual(values, sorted(values, key=updates.version_key))
        with self.assertRaises(RuntimeError):updates.version_key('main')

    def test_check_is_cached_and_never_contacts_the_station(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(updates, 'HOME', Path(folder)), \
             patch.object(updates, 'target_platform', return_value='linux-x86_64'), \
             patch.object(updates, 'request', return_value=json.dumps(self.release()).encode()) as request, \
             patch.object(updates, 'mode_plan') as station:
            first = updates.check(force=True)
            second = updates.check()
            self.assertTrue(first['available'])
            self.assertEqual(first, second)
            self.assertEqual(request.call_count, 1)
            station.assert_not_called()

    def test_bad_release_url_or_missing_platform_asset_never_offers_install(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(updates, 'HOME', Path(folder)), \
             patch.object(updates, 'target_platform', return_value='linux-x86_64'):
            value = self.release()
            value['assets'] = []
            with patch.object(updates, 'request', return_value=json.dumps(value).encode()):
                self.assertFalse(updates.check(force=True)['available'])
            value = self.release()
            value['assets'][0]['browser_download_url'] = 'https://elsewhere.example/program.zip'
            with patch.object(updates, 'request', return_value=json.dumps(value).encode()):
                with self.assertRaises(RuntimeError):updates.check(force=True)

    def test_malformed_metadata_is_rejected_and_does_not_block_startup(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(updates, 'HOME', Path(folder)), \
             patch.object(updates, 'ROOT', Path(folder)), \
             patch.object(updates, 'target_platform', return_value='linux-x86_64'):
            invalid = [[], {'tag_name': 123}, {'tag_name': 'v99.0.0', 'assets': None}]
            value = self.release()
            value['assets'][0]['browser_download_url'] = None
            invalid.append(value)
            for value in invalid:
                with self.subTest(value=value), patch.object(updates, 'request', return_value=json.dumps(value).encode()):
                    with self.assertRaises(RuntimeError):updates.check(force=True)
                    updates.startup_offer()
            (Path(folder) / 'update-status.json').write_text('[]')
            updates.show_result()

    def test_decline_is_session_only_and_never_choice_disables_future_network_checks(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(updates, 'HOME', Path(folder)), \
             patch.object(updates, 'ROOT', Path(folder)), patch.object(updates, 'prepare') as prepare:
            value = {'available': True, 'latest_version': '99.0.0', 'current_version': '0.3.0'}
            with patch.object(updates.ui, 'select', return_value='later') as select:
                updates.offer(value)
                self.assertEqual([x[1] for x in select.call_args.args[1]], ['apply', 'later', 'never'])
            self.assertTrue(updates.preferences()['ask_on_start'])
            with patch.object(updates.ui, 'select', return_value='never'):
                updates.offer(value)
            with patch.object(updates, 'check') as check:
                updates.startup_offer()
            check.assert_not_called()
            prepare.assert_not_called()
            self.assertFalse(updates.preferences()['ask_on_start'])
            updates.set_startup(True)
            self.assertTrue(updates.preferences()['ask_on_start'])

    def test_offline_or_escape_does_not_prevent_starting_cli(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(updates, 'ROOT', Path(folder)), \
             patch.object(updates, 'preferences', return_value={'ask_on_start': True}):
            with patch.object(updates, 'check', side_effect=RuntimeError('offline')):
                updates.startup_offer()

    def test_failed_release_is_not_offered_on_startup_but_newer_release_and_manual_retry_are(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(updates,'HOME',Path(folder)), \
             patch.object(updates,'ROOT',Path(folder)), patch.object(updates,'offer') as offer:
            for phase in ('rolled_back','recovery_needed'):
                (Path(folder)/'update-status.json').write_text(json.dumps({'phase':phase,'version':'99.0.0','notice_seen':True}))
                value={'available':True,'latest_version':'99.0.0'}
                with patch.object(updates,'check',return_value=value):updates.startup_offer()
                offer.assert_not_called()
                # The manual command still reaches the normal update offer.
                with patch.object(updates,'check',return_value=value):updates.command([])
                offer.assert_called_once();offer.reset_mock()
                with patch.object(updates,'check',return_value={**value,'latest_version':'99.0.1'}):updates.startup_offer()
                offer.assert_called_once();offer.reset_mock()

    def test_manual_cli_cancel_returns_cleanly(self):
        from alisa_cli import cli
        for error, code in [(updates.ui.Back, 0), (KeyboardInterrupt, 130)]:
            with self.subTest(error=error), patch.object(updates, 'command', side_effect=error):
                self.assertEqual(cli.main(['update']), code)
            with patch.object(updates, 'check', return_value={'available': True}), \
                 patch.object(updates, 'offer', side_effect=updates.ui.Back):
                updates.startup_offer()

    def test_cleanup_keeps_modified_unknown_and_account_files_and_retries_locks(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(updates, 'HOME', Path(folder)):
            root=Path(folder)
            job=root/'updates/install-test'
            payload=job/'payload';payload.mkdir(parents=True)
            account=root/'native/session.json';account.parent.mkdir();account.write_bytes(b'keep account')
            generated=payload/'generated.dll';generated.write_bytes(b'generated payload')
            modified=payload/'modified.txt';modified.write_bytes(b'owner changed this')
            unknown=payload/'unknown.txt';unknown.write_bytes(b'owner notes')
            plan={'payload/generated.dll':app_install.file_hash(generated),'payload/modified.txt':hashlib.sha256(b'original').hexdigest()}
            app_install.private_json(job/'cleanup.json',plan)
            app_install.private_json(job/'result.json',{'phase':'complete','cleanup_sha256':app_install.file_hash(job/'cleanup.json')})
            real_unlink=Path.unlink
            def locked(path,*args,**kwargs):
                if path==generated:raise PermissionError('synthetic Windows loaded image')
                return real_unlink(path,*args,**kwargs)
            with patch.object(Path,'unlink',locked):updates.clean_downloads()
            self.assertTrue(generated.exists());self.assertTrue((job/'cleanup.json').exists())
            updates.clean_downloads()
            self.assertFalse(generated.exists());self.assertFalse((job/'cleanup.json').exists())
            self.assertEqual(modified.read_bytes(),b'owner changed this')
            self.assertEqual(unknown.read_bytes(),b'owner notes')
            self.assertEqual(account.read_bytes(),b'keep account')

    def test_mode_plan_does_not_interrupt_voice_and_windows_ignores_bridge(self):
        from alisa_cli import runtime
        config = lambda mode: {'mode': mode, 'installed': True}
        for phase in ('active', 'connecting', 'opening', 'starting', 'stopping'):
            with self.subTest(phase=phase), patch.object(updates, 'load', side_effect=config), \
                 patch.object(runtime, 'status', return_value={'phase': phase}) as status:
                with self.assertRaisesRegex(RuntimeError, 'закройте голосовой чат'):
                    updates.mode_plan()
                self.assertEqual(status.call_count, 1)
        with patch.object(updates, 'sys') as host, patch.object(updates, 'load', side_effect=config), \
             patch.object(runtime, 'status', return_value={'phase': 'idle', 'agent_connected': True}) as status:
            host.platform = 'win32'
            self.assertEqual(updates.mode_plan(), [{'mode': 'native', 'running': True}])
            self.assertEqual(status.call_count, 1)


class Archives(unittest.TestCase):
    def test_empty_or_normalized_manifest_paths_are_rejected(self):
        for name in ['', '.', './file', 'dir//file', 'dir/../file', '/file', 'C:/file']:
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                app_install.relative_name(name)

    def test_traversal_links_and_duplicate_paths_are_rejected_before_extracting(self):
        for target in updates.ASSETS:
            prefix = updates.ASSETS[target][1]
            for kind in ('traversal', 'link', 'duplicate'):
                with self.subTest(target=target, kind=kind), tempfile.TemporaryDirectory() as folder:
                    root = Path(folder)
                    archive = root / ('test.zip' if target == 'windows-amd64' else 'test.tar.gz')
                    if target == 'windows-amd64':
                        with zipfile.ZipFile(archive, 'w') as output:
                            if kind == 'link':
                                item = zipfile.ZipInfo(prefix + '/link')
                                item.external_attr = 0o120777 << 16
                                output.writestr(item, b'target')
                            else:
                                output.writestr(prefix + ('/../../escape' if kind == 'traversal' else '/file'), b'x')
                                if kind == 'duplicate':output.writestr(prefix + '/file', b'y')
                    else:
                        with tarfile.open(archive, 'w:gz') as output:
                            item = tarfile.TarInfo(prefix + ('/../../escape' if kind == 'traversal' else '/file'))
                            if kind == 'link':
                                item.type = tarfile.SYMTYPE
                                item.linkname = 'outside'
                                output.addfile(item)
                            else:
                                item.size = 1
                                output.addfile(item, io.BytesIO(b'x'))
                                if kind == 'duplicate':output.addfile(item, io.BytesIO(b'y'))
                    extracted = root / 'extract'
                    extracted.mkdir()
                    with self.assertRaises(RuntimeError):updates.unpack(archive, extracted, target)
                    self.assertFalse(list(extracted.iterdir()))

    def test_local_archive_checksum_failure_keeps_installation_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            archive = base / 'fake.tar.gz'
            archive.write_bytes(b'corrupt')
            Path(str(archive) + '.sha256').write_text('0' * 64)
            with patch.dict(os.environ, {'ALISA_WINDOWS_HANDOFF':str(base/'handoff.json')}), patch.object(updates, 'HOME', base / 'state'), patch.object(updates, 'installed_root', return_value=base / 'app'), \
                 patch.object(updates, 'mode_plan') as station:
                with self.assertRaisesRegex(RuntimeError, 'SHA-256'):
                    updates.prepare({'platform': 'linux-x86_64', 'latest_version': '99.0.0'}, archive=archive)
            station.assert_not_called()
            self.assertEqual(list((base / 'state/updates').iterdir()), [])

    def test_matching_checksum_on_invalid_archive_still_returns_readable_error(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            archive = base / 'invalid.zip'
            archive.write_bytes(b'not an archive')
            Path(str(archive) + '.sha256').write_text(app_install.file_hash(archive))
            with patch.dict(os.environ, {'ALISA_WINDOWS_HANDOFF':str(base/'handoff.json')}), patch.object(updates, 'HOME', base / 'state'), patch.object(updates, 'installed_root', return_value=base / 'app'), \
                 patch.object(updates, 'mode_plan') as station:
                with self.assertRaisesRegex(RuntimeError, 'распаковать'):
                    updates.prepare({'platform': 'windows-amd64', 'latest_version': '99.0.0'}, archive=archive)
            station.assert_not_called()
            self.assertEqual(list((base / 'state/updates').iterdir()), [])


class WorkerPreflight(unittest.TestCase):
    def worker(self):
        spec=importlib.util.spec_from_file_location('tested_update_worker',ROOT/'scripts/update_worker.py')
        worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
        return worker

    def test_child_error_only_uses_known_public_codes(self):
        worker=self.worker()
        for data,expected in [(b'{"update_error":"ssh_unreachable","secret":"private"}','Нет подключения SSH'),
                              (b'{"update_error":"private"}',None),(b'private',None)]:
            with patch.object(worker.subprocess,'run',return_value=subprocess.CompletedProcess([],1,stdout=data,stderr=b'private')):
                with self.assertRaises(RuntimeError) as caught:worker.execute(ROOT,'linux-x86_64',['native','status'],'проверка')
            self.assertNotIn('private',str(caught.exception))
            if expected:self.assertIn(expected,str(caught.exception))

    def test_candidate_preflight_failure_does_not_replace_app_stop_or_restore_components(self):
        worker=self.worker()
        for failure in ('status','components','became_busy'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as folder:
                base=Path(folder);current=base/'app';payload=base/'payload';state=base/'state'
                current.mkdir();payload.mkdir();state.mkdir()
                job=base/'job.json'
                job.write_text(json.dumps({'schema':1,'current_root':str(current),'payload':str(payload),
                    'platform':'linux-x86_64','version':'2.0.0','parent_pid':0,
                    'modes':[{'mode':'native','running':True}],'state_directory':str(state),'relaunch':True}))
                cwd=Path.cwd()
                observed=[]
                def execute(root,target,args,phase,**kwargs):
                    self.assertEqual(root,payload)
                    observed.append(args)
                    if args==['native','status'] and failure!='status':
                        if failure=='became_busy' and len(observed)>1:return b'{"phase":"opening","agent_connected":true}'
                        return b'{"phase":"idle","agent_connected":true}'
                    if failure=='became_busy':return b'{}'
                    raise RuntimeError('SSH недоступен' if failure=='status' else 'Проверка пакета не пройдена')
                try:
                    with patch.object(worker,'PAYLOAD',payload),patch.object(worker.app_install,'validate',return_value={'files':{}}), \
                         patch.object(worker.app_install,'owned',return_value=True),patch.object(worker,'wait_previous_process'), \
                         patch.object(worker,'execute',side_effect=execute) as calls,patch.object(worker,'install_application') as install, \
                         patch.object(worker.app_install,'rollback') as rollback,patch.object(worker.os,'execv') as relaunch:
                        self.assertEqual(worker.apply(job),1)
                        install.assert_not_called();rollback.assert_not_called()
                        relaunch.assert_not_called()
                        self.assertEqual(calls.call_count,{'status':1,'components':2,'became_busy':3}[failure])
                        if failure=='components':self.assertIn('--dry-run',calls.call_args.args[2])
                        receipt=json.loads((state/'update-status.json').read_text())
                        self.assertEqual(receipt['rollback_failures'],[])
                finally:os.chdir(cwd)

    def test_busy_mode_recheck_does_not_stop_or_replace_anything(self):
        spec = importlib.util.spec_from_file_location('tested_update_worker', ROOT / 'scripts/update_worker.py')
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        for phase in ('active', 'connecting', 'opening', 'starting', 'stopping'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as folder:
                base = Path(folder)
                current, payload, state = base / 'app', base / 'payload', base / 'state'
                current.mkdir(); payload.mkdir(); state.mkdir()
                job = base / 'job.json'
                job.write_text(json.dumps({'schema': 1, 'current_root': str(current), 'payload': str(payload),
                    'platform': 'windows-amd64', 'version': '2.0.0', 'parent_pid': 0,
                    'modes': [{'mode': 'native', 'running': True}], 'state_directory': str(state), 'relaunch': False}))
                original_directory = Path.cwd()
                try:
                    with patch.object(worker, 'PAYLOAD', payload), \
                         patch.object(worker.app_install, 'validate', return_value={'files': {}}), \
                         patch.object(worker.app_install, 'owned', return_value=True), \
                         patch.object(worker, 'wait_previous_process'), \
                         patch.object(worker, 'execute', return_value=json.dumps({'phase': phase}).encode()) as execute, \
                         patch.object(worker, 'install_application', return_value={'destination': str(current), 'backup': None}) as install, \
                         patch.object(worker.app_install, 'rollback') as rollback:
                        self.assertEqual(worker.apply(job), 1)
                        install.assert_not_called()
                        rollback.assert_not_called()
                        self.assertEqual([call.args[2] for call in execute.call_args_list], [['native', 'status']])
                finally:
                    os.chdir(original_directory)


@unittest.skipUnless(sys.platform == 'linux', 'real Linux worker subprocess')
class WorkerTransaction(unittest.TestCase):
    def fixture(self, base, version, destination):
        script = '''import json,os,sys
from pathlib import Path
VERSION=VERSION_VALUE
device=Path(os.environ['ALISA_FIXTURE_DEVICE'])
args=sys.argv[1:]
if args==['--version']:print(VERSION);raise SystemExit()
mode,action=args[:2]
state=json.loads((device/'runtime.json').read_text())
if action=='status':print(json.dumps(state));raise SystemExit()
if action=='install':
 if '--dry-run' in args:raise SystemExit()
 (device/'components.txt').write_text(VERSION)
 if os.environ.get('ALISA_FIXTURE_FAIL') and (VERSION=='2.0.0' or os.environ['ALISA_FIXTURE_FAIL']=='rollback'):raise SystemExit(1)
elif action in ('start','stop'):
 state['agent_connected']=action=='start';(device/'runtime.json').write_text(json.dumps(state))
'''.replace('VERSION_VALUE', repr(version))
        installer = '''import argparse,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from alisa_cli import app_install
p=argparse.ArgumentParser();p.add_argument('--install',action='store_true');p.add_argument('--record');a=p.parse_args()
app_install.replace(Path(__file__).resolve().parents[1],Path(DESTINATION_VALUE),'linux-x86_64',record_path=Path(a.record))
'''.replace('DESTINATION_VALUE', repr(str(destination)))
        files = {'alisa.py': script.encode(), 'scripts/release.py': installer.encode(),
                 'scripts/update_worker.py': (ROOT / 'scripts/update_worker.py').read_bytes(),
                 'alisa_cli/errors.py': (ROOT / 'alisa_cli/errors.py').read_bytes(),
                 'alisa_cli/__init__.py': b'', 'alisa_cli/app_install.py': (ROOT / 'alisa_cli/app_install.py').read_bytes()}
        return package(base, version, contents=files)

    def test_real_worker_updates_or_rolls_back_host_components_and_running_state(self):
        for fail, running in (('', True), ('candidate', True), ('rollback', True), ('', False)):
            with self.subTest(fail=fail, running=running), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                old = self.fixture(base / 'app', '1.0.0', base / 'app')
                (old / 'unknown.txt').write_bytes(b'user notes')
                payload = self.fixture(base / 'payload', '2.0.0', base / 'app')
                device, state = base / 'device', base / 'state'
                device.mkdir();state.mkdir()
                (device / 'session.json').write_bytes(b'synthetic account remains unchanged')
                (device / 'components.txt').write_text('1.0.0')
                (device / 'runtime.json').write_text(json.dumps({'phase': 'idle', 'agent_connected': running}))
                job = base / 'job.json'
                job.write_text(json.dumps({'schema': 1, 'current_root': str(old), 'payload': str(payload),
                                          'platform': 'linux-x86_64', 'version': '2.0.0', 'parent_pid': 0,
                                          'modes': [{'mode': 'native', 'running': running}],
                                          'state_directory': str(state), 'relaunch': False}))
                env = {**os.environ, 'ALISA_FIXTURE_DEVICE': str(device), 'ALISA_FIXTURE_FAIL': fail}
                result = subprocess.run([sys.executable, '-B', str(payload / 'scripts/update_worker.py'), str(job)],
                                        capture_output=True, env=env, timeout=30)
                self.assertEqual(result.returncode, 1 if fail else 0, result.stdout + result.stderr)
                expected = '1.0.0' if fail else '2.0.0'
                self.assertEqual(app_install.validate(old, 'linux-x86_64')['version'], expected)
                self.assertEqual((device / 'components.txt').read_text(), expected)
                self.assertEqual((device / 'session.json').read_bytes(), b'synthetic account remains unchanged')
                self.assertEqual(json.loads((device / 'runtime.json').read_text())['agent_connected'], running)
                receipt = json.loads((state / 'update-status.json').read_text())
                self.assertEqual(receipt['phase'], 'recovery_needed' if fail=='rollback' else 'rolled_back' if fail else 'complete')
                if fail=='rollback':self.assertTrue(receipt['rollback_failures'])
                if fail:self.assertEqual((old / 'unknown.txt').read_bytes(), b'user notes')


if __name__ == '__main__':
    unittest.main()
