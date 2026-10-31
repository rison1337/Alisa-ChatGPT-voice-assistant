"""Read-only space planning for the actual target and selected package files."""
import hashlib,os,shlex,shutil
from pathlib import Path
from .state import HOME,ROOT
from .ssh import Station
from .runtime import NATIVE,TOOLS
RESERVE=8*1024*1024

def size_text(value):
    if value is None:return 'не определено'
    return f'{value/1048576:.1f} МиБ' if value<1024**3 else f'{value/1024**3:.1f} ГиБ'

def payload(config,station):
    if config['mode']=='native':
        from .install import native_files
        return native_files(station,config.get('model'))
    base='/data/opt/alice-voice';folder=ROOT/'engines/bridge'
    names=['capture_tap.so','vqe_tap.so','playback_worker.so','packet_playback.so','libopus.so.0','opus-copyright','native_agent.so','tts_gate.so']
    files={base+'/'+name:(folder/'build'/name).read_bytes() for name in names}
    files[base+'/profile.sh']=(folder/'station/profile.sh').read_bytes()
    files[base+'/indicator.sh']=(folder/'station/indicator.sh').read_bytes()
    # Match the profiles and silence leases that the bridge generates.
    files.update({base+'/'+name:bytes(8192) for name in ['launch.bridge.json','quasar.speech.json','tts-asound.conf','commands.txt']})
    import sys
    sys.path.insert(0,str(folder))
    try:
        from audio_focus import FOCUS_FILES,silent_lease
        for name in FOCUS_FILES:files[base+'/'+name]=silent_lease()
    finally:sys.path.pop(0)
    return files

def remote_sizes(station,paths):
    quoted=' '.join(shlex.quote(p) for p in paths)
    script='for f in '+quoted+'; do if [ -f "$f" ] && [ ! -L "$f" ]; then '
    script+='printf "%s\\t" "$f"; wc -c < "$f" | tr -d " \\n"; printf "\\t"; sha256sum "$f" | cut -d " " -f1; fi; done'
    result={}
    for line in station.text(script,timeout=90).splitlines():
        parts=line.split('\t')
        if len(parts)==3 and parts[1].isdigit():result[parts[0]]=(int(parts[1]),parts[2])
    return result

