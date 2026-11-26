# Yandex Station Street (Mango) support

This branch adds support for **Yandex Station Street** (`Yandex-Station-Street-*`, SoC platform
`Mango`) to Alisa Voice. It is a fork of [rison1337/Alisa-ChatGPT-voice-assistant](https://github.com/rison1337/Alisa-ChatGPT-voice-assistant)
at `d9e7c47` (v0.3.11). Upstream only supports Lite 1/2, Mini 2 and Station 2, where the voice
processing unit runs inside `maind` on the CPU.

Verified on firmware `0.376.1.20.4861173338.20261116.100`: wake phrase → ChatGPT voice call →
audible answer, fully on the speaker, no browser bridge, no host PC in the audio path.

## Before you start — read this

1. **Root and SSH are a prerequisite, not a result.** This port touches system components: it
   preloads a library into `maind` (the vendor voice daemon), reads `/dev/input/event0` for the
   hardware mute switch, and installs an init script into `/etc/init.d`. Obtaining root on the
   speaker is **not** part of Alisa Voice and is not described here. Autostart additionally requires
   writing one startup script to the read-only rootfs (temporarily remounted `rw`).
2. **Firmware is pinned.** `stack.sh` requires an exact version match with
   `0.376.1.20.4861173338.20261116.100` (see the `version_full` check). On any other build the stack
   refuses to take over and restores stock Alice. No support for "any Street on any firmware" is
   promised.
3. **The repository alone is not installable.** `stack.sh` launches `relay`, `loopback.so`,
   `dispatch`, `holder`, `client.armhf`, `vqe_tap_mango_dsp.so`, `native_agent.so` and a `state/`
   directory with an existing session; `install.sh` expects an already prepared installation and
   verifies the SHA-256 of the `MANIFEST`. Those binaries are build artifacts and are not published
   here — see [Build](#build) and prepare them on the speaker first.
4. **The install command in the upstream README is not for Street.** It downloads the original
   Alisa Voice for supported models. For Street, follow this document only.

## Why an SSH server is needed

`chatgpt.com` may refuse requests coming directly from the speaker. The client therefore opens an
**outbound** SSH connection to a small relay host, and HTTPS to `chatgpt.com` is tunnelled through
it. The relay does not decrypt TLS, does not expose the speaker and accepts no inbound sessions —
it is a one-way path "speaker → relay → site". Without a reachable relay the voice stack does not
start; with the relay down, the speaker simply stays a stock Alice.

## Why Street needs a different tap

| | Lite 2 (Plum) | Street (Mango) |
|---|---|---|
| VQE processing | `maind` on CPU | HiFi4 DSP (`/dev/hifi4dsp0`) |
| Stock access point | `YandexVqeC_GetOutputChannelData` | `YandexVqeC_DspClient_Process` (`libyandex_evil.so`) |
| Channel layout | fixed index | 16 tracks split in `YandexVqeDspClientEngine`, `vqe_0` = `(kind=6, index=0)` by label |

The upstream `vqe_tap.c` hooks the getter. On Street it loads but never fires (`hits=0`).
`engines/native/station/vqe_tap_mango_dsp.c` interposes the DSP client call instead and selects the
processed channel **by its `(kind, index)` label**, never by array position.

Everything else stays stock: the speaker keeps its own buffers, mute control and DSP lifecycle. The
tap only emits optional non-blocking `AF_UNIX` datagrams.

## Client changes

- **Mute** is read from the Street `gpio_keypad` (`EVIOCGSW`, `SW_MUTE_DEVICE`) and is fail-closed:
  if it cannot be read, the capture path stays muted. Street's `/dev/input/event0` is `0660
  root:input`, so the client process needs the `input` group — grant it to the process only
  (`setpriv --groups 29,104`), never by editing `/etc/group`.
- **Playback** goes through the stock `speakers` → `plug`/`dmix` → `hw:0,1` route, with `default`
  as fallback and separate error codes for `snd_pcm_open` vs `snd_pcm_set_params`.
- **Voice and reasoning** are settable: `voice` (`breeze, cove, ember, fathom, glimmer, juniper,
  maple, orbit, vale`) and `reasoning` (`instant` default, `high`). If the server rejects `high`
  when a call starts, the client retries once with `instant` and logs a fixed event.

### The dmix semaphore trap

On Street the stock mixer is opened through dmix, whose SysV semaphore (`key 0x404`) is created by
whoever opens the PCM first, owned by that UID with mode `0600`. If root/`mediad` wins the race, a
client running as the unprivileged station user gets `EACCES` on `snd_pcm_open` for **both**
`speakers` and `default`, while `plughw:0,1` returns `EBUSY`.

`deploy/street/holder.c` solves this without touching permissions: it runs as the client UID, opens
`speakers` first and keeps the descriptor, so the client attaches to the same mixer. Do not delete
the IPC objects.

## Autostart

`rcS` only executes `/etc/init.d/S??*` on the read-only rootfs; the firmware does not source any
hook from `/bkupgrade`. So persistent start needs one script in `/etc/init.d` (remount rootfs `rw`,
write, remount `ro`), which then calls `deploy/street/stack.sh` from the writable partition.

`stack.sh` is fail-open by design: if anything does not come up (relay, holder, client, manifest,
firmware version) it restores the stock launcher, so the speaker always remains a normal Alice.

Three failure modes were paid for in soak runs and are handled:

1. **Socket ownership check.** A listener and an accepted connection share the same pathname but
   have different inodes. Comparing full inode sets rejects a healthy relay.
2. **Single probe failure.** One `vpn-health rc=2` is not an outage: restart only the relay, mark a
   series after 3 failures within 90 s, and roll back only after 600 s of continuous failure.
3. **Sleep.** `maind` pushes the speaker into deep suspend through `yandex-standby`, which writes
   `mem` to `/sys/power/state`. A wake lock alone does not stop it; `standby-inhibit.sh` intercepts
   exactly that call while the stack is active.

## Egress

`chatgpt.com` refuses direct requests from the speaker. The client therefore talks to a small SSH
relay (`route`/`egress.py`): the speaker opens an **outbound** SSH connection to the relay host, so
ChatGPT works from any network. This is not a reverse tunnel — remote administration still needs
either the same LAN or a separate `ssh -R` service on the speaker.

## Build

The repository ships sources and deployment scripts; the runtime binaries must be built for the
speaker (ARMhf, EABI5, hard-float) and are deliberately not published here.

```sh
# 1. DSP tap
aarch64-linux-gnu-gcc -marm -mfloat-abi=hard -shared -fPIC -O2 \
  -o vqe_tap_mango_dsp.so engines/native/station/vqe_tap_mango_dsp.c -ldl

# 2. mixer holder (needs libasound headers for ARMhf)
aarch64-linux-gnu-gcc -marm -mfloat-abi=hard -O2 -o holder deploy/street/holder.c -lasound

# 3. dispatch (Go, linux/arm)
GOOS=linux GOARCH=arm GOARM=7 go build -o dispatch deploy/street/dispatch.go

# 4. client — upstream toolchain (see docs/), patched in this branch for SW14 mute,
#    stock `speakers` playback and selectable reasoning effort
```

Always verify the hash **on the speaker**: cross-builds of this client are not reproducible
byte-for-byte.

## Install

Prepare this directory on the writable partition (`/bkupgrade/data/street-alisa-port-v1` in the
reference setup):

| Component | Where it comes from |
|---|---|
| `vqe_tap_mango_dsp.so` | built from `engines/native/station/vqe_tap_mango_dsp.c` |
| `holder` | built from `deploy/street/holder.c` |
| `dispatch` | built from `deploy/street/dispatch.go` |
| `client.armhf` | patched client build |
| `native_agent.so`, `loopback.so` | upstream install output |
| `relay` | relay client build (matches your egress server) |
| `state/` | existing ChatGPT session, created by a normal login on the speaker |
| `launch.json` | `deploy/street/launch.json`, with paths adjusted |
| `stack.sh`, `standby-inhibit.sh`, `S99alisa-port`, `uninstall.sh` | `deploy/street/` |

Then:

1. Generate `MANIFEST` (`sha256sum` of the set) so `stack.sh` can refuse a tampered or incomplete
   install; `install.sh` additionally verifies the manifest's own hash.
2. Install `S99alisa-port` into `/etc/init.d` while the rootfs is temporarily `rw`.
3. Power-cycle the speaker. `verify-post-power.sh` checks the new boot ID, stock-to-port takeover,
   tap mappings, holder state and counters.

`install.sh` is an in-place updater for an already working installation, not a first-time installer.

## Verify

Only counters, never audio: `Process`, `layout_ok`, `eligible_blocks`, `vqe_0_datagrams`, `mute`,
`phase`. Datagrams must grow while the hardware mute switch is open and stop when it is closed.
`MANGO-NATIVE/1`, a loaded `.so` in `maps`, an HTTP 200 or an open WebRTC channel are **not** proof
of a working voice exchange on their own.

## Rollback

```sh
deploy/street/uninstall.sh          # removes the init hook, restores the stock launcher
touch <data-dir>/DISABLED           # or just disable without uninstalling
```

## Limits

- The relay host must be up; without it the speaker falls back to stock Alice.
- The server does not echo back which reasoning effort it applied (`effort_reported=unknown`), so
  `high` is verified by request acceptance and perceived latency, not by a status field.
- Firmware updates overwrite `/etc/init.d`.

## Credits and licence

All original work is by the upstream author; this fork adds Street/Mango support only. The upstream
repository carries no licence file, so treat this branch as a personal derivative for
interoperability and do not redistribute the binaries commercially.
