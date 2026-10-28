"""Windows session readiness and cancellation must release owned resources."""
import os,sqlite3,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from alisa_cli import accounts,login_browser,state,ui,win_browser,vpn

class BrowserCookieGroupsTests(unittest.TestCase):
    def cookie(self,name,value,domain):return {'name':name,'value':value,'domain':domain,'path':'/'}
    def test_duplicate_domains_never_mix_session_chunks(self):
        prefix='__Secure-next-auth.session-token'
        cookies=[self.cookie(prefix+'.0','old-zero','chatgpt.com'),self.cookie(prefix+'.1','new-one','.chatgpt.com'),
            self.cookie(prefix+'.0','new-zero','.chatgpt.com'),self.cookie(prefix+'.1','old-one','chatgpt.com')]
        self.assertEqual(win_browser.session_cookies(cookies),{prefix+'.0':'new-zero',prefix+'.1':'new-one'})

    def test_incomplete_group_does_not_borrow_another_domains_chunk(self):
        prefix='__Secure-next-auth.session-token'
        cookies=[self.cookie(prefix+'.1','orphan','.chatgpt.com'),self.cookie(prefix+'.0','valid-zero','chatgpt.com'),self.cookie(prefix+'.1','valid-one','chatgpt.com')]
        self.assertEqual(win_browser.session_cookies(cookies),{prefix+'.0':'valid-zero',prefix+'.1':'valid-one'})

    def test_incoherent_or_foreign_sessions_are_rejected(self):
        prefix='__Secure-next-auth.session-token'
        for cookies in ([self.cookie(prefix+'.1','orphan','.chatgpt.com')],
            [self.cookie(prefix,'base','.chatgpt.com'),self.cookie(prefix+'.0','chunk','.chatgpt.com')],
            [self.cookie(prefix,'foreign','elsewhere.example')]):
            with self.assertRaises(RuntimeError):win_browser.session_cookies(cookies)


@unittest.skipUnless(sys.platform=='win32','Windows resource ownership')
class WindowsLoginTests(unittest.TestCase):
    def test_signin_and_import_keep_same_normal_browser_identity(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(win_browser._processes,{},clear=True),patch.object(win_browser,'browser',return_value=Path('synthetic-edge.exe')),patch.object(win_browser,'browser_user_agent',return_value='Synthetic normal browser identity'),patch.object(win_browser,'private_directory',side_effect=lambda path:Path(path).mkdir(parents=True,exist_ok=True)),patch.object(win_browser.time,'sleep'),patch.object(win_browser.subprocess,'Popen') as launch:
            profile=Path(folder)/'profile';launch.return_value.poll.return_value=None
            win_browser.open_window(profile);visible=launch.call_args.args[0]
            win_browser._processes.clear();launch.return_value.poll.return_value=0
            with self.assertRaises(RuntimeError):win_browser.import_session(profile,Path(folder)/'session.json')
            imported=launch.call_args.args[0]
            marker='--user-agent=Synthetic normal browser identity'
            self.assertIn(marker,visible);self.assertIn(marker,imported)
            self.assertIn('--start-maximized',visible)

    def test_visible_and_import_browsers_disable_extensions_and_sync(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(win_browser._processes,{},clear=True),patch.object(win_browser,'browser',return_value=Path('synthetic-edge.exe')),patch.object(win_browser,'private_directory',side_effect=lambda path:Path(path).mkdir(parents=True,exist_ok=True)),patch.object(win_browser.time,'sleep'),patch.object(win_browser.subprocess,'Popen') as launch:
            profile=Path(folder)/'profile';launch.return_value.poll.return_value=None
            win_browser.open_window(profile)
            visible=launch.call_args.args[0]
            self.assertIn('--disable-extensions',visible);self.assertIn('--disable-sync',visible)
            self.assertFalse(any('remote-debugging' in item or 'enable-automation' in item for item in visible))
            win_browser._processes.clear();launch.return_value.poll.return_value=0
            with self.assertRaises(RuntimeError):win_browser.import_session(profile,Path(folder)/'session.json')
            imported=launch.call_args.args[0]
            self.assertIn('--disable-extensions',imported);self.assertIn('--disable-sync',imported)

    def test_xray_zero_exit_without_success_never_reaches_station(self):
        with tempfile.TemporaryDirectory() as folder:
            config=state.defaults('native');config['host']='192.0.2.44'
            selection={'servers':[{}],'rejected':0,'selected':{'_engine':'xray'},'client':'standard'}
            rejected=subprocess.CompletedProcess([],0,b'Failed to load config',b'')
            with patch.object(vpn,'HOME',Path(folder)),patch.object(vpn,'core_config',return_value={}),patch.object(vpn.subprocess,'run',return_value=rejected),patch.object(vpn,'Station') as station:
                with self.assertRaisesRegex(RuntimeError,'отклонил'):vpn.configure(config,'synthetic-source',selection=selection)
            station.assert_not_called()

    def test_locked_cookies_use_owned_signed_in_window_readiness(self):
        with patch.object(login_browser,'session_fingerprint',return_value=None),patch.object(win_browser,'signed_in_page_ready',return_value=True),patch.object(login_browser.time,'monotonic',side_effect=[0,0,1,2.1]):
            watcher=login_browser.SignInWatcher(Path('/synthetic-profile'))
            self.assertEqual([watcher(),watcher(),watcher()],[False,False,True])

    def test_locked_cookies_and_logged_out_window_do_not_finish_login(self):
        with patch.object(login_browser,'session_fingerprint',return_value=None),patch.object(win_browser,'signed_in_page_ready',return_value=False),patch.object(login_browser,'profile_pid',return_value=42):
            self.assertFalse(login_browser.SignInWatcher(Path('/synthetic-profile'))())

    def test_cookie_probe_releases_database_before_returning(self):
        with tempfile.TemporaryDirectory() as folder:
            profile=Path(folder);database=profile/'Default/Network/Cookies'
            database.parent.mkdir(parents=True)
            connection=sqlite3.connect(database)
            try:
                connection.execute('CREATE TABLE cookies(host_key TEXT,name TEXT,encrypted_value BLOB,value TEXT,expires_utc INTEGER)')
                connection.execute('INSERT INTO cookies VALUES (?,?,?,?,?)',('chatgpt.com','__Secure-authjs.session-token',b'synthetic-cookie','',0))
                connection.commit()
            finally:connection.close()
            self.assertIsInstance(login_browser.session_fingerprint(profile),bytes)
            # An open SQLite handle blocks this operation on Windows.
            moved=database.with_name('Cookies.moved')
            os.replace(database,moved);moved.unlink()

    def test_cancelled_login_closes_owned_browser_and_preserves_session(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);destination=root/'native/session.json'
            destination.parent.mkdir();previous=b'{"saved":"synthetic-previous-session"}'
            destination.write_bytes(previous)
            config=state.defaults('native');config['host']='192.0.2.44'
            with patch.object(accounts,'HOME',root),patch.object(accounts,'open_window'),patch.object(ui,'note'),patch.object(ui,'wait_until',side_effect=ui.Back),patch.object(accounts,'close_profile') as close,patch.object(accounts,'install_session') as install:
                with self.assertRaises(ui.Back):accounts.login(config)
            self.assertEqual(destination.read_bytes(),previous)
            close.assert_called_once_with(root/'native/browser-profile')
            install.assert_not_called()
            self.assertFalse(list(root.rglob('.login-candidate-*')))


if __name__=='__main__':unittest.main()
