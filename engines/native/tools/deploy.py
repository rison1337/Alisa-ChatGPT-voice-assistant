#!/usr/bin/env python3
"""Stage only this experiment; do not switch the running Quasar profile."""
import argparse
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import wave

ROOT=Path(__file__).resolve().parents[1]
BASE='/data/opt/alisa-standalone'
RUNTIME='/tmp/alisa-standalone'


def ssh(host,command,data=None):
    if host.startswith('-') or any(c.isspace() for c in host):raise ValueError('Invalid SSH alias')
    return subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=6',host,command],
                          input=data,capture_output=True,check=True,timeout=30).stdout


def check_firmware(host):
    import sys
    sys.path.insert(0,str(ROOT.parents[1]))
    from alisa_cli.firmware import classify,ARCHITECTURES
    compatible=ssh(host,'cat /proc/device-tree/compatible').decode(errors='replace')
    pair=tuple(ssh(host,'sha256sum '+path).decode().split()[0] for path in
               ['/system/vendor/quasar/maind','/system/vendor/quasar/libs/libyandex_evil.so'])
    report=classify(compatible,pair)
    if not report['model']:raise RuntimeError('Only Station Lite 1 and Lite 2 are supported')
    if not report['firmware_files_present']:raise RuntimeError('Cannot read firmware components')
    if ssh(host,'uname -m').decode().strip() not in ARCHITECTURES:raise RuntimeError('Unsupported station architecture')
    for warning in report['warnings']:print(warning,file=sys.stderr)
    return report


def profiles(launch,quasar):
    launch=copy.deepcopy(launch);quasar=copy.deepcopy(quasar);counts={'--platformd':0,'--mediad':0}
    for node in launch['nodes']:
        for task in node['tasks']:
            for kind in counts:
                if kind not in task.get('arg',[]):continue
                counts[kind]+=1;env=task.get('envs',[])
                if any(x.startswith('LD_PRELOAD=') for x in env):raise RuntimeError('Stock launcher already has a preload')
                if kind=='--platformd':
                    extra=[f'LD_PRELOAD={BASE}/vqe_tap.so',f'ALICE_CAPTURE_SOCKET={RUNTIME}/microphone.sock']
                else:
                    extra=[f'LD_PRELOAD={BASE}/tts_gate.so',f'ALICE_QUASAR_CONFIG={BASE}/quasar.speech.json',
                           f'ALSA_CONFIG_PATH={BASE}/tts-asound.conf',f'ALICE_TTS_GATE_FILE={RUNTIME}/tts.lease',
                           f'ALICE_TTS_GATE_STATUS={RUNTIME}/tts-gate.status']
                task['envs']=env+extra
    if any(x!=1 for x in counts.values()):raise RuntimeError('Unexpected stock launcher layout')
    params=quasar['audioclient']['controller']['playbackParams']
    for kind in ['OPUS_STREAM','PCM_STREAM']:
        pipeline=params[kind]['gstPipeline']
        if pipeline=='{STREAM_PIPELINE}':pipeline=params['common']['gstProperties']['STREAM_PIPELINE']
        if 'appsrc name=stream-src' not in pipeline or pipeline.count('{SINK}')!=1:raise RuntimeError('Unexpected native speech route')
        params[kind]['gstPipeline']=pipeline.replace('{SINK}',
            'audioconvert ! audioresample ! audio/x-raw,format=S16LE,rate=48000,channels=2 ! alsasink device=alice_native_tts')
    return launch,quasar


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--host',required=True)
    parser.add_argument('--include-login',action='store_true')
    parser.add_argument('--binary',type=Path,default=ROOT/'build/client/alisa-standalone',
                        help='Validated ARM client binary (default: build/client/alisa-standalone)')
    args=parser.parse_args()
    check_firmware(args.host)
    launch=json.loads(ssh(args.host,'cat /system/vendor/quasar/launch.json'))
    quasar=json.loads(ssh(args.host,'cat /system/vendor/quasar/quasar.cfg'))
    launch,quasar=profiles(launch,quasar)
    stream=io.BytesIO()
    with wave.open(stream,'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(bytes(16000*2*12))
    files={'launch.bridge.json':json.dumps(launch).encode(),'quasar.speech.json':json.dumps(quasar).encode(),
           'tts-asound.conf':b'</usr/share/alsa/alsa.conf>\npcm.alice_native_tts { type plug slave.pcm "default" }\n',
           'focus-0.wav':stream.getvalue(),'bin/alisa-standalone':args.binary.read_bytes(),
           'licenses/sonic-LICENSE':(ROOT/'research/upstream/sonic/LICENSE').read_bytes()}
    for name,path in {
        'webrtc-LICENSE':'webrtc-neteq/LICENSE',
        'webrtc-PATENTS':'webrtc-neteq/PATENTS',
        'webrtc-AUTHORS':'webrtc-neteq/AUTHORS',
        'webrtc-ooura-LICENSE':'webrtc-neteq/common_audio/third_party/ooura/LICENSE',
        'webrtc-spl-sqrt-LICENSE':'webrtc-neteq/common_audio/third_party/spl_sqrt_floor/LICENSE',
        'abseil-LICENSE':'abseil-cpp/LICENSE',
    }.items():files['licenses/'+name]=(ROOT/'research/upstream'/path).read_bytes()
    for name in ['native_agent','vqe_tap','tts_gate']:files[name+'.so']=(ROOT/'build'/(name+'.so')).read_bytes()
    for name in ['profile.sh','indicator.sh','run.sh']:files[name]=(ROOT/'station'/name).read_bytes()
    if args.include_login:files['state/web-session.json']=(ROOT/'state/web-session.json').read_bytes()
    ssh(args.host,f'umask 077; mkdir -p {BASE}/bin {BASE}/state {BASE}/licenses; chmod 700 {BASE} {BASE}/state')
    hashes={}
    for name,data in files.items():
        path=BASE+'/'+name
        ssh(args.host,f'umask 077; cat > {path}.new && mv {path}.new {path}',data)
        if name.endswith('.sh') or name.startswith('bin/'):ssh(args.host,'chmod 700 '+path)
        digest=hashlib.sha256(data).hexdigest()
        if ssh(args.host,'sha256sum '+path).decode().split()[0]!=digest:raise RuntimeError('Deployment hash mismatch')
        if not name.startswith('state/'):hashes[name]=digest
    (ROOT/'build').mkdir(exist_ok=True)
    (ROOT/'build/deployed-files.json').write_text(json.dumps(hashes,indent=2)+'\n')
    print('Experiment staged and hashes verified. Running profile was not switched.')


if __name__=='__main__':main()
