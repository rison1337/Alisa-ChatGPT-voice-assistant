import argparse,copy,json,subprocess,sys,time
from .state import ROOT,MODES,STYLES,STYLE_NAMES,VOICES,load,save,validate
from . import doctor,network,runtime,autostart,accounts,vpn
from . import ui,ssh_setup
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
      '2. Введите IP, порт и пароль. После проверки сохранится SSH-ключ.',
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
    if not source.strip().startswith('vless://'):
        client=pick('Формат подписки', [('Обычная VLESS / Base64 / JSON / YAML','standard'),('Incy (Xray JSON)','incy'),('Happ','happ')])
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
    validate(config)
    if apply:save_preferences(config,previous)
    else:ui.note('Настройки выбраны.','green')

def save_preferences(config,previous):
    if all(config[key]==previous[key] for key in ('phrases','led')):
        ui.note('Настройки не изменились.');return
    runtime.update_preferences(config,previous)
    save(config);ui.note('Настройки сохранены.','green')

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

def friendly(error):
    return {'voice_daily_limit_reached':'Дневной лимит голосового ChatGPT исчерпан. Возвращена Алиса; повторите после сброса лимита.',
       'browser_verification_required':'Нужна проверка браузера: выполните bridge login. Алиса восстановлена.',
       'login_required':'Нужно снова войти: login','voice_session_ended':'ChatGPT завершил сеанс; возвращена Алиса.'}.get(error,error)

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
            source=ui.secret('Ссылка vless:// или HTTPS-подписка')
            client='standard' if source.strip().startswith('vless://') else pick('Формат подписки',[('Обычная подписка','standard'),('Incy','incy'),('Happ','happ')])
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
    return {'use_vpn':use_vpn,'source':source,'selection':selection,'vpn_autostart':vpn_auto}

def wizard(config):
    updating=config.get('installed',False)
    ui.note(NAMES[config['mode']],'cyan');ui.note('Нужна Лайт 2 с готовыми root и SSH.')
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
    show(report);doctor.require(config,report=report)
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
    if yes('Проверить голос коротким тестовым ответом?',True):show(verify(config))
    ui.note(('Обновление завершено.' if updating else 'Установка завершена.')+' Управление доступно в меню.','green')

def main(argv=None):
    parser=argparse.ArgumentParser(prog='alisa',description='Лайт 2: тестовый нативный установщик Windows.' if sys.platform=='win32' else 'Лайт 2: нативный GPT или браузерный мост. Сначала выберите режим.')
    parser.add_argument('mode',nargs='?',choices=MODES)
    parser.add_argument('action',nargs='?',choices=['install','start','run','stop','toggle','status','doctor','network','settings','autostart','login','logout','verify','vpn','ssh','voice','storage','uninstall','help'])
    parser.add_argument('operation',nargs='?')
    parser.add_argument('--host');parser.add_argument('--port',type=int);parser.add_argument('--user');parser.add_argument('--identity')
    parser.add_argument('--dry-run',action='store_true');parser.add_argument('--yes',action='store_true');parser.add_argument('--no-start',action='store_true')
    parser.add_argument('--led',choices=STYLES)
    args=parser.parse_args(argv)
    try:
        interactive=not args.action
        if interactive:
            ui.banner()
            requested_mode=args.mode
            while True:
                mode=requested_mode or pick('1/7 Alisa Voice — выберите режим',[(NAMES[x],x) for x in MODES]+[('Выход','exit')])
                if mode=='exit':return 0
                if not load(mode).get('installed'):
                    try:wizard(load(mode))
                    except ui.Back:
                        requested_mode=None;continue
                    if not load(mode).get('installed'):
                        if args.mode:return 0
                        continue
                def execute(action,operation=None):
                    forwarded=[mode,action]
                    if operation:forwarded.append(operation)
                    for key in ['host','port','user','identity','led']:
                        if getattr(args,key) is not None:forwarded+=['--'+key,str(getattr(args,key))]
                    return main(forwarded)
                from . import menus
                if menus.run(mode,execute)==130:return 130
                if args.mode:return 0
        mode=args.mode or pick('1/7 Alisa Voice — выберите режим',[(NAMES[x],x) for x in MODES])
        config=load(mode)
        for key in ['host','port','user','identity']:
            if getattr(args,key) is not None:config[key]=getattr(args,key)
        if args.action!='help' and not (args.action in ('install','ssh') and not args.yes and not args.dry_run):validate(config)
        action=args.action
        if action=='install':
            if args.dry_run or args.yes:
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
            if args.led:
                previous=copy.deepcopy(config);config['led']=args.led
                save_preferences(config,previous)
            elif args.operation in ('phrases','led'):settings(config,fields=(args.operation,))
            else:settings(config)
        elif action=='autostart':
            operation=args.operation or pick('Автозапуск', [('Включить','on'),('Выключить','off'),('Проверить','status')])
            if operation=='status':show({'enabled':autostart.enabled(config)})
            elif operation=='configure':return auto_settings(config)
            elif operation in ['on','off']:autostart.set_enabled(config,operation=='on');save(config);show({'enabled':operation=='on'})
            else:raise ValueError('Используйте autostart on, off или status')
        elif action=='login':accounts.login(config,change=args.operation=='change')
        elif action=='logout':accounts.logout(config)
        elif action=='verify':show(verify(config))
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
            if operation=='configure':show(vpn_configure(config,ui.secret('VLESS или подписка')))
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
        # Unexpected subprocess errors can contain commands; avoid printing those.
        if isinstance(error,subprocess.SubprocessError):ui.note('Ошибка внешнего компонента; проверьте doctor и status.','red')
        else:ui.note(str(error),'red')
        return 1
