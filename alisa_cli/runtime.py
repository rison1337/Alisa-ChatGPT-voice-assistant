import json,os,shlex,subprocess,sys,time
from .state import HOME,ROOT,load,save,write_private,command_file
from .ssh import Station,check_result
from .errors import PublicError
NATIVE='/data/opt/alisa-standalone'
TOOLS='/data/opt/alisa-cli'

def bridge_env(config):
    env={**os.environ,'ALISA_BRIDGE_STATE':str(HOME/'bridge/runtime'),'ALICE_MICROPHONE_SOURCE':'vqe',
         'ALICE_LED_STYLE':config['led'],'ALICE_AUDIO_FOCUS_MODE':'native'}
    env.pop('ALISA_SSH_CONFIG',None)
    host,ssh_config=Station(config).configure_bridge()
    if ssh_config:env['ALISA_SSH_CONFIG']=ssh_config
    return env,host

def bridge_command(config,action,**kwargs):
    env,host=bridge_env(config)
    return subprocess.run([sys.executable,str(ROOT/'engines/bridge/bridge.py'),action,'--host',host],
                          env=env,timeout=65,capture_output=True,**kwargs)

def status(config):
    if config['mode']=='native':
        r=Station(config).run(NATIVE+'/bin/alisa-standalone status',check=False)
        if r.returncode==255:check_result(r)
        try:result=json.loads(r.stdout)
        except ValueError:
            check_result(r)
            raise PublicError('station_reply_invalid') from None
        if not isinstance(result,dict):raise PublicError('station_reply_invalid')
        if result.get('error')=='control_unavailable':result={'phase':'stopped','agent_connected':False,'error':''}
        else:
            check_result(r)
            if not isinstance(result.get('phase'),str):raise PublicError('station_reply_invalid')
        return {'mode':'native',**result}
    if sys.platform!='linux':return {'mode':'bridge','phase':'unsupported','error':'Windows ещё не проверена'}
    r=bridge_command(config,'status')
    try:result=json.loads(r.stdout)
    except ValueError:result={'phase':'stopped'}
    if result.get('process_alive') is False and result.get('phase') in ('starting','running','stopping'):
        result['phase']='stopped'
    try:
        voice=json.loads((HOME/'bridge/runtime/voice.json').read_text(encoding='utf-8'))
        result['voice_active']=bool(result.get('native_alice_paused'))
        pid=int(voice.get('pid',0))
        owned=pid>0 and str(ROOT/'engines/bridge/voice.py').encode() in __import__('pathlib').Path(f'/proc/{pid}/cmdline').read_bytes()
        if owned and result.get('phase')=='running' and voice.get('error'):result['error']=voice['error']
    except (OSError,ValueError,TypeError):pass
    return {'mode':'bridge',**result}

def native_environment(config):
    lines=['export ALISA_LED_STYLE='+shlex.quote(config['led'])]
    if config['vpn'].get('enabled',False):
        lines+=['export ALISA_VPN_REQUIRED=1','export ALISA_PROXY_URL=socks5h://127.0.0.1:18081','export ALISA_ICE_BIND_ADDRESS=172.31.254.1','export LD_PRELOAD='+TOOLS+'/mark.so']
    return ('\n'.join(lines)+'\n').encode()

def apply_settings(config):
    s=Station(config);mode=config['mode'];base=NATIVE if mode=='native' else '/data/opt/alice-voice'
    s.put(base+'/commands.txt',command_file(config['phrases']))
    if mode=='native':
        vpn=config['vpn'].get('enabled',False)
        s.put(NATIVE+'/runtime.env',native_environment(config))
        if vpn:
            s.run(TOOLS+'/vpn.sh guard')
            s.put(TOOLS+'/vpn-required',b'required\n')
        else:s.run('rm -f '+TOOLS+'/vpn-required')
    else:write_private(HOME/'bridge/runtime/preferences.json',{'led':config['led']})

