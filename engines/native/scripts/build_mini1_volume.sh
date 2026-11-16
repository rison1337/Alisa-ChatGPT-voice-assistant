#!/bin/sh
set -eu
native=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
zig="$native/toolchains/zig-x86_64-linux-0.14.1/zig"
"$zig" cc -target arm-linux-gnueabihf.2.23 -shared -fPIC -O2 \
    -Wl,-z,nodelete -Wl,-soname,libasound.so.2 \
    -o "$native/station/libasound.so.2" "$native/station/mini1_volume.c" -ldl
"$zig" cc -target arm-linux-gnueabihf.2.23 -O2 \
    -o "$native/station/mini1-volume-probe" "$native/station/mini1_volume_probe.c" -ldl
