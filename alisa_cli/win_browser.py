"""Dedicated Windows sign-in window and short-lived loopback session import.

Normal sign-in has no automation/debugging flags. The subsequent import owns
only its private profile; the user's regular Chrome/Edge is never terminated.
"""
import contextlib,ctypes,json,os,re,subprocess,time,urllib.parse
from pathlib import Path
from .windows import browser,private_directory,process_options

_processes={}

def profile_pid(profile):
    process=_processes.get(str(Path(profile).resolve()))
    return process.pid if process is not None and process.poll() is None else None

def windows_for_pid(pid):
    from ctypes import wintypes as w
    user=ctypes.WinDLL('user32',use_last_error=True);result=[]
    callback_type=ctypes.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
    user.GetWindowThreadProcessId.argtypes=[w.HWND,ctypes.POINTER(w.DWORD)]
    user.GetWindowTextLengthW.argtypes=[w.HWND];user.GetWindowTextW.argtypes=[w.HWND,w.LPWSTR,ctypes.c_int]
    user.EnumWindows.argtypes=[callback_type,w.LPARAM]
    @callback_type
    def visit(window,_):
        owner=w.DWORD();user.GetWindowThreadProcessId(window,ctypes.byref(owner))
        if owner.value==pid:
            title=ctypes.create_unicode_buffer(user.GetWindowTextLengthW(window)+1)
            user.GetWindowTextW(window,title,len(title));result.append((window,title.value))
        return True
    user.EnumWindows(visit,0);return result

def page_ready(profile):
    from .login_browser import login_title_ready
    pid=profile_pid(profile)
    return bool(pid and any(login_title_ready(title) for _,title in windows_for_pid(pid)))

