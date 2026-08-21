# XScan DSDPlus Framework

Private, migration-ready Windows package for the complete XScan scanner stack:

- DSDPlus decoder and FMP24 RTL-SDR tuner
- Scanner recorder and metadata monitor
- FFmpeg recording/stream publisher
- MediaMTX WebRTC audio
- Six web interfaces
- Caddy public HTTPS proxy for `xscan.cc-group.org`

## New-machine quick start

Open PowerShell and run:

```powershell
git lfs install
git clone --branch codex/rebuild-recorder --single-branch https://github.com/kcwayne500/xscan-dsdplus-framework.git C:\DSDPlusFastlane\scanner-recorder-repo
cd C:\DSDPlusFastlane\scanner-recorder-repo
git lfs pull
.\INSTALL.cmd
```

Approve the administrator prompt. If VB-CABLE is not installed, complete its signed driver installer, reboot Windows, and run `INSTALL.cmd` again.

After installation, double-click the **Start XScan** desktop shortcut or:

```powershell
C:\DSDPlusFastlane\START_EVERYTHING.cmd
```

The launcher is safe to run repeatedly. It keeps healthy processes, replaces stale scanner instances, and verifies DSDPlus, FMP24, recording, streaming, Caddy, and all web routes.

## Documentation

- [MIGRATION.md](MIGRATION.md) — full human migration and troubleshooting guide
- [AGENTS.md](AGENTS.md) — exact instructions for an AI agent performing the install
- [THIRD_PARTY.md](THIRD_PARTY.md) — bundled runtime and licensing notes

## Web interfaces

| Interface | Local URL | Public URL |
|---|---|---|
| Main | http://127.0.0.1:8890/ | https://xscan.cc-group.org/ |
| Mobile | http://127.0.0.1:8890/m/ | https://xscan.cc-group.org/m/ |
| M2 | http://127.0.0.1:8890/m2/ | https://xscan.cc-group.org/m2/ |
| Radio | http://127.0.0.1:8890/radio/ | https://xscan.cc-group.org/radio/ |
| Mobile Player | http://127.0.0.1:8890/mobile-player | https://xscan.cc-group.org/mobile-player |
| Recordings | http://127.0.0.1:8890/recordings/ | https://xscan.cc-group.org/recordings/ |

## Installer options

```powershell
.\INSTALL.ps1 `
  -InstallDir C:\DSDPlusFastlane `
  -PublicHost xscan.cc-group.org `
  -RtlIndex 2 `
  -DsdAudioOutput 2M
```

Useful switches:

- `-NoLaunch` installs and verifies without starting the radio stack.
- `-SkipDriverInstall` skips the interactive VB-CABLE driver installer.
- `-SkipSystemChanges` creates a staging install without Caddy, firewall, drivers, or router changes.

Runtime recordings and logs are intentionally excluded from Git. They are output data, not installation dependencies.
