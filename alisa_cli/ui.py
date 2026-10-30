"""Terminal UI on the installation computer; nothing runs on the station."""
import contextlib,contextvars,getpass,json,os,shutil,sys
from dataclasses import dataclass
from pathlib import Path

def interactive():
    return sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get('TERM')!='dumb'

def color(text,name):
    if not sys.stdout.isatty() or 'NO_COLOR' in os.environ:return str(text)
    # Let the terminal theme supply its palette. ANSI bright-black can be
    # almost invisible on translucent backgrounds, so secondary text stays
    # in the normal foreground rather than being dimmed.
    code={'cyan':'34','blue':'34','purple':'34','accent':'34','green':'32','yellow':'33','red':'31',
          'muted':'39','bold':'1'}[name]
    return '\033['+code+'m'+str(text)+'\033[0m'

def library():
    vendor=str(Path(__file__).parent/'_vendor')
    if vendor not in sys.path:sys.path.insert(0,vendor)
    import questionary
    return questionary

def style():
    q=library()
    if 'NO_COLOR' in os.environ:return q.Style([(key,'bold' if key in ('highlighted','question') else '') for key in ('qmark','question','answer','pointer','highlighted','selected','separator','instruction','text')])
    return q.Style([('qmark','fg:ansiblue'),('question','bold'),('answer','fg:ansiblue'),
       ('pointer','fg:ansiblue bold'),('highlighted','reverse bold'),('selected','fg:ansiblue bold'),
       ('separator',''),('instruction',''),('text','')])

def banner():
    print()
    print('  '+color('ALISA VOICE','bold'))
    print('  ChatGPT Voice · Яндекс Станция Лайт 2')
    print('  '+color('─'*min(44,max(12,shutil.get_terminal_size((80,24)).columns-4)),'accent'))
    print()

_feedback=contextvars.ContextVar('alisa_feedback',default=(None,False))

class Back(Exception):
    """Cancel the current dialog without leaving management."""

@dataclass(frozen=True)
class FocusContent:
    text:str=''
    refresh:bool=False

def ask(question):
    from prompt_toolkit.key_binding import KeyBindings,merge_key_bindings
    bindings=KeyBindings()
    @bindings.add('escape',eager=True)
    def back(event):event.app.exit(exception=Back)
    question.application.key_bindings=merge_key_bindings([question.application.key_bindings,bindings])
    return question.unsafe_ask()

@contextlib.contextmanager
def feedback(quiet=False):
    messages=[];token=_feedback.set((messages,quiet))
    try:yield messages
    finally:_feedback.reset(token)

def note(text,kind='muted'):
    messages,quiet=_feedback.get()
    if messages is not None:messages.append(str(text))
    if not quiet:print(color(text,kind))

@contextlib.contextmanager
def activity(title):
    """Show the current long operation without changing redirected JSON."""
    messages,quiet=_feedback.get()
    if not interactive() or quiet:
        yield lambda text:None
        return
    import threading,time
    started=time.monotonic();done=threading.Event();label=[title]
    def render():
        frames='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏';index=0
        while not done.is_set():
            width=max(12,shutil.get_terminal_size((80,24)).columns-2)
            line=f'{frames[index%len(frames)]} {label[0]} · {time.monotonic()-started:.0f} с'
            print('\r\033[2K'+line[:width],end='',flush=True)
            index+=1;done.wait(.15)
    thread=threading.Thread(target=render,daemon=True);thread.start();success=False
    try:
        yield lambda text:label.__setitem__(0,text)
        success=True
    finally:
        done.set();thread.join()
        print('\r\033[2K',end='',flush=True)
        note(title+(' — готово' if success else ' — остановлено')+f' ({time.monotonic()-started:.1f} с)', 'green' if success else 'yellow')

