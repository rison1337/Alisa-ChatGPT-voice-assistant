"""Compact management pages with immediate hints and cached device reads."""
import time
from .state import load,STYLE_NAMES
from . import ui

TITLES={'main':'Нативный режим','settings':'Настройки','account':'Аккаунт ChatGPT',
        'vpn':'VPN / VLESS','service':'Установка и обслуживание','checks':'Проверки','autostart':'Автозапуск','updates':'Обновления'}

def auto_entries(mode,config):
    values=[(('[✓]' if config.get('autostart') else '[ ]')+' Автозапуск GPT',('autostart','off' if config.get('autostart') else 'on'))]
    vpn=config.get('vpn',{})
    if mode=='native' and vpn.get('enabled'):
        values.append((('[✓]' if vpn.get('autostart') else '[ ]')+' Автозапуск VLESS',('vpn','autostart-off' if vpn.get('autostart') else 'autostart-on')))
    return values

def entries(mode,page,config=None):
    config=config or {}
    if page=='main':
        values=[('GPT — запустить / остановить',('toggle',None)),('Настройки','@settings'),('Аккаунт ChatGPT','@account')]
        if mode=='native':values.append(('VPN / VLESS','@vpn'))
        values.append(('Установка и обслуживание','@service'))
    elif page=='settings':
        values=[('Голосовые фразы',('settings','phrases')),('Подсветка',('settings','led'))]
        if mode=='native':values.append(('Голос GPT',('voice',None)))
        values+=auto_entries(mode,config)
    elif page=='account':values=[('Войти',('login',None)),('Сменить аккаунт',('login','change')),('Выйти из аккаунта',('logout',None))]
    elif page=='vpn':values=[('Запустить VPN',('vpn','start')),('Остановить VPN',('vpn','stop')),
        ('Сервер / подписка',('vpn','configure')),
        (('[✓]' if config.get('vpn',{}).get('autostart') else '[ ]')+' Автозапуск VLESS',('vpn','autostart-off' if config.get('vpn',{}).get('autostart') else 'autostart-on')),
        ('Не использовать VPN',('vpn','disable')),('Удалить VLESS и его файлы',('vpn','remove'))]
    elif page=='service':values=[('Обновления программы','@updates'),('Настроить / переустановить' if config.get('installed') else 'Мастер установки',('install',None)),('Место и компоненты',('storage',None)),
        ('Проверки','@checks'),('Подключение SSH',('ssh',None)),('Как работает установка',('help',None)),('Удалить программу',('uninstall',None))]
    elif page=='updates':
        from .updates import preferences
        ask=preferences()['ask_on_start']
        values=[('Проверить и обновить',('update',None)),
                (('[✓]' if ask else '[ ]')+' Предлагать при запуске',('update','ask-off' if ask else 'ask-on'))]
    elif page=='checks':values=[('Колонка и компоненты',('doctor',None)),('Сеть ChatGPT',('network',None)),('Голосовой ответ',('verify',None))]
    elif page=='autostart':values=auto_entries(mode,config)
    else:raise ValueError('Неизвестная страница')
    return values+[('Другой режим' if page=='main' else 'Назад','back')]

