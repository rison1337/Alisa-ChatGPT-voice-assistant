"""Explicit host dependency setup, selected by the interactive installer."""
import shutil,subprocess,sys

def command(mode):
    if sys.platform!='linux':raise RuntimeError('Эта сборка установщика проверяется на Linux')
    native=['openssh','chromium']
    bridge=['libpulse','xorg-server-xvfb','xorg-xauth','opus']
    if shutil.which('pacman'):return ['sudo','pacman','-S','--needed',*(native+(bridge if mode=='bridge' else []))]
    if shutil.which('apt-get'):return ['sudo','apt-get','install','openssh-client','chromium',*(['pulseaudio-utils','xvfb','xauth','libopus0'] if mode=='bridge' else [])]
    if shutil.which('dnf'):return ['sudo','dnf','install','openssh-clients','chromium',*(['pulseaudio-utils','xorg-x11-server-Xvfb','xorg-x11-xauth','opus'] if mode=='bridge' else [])]
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
