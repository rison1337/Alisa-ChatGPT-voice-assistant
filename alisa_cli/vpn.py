import copy,hashlib,json,shlex,shutil,subprocess,sys,tempfile,time
from pathlib import Path
from .state import HOME,ROOT,save,write_private
from .ssh import Station
from .runtime import TOOLS,NATIVE,status,stop,start,apply_settings
from .vless import parse_link,fetch_subscription,core_config
from .autostart import station_flag

def select_server(source,index=None,client='standard'):
    if source.strip().startswith('vless://'):servers=[parse_link(source)];rejected=0
    else:servers,rejected=fetch_subscription(source.strip(),client=client)
    if index is None and len(servers)>1:
        from .ui import select
        index=select('Сервер VLESS',[(f'{item.get("_name") or item["server"]} ({item["server_port"]})',i) for i,item in enumerate(servers)])
    index=0 if index is None else index
    if not 0<=index<len(servers):raise ValueError('Номер сервера вне списка')
    selected=servers[index]
    return {'servers':servers,'rejected':rejected,'selected':selected,'client':client}

def configure(config,source,index=None,client='standard',selection=None):
    if config['mode']!='native':raise ValueError('VLESS в первом этапе поддерживается в нативном режиме')
    selection=select_server(source,index,client) if selection is None else selection
    servers=selection['servers'];rejected=selection['rejected'];selected=selection['selected'];client=selection['client']
    engine=selected.get('_engine','sing-box')
    value=core_config(selected)
    # Validate with the exact bundled core before touching the device.
    path=HOME/'native/vless.json'
    if engine=='xray':
        spec=__import__('importlib.util',fromlist=['spec_from_file_location'])
        definition=spec.spec_from_file_location('fetch_xray',ROOT/'scripts/fetch_xray.py');module=spec.module_from_spec(definition);definition.loader.exec_module(module)
        if sys.platform!='win32':module.fetch('linux-amd64')
        module.fetch('linux-armv7')
    core=ROOT/'assets/vpn'/('windows-amd64' if sys.platform=='win32' else 'linux-amd64')/(engine+'.exe' if sys.platform=='win32' else engine)
    if sys.platform=='win32' and not core.exists():raise RuntimeError('Windows-пакет VPN неполный. Повторите установку CLI.')
    if core.exists():
        with tempfile.TemporaryDirectory(prefix='alisa-vless-') as folder:
            candidate=__import__('pathlib').Path(folder)/'config.json';write_private(candidate,value)
            check=subprocess.run([str(core),*(['run','-test'] if engine=='xray' else ['check']),'-c',str(candidate)],capture_output=True)
        if check.returncode or (engine=='xray' and b'Configuration OK' not in check.stdout+check.stderr):
            raise RuntimeError(engine+' отклонил конфигурацию VLESS; параметры сервера не поддерживаются')
    station=Station(config)
    from . import storage
    space=storage.report(config,vpn_engine=engine)
    storage.require(space)
    arm=ROOT/'assets/vpn/linux-armv7'/engine
    if not arm.exists():
        spec=__import__('importlib.util',fromlist=['spec_from_file_location'])
        definition=spec.spec_from_file_location('fetch_vpn',ROOT/'scripts/fetch_vpn.py');module=spec.module_from_spec(definition);definition.loader.exec_module(module)
        arm=module.fetch('linux-armv7')
    data=arm.read_bytes();digest=hashlib.sha256(data).hexdigest()
    current=station.run('sha256sum '+TOOLS+'/'+engine,check=False)
    if current.returncode or current.stdout.decode().split()[0]!=digest:
        station.put(TOOLS+'/'+engine,data,0o700)
    station.put(TOOLS+'/vpn.sh',(ROOT/'station/vpn.sh').read_bytes(),0o700)
    candidate=json.dumps(value).encode()
    station.put(TOOLS+'/vless-candidate.json',candidate)
    station.run(TOOLS+'/'+engine+(' run -test' if engine=='xray' else ' check')+' -c '+TOOLS+'/vless-candidate.json')
    previous=station.run('cat '+TOOLS+'/vless.json',check=False)
    previous_engine=station.run('cat '+TOOLS+'/vpn-engine',check=False)
    before=copy.deepcopy(config)
    same=False
    try:same=previous.returncode==0 and json.loads(previous.stdout)==value and previous_engine.stdout.decode().strip()==engine and config['vpn'].get('enabled',False)
    except (ValueError,UnicodeError):pass
    if same:
        station.run('rm -f '+TOOLS+'/vless-candidate.json')
        write_private(path,value);write_private(HOME/'native/servers.json',servers)
        if not source.strip().startswith('vless://'):write_private(HOME/'native/subscription.txt',source.strip().encode())
        else:(HOME/'native/subscription.txt').unlink(missing_ok=True)
        config['vpn'].update(client=client,engine=engine);save(config)
        return {'configured':True,'servers_available':len(servers),'rejected':rejected,'engine':engine,
                'message':'Этот сервер уже настроен. Файлы проверены; перезапуск GPT и VPN не потребовался.'}
    was_running=status(config).get('agent_connected',False)
    vpn_running=station.run(TOOLS+'/vpn.sh status',check=False).returncode==0
    try:
        if was_running:stop(config)
        if vpn_running:station.run(TOOLS+'/vpn.sh stop')
        station.run('mv '+TOOLS+'/vless-candidate.json '+TOOLS+'/vless.json')
        station.put(TOOLS+'/vpn-engine',(engine+'\n').encode())
        config['vpn'].update({'enabled':True,'required':True})
        apply_settings(config)
        if vpn_running:station.run(TOOLS+'/vpn.sh start')
        if was_running:station.run(NATIVE+'/run.sh start')
        write_private(path,value)
        write_private(HOME/'native/servers.json',servers)
        if not source.strip().startswith('vless://'):write_private(HOME/'native/subscription.txt',source.strip().encode())
        else:(HOME/'native/subscription.txt').unlink(missing_ok=True)
        config['vpn'].update(client=client,engine=engine);save(config)
    except BaseException:
        station.run(TOOLS+'/vpn.sh stop',check=False)
        if previous.returncode==0:station.put(TOOLS+'/vless.json',previous.stdout)
        else:station.run('rm -f '+TOOLS+'/vless.json',check=False)
        if previous_engine.returncode==0:station.put(TOOLS+'/vpn-engine',previous_engine.stdout)
        else:station.run('rm -f '+TOOLS+'/vpn-engine',check=False)
        config.clear();config.update(before);apply_settings(config);save(config)
        if vpn_running:station.run(TOOLS+'/vpn.sh start',check=False)
        if was_running:station.run(NATIVE+'/run.sh start',check=False)
        raise
    finally:station.run('rm -f '+TOOLS+'/vless-candidate.json',check=False)
    # Keep one VPN binary on the small persistent volume. The other pinned
    # binary remains available in the installer if the user changes transport.
    unused='sing-box' if engine=='xray' else 'xray'
    station.run('rm -f '+TOOLS+'/'+unused,check=False)
    return {'configured':True,'servers_available':len(servers),'rejected':rejected,'started':vpn_running,'engine':engine}

