"""Default-profile migration and native device isolation without speaker access."""
import contextlib,hashlib,importlib.util,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from alisa_cli import cli,devices,install,menus,runtime,state,ui,updates

ROOT=Path(__file__).resolve().parents[1]
SECOND='a'*32


class NativeDevices(unittest.TestCase):
    def setUp(self):
        self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='alisa-device-test-')))
        for key,value in (('BASE_HOME',self.root),('HOME',self.root),('DEVICE','default')):
            self.stack.enter_context(patch.object(state,key,value))
        self.stack.enter_context(patch.dict(os.environ,{},clear=False))
        os.environ.pop('ALISA_NATIVE_DEVICE',None)
        self.config=state.defaults('native');self.config.update(host='speaker-one',model='mini2',installed=True)
        state.save(self.config)

    def catalog(self,selected='default'):
        value={'version':1,'selected':selected,'devices':[
            {'id':'default','name':'Кухня'},{'id':SECOND,'name':'Кабинет'}]}
        state.write_private(self.root/'native-devices.json',value)
        return value

    def test_legacy_default_keeps_all_files_and_gets_a_name_from_existing_connection(self):
        files={'native/config.json':(self.root/'native/config.json').read_bytes(),
               'native/session.json':b'fixture account','native/browser-profile/owner':b'fixture browser',
               'ssh/key':b'fixture key','native/unknown.bin':b'owner file'}
        for name,data in files.items():state.write_private(self.root/name,data)
        value=devices.catalog()
        self.assertEqual(value['selected'],'default');self.assertEqual(value['devices'][0]['id'],'default')
        self.assertIn('speaker-one',value['devices'][0]['name']);self.assertIn('Мини 2',value['devices'][0]['name'])
        self.assertEqual(devices.home('default'),self.root)
        self.assertFalse((self.root/'native-devices.json').exists())
        for name,data in files.items():self.assertEqual((self.root/name).read_bytes(),data)

    def test_add_starts_an_empty_profile_without_copying_existing_credentials(self):
        state.write_private(self.root/'native/session.json',b'fixture existing login')
        with patch.object(ui,'select',return_value='add'),patch.object(ui,'text',return_value='Кабинет'),\
             patch.object(devices.uuid,'uuid4') as identifier:
            identifier.return_value.hex=SECOND
            with self.assertRaises(devices.Switch) as caught:devices.choose()
        self.assertEqual(caught.exception.device,SECOND)
        self.assertEqual(devices.catalog()['selected'],SECOND)
        self.assertFalse((devices.home(SECOND)/'native/session.json').exists())
        self.assertEqual((self.root/'native/session.json').read_bytes(),b'fixture existing login')
        self.assertEqual(state.load('native')['host'],'speaker-one')

    def test_cancel_and_rename_keep_selection_and_profile_paths(self):
        original=self.catalog()
        with patch.object(ui,'select',return_value='add'),patch.object(ui,'text',side_effect=ui.Back):
            with self.assertRaises(ui.Back):devices.choose()
        self.assertEqual(devices.catalog(),original)
        with patch.object(ui,'select',return_value='rename'),patch.object(ui,'text',return_value='Гостиная'):
            devices.choose()
        changed=devices.catalog();self.assertEqual(changed['selected'],'default')
        self.assertEqual(changed['devices'][0],{'id':'default','name':'Гостиная'})
        self.assertEqual(devices.home('default'),self.root)

    def test_duplicate_names_do_not_modify_the_catalog(self):
        original=self.catalog()
        with patch.object(ui,'select',return_value='rename'),patch.object(ui,'text',return_value='КАБИНЕТ'):
            with self.assertRaisesRegex(ValueError,'уже используется'):devices.choose()
        self.assertEqual(devices.catalog(),original)

    def test_selection_keeps_profiles_added_by_another_window_while_picker_was_open(self):
        self.catalog()
        def choose(*args,**kwargs):
            value=devices.catalog();value['devices'].append({'id':'b'*32,'name':'Спальня'})
            state.write_private(self.root/'native-devices.json',value)
            return SECOND
        with patch.object(ui,'select',side_effect=choose):
            with self.assertRaises(devices.Switch):devices.choose()
        self.assertEqual(len(devices.catalog()['devices']),3)
        self.assertEqual(devices.catalog()['selected'],SECOND)

    @unittest.skipUnless(sys.platform=='linux','POSIX file-lock fixture')
    def test_busy_catalog_is_not_overwritten(self):
        original=self.catalog()
        with devices.edit_catalog():
            with self.assertRaisesRegex(RuntimeError,'другом окне'):
                with devices.edit_catalog():self.fail('second writer acquired catalog lock')
        self.assertEqual(devices.catalog(),original)

    def test_selection_does_not_call_speaker_actions_or_reuse_loaded_modules(self):
        self.catalog()
        with patch.object(ui,'select',return_value=SECOND),patch.object(runtime,'stop') as stop,\
             patch.object(runtime,'start') as start:
            with self.assertRaises(devices.Switch) as caught:devices.choose()
        self.assertEqual(caught.exception.arguments,['native'])
        stop.assert_not_called();start.assert_not_called()
        with patch.object(devices.os,'execve') as execute:devices.restart(caught.exception)
        env=execute.call_args.args[2]
        self.assertEqual(env['ALISA_NATIVE_DEVICE'],SECOND)
        self.assertEqual(execute.call_args.args[1][-1],'native')

    def test_running_process_stays_pinned_when_another_window_changes_selection(self):
        self.catalog();devices.enter('native',['native','status'])
        self.catalog(selected=SECOND)
        devices.enter('native',['native','stop'])
        self.assertEqual(os.environ['ALISA_NATIVE_DEVICE'],'default')
        os.environ.pop('ALISA_NATIVE_DEVICE')
        with self.assertRaises(devices.Switch) as caught:devices.enter('native',['native','status'])
        self.assertEqual(caught.exception.device,SECOND)

    def test_bridge_returns_to_original_root(self):
        self.catalog(selected=SECOND)
        with patch.object(state,'DEVICE',SECOND),patch.dict(os.environ,{'ALISA_NATIVE_DEVICE':SECOND}):
            with self.assertRaises(devices.Switch) as caught:devices.enter('bridge',['bridge','status'])
        with patch.object(devices.os,'execve') as execute:devices.restart(caught.exception)
        self.assertNotIn('ALISA_NATIVE_DEVICE',execute.call_args.args[2])

    def test_invalid_catalog_paths_and_control_characters_are_rejected(self):
        for identifier in ('../other','/tmp/other','','b'*31,None):
            with self.subTest(identifier=identifier),self.assertRaises(ValueError):devices.home(identifier)
        for name in ('','bad\nname','\x1b[31mname','x'*81,' name '):self.assertFalse(devices.valid_name(name))
        value=self.catalog();value['devices'][1]['id']='../outside'
        state.write_private(self.root/'native-devices.json',value)
        with self.assertRaises(ValueError):devices.catalog()

    def test_fresh_processes_isolate_connection_browser_and_key_roots(self):
        self.catalog(selected=SECOND)
        other=devices.home(SECOND);config=state.defaults('native');config.update(host='speaker-two',installed=True)
        state.write_private(other/'native/config.json',config)
        code='''import json
from alisa_cli import state,accounts,ssh_setup
print(json.dumps({'home':str(state.HOME),'account_home':str(accounts.HOME),'ssh_home':str(ssh_setup.HOME),'host':state.load('native')['host']}))
'''
        for identifier,expected,host in (('default',self.root,'speaker-one'),(SECOND,other,'speaker-two')):
            environment={**os.environ,'ALISA_HOME':str(self.root),'ALISA_NATIVE_DEVICE':identifier}
            value=json.loads(subprocess.check_output([sys.executable,'-c',code],env=environment,cwd=ROOT,text=True))
            self.assertEqual(value,dict(home=str(expected),account_home=str(expected),ssh_home=str(expected),host=host))

    def test_stale_components_allow_diagnostics_but_block_changes_until_selected_update(self):
        self.config['native_bundle']='old bundle'
        for action in ('start','toggle','settings','login','voice','vpn'):
            with self.subTest(action=action),self.assertRaisesRegex(RuntimeError,'Компоненты выбранной'):
                devices.require_current(self.config,action)
        for action in ('status','doctor','stop','install','devices'):devices.require_current(self.config,action)
        self.config['native_bundle']=devices.bundle();self.assertFalse(devices.needs_update(self.config))
        self.config.pop('native_bundle');self.assertFalse(devices.needs_update(self.config))

    def test_menu_names_selected_device_and_exposes_component_update(self):
        self.catalog()
        self.assertIn(('Колонка: Кухня',('devices',None)),menus.entries('native','main',self.config))
        self.assertEqual(menus.entries('native','main',self.config)[0][1],('toggle',None))
        self.assertIn(('Обновить компоненты колонки',('install','components')),menus.entries('native','service',self.config))
        self.assertFalse(any(isinstance(target,tuple) and target[0]=='devices' for _,target in menus.entries('bridge','main',self.config)))

    def test_selected_native_update_does_not_enumerate_bridge_or_other_profiles(self):
        self.catalog()
        devices.enter('native',['update'])
        with patch.object(updates,'load',return_value=self.config) as load,\
             patch.object(runtime,'status',return_value={'phase':'idle','agent_connected':True}):
            self.assertEqual(updates.mode_plan(),[{'mode':'native','running':True}])
        load.assert_called_once_with('native')

    def test_legacy_default_remains_usable_without_a_component_marker(self):
        original=(self.root/'native/config.json').read_bytes()
        with patch.object(cli,'load',return_value=self.config),patch.object(cli,'start_mode',return_value={}) as start,patch.object(cli,'show'):
            self.assertEqual(cli.main(['native','start']),0)
        start.assert_called_once_with(self.config,foreground=False)
        self.assertEqual((self.root/'native/config.json').read_bytes(),original)

    def test_dashboard_unwinds_before_profile_restart(self):
        self.catalog()
        active=[False]
        def dashboard(**view):
            active[0]=True
            try:return view['on_select'](('devices',None))
            finally:active[0]=False
        def restart(request):self.assertFalse(active[0]);self.assertEqual(request.device,SECOND)
        with patch.object(cli,'load',return_value=self.config),patch.object(menus,'load',return_value=self.config),\
             patch.object(ui,'interactive',return_value=True),patch.object(ui,'dashboard',side_effect=dashboard),\
             patch.object(ui,'select',return_value=SECOND),patch.object(devices,'restart',side_effect=restart) as change,\
             patch.object(cli.updates,'startup_offer'),patch.object(cli.updates,'clean_downloads'),patch.object(cli.updates,'show_result'),\
             patch.object(sys,'argv',['alisa.py','native']),patch.object(ui,'banner'):
            cli.main()
        change.assert_called_once()

    def test_component_update_preserves_stopped_state_and_refuses_live_conversation(self):
        for connected in (False,True):
            with patch.object(cli,'load',return_value=self.config),patch.object(runtime,'status',return_value={'phase':'idle','agent_connected':connected}),\
                 patch.object(install,'install',return_value={}) as update,patch.object(cli,'show'):
                self.assertEqual(cli.main(['native','install','components']),0)
            update.assert_called_once_with(self.config,activate=connected)
        with patch.object(cli,'load',return_value=self.config),patch.object(runtime,'status',return_value={'phase':'active'}),\
             patch.object(install,'install') as update,patch.object(ui,'note'):
            self.assertEqual(cli.main(['native','install','components']),1)
        update.assert_not_called()

    def test_restart_uses_a_real_new_process_with_the_selected_profile(self):
        self.catalog(selected=SECOND)
        state.write_private(devices.home(SECOND)/'native/config.json',{**self.config,'host':'speaker-two'})
        entry=self.root/'app/alisa.py';entry.parent.mkdir()
        entry.write_text("import json\nfrom alisa_cli import state,accounts\nprint(json.dumps({'home':str(state.HOME),'accounts':str(accounts.HOME),'host':state.load('native')['host']}))\n")
        environment={**os.environ,'ALISA_HOME':str(self.root),'PYTHONPATH':str(ROOT)}
        environment.pop('ALISA_NATIVE_DEVICE',None)
        code="from pathlib import Path\nfrom alisa_cli import devices,state\nstate.ROOT=Path("+repr(str(entry.parent))+")\ntry:devices.enter('native',['native'])\nexcept devices.Switch as request:devices.restart(request)\n"
        result=json.loads(subprocess.check_output([sys.executable,'-c',code],env=environment,cwd=ROOT,text=True))
        self.assertEqual(result,{'home':str(devices.home(SECOND)),'accounts':str(devices.home(SECOND)),'host':'speaker-two'})


