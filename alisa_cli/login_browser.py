"""Ordinary owner sign-in before any private session import is attached."""
import contextlib,ctypes,ctypes.util,hashlib,os,re,shutil,signal,sqlite3,subprocess,time,sys
from pathlib import Path

PROC=Path('/proc')

def session_fingerprint(profile):
    """Read only cookie metadata for readiness; no values leave this process."""
    database=next((p for p in [Path(profile)/'Default/Network/Cookies',Path(profile)/'Default/Cookies'] if p.exists()),None)
    if database is None:return None
    try:
        with contextlib.closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True,timeout=.2)) as connection:
            now=int((time.time()+11644473600)*1000000)
            rows=connection.execute("SELECT name, encrypted_value, value FROM cookies WHERE host_key IN ('.chatgpt.com','chatgpt.com') AND name LIKE '%session-token%' AND (expires_utc=0 OR expires_utc>?) ORDER BY name",(now,)).fetchall()
        rows=[row for row in rows if row[1] or row[2]]
        if not rows:return None
        digest=hashlib.sha256()
        for name,encrypted,plain in rows:
            digest.update(name.encode()+b'\0'+(encrypted or str(plain or '').encode())+b'\0')
        return digest.digest()
    except (sqlite3.Error,OSError):return None

def login_title_ready(title):
    title=title.strip().lower()
    return 'chatgpt' in title and not any(word in title for word in ('just a moment','verify you are human','checking your browser','один момент','проверяем','login','log in','sign in'))

def browser_page_ready(profile,after=None):
    """Read only the ordinary X11 window title belonging to this profile.

    Chromium holds its History database exclusively while it is open; it is
    unsuitable for detecting live sign-in. No debugger is attached here.
    """
    if sys.platform=='win32':
        from .win_browser import page_ready
        return page_ready(profile)
    pid=profile_pid(profile)
    if pid is None:return False
    try:
        x=ctypes.CDLL(ctypes.util.find_library('X11') or 'libX11.so.6')
        pointer=ctypes.c_void_p;number=ctypes.c_ulong
        x.XOpenDisplay.argtypes=[ctypes.c_char_p];x.XOpenDisplay.restype=pointer
        x.XDefaultRootWindow.argtypes=[pointer];x.XDefaultRootWindow.restype=number
        x.XInternAtom.argtypes=[pointer,ctypes.c_char_p,ctypes.c_int];x.XInternAtom.restype=number
        x.XGetWindowProperty.argtypes=[pointer,number,number,ctypes.c_long,ctypes.c_long,ctypes.c_int,number,ctypes.POINTER(number),ctypes.POINTER(ctypes.c_int),ctypes.POINTER(number),ctypes.POINTER(number),ctypes.POINTER(pointer)]
        x.XQueryTree.argtypes=[pointer,number,ctypes.POINTER(number),ctypes.POINTER(number),ctypes.POINTER(pointer),ctypes.POINTER(ctypes.c_uint)]
        x.XFree.argtypes=[pointer];x.XCloseDisplay.argtypes=[pointer]
        x.XSetErrorHandler.argtypes=[pointer];x.XSetErrorHandler.restype=pointer
        display=x.XOpenDisplay(None)
        if not display:return False
        handler=ctypes.CFUNCTYPE(ctypes.c_int,pointer,pointer)(lambda *_:0)
        previous=x.XSetErrorHandler(ctypes.cast(handler,pointer))
        try:
            def prop(window,name):
                kind=number();format=ctypes.c_int();count=number();remaining=number();data=pointer()
                atom=x.XInternAtom(display,name.encode(),True)
                if not atom:return None
                result=x.XGetWindowProperty(display,window,atom,0,4096,False,0,ctypes.byref(kind),ctypes.byref(format),ctypes.byref(count),ctypes.byref(remaining),ctypes.byref(data))
                try:
                    if result or not data:return None
                    if format.value==32:return list(ctypes.cast(data,ctypes.POINTER(number))[:count.value])
                    if format.value==8:return ctypes.string_at(data,count.value).decode(errors='replace')
                finally:
                    if data:x.XFree(data)
            root=x.XDefaultRootWindow(display);windows=prop(root,'_NET_CLIENT_LIST') or []
            if not windows:
                root_return=number();parent=number();children=pointer();count=ctypes.c_uint()
                if x.XQueryTree(display,root,ctypes.byref(root_return),ctypes.byref(parent),ctypes.byref(children),ctypes.byref(count)):
                    if children:
                        windows=list(ctypes.cast(children,ctypes.POINTER(number))[:count.value]);x.XFree(children)
            for window in windows:
                if prop(window,'_NET_WM_PID')==[pid] and login_title_ready(prop(window,'_NET_WM_NAME') or ''):return True
        finally:
            x.XCloseDisplay(display);x.XSetErrorHandler(previous)
    except (OSError,AttributeError):pass
    return False

