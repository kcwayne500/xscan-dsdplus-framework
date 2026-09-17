# XScan V2 operations runbook

This is the machine-specific runbook for the production XScan V2 scanner host.
Use it for normal start, stop, restart, health checks, and recovery. Use
[`NEW_MACHINE_INSTALL.md`](NEW_MACHINE_INSTALL.md) only for installation or a
machine replacement.

## Production identity and paths

- Source of truth: `C:\xscan-dsdplus\framework`
- Git remote: `https://github.com/kcwayne500/xscan-dsdplus-framework.git`
- Production source branch: `main`
- Installed host: `%LOCALAPPDATA%\Programs\XScan\XScanV2.exe`
- Recovery launcher: `%LOCALAPPDATA%\XScan\Start-XScan.ps1`
- State, settings, database, and logs: `%LOCALAPPDATA%\XScan`
- DSDPlus/FMP24 runtime: `C:\DSDPlusFastLane`
- Local dashboard: `http://127.0.0.1:8890/`
- V2 health endpoint: `http://127.0.0.1:8890/api/m2/status`

Do not use the root legacy Python recorder or treat
`C:\DSDPlusFastLane\scanner-recorder-repo` as the production V2 source. The
correct V2 UI identifies itself as `XScan V2`, `/m2/` identifies itself as
`XScan M2`, and `/api/m2/status` returns HTTP 200. A `Scanner Live` page or a
404 from the V2 health endpoint means the wrong recorder is running.

## Start the host and scanner

Run the recovery launcher in PowerShell:

```powershell
& "$env:LOCALAPPDATA\XScan\Start-XScan.ps1"
```

The launcher is idempotent. If the dashboard is healthy, it keeps the current
host. Otherwise it replaces an unresponsive installed `XScanV2.exe`, sets
`runtime.desired_running` to `true`, starts the installed V2 host with the
production paths and port, waits for the health endpoint, and opens the local
dashboard.

The equivalent direct host command is a recovery fallback, not the preferred
daily command:

```powershell
& "$env:LOCALAPPDATA\Programs\XScan\XScanV2.exe" `
  --no-tray `
  --state-dir "$env:LOCALAPPDATA\XScan" `
  --dsdplus-root 'C:\DSDPlusFastLane' `
  --port 8890
```

`C:\DSDPlusFastLane\START_EVERYTHING.cmd` currently delegates to the same V2
recovery launcher for compatibility. Prefer `Start-XScan.ps1` so the intended
runtime is explicit.

## Normal management

After signing in to the dashboard, use the **Start**, **Stop**, and **Restart**
buttons under Scanner power. These controls manage DSDPlus, FMP24, audio
capture, recording, FFmpeg, and MediaMTX together while leaving the XScan web
host available. Use **Restart** after scan-list or audio-device changes.

Do not use `Stop-Process` as the normal scanner stop procedure. It bypasses the
authenticated runtime controls and can leave child processes behind. Reserve
process termination for recovery after confirming the dashboard and runtime
controls are unavailable.

## Verify health

Quick unauthenticated V2 identity and stream check:

```powershell
$status = Invoke-RestMethod `
  -Uri 'http://127.0.0.1:8890/api/m2/status' `
  -TimeoutSec 10
$status | Select-Object running, recording, stream_ready, `
  audio_device_name, now_playing
```

Formal machine validation from the correct repo:

```powershell
Set-Location 'C:\xscan-dsdplus\framework'
.\scripts\test-new-machine.ps1 -Cutover
```

A healthy production tree has one each of `XScanV2`, `DSDPlus`, `FMP24`,
`ffmpeg`, and `mediamtx`, with no legacy Python scanner process. Expected local
listeners are:

- `8890`: XScan dashboard and API
- `8554`: MediaMTX RTSP
- `8888`: MediaMTX HLS
- `8889`: MediaMTX WebRTC HTTP

Use this diagnostic command when the formal check fails:

```powershell
Get-Process XScanV2,DSDPlus,FMP24,ffmpeg,mediamtx `
  -ErrorAction SilentlyContinue |
  Select-Object Id, ProcessName, StartTime, Path

Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object LocalPort -in 8890,8554,8888,8889 |
  Select-Object LocalAddress, LocalPort, OwningProcess
```

Process and port checks prove that the local stack is up. They do not prove
that a real call is audible. Audio acceptance requires listening to a known
call and confirming a matching completed recording/history entry.

## Logs and recovery

- Host logs: `%LOCALAPPDATA%\XScan\logs`
- Host settings: `%LOCALAPPDATA%\XScan\settings.json`
- Call database: `%LOCALAPPDATA%\XScan\xscan.db`
- DSDPlus event data and operational scan list: `C:\DSDPlusFastLane`

Do not put those runtime files, credentials, recordings, licensed DSDPlus
files, or dependency binaries in Git.

If `XScanV2.exe` is using CPU but port 8890 has not opened, do not immediately
kill it. A history migration can delay HTTP startup. Check process CPU, database
growth, and the newest host log before deciding it is stalled.

If the V2 health endpoint is down, run `Start-XScan.ps1` once, allow it to
finish, and then run the formal cutover test. Inspect the newest log before
forcing individual processes to stop. Preserve the instance lock, child-process
cleanup, and append-only operational logging as one recovery system.

## Logon startup

The authoritative logon entry is the per-user registry value:

```text
HKCU\Software\Microsoft\Windows\CurrentVersion\Run\XScan V2
```

It launches Windows PowerShell hidden with
`%LOCALAPPDATA%\XScan\Start-XScan.ps1`. The Task Scheduler task
named `XScan V2` is disabled because direct scheduled execution stalled on this
host. Do not enable it or add a second Startup-folder shortcut while the
registry launcher exists; competing logon launchers can race.

Check the active logon command with:

```powershell
Get-ItemPropertyValue `
  -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' `
  -Name 'XScan V2'
```

## Updating XScan

For the dual-feed candidate, use [DUAL_FEEDS.md](DUAL_FEEDS.md) and
`scripts/upgrade-dual-feed.ps1`. The candidate is not installed by a source edit.
The fresh-install script now refuses existing installations; the setup commands
below are for initial installation, not an in-place dual-feed upgrade. Preserve
the existing logon launcher and validate Feed 1 before enabling Feed 2.

Before updating, inspect `git status` and preserve unrelated local changes.
Compare local `main` with `origin/main`; do not replace the installed host from
the older recorder checkout. For a cutover reinstall on this machine, use the
explicit Python 3.12 interpreter and then validate:

```powershell
Set-Location 'C:\xscan-dsdplus\framework'
.\setup.ps1 `
  -PythonExe "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" `
  -Cutover
.\scripts\test-new-machine.ps1 -Cutover
```

The state directory is outside Git and must be preserved. Never request, print,
copy into documentation, or commit the administrator password or `auth.json`.

Public HTTPS is a separate acceptance surface. A healthy local port 8890 does
not prove that public DNS, Caddy, router forwarding, firewall policy, or remote
audio works.