def dashboard(title,items,default=None,details='',subtitle='',help_text='',on_select=None,inline=None,on_focus=None,on_focus_hint=None,focus_delay=.35):
    """One screen in the alternate buffer, with a stable selection and results."""
    if not interactive():return select(title,items,default)
    library()
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout,HSplit,VSplit,Window,FormattedTextControl
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.data_structures import Point
    from prompt_toolkit.styles import Style
    from prompt_toolkit.application import run_in_terminal
    import asyncio
    cursor=next((i for i,(_,v) in enumerate(items) if v==default),0)
    busy=False;cancel_requested=False;preview_task=None;generation=0
    preview_lock=asyncio.Lock()
    def options():
        result=[]
        for i,(label,_) in enumerate(items):
            result += [('class:accent' if i==cursor else '', ' › ' if i==cursor else '   '),
                       ('class:active' if i==cursor else '',label),('', '\n')]
        return result[:-1]
    options_control=FormattedTextControl(options,get_cursor_position=lambda:Point(0,cursor),show_cursor=False)
    if not details:details=help_text
    def right():return details.strip()
    columns=shutil.get_terminal_size((100,28)).columns
    menu=Window(options_control,wrap_lines=True,width=Dimension.exact(40))
    if columns>=88:
        body=VSplit([menu,Window(width=4,char=' '),Window(FormattedTextControl(right),wrap_lines=True,width=Dimension(min=32,preferred=74,max=74)),Window(width=Dimension(min=0,weight=1))])
    else:
        body=HSplit([menu,Window(height=1),Window(FormattedTextControl(right),wrap_lines=True,height=Dimension(min=3,preferred=7,max=9))])
    header=Window(FormattedTextControl(lambda:[('class:accent',' ALISA VOICE  '),('class:title',title),('', '\n '+subtitle)]),height=3,wrap_lines=True)
    def footer_text():
        if busy:return ' Дождитесь завершения операции…'
        return ' ↑/↓ — выбор   Enter — выполнить'+('   Пробел — переключить' if any(label.startswith(('[✓]','[ ]')) for label,_ in items) else '')+'   Esc — назад   Ctrl+C — выйти'
    footer=Window(FormattedTextControl(footer_text),height=2,wrap_lines=True)
    layout=Layout(HSplit([header,body,Window(height=1),footer]),focused_element=menu)
    bindings=KeyBindings()
    @bindings.add('up')
    @bindings.add('k')
    def up(event):
        nonlocal cursor
        if not busy:cursor=(cursor-1)%len(items);preview(event.app)
    @bindings.add('down')
    @bindings.add('j')
    def down(event):
        nonlocal cursor
        if not busy:cursor=(cursor+1)%len(items);preview(event.app)
    @bindings.add('home')
    def home(event):
        nonlocal cursor
        if not busy:cursor=0;preview(event.app)
    @bindings.add('end')
    def end(event):
        nonlocal cursor
        if not busy:cursor=len(items)-1;preview(event.app)
    def cancel_preview():
        nonlocal generation
        generation+=1
        if preview_task is not None:preview_task.cancel()
    async def load_preview(app,token,context,choice):
        nonlocal details
        await asyncio.sleep(focus_delay)
        async with preview_lock:
            if token!=generation:return
            request=asyncio.create_task(asyncio.to_thread(on_focus,context,choice))
            try:value=await asyncio.shield(request)
            except asyncio.CancelledError:
                await request
                raise
        if token==generation and not busy and value is not None:
            details=value;app.invalidate()
    def preview(app):
        nonlocal preview_task,details
        cancel_preview()
        content=on_focus_hint(title,items[cursor][1]) if on_focus_hint is not None else FocusContent(details,on_focus is not None)
        details=content.text
        if content.refresh and on_focus is not None:
            preview_task=app.create_background_task(load_preview(app,generation,title,items[cursor][1]))
        app.invalidate()
    async def perform(event,choice):
        nonlocal title,items,details,subtitle,help_text,cursor,busy
        try:
            if inline is None or inline(choice):result=await asyncio.to_thread(on_select,choice)
            else:
                def dialog():
                    # The suspended parent's pending Escape flush timer must
                    # never consume input intended for a child prompt.
                    from prompt_toolkit.application import create_app_session
                    from prompt_toolkit.input import create_input
                    child_input=create_input()
                    try:
                        with create_app_session(input=child_input):return on_select(choice)
                    finally:child_input.close()
                result=await run_in_terminal(dialog,in_executor=True)
            if isinstance(result,int):event.app.exit(result=result);return
            title=result['title'];items=result['items'];details=result.get('details','')
            subtitle=result.get('subtitle','');help_text=result.get('help_text','')
            cursor=next((i for i,(_,v) in enumerate(items) if v==result.get('default')),0)
            if cancel_requested:event.app.exit(result=130)
            elif result.get('default')!=choice:preview(event.app)
        except (KeyboardInterrupt,EOFError):event.app.exit(result=130)
        except Exception as error:
            event.app.exit(exception=error)
        finally:busy=False;event.app.invalidate()
    def choose(event,choice):
        nonlocal busy
        if busy:return
        if on_select is None:event.app.exit(result=choice);return
        cancel_preview()
        busy=True;event.app.create_background_task(perform(event,choice));event.app.invalidate()
    @bindings.add('enter')
    def enter(event):choose(event,items[cursor][1])
    @bindings.add(' ')
    def toggle(event):
        if items[cursor][0].startswith(('[✓]','[ ]')):choose(event,items[cursor][1])
    @bindings.add('escape')
    def back(event):choose(event,'back')
    @bindings.add('c-c')
    def cancel(event):
        nonlocal cancel_requested
        if busy:cancel_requested=True
        else:event.app.exit(exception=KeyboardInterrupt)
    theme=Style([('accent','fg:ansiblue bold'),('title','bold'),('active','reverse bold')])
    if 'NO_COLOR' in os.environ:theme=Style([('accent','bold'),('title','bold'),('active','reverse bold')])
    app=Application(layout=layout,key_bindings=bindings,style=theme,full_screen=True,mouse_support=False,min_redraw_interval=1/60)
    return app.run(pre_run=lambda:preview(app))

