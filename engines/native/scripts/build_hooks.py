#!/usr/bin/env python3
"""Build pinned-ABI preloads without bundling another C/C++ runtime."""
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
(ROOT/'build').mkdir(parents=True,exist_ok=True)
# glibc 2.30 on Lite 1 keeps dlsym in libdl. mute_gpio is inherited by every
# client child, including busybox sh, so it must load libdl itself. An empty
# stub only supplies the DT_NEEDED soname; the station's libdl is used.
stub=ROOT/'build'/'libdl.so.2'
(ROOT/'build'/'libdl-stub.c').write_text('\n')
subprocess.run(['clang','--target=arm-linux-gnueabihf','-fuse-ld=lld','-shared','-nostdlib',
    '-Wl,-soname,libdl.so.2',str(ROOT/'build'/'libdl-stub.c'),'-o',str(stub)],check=True)
for name in ['native_agent','vqe_tap','tts_gate','mute_gpio']:
    subprocess.run(['clang','--target=arm-linux-gnueabihf','-mcpu=cortex-a53',
        '-fuse-ld=lld','-fPIC','-shared','-nostdlib','-O2','-fno-builtin',
        '-Wl,--hash-style=both','-Wl,-s','-Wall','-Wextra',
        str(ROOT/'station'/(name+'.c')),*([str(stub)] if name=='mute_gpio' else []),
        '-o',str(ROOT/'build'/(name+'.so'))],check=True)
    symbols=subprocess.check_output(['readelf','-Ws',str(ROOT/'build'/(name+'.so'))],text=True)
    if any(' UND ' in line and '__aeabi_' in line for line in symbols.splitlines()):
        raise RuntimeError('Preload requires an unavailable ARM compiler runtime')
print('Four ARMhf preloads built.')
