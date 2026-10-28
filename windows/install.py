"""Offline per-user Windows installation. No station operations happen here."""
import argparse,hashlib,json,os,shutil,subprocess,sys,tempfile,time
from pathlib import Path,PurePosixPath
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from alisa_cli.windows import private_directory
PACKAGE='alisa-voice-windows-native'

def validate(root):
    manifest=json.loads((root/'windows-package.json').read_text(encoding='utf-8'))
    if manifest.get('package')!=PACKAGE:raise RuntimeError('Неизвестный Windows-пакет.')
    for name,digest in manifest['files'].items():
        relative=PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name:raise RuntimeError('Некорректный путь пакета.')
        path=root/name
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise RuntimeError('Повреждён файл пакета: '+name)
    return manifest

def owned(path):
    try:return json.loads((path/'windows-package.json').read_text(encoding='utf-8')).get('package')==PACKAGE
    except (OSError,ValueError):return False

def main():
    parser=argparse.ArgumentParser(description='Alisa Voice: Windows, нативный режим')
    parser.add_argument('--check-only',action='store_true');parser.add_argument('--no-run',action='store_true')
    args,forwarded=parser.parse_known_args()
    if sys.platform!='win32':raise RuntimeError('Этот установщик запускается в Windows 10/11 x64.')
    print('Alisa Voice — тестовая Windows-сборка нативного режима',flush=True)
    manifest=validate(ROOT)
    if args.check_only:
        print('Пакет цел: '+str(len(manifest['files']))+' файлов. Колонка и данные пользователя не изменены.');return 0
    base=Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData/Local'))/'AlisaVoice'
    destination=base/'app';private_directory(base)
    def current():
        try:return owned(destination) and validate(destination)==manifest
        except (OSError,ValueError,RuntimeError):return False
    if destination.resolve()!=ROOT and not current():
        if destination.exists() and not owned(destination):raise RuntimeError('Папка установки занята другими файлами. Они сохранены.')
        required=sum((ROOT/name).stat().st_size for name in manifest['files'])+16*1024*1024
        if shutil.disk_usage(base).free<required:raise RuntimeError('Не хватает места для установки и резервной копии.')
        print('Устанавливаю программу текущему пользователю…',flush=True)
        with tempfile.TemporaryDirectory(prefix='.install-',dir=base) as temporary:
            staged=Path(temporary)/'app';staged.mkdir()
            for name in manifest['files']:
                path=staged/name;path.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,path)
            shutil.copy2(ROOT/'windows-package.json',staged/'windows-package.json');validate(staged)
            backup=base/'app.previous'
            if backup.exists():
                # A previous copy can contain edits or unknown user files.
                # Keep it intact rather than treating its ownership marker as
                # permission to erase everything inside it.
                backup=base/('app.previous-'+str(time.time_ns()))
            if destination.exists():destination.rename(backup)
            try:staged.rename(destination)
            except BaseException:
                if backup.exists():backup.rename(destination)
                raise
    launcher=base/'alisa.cmd'
    if launcher.exists() and 'ALISA_VOICE_WINDOWS' not in launcher.read_text(encoding='utf-8'):raise RuntimeError('Команда запуска занята другим файлом.')
    launcher.write_text('@echo off\nrem ALISA_VOICE_WINDOWS\nchcp 65001 >nul\n"%~dp0app\\windows\\runtime\\python.exe" -X utf8 "%~dp0app\\alisa.py" %*\n',encoding='utf-8',newline='\r\n')
    print('Установлено: '+str(destination));print('Настройки и входы: '+str(base/'state'))
    if args.no_run:return 0
    return subprocess.call([str(destination/'windows/runtime/python.exe'),'-X','utf8',str(destination/'alisa.py'),*forwarded],cwd=destination)

if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError,KeyError,RuntimeError) as error:
        print('Установка остановлена: '+str(error),file=sys.stderr);raise SystemExit(1)
