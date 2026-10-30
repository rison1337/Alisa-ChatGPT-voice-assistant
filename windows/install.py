"""Offline per-user Windows installation. No station operations happen here."""
import argparse,hashlib,json,os,shutil,subprocess,sys,tempfile,time
from pathlib import Path,PurePosixPath
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from alisa_cli.windows import private_directory
from alisa_cli import app_install
PACKAGE='alisa-voice-windows-native'

def install_launcher(base):
    # A waiting interpreter inside app would keep its Windows DLLs locked.
    files={'launcher.py':(ROOT/'windows/launcher.py').read_bytes()}
    for path in (ROOT/'windows/runtime').iterdir():
        if path.is_file():files['runtime/'+path.name]=path.read_bytes()
    files['runtime/python313._pth']=b'python313.zip\n.\n'
    identity=hashlib.sha256()
    for name,data in sorted(files.items()):
        identity.update(name.encode()+b'\0'+hashlib.sha256(data).digest())
    folder=base/'launchers';private_directory(folder)
    destination=folder/identity.hexdigest()[:20]
    if destination.exists() and not all((destination/name).is_file() and (destination/name).read_bytes()==data for name,data in files.items()):
        # Keep modified/unknown files and never overwrite a running runtime.
        destination=Path(tempfile.mkdtemp(prefix=identity.hexdigest()[:20]+'-',dir=folder))
    if not destination.exists():destination.mkdir()
    for name,data in files.items():
        path=destination/name
        if path.is_file() and path.read_bytes()==data:continue
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    relative=destination.relative_to(base)
    text=('@echo off\nrem ALISA_VOICE_WINDOWS\nsetlocal DisableDelayedExpansion\nchcp 65001 >nul\n'
            '"%~dp0'+str(relative/'runtime/python.exe')+'" -B -X utf8 "%~dp0'+str(relative/'launcher.py')+'" %*\n'
            'exit /b %errorlevel%\n')
    return text,destination


def validate(root):
    return app_install.validate(root,'windows-amd64')

def owned(path):
    try:return json.loads((path/'windows-package.json').read_text(encoding='utf-8')).get('package')==PACKAGE
    except (OSError,ValueError):return False

def main():
    parser=argparse.ArgumentParser(description='Alisa Voice: Windows, нативный режим')
    parser.add_argument('--check-only',action='store_true');parser.add_argument('--no-run',action='store_true')
    parser.add_argument('--record',type=Path,help=argparse.SUPPRESS)
    args,forwarded=parser.parse_known_args()
    if sys.platform!='win32':raise RuntimeError('Этот установщик запускается в Windows 10/11 x64.')
    print('Alisa Voice — Windows, нативный режим',flush=True)
    manifest=validate(ROOT)
    if args.check_only:
        print('Пакет цел: '+str(len(manifest['files']))+' файлов. Колонка и данные пользователя не изменены.');return 0
    base=Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData/Local'))/'AlisaVoice'
    destination=base/'app';private_directory(base)
    launcher=base/'alisa.cmd'
    if launcher.exists() and 'ALISA_VOICE_WINDOWS' not in launcher.read_text(encoding='utf-8'):raise RuntimeError('Команда запуска занята другим файлом.')
    record={'destination':str(destination),'backup':None,'platform':'windows-amd64','version':manifest.get('version')}
    def current():
        try:return owned(destination) and validate(destination)==manifest
        except (OSError,ValueError,RuntimeError):return False
    if destination.resolve()!=ROOT and not current():
        print('Устанавливаю программу текущему пользователю…',flush=True)
        record=app_install.replace(ROOT,destination,'windows-amd64',record_path=args.record)
    launcher_text,supervisor=install_launcher(base)
    if not launcher.exists() or launcher.read_text(encoding='utf-8')!=launcher_text:
        launcher.write_text(launcher_text,encoding='utf-8',newline='\r\n')
    print('Установлено: '+str(destination));print('Настройки и входы: '+str(base/'state'))
    if args.record:app_install.private_json(args.record,record)
    if args.no_run:return 0
    return subprocess.call([str(supervisor/'runtime/python.exe'),'-B','-X','utf8',str(supervisor/'launcher.py'),*forwarded],cwd=base)

if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError,KeyError,RuntimeError) as error:
        print('Установка остановлена: '+str(error),file=sys.stderr);raise SystemExit(1)
