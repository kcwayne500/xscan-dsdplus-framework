# Two local USB feeds

Status: implementation on `feature/dual-usb-feeds`. A two-USB shared-stereo
trial was deployed on 2026-09-16 after the initial single-feed web pilot.
The existing Feed 1 list is unchanged; this host's Feed 2 has three additional
local VHF channels. Feed 2 remains disabled and empty on an ordinary upgrade.
Both real radios passed analog lane isolation and independent-stop tests;
live Feed 1 NXDN audio also stayed out of Feed 2. Feed 2 local dispatch traffic,
mobile listening, hardware recovery and long-run soak remain acceptance gates.
Remote RTL-TCP sources and native APK changes are explicitly outside this release.

## Ownership

One portal, login, SQLite database and MediaMTX server serve two independent
receiver/decoder/capture/recording pipelines. Stopping one feed leaves the other
running. Both is a third listening stream, not a third scanner or recording.

| Resource | Feed 1 | Feed 2 |
| --- | --- | --- |
| DSDPlus working directory | Existing `--dsdplus-root` | State directory `feeds/feed2/dsdplus` |
| Settings | Existing `settings.json` | `feeds/feed2/settings.json` |
| Scan list | Existing `FMP24.ScanList` | Separate, initially empty list |
| Recordings / trash | Existing paths, unchanged | Separate folders under Feed 2's DSDPlus directory |
| History | Existing rows become `feed1` | Only `feed2` rows |
| Live stream | Existing stream name | Existing name plus `-feed2` |

The shared mix uses the existing stream name plus `-both`. Each input has a
bounded queue; late input is dropped and missing input becomes silence. Mixing
is 48 kHz mono PCM, paced in 20 ms blocks. Resampling affects listening only.
Weights range from 0 to 1; output is divided by max(1, sum of weights), then
clamped to int16. A missing feed does not cause the other to jump in volume.
The basic blockwise resampler/drift correction needs real-device listening and
long-run verification; it is not claimed to provide sample-accurate synchronization.

Legacy unscoped APIs remain Feed 1 aliases. Scoped resources are
`/api/v1/feeds/feed1/...`, `/api/v1/feeds/feed2/...` and public listener equivalents
under `/api/m2/feeds/...`. Administrator authentication and CSRF checks are reused.
WHEP sessions cannot be used through another feed's route. Shared settings live
on Feed 1; name/enabled and mix weights have dedicated control endpoints.
M2 history remains separate, including when listening to Both.

## Configure the second receiver

1. Connect the second RTL-SDR and verify its WinUSB driver. Do not change an
   unrelated USB device's driver. Identify each physical dongle and confirm
   distinct FMP24-compatible serial numbers. Identical factory serials must be
   resolved before enabling dual mode; do not rely on enumeration indexes.
   For Blog V4 hardware, FMP24 2.86 requires its supported V4 library: back up
   `rtlsdr.dll`, then use the supplied `rtlsdr_V4.dll` as `rtlsdr.dll` while
   receivers are stopped. The current generic vendor DLL can work with
   `rtl_test` yet fail FMP24's checksum check; diagnostic success does not prove
   FMP24 compatibility. Verify the actual FMP24 log detects two physical radios
   and opens the intended tuner. Keep all DLLs and EEPROM backups outside Git.
   A serial change needs a physical reconnect before Windows refreshes the USB
   descriptor; verify unique serials again before configuring the portal.
2. Choose independent cables OR the shared-stereo mode below. Identify each
   playback/input side and capture/output side. Two endpoints of the same cable
   are not two independent cables. Do not change the Windows default playback
   endpoint to a scanner cable.
3. Stop Feed 1 in the portal before editing its hardware assignment. In
   **Receiver setup and shared audio mix**, enter its confirmed serial, matching
   FMP output / DSD input link ID, DSDPlus playback-device number, and exact
   capture endpoint and host API. Verify the DSDPlus number against its own device
   listing; it is NOT the PortAudio capture index. No tool rewrites SDR EEPROMs.
4. Select Feed 2, click **Prepare Feed 2 runtime files**. This copies only local
   DSDPlus/FMP24 executables, DLL dependencies and FMP24 calibration defaults.
   Licensed files never enter Git. Existing target files are not overwritten.
5. Assign its unique serial, a different matching direct-link ID (256-65535),
   DSDPlus output number (1-255), and exact capture cable. Independent cables
   need different output numbers; shared stereo needs the same output number
   and capture endpoint, with opposite left/right routing.
   Save and enable Feed 2. Add its channels through the selected feed's Scanlists
   page. Feed 2 cannot start with an empty list.
