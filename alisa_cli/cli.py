import argparse,copy,json,subprocess,sys,time
from .state import ROOT,MODES,STYLES,STYLE_NAMES,LED_SCOPES,VOICES,load,save,validate
from . import doctor,network,runtime,autostart,accounts,vpn
from . import ui,ssh_setup
from . import __version__,updates
from .firmware import apply_model,supports_lighting,LIGHTING_UNAVAILABLE
if sys.platform=='win32':MODES=('native',)

NAMES={'native':'Нативный — обработка на колонке, ПК после установки не нужен',
       'bridge':'Мост — браузер ChatGPT на Linux-ПК, колонка передаёт звук'}

def pick(title,items):
    return ui.select(title,items)

def yes(title,default=False):
    return ui.confirm(title,default)

def show(value):ui.show(value)

def show_storage(value):
    from . import storage
    if not ui.interactive():show(value);return
    for line in storage.lines(value):ui.note(line,'green' if line=='Места достаточно.' else 'muted')

def installation_help():
    ui.note('Мастер установки','bold')
    for line in [('1. Нативный режим: обработка на колонке.' if sys.platform=='win32' else '1. Выберите нативный режим или браузерный мост.'),
      ('2. Введите IP, порт и выберите вход по паролю или существующим ключом.' if sys.platform=='win32' else '2. Введите IP, порт и пароль. После проверки сохранится SSH-ключ.'),
      '3. Мастер проверит модель, прошивку и компоненты.',
      '4. Мастер по очереди предложит VPN, автозапуск, фразы и подсветку.',
      '5. Посмотрите состав пакета, свободное место и итоговый размер.',
      '6. Подтвердите установку: файлы будут скопированы и проверены.',
      '7. Мастер запустит выбранный режим и предложит вход в ChatGPT и тест голоса.',
      '', 'После установки пункт «GPT — запустить / остановить» управляет режимом. При запуске настроенный VLESS включается вместе с ним.',
      '«Установка и обслуживание → Удалить программу» возвращает штатную Алису и сохраняет SSH, вход и чаты.']:
        ui.note(line)

def start_mode(config,foreground=False):
    if config['mode']=='native' and config['vpn'].get('enabled') and not vpn.operate(config,'status')['running']:
        if ui.interactive():ui.note('Запускаю настроенный VLESS…','cyan')
        vpn.operate(config,'start')
    if ui.interactive():ui.note('Запускаю GPT…','cyan')
    return runtime.start(config,foreground=foreground)

def toggle_mode(config):
    current=runtime.status(config)
    running=current.get('agent_connected') if config['mode']=='native' else current.get('phase')=='running'
    if running or current.get('phase') in ('starting','opening','active'):
        runtime.stop(config)
        return {'mode':config['mode'],'phase':'stopped'}
    return start_mode(config)

def auto_settings(config):
    from . import menus
    def execute(action,operation=None):
        return main([config['mode'],action]+([operation] if operation else []))
    return menus.run(config['mode'],execute,start_page='autostart')

def vpn_configure(config,source):
    client='standard'
    if not vpn.is_proxy_link(source):
        client=pick('Формат подписки', [('Обычная / Base64 / JSON / YAML','standard'),('Incy (Xray JSON)','incy'),('Happ','happ')])
    selection=vpn.select_server(source,client=client)
    enabled=yes('Запускать VLESS при включении колонки?',config['vpn'].get('autostart',False))
    with ui.activity('Устанавливаю и проверяю VLESS'):
        result=vpn.configure(config,source,client=client,selection=selection)
        autostart.station_flag(config,'vpn-autostart',enabled)
        config['vpn']['autostart']=enabled;save(config)
    result['autostart']=enabled
    return result

def settings(config,apply=True,fields=('phrases','led')):
    previous=copy.deepcopy(config)
    if 'phrases' in fields:
        ui.note('Фразы после обращения «Алиса».')
        for key,title in [('open','Открыть'),('close','Выйти'),('new','Новый чат'),('previous','Предыдущий чат')]:
            config['phrases'][key]=ui.text(title,config['phrases'][key])
    if 'led' in fields:
        from . import lighting
        config['led']=lighting.choose(config)
    if 'display' in fields:
        from . import display
        chosen=display.choose(config)
        if config.get('model')=='cucumber':config.update(midi_eyes=chosen=='eyes',midi_matrix=chosen=='matrix')
        else:config['display']=chosen
    validate(config)
    if apply:save_preferences(config,previous)
    else:ui.note('Настройки выбраны.','green')

