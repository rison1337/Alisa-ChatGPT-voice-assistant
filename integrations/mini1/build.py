"""Build source-only Mini 1 optional integrations; never deploy to a station."""
import hashlib,json,os,subprocess,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OUT=ROOT.parents[1]/'build/mini1-integrations'


def archive(path,files):
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as output:
        for name,data in files.items():
            info=zipfile.ZipInfo(name)
            info.create_system=3
            info.external_attr=(0o100700 if name.endswith(('.sh','.elf')) else 0o100600)<<16
            output.writestr(info,data,compress_type=zipfile.ZIP_DEFLATED)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    for command in ('open','close'):
        root=ROOT/'luna'/command
        archive(OUT/f'{command}.yap',{name:(root/name).read_bytes() for name in ('plugin.toml','main.lua','action.sh')})
    binary=OUT/'plugin.elf'
    env={**os.environ,'CGO_ENABLED':'0','GOOS':'linux','GOARCH':'arm64'}
    subprocess.run(['go','build','-trimpath','-o',str(binary),str(ROOT/'homebrew/main.go')],env=env,check=True)
    payload=binary.read_bytes()
    manifest=json.loads((ROOT/'homebrew/manifest.json').read_text(encoding='utf-8'))
    manifest['module_sha256']=hashlib.sha256(payload).hexdigest()
    archive(OUT/'luna-boot.aliceplugin',{'manifest.json':json.dumps(manifest,ensure_ascii=False,indent=2).encode(),'plugin.elf':payload})
    print('Built local integration archives in',OUT)


if __name__=='__main__':main()
