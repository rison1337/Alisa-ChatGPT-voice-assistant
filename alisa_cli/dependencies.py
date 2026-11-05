"""Explicit host dependency setup, selected by the interactive installer."""
import shlex,shutil,subprocess,sys
from .state import ROOT

def login_missing():
    if sys.platform!='linux':return []
    bundled=ROOT/'engines/bridge/display-tools/usr/bin/Xvfb'
    return [tool for tool in ('Xvfb','xauth') if not shutil.which(tool) and not (tool=='Xvfb' and bundled.is_file())]

def require_login_tools():
    missing=login_missing()
    if not missing:return
    try:hint=' Выполните: '+shlex.join(command('native'))+'.'
    except RuntimeError:hint=' Установите Xvfb и xauth через менеджер пакетов.'
    raise RuntimeError('Для сохранения входа ChatGPT на компьютере не хватает: '+', '.join(missing)+'.'+hint+' Прежняя сессия сохранена.')

def command(mode):
    if sys.platform!='linux':raise RuntimeError('Эта сборка установщика проверяется на Linux')
    native=['openssh','chromium','xorg-server-xvfb','xorg-xauth']
    bridge=['libpulse','opus']
    if shutil.which('pacman'):return ['sudo','pacman','-S','--needed',*(native+(bridge if mode=='bridge' else []))]
    if shutil.which('apt-get'):return ['sudo','apt-get','install','openssh-client','chromium','xvfb','xauth',*(['pulseaudio-utils','libopus0'] if mode=='bridge' else [])]
    if shutil.which('dnf'):return ['sudo','dnf','install','openssh-clients','chromium','xorg-x11-server-Xvfb','xorg-x11-xauth',*(['pulseaudio-utils','opus'] if mode=='bridge' else [])]
    raise RuntimeError('Пакетный менеджер не распознан. Установите компоненты из README вручную.')

def install(mode):
    if sys.platform=='win32':
        from .windows import missing
        problems=missing()
        if problems:raise RuntimeError('Установите Google Chrome или Microsoft Edge; библиотеки SSH входят в Windows-пакет. Не хватает: '+', '.join(problems))
        return
    argv=command(mode)
    print('Установка компонентов:', ' '.join(argv))
    subprocess.run(argv,check=True)
