# XScan migration guide

This guide migrates the working scanner system to another Windows 11 machine. The repository contains the tested runtime binaries and configuration snapshot. Large binaries use Git LFS.

## 1. Hardware and account requirements

- A Windows 11 x64 computer
- The RTL-SDR dongle used by FMP24
- Administrator access for the audio driver, Caddy service, and firewall rules
- Access to the private GitHub repository
- Access to the router if its existing port forwards target the old computer
- Control of the `xscan.cc-group.org` DNS record

Keep this repository private. It contains a personal migration snapshot of third-party runtime files, including DSDPlus Fast Lane.

## 2. Clone the migration branch

Install Git for Windows with Git LFS, then run:

```powershell
git lfs install
git clone --branch codex/rebuild-recorder --single-branch https://github.com/kcwayne500/xscan-dsdplus-framework.git C:\DSDPlusFastlane\scanner-recorder-repo
cd C:\DSDPlusFastlane\scanner-recorder-repo
git lfs pull
```

Confirm the LFS payload is real before installing:

```powershell
Get-Item runtime\caddy\caddy.exe, runtime\mediamtx\mediamtx.exe
```

Both files should be tens of megabytes, not small text pointer files.

## 3. Run the complete installer

```powershell
.\INSTALL.cmd
```

The installer:

1. Elevates through the normal Windows UAC prompt.
2. Restores Git LFS files if needed.
3. Deploys DSDPlus/FMP24 and the current channel databases.
4. Creates `C:\DSDPlusFastlane\.venv` and installs pinned Python dependencies.
5. Deploys the scanner recorder and every web interface.
6. Deploys FFmpeg and MediaMTX.
7. Detects/configures the VB-CABLE recording endpoint.
8. Installs Caddy as an automatic Windows service.
9. Adds narrowly scoped firewall rules for TCP 80/443 and UDP 8189.
10. Attempts UPnP mappings for TCP 80/443 and UDP 8189.
11. Creates a **Start XScan** desktop shortcut.
12. Runs offline file, import, and syntax verification.

The script is idempotent: rerunning it repairs missing files and dependencies without deleting recordings.

## 4. Complete machine-specific driver steps

### VB-CABLE

If missing, the installer downloads the official `VBCABLE_Driver_Pack45.zip` and opens `VBCABLE_Setup_x64.exe`. Complete that installer and reboot Windows. The vendor requires a reboot. After reboot, run `INSTALL.cmd` again so the exact `CABLE Output` recording endpoint is saved.

Official source: https://vb-audio.com/Cable/

### RTL-SDR / Zadig

Connect the RTL-SDR dongle. In Device Manager it should appear as two `Bulk-In, Interface` devices under **Universal Serial Bus devices**. If it does not, use Zadig to assign **WinUSB** to both RTL2838 Bulk-In interfaces.

Official source: https://zadig.akeo.ie/

Do not change unrelated USB devices in Zadig.

## 5. Verify machine-specific scanner indexes

The migrated defaults match the source machine:

- FMP24 RTL device index: `2`
- DSDPlus output device: `2M`
- FMP/DSD direct-link port: `20001`

They live in `C:\DSDPlusFastlane\startup\stack_config.json`. If the new machine enumerates the RTL-SDR or VB-CABLE differently, edit only `rtl_index` or `dsd_audio_output`, then rerun `START_EVERYTHING.cmd`.

## 6. Start and verify

Double-click **Start XScan** or run:

```powershell
C:\DSDPlusFastlane\START_EVERYTHING.cmd
powershell -ExecutionPolicy Bypass -File C:\DSDPlusFastlane\scanner-recorder-repo\VERIFY_INSTALL.ps1
```

Successful health output must show one controller, decoder, tuner, recorder, MediaMTX, and FFmpeg process; 6/6 web variants; monitoring `RUNNING` or `RECORDING`; stream `LIVE`; and Caddy `RUNNING`.

## 7. Public hostname migration

The new router must forward:

| Protocol | External | Internal | Purpose |
|---|---:|---:|---|
| TCP | 80 | 80 | Caddy HTTP/certificate redirect |
| TCP | 443 | 443 | Caddy HTTPS web apps |
| UDP | 8189 | 8189 | MediaMTX WebRTC audio |

The installer tries UPnP first. A warning that port 80 or 443 is “in use” means a manual router rule already exists. Sign in to the router and point that rule to the new machine; do not create a duplicate.

Update the `xscan.cc-group.org` A record to the new connection's public IPv4. Compare:

```powershell
Resolve-DnsName xscan.cc-group.org -Type A
Invoke-RestMethod https://api.ipify.org
```

## 8. Recordings and historical logs

The 1.6+ GB recordings library and large DSDPlus WAV/event logs are deliberately not stored in Git. If history is required, copy `C:\DSDPlusFastlane\recordings\` separately after installation. Do not copy active runtime files while the old scanner is running.

## Troubleshooting

- Installer log: `C:\DSDPlusFastlane\install.log`
- Launcher log: `C:\DSDPlusFastlane\startup\launch_all.log`
- DSDPlus log: `C:\DSDPlusFastlane\startup\dsdplus_runtime.log`
- API status: http://127.0.0.1:8890/api/status
- MediaMTX player: http://127.0.0.1:8889/scanner

Rerun `INSTALL.cmd` to repair dependencies. Rerun `START_EVERYTHING.cmd` to repair stale processes.