def calculate(entries,existing,free_bytes,reserve=RESERVE):
    """Each put writes a .new before replacing the old file; preserve that peak."""
    block=lambda n:((n+4095)//4096)*4096
    delta=0;peak=0;total=0;transfer=0
    for path,data,skip_matching in entries:
        old,digest=existing.get(path,(0,''));total+=len(data)
        if skip_matching and digest==hashlib.sha256(data).hexdigest():continue
        temporary=block(len(data));peak=max(peak,delta+temporary)
        delta+=temporary-block(old);transfer+=len(data)
    required=max(0,peak)+reserve
    return {'package_bytes':total,'transfer_bytes':transfer,'additional_after_bytes':max(0,delta),
            'peak_additional_bytes':max(0,peak),'required_free_bytes':required,
            'reserve_bytes':reserve,'free_bytes':free_bytes,'enough':free_bytes is not None and free_bytes>=required,
            'free_after_bytes':None if free_bytes is None else free_bytes-delta}

def report(config,vpn_engine='saved',files=None):
    station=Station(config)
    files=payload(config,station) if files is None else files
    entries=[(path,data,config['mode']=='native') for path,data in files.items()]
    components=[{'name':'Клиент GPT и аудиомодули' if config['mode']=='native' else 'Аудиомодули моста',
                 'package_bytes':sum(len(data) for data in files.values()),'paths':list(files)}]
    if vpn_engine=='saved':vpn_engine=config['vpn'].get('engine','sing-box') if config['mode']=='native' and config['vpn'].get('enabled') else None
    if vpn_engine:
        if vpn_engine not in ('xray','sing-box'):raise ValueError('Неизвестный VPN-движок')
        binary=ROOT/'assets/vpn/linux-armv7'/vpn_engine
        if not binary.exists():raise RuntimeError('VPN-бинарник отсутствует в пакете. Повторно загрузите полный установщик.')
        entries.append((TOOLS+'/'+vpn_engine,binary.read_bytes(),True))
        components.append({'name':'VPN '+('Xray (XHTTP)' if vpn_engine=='xray' else 'sing-box'),
                           'package_bytes':binary.stat().st_size,'paths':[TOOLS+'/'+vpn_engine]})
    result=station.text('df -Pk /data; printf "MEM\\n"; awk \'/MemAvailable:/ {print $2}\' /proc/meminfo')
    free=None;mem=None
    for line in result.splitlines():
        fields=line.split()
        if len(fields)>=6 and fields[3].isdigit():free=int(fields[3])*1024
    if 'MEM\n' in result:
        raw=result.split('MEM\n',1)[1].strip()
        if raw.isdigit():mem=int(raw)*1024
    unused=(TOOLS+('/sing-box' if vpn_engine=='xray' else '/xray')) if vpn_engine else None
    sizes=remote_sizes(station,[entry[0] for entry in entries]+([unused] if unused else []))
    for component in components:
        component['installed_bytes']=sum(sizes.get(p,(0,''))[0] for p in component.pop('paths'))
    calculated=calculate(entries,sizes,free)
    matching=sum(skip and sizes.get(path,(0,''))[1]==hashlib.sha256(data).hexdigest() for path,data,skip in entries)
    if unused and unused in sizes and calculated['free_after_bytes'] is not None:
        calculated['free_after_bytes']+=sizes[unused][0]
    path=HOME
    while not path.exists():path=path.parent
    pc_free=shutil.disk_usage(path).free
    backup_bytes=sum(size for size,_ in sizes.values())+RESERVE
    return {'mode':config['mode'],'space':calculated,'vpn_engine':vpn_engine,
            'matching_files':matching,'total_files':len(entries),'installed_files':sum(path in sizes for path,_,_ in entries),
            'components':components,
            'computer_free_bytes':pc_free,'computer_required_bytes':backup_bytes,
            'memory_available_bytes':mem,'enough':calculated['enough'] and pc_free>=backup_bytes}

def lines(value):
    s=value['space']
    result=['Свободно на колонке: '+size_text(s['free_bytes']),
            'Размер выбранных компонентов: '+size_text(s['package_bytes'])]+[
            '  '+c['name']+': '+size_text(c['package_bytes']) for c in value.get('components',[])]+[
            'Нужно свободно для установки: '+size_text(s['required_free_bytes']),
            '  включая запас: '+size_text(s['reserve_bytes']),
            'Оценка остатка после установки: '+size_text(s['free_after_bytes']),
            'Свободно на компьютере: '+size_text(value['computer_free_bytes']),
            'Доступная RAM колонки: '+size_text(value['memory_available_bytes'])]
    if value.get('matching_files'):
        result.insert(1,'Уже совпадают: '+str(value['matching_files'])+' из '+str(value['total_files'])+' файлов — будут пропущены.')
    if 'transfer_bytes' in s:result.insert(2,'Будет передано на колонку: '+size_text(s['transfer_bytes']))
    if s['free_bytes'] is None:result.append('Свободное место на колонке не определено — установка не начнётся.')
    elif s['free_bytes']<s['required_free_bytes']:
        result.append('Не хватает места на колонке: '+size_text(s['required_free_bytes']-s['free_bytes'])+'.')
    if value['computer_free_bytes']<value['computer_required_bytes']:
        result.append('Не хватает места на компьютере: '+size_text(value['computer_required_bytes']-value['computer_free_bytes'])+'.')
    if value['enough']:result.append('Места достаточно.')
    return result

def require(value):
    if not value['enough']:raise RuntimeError('Недостаточно места для выбранных компонентов. Освободите место или измените состав установки.')
