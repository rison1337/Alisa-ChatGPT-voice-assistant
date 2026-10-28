#!/usr/bin/env python3
"""Fetch only pinned official Windows dependencies; no user data is read."""
import hashlib,json,shutil,urllib.request,zipfile
from pathlib import Path,PurePosixPath
ROOT=Path(__file__).resolve().parents[1]

def fetch(item):
    path=ROOT/'build/windows-downloads'/item.get('filename',item['url'].rsplit('/',1)[1])
    path.parent.mkdir(parents=True,exist_ok=True)
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
        temporary=path.with_suffix(path.suffix+'.partial')
        with urllib.request.urlopen(item['url'],timeout=60) as source,temporary.open('wb') as output:shutil.copyfileobj(source,output)
        if hashlib.sha256(temporary.read_bytes()).hexdigest()!=item['sha256']:temporary.unlink();raise RuntimeError('SHA-256 dependency mismatch: '+path.name)
        temporary.replace(path)
    return path

def extract(archive,directory):
    directory.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            path=PurePosixPath(info.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in info.filename or ':' in info.filename:raise RuntimeError('Invalid dependency archive path')
        bundle.extractall(directory)

def main():
    manifest=json.loads((ROOT/'windows/dependencies.json').read_text())
    runtime=ROOT/'windows/runtime';extract(fetch(manifest['python']),runtime)
    (runtime/'python313._pth').write_text('python313.zip\n.\n..\\..\n..\\vendor\n..\\..\\alisa_cli\\_vendor\nimport site\n')
    for wheel in manifest['wheels']:extract(fetch(wheel),ROOT/'windows/vendor')
    for item in manifest['cores']:
        destination=ROOT/'assets/vpn/windows-amd64'/(item['engine']+'.exe');destination.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(fetch(item)) as bundle:
            member=next(n for n in bundle.namelist() if n.rsplit('/',1)[-1]==item['engine']+'.exe')
            destination.write_bytes(bundle.read(member))
    print('Windows runtime, SSH libraries and VPN validators prepared with pinned SHA-256.')

if __name__=='__main__':main()