def close_profile(profile):
    key=str(Path(profile).resolve());process=_processes.get(key)
    if process is None or process.poll() is not None:return
    from ctypes import wintypes as w
    user=ctypes.WinDLL('user32');user.PostMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM]
    for window,_ in windows_for_pid(process.pid):user.PostMessageW(window,0x10,0,0)
    try:process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        # The PID comes from our live Popen handle, never from a process-name scan.
        subprocess.run(['taskkill.exe','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10,**process_options())
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:raise RuntimeError('Отдельный браузер входа ещё работает. Закройте это окно и повторите.') from None
    _processes.pop(key,None)

def open_window(profile,proxy=None):
    profile=Path(profile).resolve();key=str(profile)
    if profile_pid(profile):return profile_pid(profile)
    executable=browser()
    if not executable:raise RuntimeError('Для входа нужен Google Chrome или Microsoft Edge.')
    private_directory(profile)
    args=[str(executable),'--user-data-dir='+str(profile),'--no-first-run',
          '--no-default-browser-check','--disable-background-mode','--new-window']
    if proxy:args.append('--proxy-server='+proxy)
    process=subprocess.Popen(args+['https://chatgpt.com/'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    _processes[key]=process;time.sleep(.3)
    if process.poll() is not None:raise RuntimeError('Окно входа не открылось. Проверьте, не занят ли отдельный профиль браузера.')
    return process.pid

def endpoint(profile,started):
    path=Path(profile)/'DevToolsActivePort'
    if not path.exists() or path.stat().st_mtime<started-2:return None
    try:
        lines=path.read_text(encoding='utf-8').splitlines();port=int(lines[0]);route=lines[1]
    except (OSError,ValueError,IndexError):return None
    if not 1<=port<=65535 or not re.fullmatch(r'/devtools/browser/[A-Za-z0-9_-]+',route):return None
    return f'ws://127.0.0.1:{port}'+route

class CDP:
    def __init__(self,url):
        parsed=urllib.parse.urlsplit(url)
        if parsed.scheme!='ws' or parsed.hostname!='127.0.0.1' or not parsed.port:raise RuntimeError('Недопустимый локальный адрес браузера.')
        import websocket
        self.socket=websocket.create_connection(url,timeout=12,suppress_origin=True,http_no_proxy=['127.0.0.1'])
        self.counter=0
    def call(self,method,params=None,session=None):
        self.counter+=1;request={'id':self.counter,'method':method,'params':params or {}}
        if session:request['sessionId']=session
        self.socket.send(json.dumps(request));deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            response=json.loads(self.socket.recv())
            if response.get('id')==self.counter:
                if 'error' in response:raise RuntimeError('Не удалось проверить отдельную сессию браузера.')
                return response.get('result',{})
        raise RuntimeError('Браузер не ответил на проверку входа.')
    def close(self):self.socket.close()

def import_session(profile,destination,proxy=None):
    from .state import write_private
    profile=Path(profile).resolve();executable=browser()
    if profile_pid(profile):raise RuntimeError('Сначала закройте отдельное окно входа.')
    if not executable:raise RuntimeError('Chrome или Edge не найден.')
    (profile/'DevToolsActivePort').unlink(missing_ok=True)
    args=[str(executable),'--user-data-dir='+str(profile),'--headless=new',
          '--remote-debugging-address=127.0.0.1','--remote-debugging-port=0',
          '--no-first-run','--no-default-browser-check','--disable-background-mode']
    if proxy:args.append('--proxy-server='+proxy)
    process=subprocess.Popen(args+['https://chatgpt.com/'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**process_options())
    _processes[str(profile)]=process;connection=None;started=time.time()
    try:
        for _ in range(120):
            if process.poll() is not None:raise RuntimeError('Отдельный браузер проверки входа завершился.')
            url=endpoint(profile,started)
            if url:break
            time.sleep(.1)
        else:raise RuntimeError('Chrome/Edge не открыл локальную проверку входа.')
        connection=CDP(url);session=None
        for _ in range(60):
            pages=connection.call('Target.getTargets').get('targetInfos',[])
            page=next((p for p in pages if p.get('type')=='page' and p.get('url','').startswith('https://chatgpt.com/')),None)
            if page:
                session=connection.call('Target.attachToTarget',{'targetId':page['targetId'],'flatten':True})['sessionId'];break
            time.sleep(.2)
        if not session:raise RuntimeError('Откройте чат после входа и повторите авторизацию.')
        expression="location.origin==='https://chatgpt.com'?fetch('/api/auth/session',{credentials:'include'}).then(r=>r.json()).catch(()=>null):null"
        data=None
        for _ in range(20):
            response=connection.call('Runtime.evaluate',{'expression':expression,'awaitPromise':True,'returnByValue':True},session)
            data=response.get('result',{}).get('value')
            if isinstance(data,dict) and data.get('accessToken'):break
            time.sleep(.5)
        if not isinstance(data,dict) or not data.get('accessToken'):raise RuntimeError('Сохранённый вход не подтверждён. Прежняя сессия не заменена.')
        cookies=connection.call('Network.getCookies',{'urls':['https://chatgpt.com/']},session).get('cookies',[])
        allowed={c['name']:c['value'] for c in cookies if c['name'].startswith(('__Secure-next-auth.session-token','__Secure-authjs.session-token')) or c['name'] in ('oai-did','cf_clearance')}
        if not any('session-token' in key for key in allowed):raise RuntimeError('Нет обновляемой сессии. Повторите вход в отдельном окне.')
        agent=connection.call('Runtime.evaluate',{'expression':'navigator.userAgent','returnByValue':True},session).get('result',{}).get('value')
        write_private(destination,{'access_token':data['accessToken'],'expires':data.get('expires'),
                                 'account_id':(data.get('account') or {}).get('id'),'cookies':allowed,'user_agent':agent})
    except (KeyboardInterrupt,RuntimeError):raise
    except Exception:raise RuntimeError('Не удалось проверить вход через отдельный браузер. Прежняя сессия сохранена.') from None
    finally:
        if connection:
            with contextlib.suppress(Exception):connection.call('Browser.close')
            with contextlib.suppress(Exception):connection.close()
        if process.poll() is None:
            try:process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                process.terminate()
                with contextlib.suppress(subprocess.TimeoutExpired):process.wait(timeout=5)
        _processes.pop(str(profile),None)
        (profile/'DevToolsActivePort').unlink(missing_ok=True)
