"""First-time SSH setup without passwords in argv, environment or files."""
import contextlib,hashlib,ipaddress,os,shlex,socket,subprocess,sys,tempfile,threading
from pathlib import Path
from .state import HOME,save,write_private,validate
from .ssh import Station

def missing_tools():
    if sys.platform=='win32':
        try:from .winssh import library;library();return []
        except RuntimeError:return ['Windows SSH libraries']
    return [tool for tool in ('ssh','ssh-keygen','ssh-keyscan') if not __import__('shutil').which(tool)]

def ip_valid(value):
    try:ipaddress.ip_address(value);return True
    except ValueError:return 'Введите IP колонки, например 192.168.1.50'

def port_valid(value):
    return True if value.isdecimal() and 1<=int(value)<=65535 else 'Порт должен быть от 1 до 65535'

def remembered_address(config):
    host=config.get('host') or ''
    if ip_valid(host) is True:return host,config.get('port') or 2222
    if host:
        args=Station(config).args();args.insert(1,'-G')
        result=subprocess.run(args,capture_output=True,timeout=10)
        fields=dict(line.split(' ',1) for line in result.stdout.decode().splitlines() if ' ' in line)
        if ip_valid(fields.get('hostname','')) is True:return fields['hostname'],int(fields.get('port',2222))
    return '',2222

def _keys(raw):
    result=set()
    for line in raw.decode(errors='replace').splitlines():
        if line.startswith('#'):continue
        parts=line.split()
        if parts and parts[0]=='@revoked':raise RuntimeError('SSH-ключ колонки помечен как отозванный. Подключение остановлено.')
        if len(parts)>=3 and parts[1].startswith(('ssh-','ecdsa-')):result.add((parts[1],parts[2]))
    return result

def trust_host(config,confirm):
    """TOFU only for a new endpoint; existing pins may never be replaced."""
    if sys.platform=='win32':
        from .winssh import trust
        return trust(config,confirm)
    ip=config['host'];port=config['port'];endpoint=ip if port==22 else f'[{ip}]:{port}'
    scan=subprocess.run(['ssh-keyscan','-T','5','-p',str(port),ip],capture_output=True,timeout=18)
    offered=_keys(scan.stdout)
    if not offered:raise RuntimeError('SSH не отвечает. Проверьте IP, порт и доступ компьютера к колонке.')
    known=Path(config.get('known_hosts') or HOME/'ssh/known_hosts')
    trusted=set()
    for path in {known,Path.home()/'.ssh/known_hosts',Path('/etc/ssh/ssh_known_hosts')}:
        if path.is_file():
            found=subprocess.run(['ssh-keygen','-F',endpoint,'-f',str(path)],capture_output=True,timeout=10)
            trusted.update(_keys(found.stdout))
    if trusted:
        offered &= trusted
        if not offered:raise RuntimeError('SSH-ключ по этому адресу изменился. Сохранённый ключ не заменён; проверьте IP и отпечаток колонки.')
    else:
        # Confirm and pin one key, just as an initial OpenSSH connection does;
        # a second, unverified algorithm must not be trusted implicitly.
        priority=lambda item:(0 if item[0]=='ssh-ed25519' else 1 if item[0].startswith('ecdsa-') else 2,item)
        offered={min(offered,key=priority)}
        lines=''.join(f'{endpoint} {kind} {key}\n' for kind,key in sorted(offered)).encode()
        fingerprints=subprocess.run(['ssh-keygen','-lf','-','-E','sha256'],input=lines,capture_output=True,check=True).stdout.decode()
        if not confirm(ip,port,fingerprints):raise RuntimeError('SSH-ключ не подтверждён. Настройки подключения не изменены.')
    existing=known.read_bytes() if known.exists() else b''
    additions=b''
    for kind,key in sorted(offered):
        line=f'{endpoint} {kind} {key}\n'.encode()
        if line.rstrip() not in existing.splitlines():additions+=line
    if additions:write_private(known,existing.rstrip(b'\n')+b'\n'+additions)
    config['known_hosts']=str(known)

@contextlib.contextmanager
def password_channel(password):
    """OpenSSH askpass helper retrieves the password over a private socket."""
    if not password or '\n' in password or '\r' in password or '\x00' in password:raise ValueError('Некорректный пароль SSH')
    if sys.platform!='linux':raise RuntimeError('Вход по паролю в этой сборке проверяется на Linux')
    with tempfile.TemporaryDirectory(prefix='alisa-ssh-') as folder:
        path=Path(folder)/'askpass.sock';helper=Path(folder)/'askpass'
        code="import os,socket; s=socket.socket(socket.AF_UNIX); s.connect(os.environ['ALISA_ASKPASS_SOCKET']); s.sendall(b'password\\n'); data=b''\nwhile not data.endswith(b'\\n'):\n chunk=s.recv(65536)\n if not chunk: raise SystemExit(1)\n data+=chunk\nos.write(1,data); s.close()"
        helper.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' -c '+shlex.quote(code)+'\n');helper.chmod(0o700)
        stop=threading.Event()
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(path));path.chmod(0o600);server.listen(1);server.settimeout(.1)
            def serve():
                while not stop.is_set():
                    try:connection,_=server.accept()
                    except socket.timeout:continue
                    except OSError:break
                    with connection:
                        connection.settimeout(2)
                        try:
                            if connection.recv(64)==b'password\n':connection.sendall(password.encode()+b'\n')
                        except OSError:pass
            thread=threading.Thread(target=serve,daemon=True);thread.start()
            try:
                yield {**os.environ,'SSH_ASKPASS':str(helper),'SSH_ASKPASS_REQUIRE':'force',
                       'ALISA_ASKPASS_SOCKET':str(path),'DISPLAY':os.environ.get('DISPLAY') or ':0'}
            finally:stop.set();thread.join(timeout=3)

