import json,os,signal,sqlite3,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from alisa_cli import accounts,login_browser,state,ui

class OwnerBrowser(unittest.TestCase):
 def setUp(self):
  patcher=patch.object(accounts,'require_login_tools')
  self.login_tools=patcher.start();self.addCleanup(patcher.stop)

 def test_missing_import_tools_stop_before_browser_or_session_changes(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);destination=root/'native/session.json';destination.parent.mkdir();destination.write_text('{"saved":"previous"}')
   self.login_tools.side_effect=RuntimeError('Missing Xvfb')
   with patch.object(accounts,'HOME',root),patch.object(accounts,'open_window') as browser,patch.object(accounts.subprocess,'run') as run,patch.object(accounts,'Station') as station:
    with self.assertRaisesRegex(RuntimeError,'Xvfb'):accounts.login(state.defaults('native'),change=True)
   browser.assert_not_called();run.assert_not_called();station.assert_not_called()
   self.assertEqual(destination.read_text(),'{"saved":"previous"}')
   self.assertEqual(list(destination.parent.iterdir()),[destination])

 def test_bridge_readiness_rejects_challenge_and_login_titles(self):
  for title in ['Just a moment... - Chromium','Log in - ChatGPT - Chromium','Один момент…','']:
   self.assertFalse(login_browser.login_title_ready(title))
  self.assertTrue(login_browser.login_title_ready('ChatGPT - Chromium'))
  self.assertTrue(login_browser.login_title_ready('Test conversation - ChatGPT - Chromium'))

 def test_bridge_waits_for_page_even_with_an_old_session_cookie(self):
  with patch.object(login_browser,'session_fingerprint',return_value=b'old-cookie'),patch.object(login_browser,'browser_page_ready',return_value=False),patch.object(login_browser,'profile_pid',return_value=42):
   watcher=login_browser.SignInWatcher(Path('/profile'),require_page=True)
   self.assertFalse(watcher());watcher.stable_since=0;self.assertFalse(watcher())

 def test_flattened_chromium_argv_is_identified_by_its_profile_lock(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);profile=root/'profile with spaces';profile.mkdir();(profile/'SingletonLock').symlink_to('owner-host-42')
   process=root/'proc/42';process.mkdir(parents=True);executable=root/'chromium';executable.touch();(process/'exe').symlink_to(executable)
   (process/'cmdline').write_bytes(('chromium --user-data-dir='+str(profile)+' --no-first-run\0').encode())
   with patch.object(login_browser,'PROC',root/'proc'):self.assertEqual(login_browser.profile_pid(profile),42)
   (process/'cmdline').write_bytes(b'chromium --user-data-dir=/some/other/profile\0')
   with patch.object(login_browser,'PROC',root/'proc'):self.assertIsNone(login_browser.profile_pid(profile))

 def test_close_signals_only_the_selected_profile_owner(self):
  with patch.object(login_browser,'profile_pid',side_effect=[42,None]),patch.object(login_browser.os,'kill') as kill:
   login_browser.close_profile(Path('/test/dedicated'))
  kill.assert_called_once_with(42,signal.SIGTERM)

 def test_visible_login_has_no_debugging_or_automation_flags(self):
  with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'DISPLAY':':1'}),patch.object(login_browser,'profile_pid',return_value=None),patch.object(login_browser.shutil,'which',return_value='/usr/bin/chromium'),patch.object(login_browser.time,'sleep'),patch.object(login_browser.subprocess,'Popen') as launch:
   launch.return_value.poll.return_value=None
   login_browser.open_window(Path(folder)/'browser','socks5://127.0.0.1:32123')
   argv=launch.call_args.args[0]
   self.assertFalse(any('remote-debugging' in argument or 'enable-automation' in argument or 'disable-web-security' in argument for argument in argv))
   self.assertIn('--proxy-server=socks5://127.0.0.1:32123',argv)
   self.assertEqual(argv[-1],'https://chatgpt.com/auth/login')

 def test_cancelled_login_never_starts_import_or_touches_existing_session(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);destination=root/'native/session.json';destination.parent.mkdir();destination.write_text('{"saved":"previous"}')
   config=state.defaults('native');config['host']='192.0.2.44'
   with patch.object(accounts,'HOME',root),patch.object(accounts,'open_window'),patch.object(ui,'note'),patch.object(ui,'wait_until',side_effect=ui.Back),patch.object(accounts,'close_profile') as close,patch.object(accounts.subprocess,'run') as run,patch.object(accounts,'Station') as station:
    with self.assertRaises(ui.Back):accounts.login(config)
   self.assertEqual(destination.read_text(),'{"saved":"previous"}');run.assert_not_called();station.assert_not_called();close.assert_not_called()

 def test_detected_login_imports_automatically_after_browser_closed(self):
  events=[]
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);config=state.defaults('native');config['host']='192.0.2.44'
   def imported(argv,**kwargs):
    events.append('import');self.assertNotIn('--interactive',argv)
    Path(argv[argv.index('--out')+1]).write_text('{"fake":"candidate"}')
    return subprocess.CompletedProcess(argv,0,stdout=b'',stderr=b'')
   with patch.object(accounts,'HOME',root),patch.object(accounts,'open_window',side_effect=lambda *a:events.append('browser')),patch.object(accounts,'close_profile',side_effect=lambda *a:events.append('closed')),patch.object(ui,'note'),patch.object(ui,'wait_until',side_effect=lambda *a:events.append('detected')),patch.object(ui,'confirm') as confirm,patch.object(accounts.subprocess,'run',side_effect=imported),patch.object(accounts,'install_session',side_effect=lambda *a:events.append('validated')):
    accounts.login(config)
   self.assertEqual(events,['browser','detected','closed','import','validated']);confirm.assert_not_called()
   self.assertFalse(list((root/'native').glob('.login-candidate-*')))

 def test_bridge_login_pauses_controller_until_profile_is_saved(self):
  events=[]
  with tempfile.TemporaryDirectory() as folder:
   config=state.defaults('bridge');config['host']='192.0.2.44'
   with patch.object(accounts,'HOME',Path(folder)),patch.object(accounts,'bridge_env',return_value=({},'fake')),patch.object(accounts,'status',return_value={'phase':'running'}),patch.object(accounts,'stop',side_effect=lambda c:events.append('stop')),patch('alisa_cli.runtime.start',side_effect=lambda c:events.append('restart')),patch.object(accounts,'open_window',side_effect=lambda *a:events.append('browser')),patch.object(accounts,'close_profile',side_effect=lambda *a:events.append('closed')),patch.object(ui,'note'),patch.object(ui,'wait_until'),patch.object(accounts.subprocess,'run',side_effect=lambda *a,**k:events.append('verified') or subprocess.CompletedProcess([],0)):
    accounts.login(config)
  self.assertEqual(events,['stop','browser','closed','verified','closed','restart'])

 def test_rejected_candidate_keeps_local_session_and_live_account(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);candidate=root/'candidate.json';candidate.write_text('{"account_id":"candidate"}')
   destination=root/'session.json';destination.write_text('{"saved":"previous"}')
   config=state.defaults('native');config['host']='192.0.2.44'
   with patch.object(accounts,'Station') as station,patch.object(accounts,'stop') as stop,patch.object(accounts,'archive_settings') as archive:
    station.return_value.run.return_value=subprocess.CompletedProcess([],1,stdout=b'',stderr=b'')
    with self.assertRaisesRegex(RuntimeError,'прежняя сессия сохранена'):accounts.install_session(config,candidate,destination)
    self.assertEqual(station.return_value.put.call_count,1)
    self.assertTrue(station.return_value.put.call_args.args[0].endswith('auth-check/web-session.json'))
    stop.assert_not_called();archive.assert_not_called()
   self.assertEqual(destination.read_text(),'{"saved":"previous"}')

 def test_cookie_readiness_ignores_expired_sessions_and_other_hosts(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);database=root/'Default/Cookies';database.parent.mkdir()
   with sqlite3.connect(database) as connection:
    connection.execute('CREATE TABLE cookies(host_key TEXT,name TEXT,encrypted_value BLOB,value TEXT,expires_utc INTEGER)')
    connection.executemany('INSERT INTO cookies VALUES (?,?,?,?,?)',[
        ('chatgpt.com','__Secure-authjs.session-token',b'expired','',1),
        ('elsewhere.example','__Secure-authjs.session-token',b'other','',0)])
   self.assertIsNone(login_browser.session_fingerprint(root))
   with sqlite3.connect(database) as connection:connection.execute('INSERT INTO cookies VALUES (?,?,?,?,?)',('chatgpt.com','__Secure-authjs.session-token.0',b'encrypted-test','',0))
   value=login_browser.session_fingerprint(root)
   self.assertIsInstance(value,bytes);self.assertEqual(len(value),32);self.assertNotIn(b'encrypted-test',value)

 def test_cookie_chunks_must_settle_before_automatic_import(self):
  with patch.object(login_browser.time,'monotonic',side_effect=[0,0,1,1.5,3,3.6]),patch.object(login_browser,'session_fingerprint',side_effect=[b'a',b'a',b'b',b'b',b'b']):
   ready=login_browser.SignInWatcher(Path('/profile'))
   self.assertEqual([ready() for _ in range(5)],[False,False,False,False,True])
