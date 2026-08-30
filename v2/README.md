# XScan V2

XScan V2 is the active Windows scanner host. It combines a tray application, FastAPI server, SQLite call history, DSDPlus/FMP24 supervision, audio recording, FFmpeg/MediaMTX live streaming, a responsive PWA, and an optional Android player.

## Safe defaults

- The backend listens only on `127.0.0.1:8891` during development and side-by-side installation.
- Hardware control is disabled until an explicit `-Cutover` install.
- Public HTTPS is disabled until a real `https://` URL is configured.
- State defaults to `%LOCALAPPDATA%\XScan` and DSDPlus defaults to `%LOCALAPPDATA%\Programs\DSDPlus`.
- DSDPlus, FMP24, FFmpeg, MediaMTX, VB-CABLE, recordings, credentials, and signing files are never bundled in source control.

Use the repository-level [`setup.ps1`](../setup.ps1) for a normal installation and read the [installation guide](../docs/INSTALL.md).

## Development

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m xscan --no-tray --state-dir .\.dev-state --dsdplus-root C:\path\to\DSDPlus
```

Build the Windows host:

```powershell
.\build.ps1 -Clean
```

Install without hardware control on port 8891:

```powershell
.\install.ps1 `
  -DsdPlusRoot C:\path\to\DSDPlus `
  -FfmpegExe C:\path\to\ffmpeg.exe `
  -MediaMtxExe C:\path\to\mediamtx.exe
```

Use `-Cutover` only after the [acceptance checklist](acceptance-checklist.md) passes. Cutover stops conflicting legacy processes, enables receiver/audio control, changes the local port to 8890, and registers the per-user logon task. Human-editable DSDPlus files are saved atomically with timestamped backups.

## Optional Android app

Android is not part of the normal Windows build. A release build requires Android tools/signing material and an explicit public HTTPS origin:

```powershell
.\build-android.ps1 -PublicUrl https://scanner.example.com
```

The generated signing keystore and recovery secret live under `%LOCALAPPDATA%\XScan\secrets`; back them up securely and never commit them. The app uses short-lived stream credentials signed by a device Ed25519 key protected by Android Keystore.

## Optional public HTTPS

After configuring a domain, DNS, and router forwarding, run the normal install with `-PublicUrl https://scanner.example.com`, then run `deploy\install-public-https.ps1 -Domain scanner.example.com` as administrator. XScan's loopback backend must not be forwarded directly.