def operate(config,action):
    station=Station(config)
    if action=='status':
        configured=station.run('test -s '+TOOLS+'/vless.json',check=False).returncode==0
        return {'configured':configured,'vpn_enabled':config['vpn'].get('enabled',False),
                'running':station.run(TOOLS+'/vpn.sh status',check=False).returncode==0,'autostart':config['vpn'].get('autostart',False)}
    if action in ['start','stop']:
        if action=='start' and not config['vpn'].get('enabled'):raise RuntimeError('Сначала настройте VLESS: vpn configure')
        station.run(TOOLS+'/vpn.sh '+action)
        return operate(config,'status')
    if action=='disable':
        station.run(TOOLS+'/vpn.sh stop',check=False)
        was_running=status(config).get('agent_connected',False)
        if was_running:stop(config)
        config['vpn'].update({'enabled':False,'required':False,'autostart':False})
        station_flag(config,'vpn-autostart',False);apply_settings(config);save(config)
        if was_running:start(config)
        return {'configured':False,'direct_connection_selected':True}
    raise ValueError('Неизвестное действие VPN')

# Only dedicated VPN files; shared boot/mark/native helpers and account state
# belong to the native client and must survive VPN removal.
REMOTE_FILES=('xray','sing-box','vpn.sh','vless.json','vless-candidate.json','vpn-engine','vpn-required','vpn-autostart')
LOCAL_FILES=('vless.json','servers.json','subscription.txt','direct-vless.txt')

