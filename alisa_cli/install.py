import hashlib,importlib.util,json,shlex,time
from pathlib import Path
from .state import ROOT,HOME,save,write_private
from .ssh import Station
from .doctor import require
from .runtime import NATIVE,TOOLS,status,stop,start,apply_settings
from . import ui

def native_paths():
    manifest=json.loads((ROOT/'assets/native/manifest.json').read_text(encoding='utf-8'))
    paths=[]
    for name in manifest['files']:
        if Path(name).is_absolute() or '..' in Path(name).parts:raise RuntimeError('Некорректный manifest')
        paths.append(TOOLS+'/mark.so' if name=='mark.so' else NATIVE+'/'+name)
    return paths+[NATIVE+'/'+name for name in ['launch.bridge.json','quasar.speech.json','tts-asound.conf']]+[TOOLS+'/'+name for name in ['vpn.sh','boot.sh','native-call.sh']]

def native_files(station,model=None):
    folder=ROOT/'assets/native';manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    files={}
    for name,digest in manifest['files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts:raise RuntimeError('Некорректный manifest')
        data=(folder/name).read_bytes()
        if hashlib.sha256(data).hexdigest()!=digest:raise RuntimeError('Повреждён файл пакета: '+name)
        files[(TOOLS+'/mark.so') if name=='mark.so' else NATIVE+'/'+name]=data
    spec=importlib.util.spec_from_file_location('native_stage',ROOT/'engines/native/tools/deploy.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    launch=json.loads(station.text('cat /system/vendor/quasar/launch.json'))
    quasar=json.loads(station.text('cat /system/vendor/quasar/quasar.cfg'))
    launch,quasar=module.profiles(launch,quasar,model)
    files[NATIVE+'/launch.bridge.json']=json.dumps(launch).encode()
    files[NATIVE+'/quasar.speech.json']=json.dumps(quasar).encode()
    files[NATIVE+'/tts-asound.conf']=b'</usr/share/alsa/alsa.conf>\npcm.alice_native_tts { type plug slave.pcm "default" }\n'
    for name in ['vpn.sh','boot.sh','native-call.sh']:files[TOOLS+'/'+name]=(ROOT/'station'/name).read_bytes()
    return files

def install(config,activate=True,dry_run=False):
    with ui.activity('Проверяю колонку и компоненты'):preflight=require(config)
    s=Station(config)
    from . import storage
    if config['mode']=='bridge':
        with ui.activity('Проверяю файлы и свободное место'):
            space=storage.report(config,vpn_engine=None);storage.require(space)
        if dry_run:return {'dry_run':True,'mode':'bridge','preflight':preflight,'storage':space}
        was_running=status(config).get('phase')=='running'
        backup=HOME/'bridge/backups'/str(time.time_ns());saved={}
        with ui.activity('Сохраняю копию компонентов моста'):
            for remote in storage.payload(config,s):
                data=s.run('cat '+shlex.quote(remote),check=False)
                saved[remote]=data.stdout if data.returncode==0 else None
                if data.returncode==0:write_private(backup/hashlib.sha256(remote.encode()).hexdigest(),data.stdout)
        if was_running:
            with ui.activity('Останавливаю мост для обновления'):stop(config)
        spec=importlib.util.spec_from_file_location('staged_bridge',ROOT/'engines/bridge/bridge.py')
        # The bridge imports adjacent modules; temporarily expose only its folder.
        import sys
        sys.path.insert(0,str(ROOT/'engines/bridge'))
        import os
        old_ssh=os.environ.get('ALISA_SSH_CONFIG')
        try:
            env_host,ssh_config=s.configure_bridge()
            if ssh_config:os.environ['ALISA_SSH_CONFIG']=ssh_config
            else:os.environ.pop('ALISA_SSH_CONFIG',None)
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            with ui.activity('Копирую и проверяю компоненты моста'):
                module.stage_station(env_host,write_file=s.put)
                apply_settings(config)
        except BaseException:
            for remote,data in saved.items():
                if data is None:s.run('rm -f '+shlex.quote(remote),check=False)
                else:s.put(remote,data,0o700 if remote.endswith('.sh') else 0o600)
            if was_running:start(config)
            raise
        finally:
            sys.path.pop(0)
            if old_ssh is None:os.environ.pop('ALISA_SSH_CONFIG',None)
            else:os.environ['ALISA_SSH_CONFIG']=old_ssh
    else:
        with ui.activity('Сравниваю установленные файлы и проверяю место'):
            files=native_files(s,preflight.get('model'))
            space=storage.report(config,vpn_engine=None,files=files);storage.require(space)
        if dry_run:return {'dry_run':True,'mode':'native','preflight':preflight,'verified_files':len(files),'storage':space}
        sizes=storage.remote_sizes(s,files)
        changed={path:data for path,data in files.items() if sizes.get(path,(0,''))[1]!=hashlib.sha256(data).hexdigest()}
        old_status=status(config);was_running=old_status.get('agent_connected',False)
        backup=HOME/'native/backups'/str(time.time_ns());saved={}
        # No login files are included in this transaction.
        if changed:
            with ui.activity('Сохраняю копию изменяемых компонентов'):
                for remote in changed:
                    data=s.run('cat '+shlex.quote(remote),check=False)
                    saved[remote]=data.stdout if data.returncode==0 else None
                    if data.returncode==0:write_private(backup/hashlib.sha256(remote.encode()).hexdigest(),data.stdout)
        elif ui.interactive():ui.note('Все '+str(len(files))+' файлов клиента уже актуальны — копирование и перезапуск пропущены.','green')
        if was_running and changed:
            with ui.activity('Останавливаю клиент для обновления'):stop(config)
        try:
            if changed:
                with ui.activity('Копирую изменённые файлы') as progress:
                    for index,(remote,data) in enumerate(changed.items(),1):
                        progress(f'Копирую {index}/{len(changed)}: {Path(remote).name}')
                        executable=remote.endswith(('.sh','.so')) or '/bin/' in remote
                        s.put(remote,data,0o700 if executable else 0o600)
            with ui.activity('Применяю настройки и проверяю клиент'):
                apply_settings(config)
                if was_running and not changed and old_status.get('led_style')!=config['led']:
                    from .runtime import native_action
                    native_action(config,'led',config['led'])
                info=json.loads(s.text(NATIVE+'/bin/alisa-standalone info'))
                if info.get('audio_pipeline_revision')!=5:raise RuntimeError('Версия клиента после установки не совпала')
        except BaseException:
            for remote,data in saved.items():
                if data is None:s.run('rm -f '+shlex.quote(remote),check=False)
                else:s.put(remote,data,0o700 if remote.endswith(('.sh','.so')) or '/bin/' in remote else 0o600)
            if was_running and changed:s.run(NATIVE+'/run.sh start',check=False)
            raise
    config['installed']=True;save(config)
    result={'mode':config['mode'],'installed':True,'started':bool(was_running and config['mode']=='native' and not changed)}
    if config['mode']=='native':
        result.update(files_updated=len(changed),files_unchanged=len(files)-len(changed))
        result['message']='Все файлы уже актуальны. Настройки применены; вход и чаты сохранены.' if not changed else 'Компоненты обновлены; вход и чаты сохранены.'
    if activate:
        with ui.activity('Запускаю выбранный режим'):return start(config)
    return result
