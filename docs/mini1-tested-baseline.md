# Mini 1: preserved source snapshot, 2026-11-01

This branch preserves the source adaptation used for the YNDX-0004 experiment
reported in issue #4, before porting to the current upstream main. Parent:
`dbe3727598f37ff1baf48e19c3cf50971901512d`.

Hardware: one Mini 1, Quasar `1.355.4.3.4297407902.20260623`, kernel aarch64,
Quasar userspace ARMhf/glibc 2.23. Source-only snapshot: no new compiled clients,
libraries, plugin archives, firmware, accounts or local connection settings.
The inherited upstream assets are not a rebuilt Mini 1 distribution.

The installation on the station remains separate from this repository. Tested
client SHA-256: `c5a96045ac2d162acaa57fc4adf784d0ce95f357bc35d6564f524fe49810be1d`.
Tested volume adapter SHA-256:
`1f4ead6bf3e27bb30e671efce94a90b96420fbaa0056d041e5ecd767c7311d5a`.
These identify the tested artifacts; bit-for-bit reproducible builds are not
claimed. The original static dependency build directory is unavailable.

The owner confirmed a voice reply, two consecutive open/close conversations
with gesture volume, then a cold voice opening and gesture volume after reboot.
The first report also recorded 699 microphone frames, 71 audible blocks and zero
ALSA underruns in a short verify. This evidence does not cover another firmware,
network, model, long-running reliability or a port to a newer upstream commit.

Mini 1 workarounds: ARMhf/glibc 2.23; `/bin/true` agent donor; disabled native
audio focus and STUN; `plughw:0,0`; Quasar library path; mute GPIO shim; no GPT
LED writes. Gesture volume uses the local ALSA forwarding adapter built with
`-Wl,-z,nodelete`, needed so a second call does not resolve the real library
instead of the wrapper. `build_mini1_volume.sh` builds it and a silent null-PCM
loader lifecycle probe. The adapter is intended only for this client's S16
stereo output, not as a general replacement for system ALSA.

The optional source-only Luna/Homebrew integration is under `integrations/mini1`.
Its Homebrew manifest contains the hash of the previously tested build; a new
package must regenerate that field using the Homebrew pack command. Luna 0.2.12
uses two fixed-phrase Lua plugins. They retry their listener after Quasar IPC
disconnects; opening waits up to 30 seconds for the native client. Homebrew
`local-luna-boot` 1.0.1 launches Luna at loopback port 8100 and creates its temporary
YAM lock. It requires root consent; Luna requires `allow_plugin_binaries`.
Neither the optional integration nor the client edits the system boot scripts.

Known limits: focus/STUN bypasses, linear vendor-volume scaling, no GPT LED
implementation for Mini 1, and no automatic restart if the Homebrew child fails.
The inherited updater/package assets have not been rebuilt for this source
snapshot. Preserve account data when testing future installation updates.
