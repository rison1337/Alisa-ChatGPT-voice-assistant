"""Pinned SSH transport for Windows. Passwords are kept only in memory."""
import base64,hashlib,select,socket,subprocess,threading,time
from pathlib import Path

def library():
    try:import paramiko;return paramiko
    except ImportError:raise RuntimeError('Неполный Windows-пакет: отсутствует Paramiko. Повторите установку CLI.') from None

def host_id(config):
    return config['host'] if config.get('port',2222)==22 else '['+config['host']+']:'+str(config.get('port',2222))

def known_keys(path,endpoint):
    p=library();keys=p.HostKeys()
    if Path(path).exists():
        try:keys.load(str(path))
        except (OSError,ValueError,p.SSHException):raise RuntimeError('Не удалось прочитать сохранённые SSH-ключи. Файл не изменён.') from None
    return keys.lookup(endpoint) or {}

def trust(config,confirm):
    from .state import HOME,write_private
    p=library();transport=None;endpoint=host_id(config)
    known=Path(config.get('known_hosts') or HOME/'ssh/known_hosts')
    trusted={}
    for file in (Path.home()/'.ssh/known_hosts',known):trusted.update(known_keys(file,endpoint))
    try:
        transport=p.Transport(socket.create_connection((config['host'],config.get('port',2222)),timeout=8))
        if trusted:
            options=transport.get_security_options();options.key_types=tuple(trusted)+tuple(k for k in options.key_types if k not in trusted)
        transport.start_client(timeout=8);key=transport.get_remote_server_key()
    except (OSError,p.SSHException):raise RuntimeError('SSH не отвечает. Проверьте IP и порт колонки.') from None
    finally:
        if transport:transport.close()
    if trusted:
        saved=trusted.get(key.get_name())
        if saved is None or saved.asbytes()!=key.asbytes():raise RuntimeError('SSH-ключ по этому адресу изменился. Сохранённый ключ не заменён.')
    else:
        fingerprint='SHA256:'+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
        if not confirm(config['host'],config.get('port',2222),key.get_name()+' '+fingerprint):raise RuntimeError('SSH-ключ не подтверждён. Настройки не изменены.')
    line=(endpoint+' '+key.get_name()+' '+key.get_base64()+'\n').encode()
    previous=known.read_bytes() if known.exists() else b''
    if line.rstrip() not in previous.splitlines():write_private(known,previous.rstrip(b'\n')+b'\n'+line)
    config['known_hosts']=str(known)

def connect(config,password=None):
    p=library();client=p.SSHClient();known=config.get('known_hosts')
    if not known or not Path(known).is_file():raise RuntimeError('Сначала подтвердите SSH-ключ колонки через настройку подключения.')
    client.load_host_keys(str(known));client.set_missing_host_key_policy(p.RejectPolicy())
    try:
        identity=config.get('identity') if password is None else None
        client.connect(config['host'],port=config.get('port',2222),username=config.get('user') or 'root',
                       password=password,key_filename=str(identity) if identity else None,
                       allow_agent=False,look_for_keys=False,timeout=8,banner_timeout=12,auth_timeout=12)
        client.get_transport().set_keepalive(5);return client
    except p.BadHostKeyException:client.close();raise RuntimeError('SSH-ключ колонки изменился. Подключение остановлено.') from None
    except p.AuthenticationException:client.close();raise RuntimeError('SSH-доступ отклонён. Проверьте пароль или сохранённый ключ.') from None
    except (OSError,p.SSHException):client.close();raise RuntimeError('Не удалось подключиться по SSH. Проверьте адрес, порт и сеть.') from None

def run(config,command,data=None,check=True,timeout=60,password=None):
    client=connect(config,password);channel=None
    try:
        channel=client.get_transport().open_session(timeout=8);channel.settimeout(timeout);channel.exec_command(command)
        # Send and drain concurrently: neither a large upload nor remote output
        # can deadlock the SSH channel window.
        failures=[]
        def send():
            try:
                if data:channel.sendall(data)
                channel.shutdown_write()
            except Exception as error:failures.append(error)
        sender=threading.Thread(target=send,daemon=True);sender.start()
        out=bytearray();err=bytearray();deadline=time.monotonic()+timeout
        while True:
            if channel.recv_ready():out.extend(channel.recv(65536))
            if channel.recv_stderr_ready():err.extend(channel.recv_stderr(65536))
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():break
            if time.monotonic()>deadline:raise subprocess.TimeoutExpired('SSH',timeout)
            if len(out)+len(err)>256*1024*1024:raise RuntimeError('Ответ SSH превышает допустимый размер.')
            time.sleep(.002)
        sender.join(timeout=2)
        if sender.is_alive() or failures:raise RuntimeError('Передача SSH прервана; исходный файл сохранён.')
        result=subprocess.CompletedProcess(['SSH',config['host']],channel.recv_exit_status(),bytes(out),bytes(err))
        if check and result.returncode:raise RuntimeError('Команда на колонке завершилась с кодом '+str(result.returncode))
        return result
    finally:
        if channel:channel.close()
        client.close()