def run(mode,execute,start_page='main'):
    page=start_page;previous={};results={};history=[];cache={};revision=0;configuration=load(mode)
    titles=dict(TITLES);titles['main']='Нативный режим' if mode=='native' else 'Браузерный мост'
    def view():
        nonlocal configuration
        configuration=load(mode);values=entries(mode,page,configuration)
        old=previous.get(page)
        if isinstance(old,tuple) and old not in [value for _,value in values]:
            candidates=[value for _,value in values if isinstance(value,tuple) and value[0]==old[0] and
                        ((old[0]=='autostart' and value[1] in ('on','off')) or (old[0]=='vpn' and str(value[1]).startswith('autostart-')) or (old[0]=='update' and value[1] in ('ask-on','ask-off')))]
            if candidates:previous[page]=candidates[0]
        endpoint=(configuration.get('host') or 'IP ещё не указан')+':'+str(configuration.get('port') or 2222)
        subtitle=endpoint+'  ·  '+('Установлен' if configuration.get('installed') else 'Требуется установка')
        return dict(title=titles[page],items=values,default=previous.get(page),details=results.get(page,''),subtitle=subtitle)
    def inline(choice):
        if isinstance(choice,str):return True
        action,operation=choice
        return action in ('toggle','help','doctor','network','storage','status','start','stop','verify') or (action=='vpn' and operation in ('status','start','stop','disable','autostart-on','autostart-off')) or (action=='autostart' and operation in ('status','on','off')) or (action=='update' and operation in ('ask-on','ask-off'))
    def location(context):return next(key for key,value in titles.items() if value==context)
    def read_request(context,choice):
        if choice=='back':return None
        if choice==('toggle',None):return ('status',None)
        if choice=='@vpn' or location(context)=='vpn':return ('vpn','status')
        if isinstance(choice,tuple) and choice[0] in ('help','doctor','network','storage','status'):return choice
        return None
    def ttl(request):return 5 if request[0] in ('status','vpn') else 60 if request[0]=='network' else 30
    def phrase_summary():return '\n'.join('Алиса, '+text for text in configuration['phrases'].values())
    def led_name():
        style=configuration['led']
        return ('Пульсация: '+STYLE_NAMES[style[6:]].lower()) if style.startswith('pulse-') else STYLE_NAMES[style]
    def auto_summary():
        text='Автозапуск GPT: '+('включён' if configuration['autostart'] else 'выключен')
        if mode=='native' and configuration['vpn'].get('enabled'):text+='\nАвтозапуск VLESS: '+('включён' if configuration['vpn'].get('autostart') else 'выключен')
        return text
    def hint(choice):
        if choice=='@updates' or (isinstance(choice,tuple) and choice[0]=='update'):
            from . import __version__
            from .updates import preferences
            return ('Версия программы: '+__version__+'\nПредложения при запуске: '+('включены' if preferences()['ask_on_start'] else 'выключены')+
                    '\n\nНовая версия устанавливается после выбора «Обновить». Настройки, вход и чаты сохраняются. Для обновления установленных клиентов требуется SSH к колонке.')
        return {('login',None):'Вход в текущем профиле браузера. Подходит для первого входа и восстановления сессии.',
                ('login','change'):'Откроется чистый профиль для другого аккаунта. Текущий вход сохранится до успешной проверки нового.',
                ('logout',None):'Удалить текущий вход ChatGPT этого клиента. Чаты аккаунта на сайте и SSH колонки сохранятся.',
                ('vpn','configure'):'Добавить или заменить сервер VLESS. После выбора сервера появится вопрос об автозапуске.',
                ('vpn','disable'):'Перейти на подключение без VPN. Программа и настройки VLESS останутся на колонке.',
                ('vpn','remove'):'Удалить VPN-программу, серверы и активные настройки VLESS с колонки и ПК. GPT перейдёт на подключение без VPN; аккаунт, чаты и SSH сохранятся.',
                ('vpn','autostart-on'):'Запускать VLESS при включении колонки. Требуется настроенный сервер.',
                ('vpn','autostart-off'):'Выключить автозапуск VLESS. Работающий VPN сейчас не останавливается.',
                ('install',None):'Обновление установленной программы и настройка параметров. Совпадающие файлы нативного клиента пропускаются; вход и чаты сохраняются.' if configuration.get('installed') else 'Пошаговая установка выбранного режима.'}.get(choice,'')
    def describe(text,choice):
        extra=hint(choice)
        return text+('\n\n'+extra if extra else '')
    def focus(context,choice):
        request=read_request(context,choice)
        if request:
            if request[0]=='help':return ui.FocusContent(preview(context,choice))
            saved=cache.get(request)
            if saved:return ui.FocusContent(describe(saved[1],choice),time.monotonic()-saved[0]>=ttl(request))
            waiting={'status':'Получаю состояние GPT…','vpn':'Получаю состояние VPN…','doctor':'Проверяю колонку и компоненты…',
                     'network':'Проверяю доступность ChatGPT…','storage':'Считаю свободное место…','help':'Открываю описание установки…'}
            return ui.FocusContent(describe(waiting[request[0]],choice),True)
        if choice=='@settings':return ui.FocusContent(phrase_summary()+'\n\nПодсветка: '+led_name()+'\n\n'+auto_summary())
        if choice==('settings','phrases'):return ui.FocusContent(phrase_summary())
        if choice==('settings','led'):return ui.FocusContent('Подсветка: '+led_name())
        if isinstance(choice,tuple) and (choice[0]=='autostart' or (choice[0]=='vpn' and str(choice[1]).startswith('autostart-'))):return ui.FocusContent(auto_summary())
        if choice==('uninstall',None):return ui.FocusContent('Программные файлы будут удалены после подтверждения. SSH, аккаунт и чаты сохранятся.')
        if choice==('verify',None):return ui.FocusContent('Короткий голосовой ответ прозвучит на колонке.')
        return ui.FocusContent(hint(choice))
    def preview(context,choice):
        request=read_request(context,choice)
        if not request:return focus(context,choice).text
        saved=cache.get(request)
        if saved and time.monotonic()-saved[0]<ttl(request):return describe(saved[1],choice)
        before=revision
        with ui.feedback(quiet=True) as messages:execute(*request)
        text='\n'.join(messages) or 'Нет данных.'
        if revision==before:cache[request]=(time.monotonic(),text)
        return describe(text,choice)
    def choose(choice):
        nonlocal page,revision
        if choice=='back':
            if page=='main':return 0
            page=history.pop() if history else 'main';return view()
        previous[page]=choice
        if isinstance(choice,str) and choice.startswith('@'):
            history.append(page);page=choice[1:];return view()
        revision+=1;cache.clear()
        with ui.feedback(quiet=inline(choice)) as messages:code=execute(*choice)
        results[page]='\n'.join(messages) or ('Действие завершено.' if code==0 else 'Действие не выполнено.')
        if choice[0] in ('help','doctor','network','storage','status'):cache[choice]=(time.monotonic(),results[page])
        if choice[0]=='toggle':cache[('status',None)]=(time.monotonic(),results[page])
        if choice[0]=='vpn' and choice[1] in ('start','stop'):cache[('vpn','status')]=(time.monotonic(),results[page])
        if code==130:return code
        return view()
    if ui.interactive():return ui.dashboard(**view(),on_select=choose,inline=inline,on_focus=preview,on_focus_hint=focus)
    while True:
        result=choose(ui.dashboard(**view()))
        if isinstance(result,int):return result