def save_preferences(config,previous):
    fields={'phrases':None,'led':None,'listen_led':False,'led_scope':'speaking','display':'eyes','midi_eyes':False,'midi_matrix':False,'midi_fire':False}
    if all(config.get(key,default)==previous.get(key,default) for key,default in fields.items()):
        ui.note('Настройки не изменились.');return
    runtime.update_preferences(config,previous)
    save(config);ui.note('Настройки сохранены.','green')
    if any(config.get(key,default)!=previous.get(key,default) for key,default in [('display','eyes'),('midi_eyes',False),('midi_matrix',False)]):
        ui.note('Режим дисплея применится со следующего разговора GPT.')

def setup_key(config,allow_saved=False):
    return ssh_setup.form(config,ui,allow_saved=allow_saved)

def verify(config):
    if config['mode']=='bridge':
        from .runtime import bridge_env
        env,_=bridge_env(config)
        r=subprocess.run([sys.executable,str(ROOT/'engines/bridge/voice.py'),'open'],env=env,capture_output=True,timeout=65)
        try:opened=json.loads(r.stdout)
        except ValueError:opened={}
        if r.returncode or not opened.get('ok') or not opened.get('voice_active'):
            if opened.get('error')=='browser_verification_required':raise RuntimeError('ChatGPT требует проверку браузера. Выполните bridge login и пройдите её в открывшемся окне; Алиса восстановлена.')
            if opened.get('error')=='voice_daily_limit_reached':raise RuntimeError(friendly('voice_daily_limit_reached'))
            raise RuntimeError('Браузерный голосовой режим не открылся. Проверьте вход, лимиты и сеть; наличие процесса браузера не означает успешный запуск.')
        try:
            from .voice_probe import probe_bridge
            return {'voice_ui_opened':True,**probe_bridge()}
        finally:subprocess.run([sys.executable,str(ROOT/'engines/bridge/voice.py'),'close'],env=env,capture_output=True,timeout=15)
    if not runtime.status(config).get('agent_connected'):runtime.start(config)
    runtime.native_action(config,'open')
    try:
        deadline=time.monotonic()+45
        while time.monotonic()<deadline:
            s=runtime.status(config)
            if s.get('error'):raise RuntimeError(friendly(s['error']))
            if s.get('phase')=='active':break
            time.sleep(.3)
        else:raise RuntimeError('Голосовое соединение не установлено за 45 секунд')
        runtime.native_action(config,'check')
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            s=runtime.status(config)
            if s.get('error'):raise RuntimeError(friendly(s['error']))
            audio=s.get('audio',{})
            if audio.get('assistant_text_seen') and audio.get('audible_blocks',0)>8:
                # Finish the short fixed reply before closing its context.
                time.sleep(3);audio=runtime.status(config).get('audio',audio)
                return {'real_reply_verified':True,'audio_engine':audio.get('audio_engine'),'alsa_underruns':audio.get('alsa_underruns'),
                        'neteq':audio.get('neteq'),'account_tier_not_used_as_access_guarantee':True}
            time.sleep(.3)
        raise RuntimeError('Не получен подтверждённый голосовой ответ. Проверьте сеть и лимит аккаунта.')
    finally:runtime.native_action(config,'close')


