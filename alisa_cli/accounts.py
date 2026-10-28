import contextlib,hashlib,json,os,shutil,signal,socket,subprocess,sys,time
from pathlib import Path
from .state import HOME,ROOT,write_private
from .ssh import Station
from .runtime import NATIVE,TOOLS,stop,status,bridge_env
from .login_browser import close_profile,open_window,SignInWatcher
from . import ui

def archive_settings(station):
    raw=station.run('cat '+NATIVE+'/state/settings.json',check=False)
    if raw.returncode:return
    settings=json.loads(raw.stdout);stamp=str(time.time_ns())
    station.put(NATIVE+'/state/account-history/'+stamp+'.json',raw.stdout)
    fresh={'version':1,'voice':settings.get('voice','cove'),'selected':'','previous':'','chats':[]}
    station.put(NATIVE+'/state/settings.json',json.dumps(fresh).encode())

def install_session(config,candidate,destination):
    station=Station(config);new=json.loads(candidate.read_text(encoding='utf-8'))
    remote=TOOLS+'/auth-check'
    station.put(remote+'/web-session.json',candidate.read_bytes())
    try:
        refreshed=station.run(TOOLS+'/native-call.sh refresh '+remote,check=False)
        if refreshed.returncode:raise RuntimeError('Новый вход не подтверждён на колонке; прежняя сессия сохранена. Проверьте network и повторите login.')
        verified=station.run('cat '+remote+'/web-session.json').stdout
    finally:station.run('rm -f '+remote+'/web-session.json',check=False)
    previous=station.run('cat '+NATIVE+'/state/web-session.json',check=False)
    old=json.loads(previous.stdout) if previous.returncode==0 else {}
    was_running=status(config).get('agent_connected',False)
    if was_running:stop(config)
    if old.get('account_id')!=new.get('account_id'):archive_settings(station)
    station.put(NATIVE+'/state/web-session.json',verified)
    write_private(destination,verified)
    if was_running:
        from .runtime import start
        start(config)

def login(config,change=False):
    native=config['mode']=='native';folder=HOME/config['mode']
    profile=folder/'browser-profile' if native else HOME/'bridge/runtime/browser-profile'
    destination=folder/'session.json';folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    canonical=profile
    if change:profile=folder/('login-profile-'+str(time.time_ns()))
    candidate=folder/('.login-candidate-'+str(time.time_ns())+'.json')
    tunnel=None;proxy=None;bridge_was_running=False
    if native and config['vpn'].get('enabled'):
        station=Station(config)
        if station.run(TOOLS+'/vpn.sh status',check=False).returncode:raise RuntimeError('Для входа сначала запустите VLESS')
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        tunnel=station.forward_local(port)
        proxy='socks5://127.0.0.1:'+str(port)
        time.sleep(.5)
        if tunnel.poll() is not None:raise RuntimeError('Не удалось открыть локальный канал входа через VLESS')
    try:
        if not native:
            bridge_was_running=status(config).get('phase')=='running'
            if bridge_was_running:stop(config)
        ui.note('Войдите в ChatGPT в обычном окне Chromium. Сессия сохранится автоматически; пароль вводится только на сайте.')
        open_window(profile,proxy)
        ui.wait_until('Войдите в нужный аккаунт в открытом окне браузера.',SignInWatcher(profile,require_page=not native))
        close_profile(profile)
        ui.note('Проверяю вход и сохраняю сессию…')
        argv=[sys.executable,str(ROOT/'engines/native/tools/import_web_session.py'),'--profile',str(profile),'--out',str(candidate)]
        environment=None
        if not native:
            argv=[sys.executable,str(ROOT/'engines/bridge/browser_login.py'),'--profile',str(profile)]
            environment,_=bridge_env(config)
        if proxy:argv+=['--proxy',proxy]
        if native and sys.platform=='win32':
            from .win_browser import import_session
            import_session(profile,candidate,proxy)
        else:
            result=subprocess.run(argv,capture_output=True,timeout=120,**({'env':environment} if environment is not None else {}))
            if result.returncode:raise RuntimeError('Вход ещё не подтверждён. Убедитесь, что ChatGPT открыт после входа, и повторите. Прежняя сессия сохранена.')
        if native:install_session(config,candidate,destination)
        if change:
            close_profile(canonical)
            if canonical.exists():
                archive=folder/'account-history'/('browser-'+str(time.time_ns()))
                archive.parent.mkdir(parents=True,exist_ok=True,mode=0o700);canonical.rename(archive)
            profile.rename(canonical)
        ui.note('Вход сохранён. Проверку голоса можно запустить через verify.','green')
    finally:
        candidate.unlink(missing_ok=True)
        if native and sys.platform=='win32' and not tunnel:
            close_profile(profile)
        if tunnel:
            try:close_profile(profile)
            finally:
                tunnel.terminate()
                with contextlib.suppress(subprocess.TimeoutExpired):tunnel.wait(timeout=5)
        if bridge_was_running:
            close_profile(profile)
            from .runtime import start
            start(config)

def logout(config):
    native=config['mode']=='native'
    if native:
        station=Station(config);was_running=status(config).get('agent_connected',False)
        if was_running:stop(config)
        archive_settings(station)
        station.run('rm -f '+NATIVE+'/state/web-session.json '+NATIVE+'/state/web-session.json.new')
        if was_running:
            from .runtime import start
            start(config)
    else:stop(config)
    profile=HOME/config['mode']/'browser-profile' if native else HOME/'bridge/runtime/browser-profile'
    close_profile(profile)
    if profile.exists():shutil.rmtree(profile)
    (HOME/config['mode']/'session.json').unlink(missing_ok=True)
    if not native:
        conversation=HOME/'bridge/runtime/conversation.json'
        if conversation.exists():
            archive=HOME/'bridge/account-history'/str(time.time_ns())
            archive.mkdir(parents=True,exist_ok=True,mode=0o700)
            shutil.move(str(conversation),archive/'conversation.json')
    print('Вход на этом устройстве удалён. Указатели прежних чатов сохранены отдельно.')