6. Start Feed 1 and verify its known call and recording, then start Feed 2 and
   verify its known call and recording. Explicit serial selectors prevent index
   reordering; the application validates configuration uniqueness, not physical
   cabling. Missing hardware must fault, never be silently reassigned.

In dual mode audio endpoint matching is strict: no fuzzy-name or default-device
fallback. A driver rename requires stopping the affected feed and explicitly
reselecting its endpoint. Receiver and cable reassignment is rejected while that
feed is running. Stop both feeds before changing shared streaming configuration.

## No-purchase shared stereo cable

This is an opt-in route, not a change to existing mono installations. The one
stereo cable carries Feed 1 on left and Feed 2 on right. Set `audio.capture_channel`
to `left` / `right`, and DSDPlus to `-o<number>L` / `-o<number>R` respectively.
Use the SAME verified DSDPlus playback number and SAME exact WASAPI capture
endpoint for both feeds. Receiver setup exposes these choices under Audio routing.
Stop both feeds and disable Feed 2 before transitioning from another mapping,
save both routes, then re-enable Feed 2 after verification.

Each AudioEngine opens a shared-mode stereo WASAPI reader and selects only its
assigned lane BEFORE metering, silence detection, pre-roll, WAV writing and live
publication. Recordings and publishers stay mono. The existing Both mixer
combines the already-separated listening streams; it never feeds the recorders.
Readers have independent lifetimes; a cable/driver failure is nevertheless a
shared dependency and can affect both feeds. Two radios retain independent scan
lists, direct links, DSDPlus processes, event tails and scan hold/resume behavior.

Configuration validation rejects overlapping lanes, mismatched DSDPlus/capture
channels, shared outputs with different capture endpoints, and non-WASAPI split
capture. Missing stereo support faults rather than silently downmixing. Shared
device names alone are never sufficient to allow two feeds.

Before a live trial, stop both receivers and run the opt-in hardware test:

```powershell
.\.venv\Scripts\python.exe scripts\test_stereo_cable.py `
  --input 'CABLE Output (VB-Audio Virtual Cable)' `
  --output 'CABLE Input (VB-Audio Virtual Cable)'
```

The test refuses running receivers, injects known tones only into that explicit
virtual cable, checks each lane separately and together, and stops one reader
while verifying the other survives. It does not create production recordings.
It proves driver/capture isolation, NOT DSDPlus source-monitor routing. Also
verify actual analog and decoded digital output with one receiver at a time,
then overlapping calls, correct recording metadata and independent scan resume.
Never enable both radios if source-monitor audio leaks into the opposite lane.

DSDPlus 2.523 was measured sending analog source-monitor audio to BOTH lanes
despite `-o2R`. XScan therefore additionally applies Windows per-process
`IChannelAudioVolume` to every session owned by its decoder PID, verifies the
two-channel mask, and only then starts FMP24. Missing/non-stereo sessions fail
closed. The supervisor checks the mask during operation and stops the affected
receiver if it changes; do not change native audio endpoints/balance while live.
This never changes the Windows default device or endpoint master volume.
The dependency is `pycaw==20251023`, included in the Windows package.

