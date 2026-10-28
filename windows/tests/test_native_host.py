"""Cross-platform host tests: real loopback SSH, file integrity and host pins."""
import contextlib,hashlib,importlib.util,json,os,socket,subprocess,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
import paramiko
from alisa_cli import winssh
from alisa_cli.ssh import Station
from alisa_cli.state import defaults
from alisa_cli.win_browser import CDP,endpoint

HOST_KEY=paramiko.RSAKey.generate(2048)

class Server(paramiko.ServerInterface):
    def __init__(self):self.commands={};self.forwards={}
    def check_auth_password(self,user,password):return paramiko.AUTH_SUCCESSFUL if user=='root' and password=='synthetic-only-password' else paramiko.AUTH_FAILED
    def get_allowed_auths(self,user):return 'password'
    def check_channel_request(self,kind,channel):return paramiko.OPEN_SUCCEEDED if kind=='session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
    def check_channel_exec_request(self,channel,command):self.commands[channel.get_id()]=command.decode();return True
    def check_channel_direct_tcpip_request(self,channel,origin,destination):
        self.forwards[channel]=destination;return paramiko.OPEN_SUCCEEDED

class Peer:
    def __init__(self):
        self.socket=socket.socket();self.socket.bind(('127.0.0.1',0));self.socket.listen();self.socket.settimeout(.2)
        self.port=self.socket.getsockname()[1];self.done=threading.Event();self.clients=[];self.errors=[]
        self.thread=threading.Thread(target=self.accept,daemon=True);self.thread.start()
    def accept(self):
        while not self.done.is_set():
            try:client,_=self.socket.accept()
            except socket.timeout:continue
            except OSError:return
            thread=threading.Thread(target=self.serve,args=(client,),daemon=True);thread.start()
    def serve(self,client):
        transport=paramiko.Transport(client);self.clients.append(transport);transport.add_server_key(HOST_KEY);server=Server()
        try:
            transport.start_server(server=server);channel=transport.accept(10)
            if channel is None:return
            if channel.get_id() in server.forwards:
                while True:
                    data=channel.recv(65536)
                    if not data:break
                    channel.sendall(data)
                channel.close();return
            deadline=time.monotonic()+5
            while channel.get_id() not in server.commands and time.monotonic()<deadline:time.sleep(.01)
            command=server.commands.get(channel.get_id(),'');data=bytearray()
            while True:
                part=channel.recv(65536)
                if not part:break
                data.extend(part)
            if command=='digest':channel.sendall(hashlib.sha256(data).hexdigest().encode())
            elif command=='large':channel.sendall(bytes(range(256))*8192)
            elif command=='fail':channel.send_stderr(b'private remote details');channel.send_exit_status(7);channel.close();return
            else:channel.sendall(bytes(data))
            channel.send_exit_status(0);channel.close()
        except (EOFError,OSError,paramiko.SSHException):pass
        finally:
            # Match a real SSH server: drain the channel-close exchange before
            # closing TCP, otherwise unread acknowledgements cause a reset.
            while transport.is_active() and not self.done.wait(.01):pass
            transport.close()
    def close(self):
        self.done.set();self.socket.close();self.thread.join(1)
        for client in self.clients:client.close()

class SSHTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.peer=Peer();self.folder=Path(self.temp.name)
        self.config=defaults('native');self.config.update(host='127.0.0.1',port=self.peer.port,user='root',known_hosts=str(self.folder/'known_hosts'))
        self.pin=winssh.host_id(self.config)+' '+HOST_KEY.get_name()+' '+HOST_KEY.get_base64()+'\n'
        (self.folder/'known_hosts').write_text(self.pin)
    def tearDown(self):self.peer.close();self.temp.cleanup()
    def test_real_ssh_roundtrip_large_upload_has_exact_digest(self):
        data=bytes(range(256))*16384
        result=winssh.run(self.config,'digest',data,password='synthetic-only-password')
        self.assertEqual(result.stdout,hashlib.sha256(data).hexdigest().encode());self.assertEqual(result.returncode,0)
    def test_real_ssh_large_download_is_not_truncated(self):
        result=winssh.run(self.config,'large',password='synthetic-only-password')
        self.assertEqual(result.stdout,bytes(range(256))*8192)
    def test_remote_failure_does_not_expose_remote_details(self):
        with self.assertRaisesRegex(RuntimeError,'кодом 7') as captured:winssh.run(self.config,'fail',password='synthetic-only-password')
        self.assertNotIn('private remote',str(captured.exception))
    def test_wrong_password_is_sanitized(self):
        with self.assertRaises(RuntimeError) as captured:winssh.run(self.config,'echo',password='do-not-log-password')
        self.assertNotIn('do-not-log-password',str(captured.exception))
    def test_changed_host_key_is_rejected(self):
        other=paramiko.RSAKey.generate(2048)
        (self.folder/'known_hosts').write_text(winssh.host_id(self.config)+' '+other.get_name()+' '+other.get_base64()+'\n')
        with self.assertRaisesRegex(RuntimeError,'изменился'):winssh.run(self.config,'echo',password='synthetic-only-password')
    def test_declining_new_host_does_not_write_pin(self):
        (self.folder/'known_hosts').unlink()
        with patch('pathlib.Path.home',return_value=self.folder):
            with self.assertRaisesRegex(RuntimeError,'не подтверждён'):winssh.trust(self.config,lambda *args:False)
        self.assertFalse((self.folder/'known_hosts').exists())
    def test_trust_pin_matches_real_handshake_and_is_reused(self):
        (self.folder/'known_hosts').unlink();confirmation=[]
        with patch('pathlib.Path.home',return_value=self.folder):winssh.trust(self.config,lambda *args:confirmation.append(args) or True)
        self.assertEqual((self.folder/'known_hosts').read_text().strip(),self.pin.strip());self.assertEqual(len(confirmation),1)
        with patch('pathlib.Path.home',return_value=self.folder):winssh.trust(self.config,lambda *args:self.fail('Existing pin must not ask again'))
    def test_station_adapter_uses_windows_transport(self):
        # Pinning, integrity and command generation remain shared with Linux.
        result=subprocess.CompletedProcess([],0,b'0\n',b'')
        with patch('alisa_cli.ssh.sys.platform','win32'),patch.object(winssh,'run',return_value=result) as run:
            self.assertEqual(Station(self.config).text('id -u'),'0\n');run.assert_called_once()
    def test_loopback_forward_relays_data_and_closes_its_listener(self):
        connection=winssh.connect(self.config,'synthetic-only-password')
        with patch.object(winssh,'connect',return_value=connection):tunnel=winssh.Forward(self.config,0)
        port=tunnel.server.getsockname()[1]
        try:
            with socket.create_connection(('127.0.0.1',port),timeout=3) as client:
                client.sendall(b'synthetic proxy traffic');self.assertEqual(client.recv(128),b'synthetic proxy traffic')
            self.assertIsNone(tunnel.poll())
        finally:tunnel.terminate();tunnel.wait(timeout=3)
        self.assertIsNotNone(tunnel.poll())
        with self.assertRaises(OSError):socket.create_connection(('127.0.0.1',port),timeout=.3)