class ProfileWorker(unittest.TestCase):
    def worker(self):
        spec=importlib.util.spec_from_file_location('device_update_worker',ROOT/'scripts/update_worker.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

    def test_worker_pins_child_state_even_when_parent_points_at_another_profile(self):
        worker=self.worker()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);entry=root/'alisa.py'
            entry.write_text("import json,os\nprint(json.dumps({'home':os.environ['ALISA_HOME'],'profile':os.environ['ALISA_NATIVE_DEVICE']}))\n")
            with patch.dict(os.environ,{'ALISA_HOME':str(root/'other'),'ALISA_NATIVE_DEVICE':SECOND}):
                result=json.loads(worker.execute(root,'linux-x86_64',['native','status'],'fixture',state_directory=root/'selected'))
            self.assertEqual(result,{'home':str(root/'selected'),'profile':'default'})

    @unittest.skipUnless(sys.platform=='linux','POSIX file-lock fixture')
    def test_parallel_app_update_is_rejected_before_any_device_action(self):
        worker=self.worker()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);job=root/'job.json'
            job.write_text(json.dumps({'current_root':str(root/'app'),'platform':'linux-x86_64'}))
            with patch.object(worker.app_install,'owned',return_value=True),patch.object(worker,'_apply') as apply,\
                 worker.app_install.exclusive_lock(root/'.alisa-update.lock','fixture'):
                with self.assertRaisesRegex(RuntimeError,'другом окне'):worker.apply(job)
            apply.assert_not_called()

    def test_real_update_and_rollback_touch_only_the_job_profile(self):
        from test_updates import WorkerTransaction
        for failed in (False,True):
            with self.subTest(failed=failed),tempfile.TemporaryDirectory() as folder:
                base=Path(folder);app=base/'app';candidate=base/'candidate'
                fixture=WorkerTransaction()
                old=fixture.fixture(app,'1.0.0',app)
                new=fixture.fixture(candidate,'2.0.0',app)
                for package,version in ((old,'1.0.0'),(new,'2.0.0')):
                    script='''import json,os,sys
from pathlib import Path
VERSION=VERSION_VALUE
args=sys.argv[1:]
if args==['--version']:print(VERSION);raise SystemExit()
root=Path(os.environ['ALISA_HOME']);path=root/'runtime.json'
value=json.loads(path.read_text())
if args[1]=='status':print(json.dumps(value));raise SystemExit()
if args[1]=='install':
 if '--dry-run' in args:raise SystemExit()
 (root/'components.txt').write_text(VERSION)
 if os.environ.get('ALISA_FIXTURE_FAIL') and VERSION=='2.0.0':raise SystemExit(1)
elif args[1] in ('start','stop'):
 value['agent_connected']=args[1]=='start';path.write_text(json.dumps(value))
'''.replace('VERSION_VALUE',repr(version))
                    (package/'alisa.py').write_text(script)
                    manifest=json.loads((package/'package-manifest.json').read_text())
                    manifest['files']['alisa.py']=hashlib.sha256(script.encode()).hexdigest()
                    (package/'package-manifest.json').write_text(json.dumps(manifest))
                roots=[base/'selected',base/'other']
                for root in roots:
                    root.mkdir();(root/'runtime.json').write_text(json.dumps({'phase':'idle','agent_connected':True}))
                    (root/'components.txt').write_text('1.0.0');(root/'session.json').write_bytes(b'fixture account')
                untouched={path.name:path.read_bytes() for path in roots[1].iterdir()}
                job=base/'job.json'
                job.write_text(json.dumps({'schema':1,'current_root':str(app),'payload':str(candidate),
                    'platform':'linux-x86_64','version':'2.0.0','parent_pid':0,
                    'modes':[{'mode':'native','running':True}],'state_directory':str(roots[0]),'relaunch':False}))
                environment={**os.environ,'ALISA_HOME':str(roots[1]),'ALISA_NATIVE_DEVICE':SECOND,
                             'ALISA_FIXTURE_FAIL':'1' if failed else ''}
                result=subprocess.run([sys.executable,'-B',str(candidate/'scripts/update_worker.py'),str(job)],
                                      env=environment,capture_output=True,text=True,timeout=20)
                self.assertEqual(result.returncode,1 if failed else 0,result.stdout+result.stderr)
                self.assertEqual((roots[0]/'components.txt').read_text(),'1.0.0' if failed else '2.0.0')
                self.assertTrue(json.loads((roots[0]/'runtime.json').read_text())['agent_connected'])
                self.assertEqual((roots[0]/'session.json').read_bytes(),b'fixture account')
                self.assertEqual({path.name:path.read_bytes() for path in roots[1].iterdir()},untouched)
