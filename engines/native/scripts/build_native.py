#!/usr/bin/env python3
"""Build pinned ARMhf/glibc-2.23 dependencies; install only into build/prefix."""
import argparse
import os
from pathlib import Path
import shlex
import subprocess

ROOT=Path(__file__).resolve().parents[1]
UPSTREAM=ROOT/'research/upstream'
BUILD=ROOT/'build/arm'
PREFIX=BUILD/'prefix'


def run(args,cwd=ROOT,env=None):
    print('Build:',args[0],flush=True)
    subprocess.run([str(x) for x in args],cwd=cwd,env=env,check=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs',type=int,default=4)
    args=parser.parse_args()
    BUILD.mkdir(parents=True,exist_ok=True)
    PREFIX.mkdir(parents=True,exist_ok=True)
    zig=ROOT/'toolchains/zig-x86_64-linux-0.14.1/zig'
    for name,kind in [('cc','cc'),('cxx','c++')]:
        path=BUILD/f'arm-{name}'
        path.write_text('#!/bin/sh\nexec '+shlex.quote(str(zig))+' '+kind+
                        ' -target arm-linux-gnueabihf.2.23 -mcpu=cortex_a53 "$@"\n')
        path.chmod(0o700)
    toolchain=BUILD/'toolchain.cmake'
    toolchain.write_text(f'''set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR arm)
set(CMAKE_C_COMPILER "{BUILD}/arm-cc")
set(CMAKE_CXX_COMPILER "{BUILD}/arm-cxx")
set(CMAKE_AR /usr/bin/llvm-ar)
set(CMAKE_RANLIB /usr/bin/llvm-ranlib)
set(CMAKE_C_FLAGS_RELEASE "-Os -DNDEBUG -ffunction-sections -fdata-sections")
set(CMAKE_CXX_FLAGS_RELEASE "-Os -DNDEBUG -ffunction-sections -fdata-sections")
set(CMAKE_FIND_ROOT_PATH "{PREFIX}")
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE BOTH)
''')
    env={**os.environ,'CC':str(BUILD/'arm-cc'),'CXX':str(BUILD/'arm-cxx'),
         'AR':'/usr/bin/llvm-ar','RANLIB':'/usr/bin/llvm-ranlib',
         'PKG_CONFIG_LIBDIR':str(PREFIX/'lib/pkgconfig')}
    openssl=BUILD/'openssl'
    openssl.mkdir(exist_ok=True)
    if not (PREFIX/'lib/libssl.a').exists():
        run(['perl',UPSTREAM/'openssl/Configure','linux-armv4',f'--prefix={PREFIX}',
             '--libdir=lib','no-shared','no-tests','no-apps','no-docs','no-asm',
             'no-legacy','no-comp','no-zlib','no-engine','-Os','-fPIC'],cwd=openssl,env=env)
        run(['make',f'-j{args.jobs}'],cwd=openssl,env=env)
        run(['make','install_sw'],cwd=openssl,env=env)
    def cmake(name,extra):
        directory=BUILD/name
        run(['cmake','-S',UPSTREAM/name,'-B',directory,'-G','Ninja',
             f'-DCMAKE_TOOLCHAIN_FILE={toolchain}',f'-DCMAKE_INSTALL_PREFIX={PREFIX}',
             '-DCMAKE_BUILD_TYPE=Release','-DCMAKE_POLICY_VERSION_MINIMUM=3.5',
             '-DCMAKE_LINK_DEPENDS_USE_LINKER=FALSE',
             '-DBUILD_SHARED_LIBS=OFF',*extra],env=env)
        run(['cmake','--build',directory,'--parallel',str(args.jobs)],env=env)
        if name!='libdatachannel':run(['cmake','--install',directory],env=env)
    cmake('opus',['-DOPUS_BUILD_TESTING=OFF','-DOPUS_BUILD_PROGRAMS=OFF',
                  '-DOPUS_DISABLE_INTRINSICS=ON','-DOPUS_DRED=OFF','-DOPUS_OSCE=OFF'])
    cmake('curl',[f'-DOPENSSL_ROOT_DIR={PREFIX}','-DOPENSSL_USE_STATIC_LIBS=ON',
                  '-DCURL_USE_OPENSSL=ON','-DBUILD_CURL_EXE=OFF','-DBUILD_TESTING=OFF',
                  '-DHTTP_ONLY=ON','-DCURL_ZLIB=OFF','-DCURL_BROTLI=OFF','-DCURL_ZSTD=OFF',
                  '-DUSE_LIBIDN2=OFF','-DCURL_USE_LIBPSL=OFF','-DUSE_NGHTTP2=OFF',
                  '-DCURL_USE_LIBSSH2=OFF','-DCURL_USE_LIBSSH=OFF','-DCURL_USE_LIBSSH=OFF'])
    cmake('libdatachannel',[f'-DOPENSSL_ROOT_DIR={PREFIX}','-DOPENSSL_USE_STATIC_LIBS=ON',
                           '-DNO_EXAMPLES=ON','-DNO_TESTS=ON','-DNO_WEBSOCKET=ON',
                           '-DNO_MEDIA=OFF','-DBUILD_SHARED_DEPS_LIBS=OFF',
                           '-Dsctp_build_programs=OFF','-Dsctp_build_shared_lib=OFF'])


if __name__=='__main__':main()
