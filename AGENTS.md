# XScan new-machine automation contract

This repository is the source of truth for XScan. An AI agent helping with a
fresh installation must read this file and `docs/NEW_MACHINE_INSTALL.md` before
changing the machine. For normal production start, stop, restart, health, or
recovery work on the installed host, read `docs/OPERATIONS.md` first.

## Never put these in Git

- DSDPlus/FMP24 executables, DLLs, licensed Fast Lane files, or their ZIP files
- recordings, SQLite databases, runtime logs, passwords, `auth.json`, or local
  `settings.json`
- certificates, private keys, Android signing material, or deployment secrets
- FFmpeg, MediaMTX, Caddy, VB-CABLE, or other downloaded binaries

Run `scripts/public-release-audit.ps1` before every push. If the audit reports a
blocked item, stop and remove the item from the index; do not weaken the audit.

## Dual-feed candidate

Read `docs/DUAL_FEEDS.md` before working on the two-USB-receiver feature. Feed 2
must remain disabled until its unique SDR serial and isolated audio mapping
are physically verified (separate cables, or verified opposite stereo channels).
Never run a candidate against production state for a
smoke test. Use `v2/scripts/smoke_dual_feeds.py` with its disposable state, then
the existing-host upgrade/checkpoint procedure instead of fresh-install cleanup.
Single-feed production and the unchanged native APK are compatibility gates.

## Supported fresh-install workflow

Use an elevated 64-bit PowerShell on Windows 10/11:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install-new-machine.ps1 `
  -DsdPlusArchive 'D:\Transfer\DSDPlusFastLane-manual-transfer.zip'
```

The first pass installs side by side on port 8891 without taking the radio.
After the RTL-SDR is using WinUSB, VB-CABLE is installed, Windows has rebooted,
and FMP24 sees the receiver, run:

```powershell
.\scripts\install-new-machine.ps1 `
  -DsdPlusArchive 'D:\Transfer\DSDPlusFastLane-manual-transfer.zip' `
  -Cutover
.\scripts\test-new-machine.ps1 -Cutover
```

The installer is deliberately rerunnable. It reuses a valid
`C:\DSDPlusFastLane`, downloads current public dependencies, rebuilds/tests
XScan, and preserves existing XScan settings through the V2 installer.

## Required agent checks

1. Confirm the archive path and its SHA-256 hash with the operator.
2. Confirm `DSDPlus.exe` and `FMP24.exe` have been restored to
   `C:\DSDPlusFastLane`.
3. Confirm the RTL-SDR appears in Device Manager with the WinUSB driver. Never
   change the driver for an unrelated USB device.
4. Confirm both `CABLE Input` and `CABLE Output` exist after the VB-CABLE reboot.
5. Run the side-by-side install and `scripts/test-new-machine.ps1` first.
6. Run cutover only after the operator is ready for legacy scanner processes to
   stop and the SDR to be claimed by XScan.
7. Open the local dashboard, create a new administrator password, listen to a
   known call, and inspect logs for I/Q loss or audio overruns.
8. Keep public HTTPS disabled unless the operator explicitly supplies a domain,
   DNS, firewall, and port-forwarding plan.

Do not guess radio gain, PPM, device index, frequencies, or audio endpoints.
Preserve the transferred FMP24 configuration initially, then tune one variable
at a time using measured decode quality and I/Q-loss evidence.

## Production operations on this host

- Treat `C:\xscan-dsdplus\framework` on `main` as the V2 source checkout.
- Start or recover production with
  `%LOCALAPPDATA%\XScan\Start-XScan.ps1`.
- Verify V2 at `http://127.0.0.1:8890/api/m2/status`, then run
  `scripts\test-new-machine.ps1 -Cutover`.
- Use the authenticated dashboard controls for normal scanner stop/restart.
- Do not start the legacy root Python recorder or use
  `C:\DSDPlusFastLane\scanner-recorder-repo` as the V2 source.
- Preserve the single HKCU Run launcher. The `XScan V2` scheduled task is
  intentionally disabled on this machine; do not add competing autostarts.
- Do not claim audio or public-network acceptance from local processes and
  ports alone.

The complete commands, paths, expected process tree, ports, logs, and recovery
rules are in `docs/OPERATIONS.md`.