def install_key(config,password):
    from .state import HOME,write_private,save
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization as s
    import shlex
    suffix=hashlib.sha256(f'{config["user"]}@{config["host"]}:{config["port"]}'.encode()).hexdigest()[:16]
    path=HOME/'ssh'/('id_ed25519_'+suffix)
    existed=path.exists()
    if password is None and not config.get('identity') and not existed:
        raise RuntimeError('Для первого входа введите пароль SSH или укажите существующий ключ через --identity.')
    if not path.exists():
        key=Ed25519PrivateKey.generate()
        write_private(path,key.private_bytes(s.Encoding.PEM,s.PrivateFormat.OpenSSH,s.NoEncryption()))
    key=library().Ed25519Key.from_private_key_file(str(path));public=key.get_name()+' '+key.get_base64()
    command='set -eu; [ "$(id -u)" = 0 ] || exit 9; umask 077; '
    command+='if [ -d /data/opt/etc/dropbear ] && pidof dropbear >/dev/null 2>&1; then f=/data/opt/etc/dropbear/authorized_keys; '
    command+='else mkdir -p "$HOME/.ssh"; f="$HOME/.ssh/authorized_keys"; fi; '
    command+='touch "$f"; chmod 600 "$f"; grep -qFx '+shlex.quote(public)+' "$f" || printf "\\n%s\\n" '+shlex.quote(public)+' >> "$f"'
    candidate={**config,'identity':str(path)}
    authentication=config if password is not None or config.get('identity') else candidate
    result=run(authentication,command,password=password,check=False)
    if result.returncode==9:raise RuntimeError('SSH работает, но у пользователя нет прав root.')
    if result.returncode:raise RuntimeError('Не удалось установить отдельный SSH-ключ.')
    if run(candidate,'id -u').stdout.strip()!=b'0':raise RuntimeError('Вход отдельным ключом не подтверждён. Настройки не сохранены.')
    config.update(candidate);save(config);return {'ssh':True,'root':True,'key_saved':True}

class Stream:
    def __init__(self,config,command):
        self.client=connect(config);self.channel=self.client.get_transport().open_session(timeout=8)
        self.channel.exec_command(command);self.stdin=self.channel.makefile_stdin('wb')
    def poll(self):return self.channel.recv_exit_status() if self.channel.exit_status_ready() else None
    def wait(self,timeout=4):
        deadline=time.monotonic()+timeout
        while self.poll() is None:
            if time.monotonic()>deadline:raise subprocess.TimeoutExpired('SSH stream',timeout)
            time.sleep(.02)
        value=self.poll();self.client.close();return value
    def terminate(self):self.channel.close();self.client.close()

class Forward:
    def __init__(self,config,port):
        self.client=connect(config);self.stop=threading.Event();self.workers=[]
        self.server=socket.socket();self.server.bind(('127.0.0.1',port));self.server.listen();self.server.settimeout(.2)
        self.thread=threading.Thread(target=self.accept,daemon=True);self.thread.start()
    def accept(self):
        while not self.stop.is_set():
            try:connection,address=self.server.accept()
            except socket.timeout:continue
            except OSError:break
            worker=threading.Thread(target=self.relay,args=(connection,address),daemon=True);self.workers.append(worker);worker.start()
    def relay(self,connection,address):
        channel=None
        try:
            channel=self.client.get_transport().open_channel('direct-tcpip',('127.0.0.1',18081),address,timeout=8)
            while not self.stop.is_set():
                ready,_,_=select.select([connection,channel],[],[],.2)
                for source in ready:
                    data=source.recv(65536)
                    if not data:return
                    (channel if source is connection else connection).sendall(data)
        except (OSError,EOFError,library().SSHException):pass
        finally:
            connection.close()
            if channel:channel.close()
    def poll(self):
        transport=self.client.get_transport()
        return None if not self.stop.is_set() and transport is not None and transport.is_active() else 1
    def terminate(self):self.stop.set();self.server.close();self.client.close()
    def wait(self,timeout=5):
        self.thread.join(timeout)
        for worker in self.workers:worker.join(.3)
        return 0