def step(number,title):
    print('\n'+color(f' {number}/7 ','purple')+' '+color(title,'bold'))

def select(title,items,default=None):
    if not items:raise ValueError('Нет доступных пунктов')
    if interactive():
        q=library()
        return ask(q.select(title,choices=[q.Choice(label,value=value) for label,value in items],default=default,
             pointer='›',qmark='◆',instruction='\n  ↑/↓ — выбор · Enter — подтвердить · Esc — назад',style=style()))
    print('\n'+title)
    for n,(label,_) in enumerate(items,1):print(f'  {n}. {label}')
    while True:
        value=input('Выбор: ').strip()
        if not value and default is not None:return default
        if value.isdigit() and 1<=int(value)<=len(items):return items[int(value)-1][1]
        print('Введите номер из списка.')

def confirm(title,default=False):
    if interactive():return select(title,[('Да',True),('Нет',False)],default=default)
    while True:
        value=input(title+(' [Д/н]: ' if default else ' [д/Н]: ')).strip().lower()
        if not value:return default
        if value in ('д','да','y','yes'):return True
        if value in ('н','нет','n','no'):return False
        print('Введите да или нет.')

def checklist(title,items,selected=()):
    if interactive():
        q=library()
        return ask(q.checkbox(title,choices=[q.Choice(label,value=value,checked=value in selected) for label,value in items],
                pointer='›',qmark='◆',instruction='\n  ↑/↓ — выбор · Пробел — галочка · Enter — продолжить · Esc — назад',style=style()))
    return [value for label,value in items if confirm(label,value in selected)]

def text(title,default='',validate=None):
    if interactive():return ask(library().text(title,default=str(default),validate=validate or (lambda _:True),qmark='◆',instruction='Esc — назад',style=style())).strip()
    while True:
        value=input(title+(f' [{default}]' if default else '')+': ').strip() or str(default)
        result=validate(value) if validate else True
        if result is True:return value
        print(result if isinstance(result,str) else 'Проверьте введённое значение.')

def secret(title,optional=False):
    if interactive():
        return ask(library().password(title,qmark='◆',instruction='Esc — назад',style=style(),validate=(lambda value:True if value or optional else 'Введите пароль')))
    while True:
        value=getpass.getpass(title+': ')
        if value or optional:return value
        print('Введите пароль.')