class SignInWatcher:
    def __init__(self,profile,require_page=False):
        self.profile=profile;self.started=time.monotonic();self.wall_started=time.time()-2;self.require_page=require_page;self.fingerprint=None;self.stable_since=None
    def __call__(self):
        now=time.monotonic()
        if now-self.started>900:raise RuntimeError('Время ожидания входа истекло. Прежняя сессия сохранена.')
        fingerprint=session_fingerprint(self.profile)
        if fingerprint is None and sys.platform=='win32':
            from .win_browser import signed_in_page_ready
            if signed_in_page_ready(self.profile):fingerprint=b'windows-owned-profile-menu'
        if fingerprint is not None and (not self.require_page or browser_page_ready(self.profile,self.wall_started)):
            if fingerprint!=self.fingerprint:self.fingerprint=fingerprint;self.stable_since=now
            return now-self.stable_since>=2
        self.fingerprint=None;self.stable_since=None
        if now-self.started>5 and profile_pid(self.profile) is None:raise RuntimeError('Окно входа закрыто до завершения. Прежняя сессия сохранена.')
        return False

def profile_pid(profile):
    """Chromium's profile lock identifies its actual owning process.

    Chromium may flatten /proc/cmdline into one argument, so exact argv
    membership alone cannot identify an existing profile on Linux.
    """
    if sys.platform=='win32':
        from .win_browser import profile_pid as windows_pid
        return windows_pid(profile)
    profile=Path(profile).resolve();lock=profile/'SingletonLock'
    try:
        pid=int(os.readlink(lock).rsplit('-',1)[1])
        process=PROC/str(pid)
        if (process/'exe').resolve(strict=True).name not in ('chromium','chrome'):return None
        raw=(process/'cmdline').read_bytes().replace(b'\0',b' ')
        marker=b'--user-data-dir='+os.fsencode(profile)
        if re.search(rb'(?:^|\s)'+re.escape(marker)+rb'(?=\s|$)',raw) and not re.search(rb'(?:^|\s)--type=',raw):return pid
    except (OSError,ValueError,IndexError):pass
    return None

def close_profile(profile):
    if sys.platform=='win32':
        from .win_browser import close_profile as close
        return close(profile)
    pid=profile_pid(profile)
    if pid is None:return
    try:os.kill(pid,signal.SIGTERM)
    except ProcessLookupError:return
    for _ in range(80):
        if profile_pid(profile) is None:return
        time.sleep(.1)
    raise RuntimeError('Окно входа ещё закрывается. Дождитесь его закрытия и повторите вход; профиль сохранён.')

def open_window(profile,proxy=None):
    if sys.platform=='win32':
        from .win_browser import open_window as open_browser
        return open_browser(profile,proxy)
    profile=Path(profile).resolve()
    if not os.environ.get('DISPLAY'):raise RuntimeError('Для входа нужен рабочий стол Linux')
    chromium=shutil.which('chromium')
    if not chromium:raise RuntimeError('Установите Chromium для однократного входа')
    existing=profile_pid(profile)
    if existing is not None:
        raw=(PROC/str(existing)/'cmdline').read_bytes().replace(b'\0',b' ')
        current_proxy=re.search(rb'(?:^|\s)--proxy-server=([^\s]+)',raw)
        expected=proxy.encode() if proxy else None
        if b'--remote-debugging-' not in raw and (current_proxy[1] if current_proxy else None)==expected:return existing
        close_profile(profile)
    profile.mkdir(parents=True,exist_ok=True,mode=0o700);profile.chmod(0o700)
    env={**os.environ,'XDG_SESSION_TYPE':'x11'};env.pop('WAYLAND_DISPLAY',None)
    args=[chromium,'--user-data-dir='+str(profile),'--ozone-platform=x11','--no-first-run','--no-default-browser-check','--new-window']
    if proxy:args.append('--proxy-server='+proxy)
    process=subprocess.Popen(args+['https://chatgpt.com/'],env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    time.sleep(.3)
    if process.poll() is not None and profile_pid(profile) is None:raise RuntimeError('Окно Chromium не открылось. Проверьте рабочий стол и доступность браузера.')
    return process.pid
