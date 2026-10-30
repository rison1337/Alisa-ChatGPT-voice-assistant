#!/usr/bin/env python3
"""Prepare installable artifacts; no private state or firmware images are copied."""
import hashlib,json,shutil,wave
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
NATIVE=ROOT/'engines/native'
def main():
    target=ROOT/'assets/native';target.mkdir(parents=True,exist_ok=True)
    items={'bin/alisa-standalone':NATIVE/'build/client/alisa-standalone'}
    for name in ['native_agent','vqe_tap','tts_gate','mute_gpio']:items[name+'.so']=NATIVE/'build'/(name+'.so')
    for name in ['run.sh','profile.sh','indicator.sh']:items[name]=NATIVE/'station'/name
    items['mark.so']=ROOT/'build/mark.so'
    licenses={'webrtc-LICENSE':'webrtc-neteq/LICENSE','webrtc-PATENTS':'webrtc-neteq/PATENTS','webrtc-AUTHORS':'webrtc-neteq/AUTHORS',
      'webrtc-ooura-LICENSE':'webrtc-neteq/common_audio/third_party/ooura/LICENSE',
      'webrtc-spl-sqrt-LICENSE':'webrtc-neteq/common_audio/third_party/spl_sqrt_floor/LICENSE','abseil-LICENSE':'abseil-cpp/LICENSE',
      'libdatachannel-LICENSE':'libdatachannel/LICENSE','libjuice-LICENSE':'libdatachannel/deps/libjuice/LICENSE',
      'usrsctp-LICENSE':'libdatachannel/deps/usrsctp/LICENSE.md','libsrtp-LICENSE':'libdatachannel/deps/libsrtp/LICENSE',
      'plog-LICENSE':'libdatachannel/deps/plog/LICENSE','json-LICENSE':'libdatachannel/deps/json/LICENSE.MIT',
      'curl-COPYING':'curl/COPYING','openssl-LICENSE':'openssl/LICENSE.txt','opus-COPYING':'opus/COPYING'}
    for name,path in licenses.items():items['licenses/'+name]=NATIVE/'research/upstream'/path
    for name,source in items.items():
        dest=target/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
    with wave.open(str(target/'focus-0.wav'),'wb') as output:
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000);output.writeframes(bytes(16000*2*12))
    hashes={str(p.relative_to(target)):hashlib.sha256(p.read_bytes()).hexdigest() for p in target.rglob('*') if p.is_file() and p.name!='manifest.json'}
    manifest={'schema':1,'model':'plum+micro','architecture':'armhf','audio_engine':'WebRTC NetEq','revision':5,'files':hashes}
    (target/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print('Native artifact manifest:',len(hashes),'files; no credentials included.')
if __name__=='__main__':main()
