"""Explicit host dependency setup, selected by the interactive installer."""
import shlex,shutil,subprocess,sys
from .state import ROOT

NATIVE_TOOLS=(('ssh','ssh-keygen','ssh-keyscan'),('chromium',),('Xvfb',),('xauth',))
AUDIO_TOOLS=('pactl','pacat','paplay')
PACKAGE_MANAGERS={
    'pacman':(['-S','--needed'],['openssh','chromium','xorg-server-xvfb','xorg-xauth'],['libpulse','opus']),
    'apt-get':(['install'],['openssh-client','chromium','xvfb','xauth'],['pulseaudio-utils','libopus0']),
    'dnf':(['install'],['openssh-clients','chromium','xorg-x11-server-Xvfb','xorg-x11-xauth'],['pulseaudio-utils','opus']),
}

def login_missing():
    if sys.platform!='linux':return []
    bundled=ROOT/'engines/bridge/display-tools/usr/bin/Xvfb'
    return [tool for tool in ('Xvfb','xauth') if not shutil.which(tool) and not (tool=='Xvfb' and bundled.is_file())]

def require_login_tools():
    missing=login_missing()
    if not missing:return
    try:hint=' Выполните: '+shlex.join(command('native',missing=missing))+'.'
    except RuntimeError:hint=' Установите Xvfb и xauth через менеджер пакетов.'
    raise RuntimeError('Для сохранения входа ChatGPT на компьютере не хватает: '+', '.join(missing)+'.'+hint+' Прежняя сессия сохранена.')

def command(mode,missing=None):
    if sys.platform!='linux':raise RuntimeError('Эта сборка установщика проверяется на Linux')
    manager=next((name for name in PACKAGE_MANAGERS if shutil.which(name)),None)
    if manager is None:raise RuntimeError('Пакетный менеджер не распознан. Установите компоненты из README вручную.')
    flags,native,bridge=PACKAGE_MANAGERS[manager]
    if missing is None:
        packages=native+(bridge if mode=='bridge' else [])
    else:
        tools=set(missing)
        packages=[name for group,name in zip(NATIVE_TOOLS,native) if tools.intersection(group)]
        if mode=='bridge' and tools.intersection(AUDIO_TOOLS):packages+=bridge
        if not packages:raise RuntimeError('Проверьте работу компонентов ПК: '+', '.join(missing)+'.')
    return ['sudo',manager,*flags,*packages]

def install(mode,missing=None):
    if sys.platform=='win32':
        from .windows import missing as windows_missing
        problems=windows_missing()
        if problems:raise RuntimeError('Установите Google Chrome или Microsoft Edge; библиотеки SSH входят в Windows-пакет. Не хватает: '+', '.join(problems))
        return
    argv=command(mode,missing=missing)
    print('Установка компонентов:',shlex.join(argv),flush=True)
    try:subprocess.run(argv,check=True)
    except subprocess.CalledProcessError:
        raise RuntimeError('Менеджер пакетов не смог установить зависимости. Подробности указаны выше. После устранения ошибки повторите мастер установки.') from None
