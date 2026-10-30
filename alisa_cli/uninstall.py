"""Remove only owned runtime files, keeping SSH, account data and chat history."""
import copy,hashlib,shlex,shutil,subprocess,time
from pathlib import Path
from .state import HOME,save,write_private
from .ssh import Station
from . import runtime,autostart,storage

def plan(config):
    station=Station(config)
    if config['mode']=='native':
        from .install import native_paths
        paths=native_paths()
    else:paths=list(storage.payload(config,station))
    if config['mode']=='native':
        paths += [runtime.NATIVE+'/commands.txt',runtime.NATIVE+'/runtime.env',runtime.NATIVE+'/listen-led',
                  runtime.TOOLS+'/xray',runtime.TOOLS+'/sing-box',runtime.TOOLS+'/vpn-required',
                  runtime.TOOLS+'/native-autostart',runtime.TOOLS+'/vpn-autostart']
    existing=storage.remote_sizes(station,paths)
    return {'mode':config['mode'],'paths':sorted(existing),'hashes':{p:v[1] for p,v in existing.items()},
            'reclaim_bytes':sum(v[0] for v in existing.values()),'account_and_chats_preserved':True,
            'ssh_preserved':True,'stock_alice_restored':True}

def remove(config,value=None):
    value=plan(config) if value is None else value
    # Rebuild the allowlist even when a precomputed review is supplied.
    current=plan(config)
    if value['paths']!=current['paths'] or value['hashes']!=current['hashes']:
        raise RuntimeError('Состав файлов изменился после проверки. Повторите удаление, чтобы увидеть новый список.')
    folder=HOME/config['mode']/'uninstall-backups'/str(time.time_ns())
    parent=HOME
    while not parent.exists():parent=parent.parent
    if shutil.disk_usage(parent).free<value['reclaim_bytes']+storage.RESERVE:
        raise RuntimeError('На компьютере недостаточно места для копии удаляемых файлов. Удаление не началось.')
    station=Station(config);saved={}
    before=copy.deepcopy(config)
    backup_timeout=max(60,60+value['reclaim_bytes']//(128*1024))
    for path in value['paths']:
        raw=station.run('cat '+shlex.quote(path),timeout=backup_timeout).stdout
        if hashlib.sha256(raw).hexdigest()!=value['hashes'][path]:raise RuntimeError('Файл изменился; удаление остановлено')
        target=folder/hashlib.sha256(path.encode()).hexdigest();write_private(target,raw);saved[path]=raw
    write_private(folder/'manifest.json',{'files':{p:hashlib.sha256(p.encode()).hexdigest() for p in saved}})
    # Stop and restore stock Alice before removing any executable/profile.
    was_running=runtime.status(config).get('agent_connected') if config['mode']=='native' else runtime.status(config).get('phase')=='running'
    vpn_running=config['mode']=='native' and station.run(runtime.TOOLS+'/vpn.sh status',check=False).returncode==0
    if config.get('installed'):runtime.stop(config)
    autostart.set_enabled(config,False)
    if config['mode']=='native':
        station.run(runtime.TOOLS+'/vpn.sh stop',check=False)
        autostart.station_flag(config,'native-autostart',False);autostart.station_flag(config,'vpn-autostart',False)
    else:subprocess.run(['systemctl','--user','stop','alisa-voice-bridge.service'],capture_output=True,check=False)
    try:
        for path in value['paths']:station.run('test ! -L '+shlex.quote(path)+' && rm -f '+shlex.quote(path))
        if config['mode']=='native':autostart.remove_owned_boot(station)
    except BaseException:
        for path,raw in saved.items():station.put(path,raw,0o700 if path.endswith(('.sh','.so')) or '/bin/' in path or path.endswith(('/xray','/sing-box')) else 0o600)
        config.clear();config.update(before);save(config)
        if before['autostart']:autostart.set_enabled(config,True)
        if vpn_running:station.run(runtime.TOOLS+'/vpn.sh start',check=False)
        if was_running:
            if config['mode']=='native':station.run(runtime.NATIVE+'/run.sh start',check=False)
            else:runtime.start(config)
        raise
    config['installed']=False;config['autostart']=False
    if config['mode']=='native':config['vpn'].update(enabled=False,required=False,autostart=False)
    save(config)
    return {'mode':config['mode'],'removed':True,'reclaimed_bytes':value['reclaim_bytes'],
            'backup':str(folder),'message':'Программа удалена. Вход, чаты и SSH сохранены; работает штатная Алиса.'}