def update_preferences(config,previous):
    """Apply only changed preferences; never restart audio or a conversation."""
    phrases=config['phrases']!=previous['phrases'];led=config['led']!=previous['led']
    if not config.get('installed') or not (phrases or led):return
    native=config['mode']=='native';station=Station(config)
    current=status(config) if native and led else {}
    live=bool(current.get('pid') or current.get('agent_connected'))
    if live and 'led_style' not in current:
        raise RuntimeError('Для смены подсветки без перезапуска обновите нативный клиент через мастер установки.')
    base=NATIVE if native else '/data/opt/alice-voice'
    try:
        if phrases:station.put(base+'/commands.txt',command_file(config['phrases']))
        if led and native:
            station.put(NATIVE+'/runtime.env',native_environment(config))
            if live:native_action(config,'led',config['led'])
        elif led:write_private(HOME/'bridge/runtime/preferences.json',{'led':config['led']})
    except BaseException:
        # Restore this small preference transaction without restarting audio.
        import contextlib
        with contextlib.suppress(Exception):
            if phrases:station.put(base+'/commands.txt',command_file(previous['phrases']))
        with contextlib.suppress(Exception):
            if led and native:
                station.put(NATIVE+'/runtime.env',native_environment(previous))
                if live:native_action(config,'led',previous['led'])
        raise

def stop(config):
    if config['mode']=='native':Station(config).run(NATIVE+'/run.sh stop')
    else:
        r=bridge_command(config,'stop')
        if r.returncode:raise RuntimeError('Мост не остановился; проверьте диагностику')

def start(config,foreground=False):
    if not config.get('installed'):raise RuntimeError('Сначала выполните install для выбранного режима')
    s=Station(config)
    if config['mode']=='native':
        # Refuse to steal another owner's profile, including the former public bridge.
        active=s.text('for p in $(pidof quasar_launcher2 2>/dev/null || true); do tr "\\000" " " < /proc/$p/cmdline; done')
        if '/data/opt/alice-voice/' in active:raise RuntimeError('Сначала остановите мост: alisa bridge stop')
        if config['vpn'].get('enabled'):
            if s.run(TOOLS+'/vpn.sh status',check=False).returncode:raise RuntimeError('VLESS выключен. Запустите alisa native vpn start; прямое подключение запрещено.')
        apply_settings(config);s.run(NATIVE+'/run.sh start')
        for _ in range(80):
            result=status(config)
            if result.get('agent_connected'):return result
            time.sleep(.5)
        raise RuntimeError('Нативный режим не запустился; штатная Алиса должна восстановиться автоматически')
    if sys.platform!='linux':raise RuntimeError('Браузерный мост Windows — следующий этап, текущая версия работает в Linux')
    if s.run('test -f /tmp/alisa-standalone/monitor.pid',check=False).returncode==0:
        raise RuntimeError('Сначала остановите нативный режим: alisa native stop')
    apply_settings(config);env,host=bridge_env(config)
    argv=[sys.executable,str(ROOT/'engines/bridge/bridge.py'),'start','--host',host]
    if foreground:
        code=subprocess.run(argv,env=env).returncode
        if code:raise RuntimeError('Браузерный мост завершился с ошибкой')
        return {'exit_code':code}
    (HOME/'bridge').mkdir(parents=True,exist_ok=True,mode=0o700)
    with (HOME/'bridge/runtime.log').open('ab') as log:
        subprocess.Popen(argv,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    for _ in range(80):
        result=status(config)
        if result.get('phase')=='running':return result
        time.sleep(.5)
    raise RuntimeError('Мост не подтвердил запуск. Смотрите alisa bridge status')

def native_action(config,action,argument=None):
    command=NATIVE+'/bin/alisa-standalone '+shlex.quote(action)+' '+shlex.quote(NATIVE+'/state')
    if argument is not None:command+=' '+shlex.quote(argument)
    r=Station(config).run(command,check=False)
    try:result=json.loads(r.stdout)
    except ValueError:raise RuntimeError('Нативный клиент не отвечает') from None
    if r.returncode:raise RuntimeError(result.get('error','Ошибка нативного клиента'))
    return result
