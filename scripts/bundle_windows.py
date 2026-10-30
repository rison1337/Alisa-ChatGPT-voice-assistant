#!/usr/bin/env python3
"""Build an offline Windows native test folder from an explicit source list."""
import argparse,hashlib,json,shutil,subprocess,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from alisa_cli import __version__

def source_files():
    try:
        names=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
        if 'alisa.py' not in names:raise ValueError('No tracked application sources')
    except (OSError,ValueError,subprocess.CalledProcessError):
        names=json.loads((ROOT/'windows/source-files.json').read_text(encoding='utf-8'))
    # Include new source files before a commit as well as rebuilds without Git.
    # Generated runtimes and credentials are handled by separate allowlists.
    for pattern in ['alisa_cli/**/*.py','tests/test_*.py','windows/*.py','windows/tests/test_*.py',
                    'scripts/*.py','docs/*.md','CODEX-HANDOFF/*.txt','engines/native/station/*.c',
                    'engines/native/station/*.h','engines/native/station/*.sh']:
        names += [p.relative_to(ROOT).as_posix() for p in ROOT.glob(pattern) if p.is_file()]
    native=json.loads((ROOT/'assets/native/manifest.json').read_text(encoding='utf-8'))
    names += ['assets/native/'+name for name in native['files']]
    names=[name for name in names if name and name!='package-manifest.json' and not name.startswith('assets/vpn/linux-amd64/')]
    return sorted(set(names))

def build(destination):
    if any(destination.iterdir()):raise RuntimeError('Каталог сборки должен быть пустым; существующие файлы сохранены.')
    names=source_files();manifest={}
    for folder in ('windows/runtime','windows/vendor','assets/vpn/windows-amd64'):
        names += [p.relative_to(ROOT).as_posix() for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo')]
    for name in sorted(set(names)):
        source=ROOT/name
        if not source.is_file() or source.is_symlink():raise RuntimeError('Missing source: '+name)
        target=destination/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        if source.suffix=='.cmd':target.write_bytes(source.read_bytes().replace(b'\r\n',b'\n').replace(b'\n',b'\r\n'))
        manifest[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    relative='windows/source-files.json';data=(json.dumps(source_files(),indent=2)+'\n').encode()
    (destination/relative).write_bytes(data);manifest[relative]=hashlib.sha256(data).hexdigest()
    (destination/'windows-package.json').write_text(json.dumps({'schema':1,'package':'alisa-voice-windows-native','version':__version__,'platform':'windows-amd64','stage':'local-test-no-publication','files':manifest},indent=2)+'\n',encoding='utf-8')
    print('Windows test folder:',destination,'—',len(manifest),'files')

def archive(folder):
    from alisa_cli.app_install import validate
    manifest=validate(folder,'windows-amd64',strict=True)
    out=ROOT/'dist';out.mkdir(exist_ok=True)
    target=out/'alisa-voice-windows.zip'
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as output:
        for name in sorted([*manifest['files'],'windows-package.json']):
            output.write(folder/name,'alisa-voice-windows/'+name)
    Path(str(target)+'.sha256').write_text(hashlib.sha256(target.read_bytes()).hexdigest()+'  '+target.name+'\n',encoding='ascii')
    print('Windows archive:',target)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'build/AlisaVoice-Windows');parser.add_argument('--archive',action='store_true');args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True);build(args.output.resolve())
    if args.archive:archive(args.output.resolve())