def verify_with_stun_retry(config):
    """Retry only an explicit ICE timeout, preserving the original setting on failure."""
    try:return verify(config)
    except RuntimeError as error:
        if (str(error)!='ice_gather_timeout' or config['mode']!='native' or
                config.get('disable_stun',False) or not ui.interactive()):raise
        ui.note('Не удалось установить голосовое соединение: ice_gather_timeout.','yellow')
        ui.note('Для этой сети может помочь отключение STUN. GPT будет перезапущен.')
        if not yes('Повторить проверку без STUN?',False):raise
    previous=copy.deepcopy(config)
    candidate=copy.deepcopy(config);candidate['disable_stun']=True
    stopped=False
    try:
        with ui.activity('Останавливаю GPT перед повтором без STUN'):
            runtime.stop(config)
        stopped=True
        with ui.activity('Запускаю GPT без STUN'):
            start_mode(candidate)
        with ui.activity('Проверяю голос без STUN'):
            result=verify(candidate)
        if not result.get('real_reply_verified'):
            raise RuntimeError('Повторная проверка не подтвердила голосовой ответ.')
        config['disable_stun']=True
        save(config)
    except BaseException:
        config.clear();config.update(previous)
        if stopped:
            try:
                with ui.activity('Восстанавливаю прежнюю настройку STUN'):
                    runtime.stop(candidate)
                    start_mode(previous)
                    save(previous)
            except Exception as rollback_error:
                raise RuntimeError('Не удалось восстановить прежний режим STUN. Проверьте состояние GPT перед повторным запуском.') from rollback_error
        ui.note('Прежняя настройка STUN восстановлена.','yellow')
        raise
    ui.note('Голосовой ответ подтверждён. Отключение STUN сохранено для следующих запусков.','green')
    return result


def stun_settings(config,operation=None):
    if config['mode']!='native':raise ValueError('STUN настраивается только в нативном режиме')
    current=not config.get('disable_stun',False)
    enabled=operation=='stun-on' if operation in ('stun-on','stun-off') else ui.select(
        'Использовать STUN?', [('Да — стандартный режим',True),('Нет — обход ICE-таймаута',False)],default=current)
    if enabled==current:
        ui.note('Настройка STUN не изменилась.');return
    candidate=copy.deepcopy(config);candidate['disable_stun']=not enabled
    save(candidate)
    config.update(candidate)
    ui.note('STUN '+('включён.' if enabled else 'отключён.')+' Применится после перезапуска GPT.','green')


def friendly(error):
    return {'voice_daily_limit_reached':'Дневной лимит голосового ChatGPT исчерпан. Возвращена Алиса; повторите после сброса лимита.',
       'browser_verification_required':'Нужна проверка браузера: выполните bridge login. Алиса восстановлена.',
       'login_required':'Нужно снова войти: login','voice_session_ended':'ChatGPT завершил сеанс; возвращена Алиса.',
       'voice_server_internal_error':'ChatGPT завершил голосовой сеанс с internal_error. Возвращена Алиса. Точная причина отказа сервера не определена.',
       'voice_server_upstream_error':'ChatGPT завершил голосовой сеанс с upstream_error. Возвращена Алиса.',
       'voice_server_error':'ChatGPT завершил голосовой сеанс с server_error. Возвращена Алиса.',
       'webrtc_connect_timeout':'Не открылся управляющий канал Voice. Проверьте сеть/VPN; этап соединения сохранён в диагностике.',
       'ice_gather_timeout':'Не удалось собрать сетевые адреса Voice вовремя. Проверьте сеть/VPN и настройку STUN.'}.get(error,error)

def installation_options(config,network_result):
    """Offer every setting directly; no checklist gates hide configuration."""
    source=None;selection=None;use_vpn=False;vpn_auto=False
    if config['mode']=='native':
        ui.note('Подключение GPT','bold')
        saved=config['vpn'].get('enabled',False)
        recommended=network_result.get('country')=='RU'
        choices=([('Оставить сохранённый VLESS','keep')] if saved else [])+[
          ('Настроить VLESS только для GPT'+(' — рекомендуется для российского IP' if recommended else ''),'configure'),
          ('Подключаться без VLESS','off')]
        choice=ui.select('Как подключать GPT?',choices,default='keep' if saved else 'configure' if recommended else 'off')
        use_vpn=choice!='off'
        if choice=='configure':
            source=ui.secret('Ссылка VLESS / Hysteria 2 / TUIC или HTTPS-подписка')
            client='standard' if vpn.is_proxy_link(source) else pick('Формат подписки',[('Обычная подписка','standard'),('Incy','incy'),('Happ','happ')])
            selection=vpn.select_server(source,client=client)
        elif choice=='keep':ui.note('Сохранённый сервер и его параметры будут использованы повторно.')
    ui.note('Автозапуск','bold')
    config['autostart']=yes('Запускать GPT '+('при включении колонки?' if config['mode']=='native' else 'при входе на ПК?'),config['autostart'])
    if use_vpn:
        vpn_auto=yes('Запускать VLESS при включении колонки?',config['vpn'].get('autostart',False) or config['autostart'])
        if config['autostart'] and not vpn_auto:ui.note('GPT при загрузке запустится только при уже работающем VLESS.')
    ui.note('Голосовые фразы','bold')
    for key,title in [('open','Открыть'),('close','Выйти'),('new','Новый чат'),('previous','Предыдущий чат')]:
        ui.note(title+': «Алиса, '+config['phrases'][key]+'»')
    if ui.select('Оставить эти фразы или изменить?', [('Оставить указанные фразы','keep'),('Изменить фразы','change')],default='keep')=='change':
        settings(config,apply=False,fields=('phrases',))
    ui.note('Подсветка GPT','bold')
    settings(config,apply=False,fields=('led',))
    if config['mode']=='native' and config.get('model') in ('bergamot','cucumber'):
        ui.note('Дисплей во время GPT','bold')
        settings(config,apply=False,fields=('display',))
    return {'use_vpn':use_vpn,'source':source,'selection':selection,'vpn_autostart':vpn_auto}