def removal_plan(config):
    if config['mode']!='native':raise ValueError('VLESS установлен только в нативном режиме')
    from .storage import remote_sizes
    station=Station(config);paths=[TOOLS+'/'+name for name in REMOTE_FILES]
    check=station.run('for f in '+' '.join(shlex.quote(p) for p in paths)+'; do test ! -L "$f" || exit 8; done',check=False)
    if check.returncode:raise RuntimeError('Файл VPN заменён ссылкой; удаление остановлено')
    remote=remote_sizes(station,paths);local={}
    for name in LOCAL_FILES:
        path=HOME/'native'/name
        if path.is_symlink():raise RuntimeError('Файл VPN заменён ссылкой; удаление остановлено')
        if path.is_file():
            data=path.read_bytes();local[name]=(len(data),hashlib.sha256(data).hexdigest())
    return {'remote':remote,'local':local,'reclaim_bytes':sum(v[0] for v in remote.values()),
            'computer_reclaim_bytes':sum(v[0] for v in local.values())}

def remove(config,reviewed=None):
    from . import autostart,ui
    current=removal_plan(config)
    if reviewed is not None and current!=reviewed:raise RuntimeError('Файлы VPN изменились. Повторите удаление для актуального списка.')
    required=current['reclaim_bytes']+current['computer_reclaim_bytes']+8*1024*1024
    parent=HOME
    while not parent.exists():parent=parent.parent
    if shutil.disk_usage(parent).free<required:raise RuntimeError('Недостаточно места на ПК для резервной копии VPN; удаление не началось')
    station=Station(config);before=copy.deepcopy(config);folder=HOME/'native/vpn-removal-backups'/str(time.time_ns());saved={};local={}
    with ui.activity('Сохраняю копию файлов VLESS'):
        for path,(_,digest) in current['remote'].items():
            data=station.run('cat '+shlex.quote(path),timeout=max(60,60+required//(128*1024))).stdout
            if hashlib.sha256(data).hexdigest()!=digest:raise RuntimeError('Файл VPN изменился; удаление остановлено')
            saved[path]=data;write_private(folder/'station'/Path(path).name,data)
        for name,(_,digest) in current['local'].items():
            data=(HOME/'native'/name).read_bytes()
            if hashlib.sha256(data).hexdigest()!=digest:raise RuntimeError('Настройки VPN изменились; удаление остановлено')
            local[name]=data;write_private(folder/'computer'/name,data)
        write_private(folder/'config.json',before['vpn'])
    was_running=bool(status(config).get('agent_connected'))
    vpn_running=station.run(TOOLS+'/vpn.sh status',check=False).returncode==0
    try:
        with ui.activity('Останавливаю GPT и VLESS'):
            if was_running:stop(config)
            if vpn_running:station.run(TOOLS+'/vpn.sh stop')
            if station.run(TOOLS+'/vpn.sh status',check=False).returncode==0:raise RuntimeError('VLESS ещё работает; файлы не удалены')
        with ui.activity('Удаляю программу и активные настройки VLESS'):
            for path in saved:station.run('test ! -L '+shlex.quote(path)+' && rm -f '+shlex.quote(path))
            for name in local:(HOME/'native'/name).unlink()
            config['vpn']={'enabled':False,'required':False,'autostart':False}
            if config.get('installed'):apply_settings(config)
            save(config)
            autostart.remove_owned_boot(station)
        if was_running:
            with ui.activity('Возвращаю готовность Алисы'):start(config)
    except BaseException:
        for path,data in saved.items():station.put(path,data,0o700 if path.endswith(('/xray','/sing-box','.sh')) else 0o600)
        for name,data in local.items():write_private(HOME/'native'/name,data)
        config.clear();config.update(before)
        if config.get('installed'):apply_settings(config)
        save(config)
        if before.get('autostart') or before['vpn'].get('autostart'):autostart.ensure_boot(station)
        if vpn_running:station.run(TOOLS+'/vpn.sh start')
        if was_running:station.run(NATIVE+'/run.sh start')
        raise
    return {'vpn_removed':True,'reclaimed_bytes':current['reclaim_bytes'],'backup':str(folder),
            'message':'VLESS и его активные настройки удалены. GPT использует сеть без VPN; вход, чаты и SSH сохранены.'}
