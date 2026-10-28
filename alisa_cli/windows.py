"""Windows host support for the native installer; no Windows audio bridge."""
import ctypes,os,shutil,subprocess,sys
from pathlib import Path

def console_setup():
    """Enable ANSI output without changing input modes used by prompt_toolkit."""
    from ctypes import wintypes as w
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetStdHandle.argtypes=[w.DWORD];kernel.GetStdHandle.restype=w.HANDLE
    kernel.GetConsoleMode.argtypes=[w.HANDLE,ctypes.POINTER(w.DWORD)]
    kernel.SetConsoleMode.argtypes=[w.HANDLE,w.DWORD]
    for identifier in (-11,-12):
        handle=kernel.GetStdHandle(identifier & 0xffffffff);mode=w.DWORD()
        if kernel.GetConsoleMode(handle,ctypes.byref(mode)):kernel.SetConsoleMode(handle,mode.value|4)

def browser():
    override=os.environ.get('ALISA_BROWSER')
    candidates=[Path(override)] if override else []
    for variable in ('PROGRAMFILES','PROGRAMFILES(X86)','LOCALAPPDATA'):
        if os.environ.get(variable):
            base=Path(os.environ[variable])
            candidates += [base/'Google/Chrome/Application/chrome.exe',
                           base/'Microsoft/Edge/Application/msedge.exe',
                           base/'Chromium/Application/chrome.exe']
    for name in ('chrome.exe','msedge.exe','chromium.exe'):
        found=shutil.which(name)
        if found:candidates.append(Path(found))
    return next((p for p in candidates if p.is_file()),None)

def missing():
    result=[]
    try:import paramiko,websocket
    except ImportError:result.append('библиотеки Windows-пакета SSH/браузера')
    if browser() is None:result.append('Google Chrome или Microsoft Edge')
    return result

def private_directory(path):
    """Protect only an application-owned directory, using the current user's SID."""
    from ctypes import wintypes as w
    path=Path(path);path.mkdir(parents=True,exist_ok=True)
    adv=ctypes.WinDLL('advapi32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetCurrentProcess.restype=w.HANDLE
    adv.OpenProcessToken.argtypes=[w.HANDLE,w.DWORD,ctypes.POINTER(w.HANDLE)]
    adv.GetTokenInformation.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD)]
    adv.ConvertSidToStringSidW.argtypes=[ctypes.c_void_p,ctypes.POINTER(w.LPWSTR)]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[w.LPCWSTR,w.DWORD,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p]
    adv.SetFileSecurityW.argtypes=[w.LPCWSTR,w.DWORD,ctypes.c_void_p]
    kernel.CloseHandle.argtypes=[w.HANDLE];kernel.LocalFree.argtypes=[ctypes.c_void_p]
    handle=w.HANDLE();size=w.DWORD();sid_text=w.LPWSTR();descriptor=ctypes.c_void_p()
    try:
        if not adv.OpenProcessToken(kernel.GetCurrentProcess(),8,ctypes.byref(handle)):raise OSError('OpenProcessToken')
        adv.GetTokenInformation(handle,1,None,0,ctypes.byref(size))
        buffer=ctypes.create_string_buffer(size.value)
        if not adv.GetTokenInformation(handle,1,buffer,size,ctypes.byref(size)):raise OSError('GetTokenInformation')
        sid=ctypes.cast(buffer,ctypes.POINTER(ctypes.c_void_p))[0]
        if not adv.ConvertSidToStringSidW(sid,ctypes.byref(sid_text)):raise OSError('ConvertSidToStringSid')
        sddl='D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;'+sid_text.value+')'
        if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl,1,ctypes.byref(descriptor),None):raise OSError('security descriptor')
        if not adv.SetFileSecurityW(str(path),0x80000004,descriptor):raise OSError('SetFileSecurity')
    except OSError:raise RuntimeError('Не удалось ограничить доступ к папке данных Windows. Используйте локальный диск NTFS.') from None
    finally:
        if descriptor:kernel.LocalFree(descriptor)
        if sid_text:kernel.LocalFree(ctypes.cast(sid_text,ctypes.c_void_p))
        if handle:kernel.CloseHandle(handle)

def process_options():
    return {'creationflags':subprocess.CREATE_NO_WINDOW} if sys.platform=='win32' else {}