def install_key(config,password=None):
    validate(config)
    if sys.platform=='win32':
        from .winssh import install_key as install
        return install(config,password)
    folder=HOME/'ssh';folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    suffix=hashlib.sha256(f'{config["user"]}@{config["host"]}:{config["port"]}'.encode()).hexdigest()[:16]
    key=folder/('id_ed25519_'+suffix)
    if not key.exists():subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],capture_output=True,check=True)
    public=key.with_suffix('.pub').read_text(encoding='utf-8').strip()
    command='set -eu; [ "$(id -u)" = 0 ] || exit 9; umask 077; '
    command+='if [ -d /data/opt/etc/dropbear ] && pidof dropbear >/dev/null 2>&1; then f=/data/opt/etc/dropbear/authorized_keys; '
    command+='else mkdir -p "$HOME/.ssh"; f="$HOME/.ssh/authorized_keys"; fi; '
    command+='touch "$f"; chmod 600 "$f"; grep -qFx '+shlex.quote(public)+' "$f" || printf "\\n%s\\n" '+shlex.quote(public)+' >> "$f"'
    station=Station(config)
    if password is not None:
        args=station.args(interactive=True)
        args=args[:-1]+['-o','PreferredAuthentications=password','-o','PubkeyAuthentication=no','-o','NumberOfPasswordPrompts=1',
                       '-o','ControlMaster=no','-o','ControlPath=none',args[-1]]
        with password_channel(password) as env:
            result=subprocess.run(args+[command],env=env,capture_output=True,timeout=60)
        if result.returncode==9:raise RuntimeError('SSH работает, но у пользователя нет прав root')
        if result.returncode:raise RuntimeError('Вход по SSH не удался. Проверьте пароль; сервер должен разрешать вход по паролю.')
    else:station.run(command)
    checked={**config,'identity':str(key)}
    if Station(checked).text('id -u').strip()!='0':raise RuntimeError('Вход новым SSH-ключом не подтверждён. Настройки не сохранены.')
    config.update(checked);save(config)
    return {'ssh':True,'root':True,'key_saved':True}

def form(config,ui,allow_saved=False):
    """No discovery: all connection details come from the owner."""
    missing=missing_tools()
    if missing:raise RuntimeError('Не хватает компонентов SSH: '+', '.join(missing))
    if allow_saved and config.get('ssh_explicit') and config.get('identity') and Path(config['identity']).is_file():
        endpoint=f'{config.get("user") or "root"}@{config["host"]}:{config.get("port") or 2222}'
        action=ui.select('Подключение к колонке',[(f'Использовать сохранённое: {endpoint}','reuse'),('Изменить IP, порт или пароль','edit')],default='reuse')
        if action=='reuse':
            if Station(config).text('id -u').strip()!='0':raise RuntimeError('Сохранённое подключение не даёт права root. Измените данные подключения.')
            ui.note('✓ Сохранённое SSH-подключение работает.','green')
            return {'ssh':True,'root':True,'key_saved':True}
    address,port=remembered_address(config)
    candidate=dict(config)
    candidate['host']=ui.text('IP-адрес колонки',address,validate=ip_valid)
    candidate['port']=int(ui.text('Порт SSH',str(port),validate=port_valid))
    candidate['user']=ui.text('Пользователь SSH',config.get('user') or 'root',validate=lambda v:True if v=='root' or __import__('re').fullmatch('[a-zA-Z0-9_-]+',v) else 'Введите имя пользователя')
    candidate['ssh_explicit']=True
    # A new endpoint must not silently inherit another column's key or pin file.
    if candidate['host']!=address or candidate['port']!=port:
        candidate['identity']=None
        candidate.pop('model',None)
        candidate['installed']=False
        candidate['autostart']=False
        candidate['vpn']={**config['vpn'],'enabled':False,'required':False,'autostart':False}
    def confirm(ip,port,fingerprints):
        ui.note(f'Новый SSH-сервер: {ip}:{port}','cyan');ui.note(fingerprints.strip(),'muted')
        return ui.confirm('Подтвердить этот отпечаток SSH?',False)
    trust_host(candidate,confirm)
    password=ui.secret('Пароль SSH колонки (Enter — сохранённый ключ)' if sys.platform=='win32' else 'Пароль SSH колонки (Enter — использовать существующий ключ)',optional=True)
    try:result=install_key(candidate,password or None)
    finally:password=None
    config.clear();config.update(candidate)
    ui.note('✓ SSH и root проверены. Для дальнейших запусков сохранён отдельный ключ.','green')
    return result