def wait_until(title,ready,interval=.5):
    """Wait for an external user action, with automatic completion and Esc."""
    import asyncio,time
    if not interactive():
        note(title)
        while not ready():time.sleep(interval)
        return
    library()
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout,HSplit,Window,FormattedTextControl
    bindings=KeyBindings()
    @bindings.add('escape',eager=True)
    def back(event):event.app.exit(exception=Back)
    @bindings.add('c-c')
    def cancel(event):event.app.exit(exception=KeyboardInterrupt)
    async def watch():
        try:
            while not await asyncio.to_thread(ready):await asyncio.sleep(interval)
            app.exit()
        except Exception as error:app.exit(exception=error)
    app=Application(layout=Layout(HSplit([
        Window(FormattedTextControl(' ALISA VOICE · Вход в ChatGPT'),height=2),
        Window(FormattedTextControl(title+'\n\nВход будет сохранён автоматически.'),wrap_lines=True),
        Window(FormattedTextControl(' Esc — отменить'),height=1)])),key_bindings=bindings,full_screen=True)
    app.run(pre_run=lambda:app.create_background_task(watch()))

LABELS={'mode':'Режим','phase':'Состояние','agent_connected':'Контроллер колонки','login_saved':'Вход ChatGPT сохранён',
 'ssh':'Подключение SSH','root':'Права root','plum':'Модель поддерживается','model':'Модель','mode_supported':'Режим поддерживается',
 'firmware_version':'Версия прошивки','firmware_status':'Проверка прошивки','warnings':'Предупреждение',
 'architecture':'Архитектура','free_data_kib':'Свободно на колонке, КиБ','host_missing':'Отсутствуют компоненты ПК',
 'station_missing':'Отсутствуют компоненты колонки','ok':'Проверка пройдена','country':'Страна выхода','ip':'Внешний IP',
 'network_status':'Проверка IP','chatgpt_http_status':'HTTP ChatGPT','voice_verified':'Голос проверен','message':'Подробности',
 'installed':'Установлен','started':'Запущен','running':'Работает','configured':'Настроен','autostart':'Автозапуск',
 'real_reply_verified':'Получен голосовой ответ','audio_engine':'Звуковой движок','alsa_underruns':'Сбои воспроизведения ALSA',
 'error':'Ошибка','error_message':'Причина','engine':'Движок VPN','servers_available':'Доступно серверов','rejected':'Пропущено записей',
 'enabled':'Включён','voice':'Голос GPT','key_saved':'SSH-ключ сохранён','rss_kib':'Память клиента, КиБ',
 'voice_ui_opened':'Голосовой интерфейс открыт','direct_connection_selected':'Выбран режим без VPN',
 'removed':'Программа удалена','reclaimed_bytes':'Освобождено байт','backup':'Копия удалённых файлов'}
LABELS.update(vpn_enabled='Используется для GPT',vpn_removed='VLESS удалён',files_updated='Обновлено файлов',files_unchanged='Файлов уже актуально')
LABELS.update(current_version='Установленная версия',latest_version='Новая версия',available='Доступно обновление',ask_on_start='Предлагать при запуске',platform='Система')

def show(value):
    if not interactive():print(json.dumps(value,ensure_ascii=False,indent=2));return
    if not isinstance(value,dict):note(str(value),'cyan');return
    messages,quiet=_feedback.get()
    if not quiet:print()
    # Keep protocol internals available in redirected JSON, while the menu
    # presents state and useful diagnostics rather than conversation UUIDs.
    for key,item in value.items():
        if key not in LABELS:continue
        if key in ('host_missing','station_missing','error','error_message','warnings') and not item:continue
        label=LABELS[key]
        if isinstance(item,bool):plain='Да' if item else 'Нет';display=color('✓ Да','green') if item else color('— Нет','muted')
        elif isinstance(item,list):
            plain=', '.join(map(str,item)) or 'нет'
            display=color(plain,'yellow') if key=='warnings' else plain
        elif item is None:plain=display='не определено'
        elif key=='free_data_kib':label='Свободно на колонке';plain=display=f'{item/1024:.1f} МиБ'
        else:
            plain=display={'native':'Нативный','bridge':'Мост','lite1':'Станция Лайт 1','lite2':'Станция Лайт 2','verified':'Проверенная сборка','unverified':'Совместимость не проверена','unsupported-model':'Модель не поддерживается','unavailable':'Компоненты не прочитаны','idle':'Готов к командам','active':'Разговор GPT','stopped':'Остановлен','starting':'Запускается','opening':'Подключается'}.get(str(item),str(item))
        if messages is not None:messages.append(label+': '+plain)
        if not quiet:print('  '+color(label.ljust(29),'muted')+' '+display)
    if not quiet:print()
