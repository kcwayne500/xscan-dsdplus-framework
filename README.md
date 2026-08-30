# XScan

XScan is a Windows host, recorder, and browser console for a DSDPlus/FMP24 radio receiver. It supervises the receiver processes, records calls, streams live audio through MediaMTX, and provides an authenticated local web interface. The active application is in [`v2`](v2/README.md); the root Python files are the original legacy recorder and remain for migration compatibility.

## What is not included

This repository does **not** contain DSDPlus, FMP24, Fast Lane files, FFmpeg, MediaMTX, VB-CABLE, radio recordings, scan lists, passwords, signing keys, or a local database. `setup.ps1` obtains public dependencies from their publishers and stores them outside the repository. Fast Lane users must install their licensed files themselves.

XScan does not decrypt encrypted traffic. Follow the radio-monitoring and rebroadcast laws that apply where you live.

## Easiest Windows setup

You need Windows 10/11 x64, an RTL-SDR supported by FMP24, an antenna, and an internet connection. In PowerShell from the cloned repository:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1
```

The first run downloads the public DSDPlus release, FFmpeg, MediaMTX, and the official VB-CABLE driver package when needed; builds and tests XScan; and starts a safe side-by-side instance at `http://127.0.0.1:8891`. Driver installation still requires an administrator right-click and a reboot, as directed by VB-Audio.

After VB-CABLE is installed and the SDR works, enable hardware control:

```powershell
.\setup.ps1 -Cutover
```

Your runtime files live under `%LOCALAPPDATA%`, not in the clone. See [Installation](docs/INSTALL.md) for custom paths, scan-list setup, public HTTPS, and troubleshooting.

Replacing an existing host with a transferred Fast Lane directory is covered by
[Complete new-machine installation](docs/NEW_MACHINE_INSTALL.md). The repository
also includes an AI automation contract in [`AGENTS.md`](AGENTS.md) and a
rerunnable installer at `scripts\install-new-machine.ps1`.

## Configuration

All machine-specific locations can be supplied as setup parameters, command-line arguments, environment variables, or settings:

- `XSCAN_STATE_DIR` or `--state-dir`
- `XSCAN_DSDPLUS_ROOT` or `--dsdplus-root`
- `tools.ffmpeg` and `tools.mediamtx` in `settings.json`
- `XSCAN_PUBLIC_URL` or `-PublicUrl` for an optional Android/public build

Start with [`examples/settings.example.json`](examples/settings.example.json) and [`examples/FMP24.ScanList.example`](examples/FMP24.ScanList.example). Local-only access is the secure default.

## Development

```powershell
cd v2
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m xscan --no-tray --state-dir .\.dev-state --dsdplus-root C:\path\to\DSDPlus
```

## Publishing and licensing

XScan source code is available under the [MIT License](LICENSE). Third-party programs keep their own licenses and are not relicensed by this project; see [Third-party notices](THIRD_PARTY_NOTICES.md).

Do not make an old private repository public if its Git history ever contained third-party binaries or private runtime data. Use the [clean-history publishing guide](docs/PUBLISHING.md) and run `scripts/public-release-audit.ps1 -IncludeHistory` first.