def wizard(config):
    updating=config.get('installed',False)
    ui.note(NAMES[config['mode']],'cyan');ui.note('Нужна Лайт 1, Лайт 2, Мини 2, Мини 3, Станция 2 или экспериментальная Миди с готовыми root и SSH. Мост — Лайт 2 и Мини 2.')
    if updating:ui.note('Уже установлено. Мастер обновит программу и настройки; вход ChatGPT и чаты сохранятся.','cyan')
    ui.note('Мастер сам выполнит проверки, предложит нужные настройки и запустит установленный режим.')
    ui.step(3,'Подключение и проверка колонки')
    import shutil
    if ssh_setup.missing_tools():
        if sys.platform=='win32':
            raise RuntimeError('Повреждены библиотеки Windows-пакета. Повторно запустите START-WINDOWS.cmd.')
        elif yes('Установить OpenSSH-клиент через менеджер пакетов?',True):
            from . import dependencies
            dependencies.install('native')
    while True:
        try:setup_key(config,allow_saved=True);break
        except (RuntimeError,subprocess.SubprocessError) as error:
            ui.note(str(error) if isinstance(error,RuntimeError) else 'Не удалось выполнить подключение SSH.','red')
            if not yes('Изменить данные подключения и повторить?',True):return
    report=doctor.inspect(config)
    if report['host_missing']:
        print('Нужны компоненты ПК:',', '.join(report['host_missing']))
        if sys.platform=='win32':
            from . import dependencies
            dependencies.install(config['mode'])
        elif yes('Установить пакеты через системный менеджер?'):
            from . import dependencies
            dependencies.install(config['mode'])
    report=doctor.inspect(config) if report['host_missing'] else report
    show(report);doctor.require(config,report=report,warn=False)
    apply_model(config,report.get('model'))
    ui.step(4,'Доступность ChatGPT');network_result=network.check(config);show(network_result)
    ui.step(5,'Настройка режима')
    ui.note('Сохранённый вход будет использован повторно; при необходимости его можно обновить после установки.' if updating else 'Вход в ChatGPT будет выполнен после установки клиента.')
    ui.step(6,'Дополнительные настройки')
    options=installation_options(config,network_result)
    use_vpn=options['use_vpn'];vpn_source=options['source'];vpn_selection=options['selection']
    ui.step(7,'Установка и проверка');ui.note('Режим: '+config['mode']+'  Автозапуск: '+('да' if config['autostart'] else 'нет')+'  VLESS: '+('да' if use_vpn else 'нет'),'cyan')
    from . import storage
    ui.note('Проверяю место для выбранных компонентов…','cyan')
    engine=vpn_selection['selected'].get('_engine','sing-box') if vpn_selection else config['vpn'].get('engine','sing-box') if use_vpn else None
    space=storage.report(config,vpn_engine=engine)
    show_storage(space);storage.require(space)
    if not yes('Применить настройки и обновить установленную программу?' if updating else 'Применить эти настройки и установить?',True):return
    from .install import install
    save(config);show(install(config,activate=False))
    if vpn_source:
        with ui.activity('Устанавливаю и проверяю VLESS'):result=vpn.configure(config,vpn_source,selection=vpn_selection)
        show(result)
    elif config['mode']=='native' and not use_vpn and config['vpn'].get('enabled'):show(vpn.operate(config,'disable'))
    if config['mode']=='native':
        config['vpn']['autostart']=options['vpn_autostart'];autostart.station_flag(config,'vpn-autostart',options['vpn_autostart'])
    with ui.activity('Запускаю GPT и проверяю контроллер'):current=start_mode(config)
    show(current)
    with ui.activity('Сохраняю автозапуск'):
        autostart.set_enabled(config,config['autostart']);save(config)
    logged_in=current.get('login_saved',False)
    if logged_in:ui.note('Вход ChatGPT уже сохранён. Повторная авторизация не нужна.','green')
    if yes('Обновить вход ChatGPT?' if logged_in else 'Войти в ChatGPT сейчас?',not logged_in):accounts.login(config)
    if yes('Проверить голос коротким тестовым ответом?',True):show(verify_with_stun_retry(config))
    ui.note(('Обновление завершено.' if updating else 'Установка завершена.')+' Управление доступно в меню.','green')

