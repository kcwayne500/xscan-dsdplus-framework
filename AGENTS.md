# AI agent installation contract

This repository is a complete Windows migration package for the user's XScan DSDPlus system.

## Objective

Install and verify the repository on the replacement Windows machine without deleting recordings, changing unrelated USB devices, or exposing additional ports.

## Required sequence

1. Confirm the checkout is on `codex/rebuild-recorder` and has no unexpected local changes.
2. Run `git lfs install` and `git lfs pull`.
3. Confirm `runtime/caddy/caddy.exe` and `runtime/mediamtx/mediamtx.exe` are real binaries larger than 10 MB. Do not run the installer with LFS pointer files.
4. Read `MIGRATION.md` completely.
5. Run `INSTALL.cmd -NoLaunch` from an administrator terminal. If UAC or the signed VB-CABLE installer appears, hand control to the user; do not bypass either prompt.
6. If VB-CABLE was installed, tell the user a Windows reboot is mandatory. After reboot, rerun `INSTALL.cmd -NoLaunch`.
7. Connect the RTL-SDR and verify both RTL2838 `Bulk-In, Interface` devices use WinUSB. Never apply Zadig to an unrelated device.
8. Run `VERIFY_INSTALL.ps1 -Offline`.
9. Run `C:\DSDPlusFastlane\START_EVERYTHING.cmd` and wait for its health check.
10. Run `VERIFY_INSTALL.ps1` without `-Offline`.
11. Query `http://127.0.0.1:8890/api/status`; require scanner status `RUNNING` or `RECORDING` and stream status `LIVE`.
12. Verify all six local routes return HTTP 200.
13. Compare the public DNS A record with the replacement connection's public IPv4.
14. Verify router forwards TCP 80/443 and UDP 8189 to the replacement PC. Router login, certificate warnings, and credentials require the user.
15. Test every public HTTPS link from a genuinely external network.

## Commands

```powershell
git status --short
git branch --show-current
git lfs pull
.\INSTALL.cmd -NoLaunch
powershell -ExecutionPolicy Bypass -File .\VERIFY_INSTALL.ps1 -InstallDir C:\DSDPlusFastlane -Offline
C:\DSDPlusFastlane\START_EVERYTHING.cmd
powershell -ExecutionPolicy Bypass -File .\VERIFY_INSTALL.ps1 -InstallDir C:\DSDPlusFastlane
Invoke-RestMethod http://127.0.0.1:8890/api/status
```

## Machine-specific configuration

`C:\DSDPlusFastlane\startup\stack_config.json` contains the only expected radio-index overrides: `rtl_index` defaults to `2` and `dsd_audio_output` defaults to `2M`. Change them only when evidence on the replacement machine shows different device enumeration.

## Acceptance criteria

- Exactly one logical controller, recorder, DSDPlus, FMP24, MediaMTX, and FFmpeg process tree
- API and all six local routes return HTTP 200
- Audio monitor is `RUNNING`/`RECORDING`
- Stream is `LIVE`
- Caddy Windows service is running
- Windows Firewall permits only the required XScan TCP 80/443 and UDP 8189 rules
- Public DNS resolves to the replacement connection
- Public pages and WebRTC audio work externally

## Safety boundaries

- Never commit recordings, logs, generated `.event`/`.wav` files, credentials, Caddy certificates, or router exports.
- Never make this repository public; bundled DSDPlus files are for the user's private migration.
- Never delete or overwrite an existing recordings directory.
- Never guess router credentials or bypass its HTTPS warning. Ask the user to sign in.
- Never change arbitrary USB drivers with Zadig.
- Do not replace pinned binaries or Python packages with “latest” versions during migration.
- Preserve user edits in the working tree and stage only task-related paths.