class BrowserAndPackageTests(unittest.TestCase):
    def test_reinstall_keeps_state_unknown_files_and_previous_copy(self):
        source=Path(__file__).resolve().parents[1]/'install.py'
        spec=importlib.util.spec_from_file_location('windows_installer_updates',source);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);root=folder/'package';root.mkdir();local=folder/'local'
            def package(content):
                (root/'app.py').write_bytes(content)
                (root/'windows-package.json').write_text(json.dumps({'package':module.PACKAGE,'files':{'app.py':hashlib.sha256(content).hexdigest()}}))
            with patch.object(module,'ROOT',root),patch.object(module,'private_directory',side_effect=lambda path:Path(path).mkdir(parents=True,exist_ok=True)),patch('sys.platform','win32'),patch('sys.argv',['install.py','--no-run']),patch.dict(os.environ,{'LOCALAPPDATA':str(local)}):
                package(b'first');self.assertEqual(module.main(),0)
                base=local/'AlisaVoice';state=base/'state';state.mkdir();(state/'keep.txt').write_text('saved state')
                (base/'app'/'unknown.txt').write_text('user file')
                package(b'second');self.assertEqual(module.main(),0)
                self.assertEqual((base/'app.previous'/'unknown.txt').read_text(),'user file')
                package(b'third');self.assertEqual(module.main(),0)
                self.assertEqual((base/'app.previous'/'unknown.txt').read_text(),'user file')
                self.assertEqual((state/'keep.txt').read_text(),'saved state')
                self.assertEqual(len(list(base.glob('app.previous-*'))),1)
                self.assertEqual((base/'app'/'app.py').read_bytes(),b'third')
                self.assertEqual(module.main(),0);self.assertEqual(len(list(base.glob('app.previous-*'))),1)
    def test_existing_identity_authorizes_new_key_before_it_is_used(self):
        from alisa_cli import state
        with tempfile.TemporaryDirectory() as temp,patch.object(state,'HOME',Path(temp)):
            config=defaults('native');config.update(host='192.0.2.1',identity=str(Path(temp)/'existing-key'))
            calls=[]
            def execute(connection,command,**options):
                calls.append(dict(connection));return subprocess.CompletedProcess([],0,b'0\n',b'')
            with patch.object(winssh,'run',side_effect=execute):winssh.install_key(config,None)
            self.assertTrue(calls[0]['identity'].endswith('existing-key'))
            self.assertNotEqual(calls[1]['identity'],calls[0]['identity']);self.assertTrue(Path(config['identity']).exists())
            self.assertNotIn('password',state.load('native'))
    def test_import_accepts_only_fresh_local_browser_endpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'DevToolsActivePort';path.write_text('12345\n/devtools/browser/1234-test\n')
            self.assertEqual(endpoint(temp,time.time()),'ws://127.0.0.1:12345/devtools/browser/1234-test')
            for raw in ['0\n/devtools/browser/test','123\n//example.com','123\n/devtools/browser/a?x=y','not-a-port']:
                path.write_text(raw);self.assertIsNone(endpoint(temp,time.time()))
            path.write_text('12345\n/devtools/browser/test');os.utime(path,(1,1));self.assertIsNone(endpoint(temp,time.time()))
    def test_debugger_never_connects_to_remote_address(self):
        for url in ('ws://192.0.2.1:123/devtools/browser/x','ws://localhost:123/devtools/browser/x','wss://127.0.0.1:123/x'):
            with self.assertRaises(RuntimeError):CDP(url)
    def test_installer_rejects_corruption_and_traversal(self):
        source=Path(__file__).resolve().parents[1]/'install.py'
        spec=importlib.util.spec_from_file_location('windows_installer',source);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'app.py').write_bytes(b'good')
            value={'package':module.PACKAGE,'files':{'app.py':hashlib.sha256(b'good').hexdigest()}}
            (root/'windows-package.json').write_text(json.dumps(value));self.assertEqual(module.validate(root),value)
            (root/'app.py').write_bytes(b'bad')
            with self.assertRaises(RuntimeError):module.validate(root)
            value['files']={'../escape':'0'*64};(root/'windows-package.json').write_text(json.dumps(value))
            with self.assertRaises(RuntimeError):module.validate(root)

if __name__=='__main__':unittest.main()