Recent DSDPlus versions ignore the legacy `-m2` argument. Initialize each new
decoder's Input menu to **Monitor Source Audio if No Sync and Signal Present**
(or the operator's verified preferred mode), then close it normally to save its
private native settings. Do not copy Feed 1's binary settings blindly; saved
device/link settings can override assumptions. Licensed/native settings stay
outside Git. A test-generated settings file is not a generic install default.

Main portal controls/history edit the selected feed. M2 has Feed 1 / Feed 2 /
Both listening buttons, a separate history selector, and local volume. Shared
mix balance changes affect all Both listeners but never individual recordings.
Playback remains blocked on the scanner host to prevent cable feedback.
Use M2 in Android Chrome/PWA for mobile acceptance; the existing native app
continues using its original Feed 1 endpoints.

## Build and isolated smoke test

From the repository's `v2` directory:

```powershell
.\.venv\Scripts\python.exe -m pytest -o addopts='' -q
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm XScanV2.spec
.\.venv\Scripts\python.exe scripts\smoke_dual_feeds.py --executable dist\XScanV2\XScanV2.exe
```

The smoke script uses a fresh temporary state/DSD directory, two synthetic
history entries, a locked hardware flag, no streaming processes, and port 8892.
It checks packaged pages, feed identity/history, cross-feed access rejection and
Feed 2's SSE snapshot. It owns and terminates only its child host. Add
`--serve-seconds 300` for a temporary browser inspection; test data stays outside
Git. This is not an RF or real-audio test.

`v2/scripts/smoke_mix.py --ffmpeg PATH --mediamtx PATH` additionally publishes
400 Hz and 1200 Hz synthetic PCM through the real FFmpeg/MediaMTX pipeline on
separate test ports, decodes Both, checks both tones and clipping, then stops
Feed 2 and verifies Feed 1 and Both survive. It never starts receiver/capture.
MediaMTX 1.20.1 is the tested version. The shared server explicitly disables
unused UDP RTSP, SRT, MoQ and playback listeners; only the configured listeners
are used. Retest the generated configuration when changing MediaMTX versions.

## Existing-host upgrade and rollback

Do not use the fresh-install script to upgrade an existing installation. It now
refuses that operation before its legacy process cleanup. Keep the existing
HKCU Run launcher and disabled scheduled-task configuration unchanged.

After the smoke test, schedule a brief interruption. Stop both feeds in the
portal, wait for conversions to finish, and quit XScan. The upgrade script
refuses a still-running owned host/receiver or occupied web port; it does not kill
processes. Use the tray Quit command if present. A no-tray host should be exited
by a separately verified owner-PID operation only after recording has stopped,
following the operations runbook. Do not run a logon launcher during upgrade.
The shared MediaMTX stays alive while the web host is running even if both
feeds are stopped. After a forced no-tray host exit, check for an orphan media
child using its exact parent PID, executable and state-specific generated
configuration path; stop only that verified child before starting the new host.
Otherwise the old listener can prevent the replacement from binding its ports.

From the repository root, with the actual installed paths:

```powershell
.\scripts\upgrade-dual-feed.ps1 -WhatIf
.\scripts\upgrade-dual-feed.ps1
& "$env:LOCALAPPDATA\XScan\Start-XScan.ps1"
```

The script stages the package, hashes a private checkpoint of state/configuration,
retains the old executable tree as a versioned sibling, and replaces only the
installed app. It does not overwrite Feed 1 DSDPlus data, recordings, logon tasks,
networking or firewall configuration. The SQLite migration is additive and also
creates a pre-schema backup when legacy calls exist. Verify Feed 1 first before
adding hardware or enabling Feed 2.

For rollback, stop both feeds and quit the host again, then pass the exact printed
checkpoint directory:

```powershell
.\scripts\upgrade-dual-feed.ps1 -Rollback 'D:\Example\XScan-upgrades\CHECKPOINT'
& "$env:LOCALAPPDATA\XScan\Start-XScan.ps1"
```

Use the real checkpoint path, not that example. Rollback restores the old app and
checkpoint database/settings, after archiving newer state into another checkpoint.
Newer recording files remain on disk but their newer history entries live in the
archived database, not the restored view. Do not discard either checkpoint; a
later reconciliation is needed to make post-checkpoint calls visible again.
Checkpoints include private settings/auth and call metadata: never upload or commit
them. Hash checks detect damage, not malicious modification of a checkpoint.

## Release acceptance still required

- Single-feed upgrade preserves existing calls, notes, favourites, tags and audio.
- Two actual dongles scan different lists; measure list revisit times and missed
  transmissions rather than promising exactly 2x throughput.
- Inject/listen to known separate audio on each cable; simultaneous recordings
  contain only the correct source, with no monitor loop or cross-feed metadata.
- Unplug/replug each dongle and each cable independently. Verify bounded recovery,
  no reassignment, no duplicate children, and no interruption to the other feed.
- Exercise repeated start/stop/restart and publisher/media-server failures.
- Android Chrome/PWA: Feed 1 / Feed 2 / Both, rapid switching, pause/resume,
  history switching, replay-to-live, volume, screen lock and network transitions.
- Confirm public proxy routing and WebRTC from an external network; local API
  success alone does not establish public audio.
- Soak for 24 hours with both radios: CPU, disk growth, PCM drops, recording
  continuity and mixer drift. Review `/api/v1/feeds` mix diagnostics and per-feed
  logs. Test upgrade/rollback on a disposable copy before production cutover.

These hardware/mobile/soak gates are not satisfied by unit tests. The web pilot
retains the previous installed system and a private state checkpoint for rollback.
Keep Feed 2 disabled until its hardware and independent audio path are verified;
do not treat the pilot as completion of the remaining release acceptance gates.