def _main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    from . import devices
    if not argv or argv[0] not in ('--version','-h','--help'):
        devices.enter('bridge' if argv and argv[0]=='bridge' else 'native',argv)
    if argv and argv[0]=='update':
        try:return updates.command(argv[1:])
        except ui.Back:return 0
        except (KeyboardInterrupt,EOFError):return 130
        except (RuntimeError,ValueError,OSError,subprocess.SubprocessError) as error:ui.note(str(error),'red');return 1
    parser=argparse.ArgumentParser(prog='alisa',description='Лайт 1/2, Мини 2/3, Станция 2 и экспериментальная Миди: нативный GPT. Мост — Linux, Лайт 2 и Мини 2.')
    parser.add_argument('--version',action='version',version=__version__)
    parser.add_argument('mode',nargs='?',choices=MODES)
    parser.add_argument('action',nargs='?',choices=['install','update','devices','start','run','stop','toggle','status','doctor','network','settings','autostart','login','logout','verify','vpn','ssh','voice','storage','uninstall','help'])
    parser.add_argument('operation',nargs='?')
    parser.add_argument('--host');parser.add_argument('--port',type=int);parser.add_argument('--user');parser.add_argument('--identity')
    parser.add_argument('--dry-run',action='store_true');parser.add_argument('--yes',action='store_true');parser.add_argument('--no-start',action='store_true')
    parser.add_argument('--led',choices=STYLES)
    parser.add_argument('--led-scope',choices=LED_SCOPES)
    args=parser.parse_args(argv)
    try:
        interactive=not args.action
        if interactive:
            ui.banner()
            updates.clean_downloads()
            updates.show_result()
            updates.startup_offer()
            requested_mode=args.mode
            while True:
                mode=requested_mode or pick('1/7 Alisa Voice — выберите режим',[(NAMES[x],x) for x in MODES]+[('Выход','exit')])
                if mode=='exit':return 0
                devices.enter(mode,[mode])
                if sys.platform=='win32':
                    from .winssh import prepare
                    prepare(load(mode))
                if not load(mode).get('installed'):
                    try:wizard(load(mode))
                    except ui.Back:
                        if mode=='native' and len(devices.catalog()['devices'])>1:devices.choose()
                        requested_mode=None;continue
                    if not load(mode).get('installed'):
                        if args.mode:return 0
                        continue
                pending_switch=None
                def execute(action,operation=None):
                    nonlocal pending_switch
                    forwarded=[mode,action]
                    if operation:forwarded.append(operation)
                    for key in ['host','port','user','identity','led','led_scope']:
                        if getattr(args,key) is not None:forwarded+=['--'+key.replace('_','-'),str(getattr(args,key))]
                    try:return main(forwarded)
                    except devices.Switch as request:
                        pending_switch=request;return devices.SWITCHED
                from . import menus
                result=menus.run(mode,execute)
                if result==devices.SWITCHED:raise pending_switch
                if result==130:return 130
                if args.mode:return 0
        mode=args.mode or pick('1/7 Alisa Voice — выберите режим',[(NAMES[x],x) for x in MODES])
        devices.enter(mode,[mode,*argv] if not args.mode else argv)
        if args.action=='devices':
            if mode!='native':raise ValueError('Выбор колонки доступен в нативном режиме')
            devices.choose();return 0
        config=load(mode)
        if args.action=='update':return updates.command([args.operation] if args.operation else [])
        for key in ['host','port','user','identity']:
            if getattr(args,key) is not None:config[key]=getattr(args,key)
        if args.identity is not None:config.pop('ssh_auth',None)
        if args.action!='help' and not (args.action in ('install','ssh') and not args.yes and not args.dry_run):validate(config)
        action=args.action
        devices.require_current(config,action)
        if sys.platform=='win32' and action not in ('ssh','help') and not (action=='install' and not args.yes and not args.dry_run):
            from .winssh import prepare
            prepare(config)
        if action=='install':
            if args.operation=='components':
                if mode!='native':raise ValueError('Обновление компонентов доступно в нативном режиме')
                current=runtime.status(config)
                if current.get('phase') in ('active','connecting','opening','starting','stopping'):
                    raise RuntimeError('Сначала закройте голосовой чат выбранной колонки.')
                from .install import install
                show(install(config,activate=bool(current.get('agent_connected'))))
            elif args.dry_run or args.yes:
                from .install import install
                show(install(config,activate=not args.no_start,dry_run=args.dry_run))
            else:wizard(config)
        elif action in ['start','run']:show(start_mode(config,foreground=action=='run'))
        elif action=='toggle':show(toggle_mode(config))
        elif action=='stop':runtime.stop(config);ui.note('Остановлено; штатная Алиса восстановлена.','green')
        elif action=='status':
            s=runtime.status(config)
            if s.get('error'):s['error_message']=friendly(s['error'])
            show(s)
        elif action=='doctor':show(doctor.inspect(config))
        elif action=='network':show(network.check(config))
        elif action=='storage':
            from . import storage
            if ui.interactive():ui.note('Считаю размер компонентов и свободное место…','cyan')
            show_storage(storage.report(config))
        elif action=='help':installation_help()
        elif action=='uninstall':
            from . import uninstall,storage
            value=uninstall.plan(config)
            if args.dry_run:show(value)
            else:
                ui.note('Удаление программы этого режима освободит примерно '+storage.size_text(value['reclaim_bytes'])+'.','cyan')
                ui.note('Штатная Алиса будет восстановлена. SSH, вход ChatGPT и чаты сохранятся. Копия удаляемых файлов останется на ПК.')
                if yes('Удалить программу выбранного режима?',False):show(uninstall.remove(config,value))
                else:ui.note('Удаление отменено.')
        elif action=='settings':
            if args.operation=='led-scope' or args.led_scope:
                if mode!='native':raise ValueError('Режим подсветки настраивается для нативного режима')
                if not supports_lighting(config.get('model')):raise ValueError(LIGHTING_UNAVAILABLE)
                from . import lighting
                previous=copy.deepcopy(config)
                config['led_scope']=args.led_scope or lighting.choose_scope(config)
                if args.led:config['led']=args.led
                save_preferences(config,previous)
                return 0
            if not supports_lighting(config.get('model')) and (args.operation=='listen-on' or (args.led and args.led!='off')):
                raise ValueError(LIGHTING_UNAVAILABLE)
            if args.operation in ('stun','stun-on','stun-off'):
                stun_settings(config,args.operation)
            elif args.operation in ('listen-on','listen-off'):
                if mode!='native':raise ValueError('Подсветка в паузах настраивается для нативного режима')
                previous=copy.deepcopy(config);config['listen_led']=args.operation=='listen-on'
                save_preferences(config,previous)
                ui.note('Подсветка в паузах GPT '+('включена.' if config['listen_led'] else 'выключена.'))
            elif args.led:
                previous=copy.deepcopy(config);config['led']=args.led
                save_preferences(config,previous)
            elif args.operation in ('midi-fire-on','midi-fire-off'):
                if mode!='native' or config.get('model')!='cucumber':raise ValueError('Пламя доступно только на Миди')
                previous=copy.deepcopy(config);config['midi_fire']=args.operation=='midi-fire-on';save_preferences(config,previous)
            elif args.operation in ('phrases','led','display'):settings(config,fields=(args.operation,))
            else:settings(config)
        elif action=='autostart':
            operation=args.operation or pick('Автозапуск', [('Включить','on'),('Выключить','off'),('Проверить','status')])
            if operation=='status':show({'enabled':autostart.enabled(config)})
            elif operation=='configure':return auto_settings(config)
            elif operation in ['on','off']:autostart.set_enabled(config,operation=='on');save(config);show({'enabled':operation=='on'})
            else:raise ValueError('Используйте autostart on, off или status')
        elif action=='login':accounts.login(config,change=args.operation=='change')
        elif action=='logout':accounts.logout(config)
        elif action=='verify':show(verify_with_stun_retry(config))
        elif action=='voice':
            if mode!='native':raise ValueError('В браузерном мосте голос выбирается в настройках ChatGPT')
            current=runtime.status(config).get('settings',{}).get('voice')
            voice=args.operation or ui.select('Голос GPT (сохранится на колонке)',[(x,x) for x in VOICES],default=current)
            if voice not in VOICES:raise ValueError('Неизвестный голос')
            if voice==current:ui.note('Голос GPT: '+voice+'. Без изменений.')
            else:
                runtime.native_action(config,'voice',voice)
                ui.note('Голос GPT: '+voice+'. Сохранён для следующего разговора.','green')
        elif action=='ssh':setup_key(config)
        elif action=='vpn':
            if mode!='native':raise ValueError('VLESS настраивается в нативном режиме')
            operation=args.operation or pick('VLESS', [('Настроить ссылку / подписку','configure'),('Запустить','start'),('Остановить','stop'),('Состояние','status'),('Автозапуск','autostart'),('Отключить использование VPN','disable')])
            if operation=='configure':show(vpn_configure(config,ui.secret('Ссылка VPN или HTTPS-подписка')))
            elif operation=='remove':
                from .storage import size_text
                value=vpn.removal_plan(config)
                if args.dry_run:show(value)
                else:
                    ui.note('Будут удалены программа VPN, серверы и активные настройки VLESS на колонке и ПК.')
                    ui.note('На колонке освободится '+size_text(value['reclaim_bytes'])+'. GPT будет использовать подключение без VPN; вход, чаты и SSH сохранятся.')
                    if yes('Удалить VLESS и его файлы?',False):show(vpn.remove(config,value))
                    else:ui.note('Удаление VLESS отменено.')
            elif operation in ('autostart','autostart-on','autostart-off'):
                enabled=yes('Запускать VLESS при загрузке колонки?',config['vpn'].get('autostart',False)) if operation=='autostart' else operation=='autostart-on'
                if enabled and not config['vpn'].get('enabled'):raise RuntimeError('Сначала настройте VLESS')
                autostart.station_flag(config,'vpn-autostart',enabled);config['vpn']['autostart']=enabled;save(config)
                ui.note('Автозапуск VLESS '+('включён.' if enabled else 'выключен.'),'green')
            else:show(vpn.operate(config,operation))
        return 0
    except ui.Back:ui.note('Изменения отменены. Возврат в меню.');return 0
    except (KeyboardInterrupt,EOFError):ui.note('\nОперация прервана.','yellow');return 130
    except (RuntimeError,ValueError,OSError,subprocess.SubprocessError) as error:
        from .errors import PublicError
        if __import__('os').environ.get('ALISA_UPDATE_WORKER')=='1' and isinstance(error,PublicError):
            print(json.dumps({'update_error':error.code}));return 1
        # Unexpected subprocess errors can contain commands; avoid printing those.
        if isinstance(error,subprocess.SubprocessError):ui.note('Ошибка внешнего компонента; проверьте doctor и status.','red')
        else:ui.note(str(error),'red')
        return 1


def main(argv=None):
    from . import devices
    try:return _main(argv)
    except devices.Switch as request:
        # Recursive menu actions must unwind the terminal before replacing it.
        if argv is not None:raise
        try:devices.restart(request)
        except (OSError,RuntimeError) as error:ui.note(str(error) or 'Не удалось переключить колонку. Повторите запуск программы.','red');return 1
    except (ValueError,RuntimeError,OSError) as error:
        ui.note(str(error),'red');return 1
