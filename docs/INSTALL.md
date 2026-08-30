# Installation guide

## 1. Hardware and Windows

Use 64-bit Windows 10 or 11, a compatible RTL-SDR, and an appropriate antenna. Plug in one SDR before cutover. FMP24 device selection defaults to device index `0`; edit `runtime.fmp24_args` if your receiver uses another index.

## 2. Clone and run setup

Open PowerShell in the clone:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1
```

Setup installs runtime dependencies outside Git:

- DSDPlus public release: `%LOCALAPPDATA%\Programs\DSDPlus`
- FFmpeg and MediaMTX: `%LOCALAPPDATA%\Programs\XScanDependencies`
- XScan application: `%LOCALAPPDATA%\Programs\XScan`
- XScan settings, database, logs, and credentials: `%LOCALAPPDATA%\XScan`

Override any location:

```powershell
.\setup.ps1 `
  -DsdPlusRoot D:\Radio\DSDPlus `
  -DependencyRoot D:\Radio\Dependencies `
  -InstallRoot D:\Radio\XScan `
  -StateRoot D:\Radio\XScanState
```

Existing DSDPlus/Fast Lane users should point `-DsdPlusRoot` at their own installation. Setup never downloads or redistributes Fast Lane files.

For a full replacement-machine migration using a private DSDPlus transfer ZIP,
follow [Complete new-machine installation](NEW_MACHINE_INSTALL.md). Its wrapper
validates the archive hash, restores either supported ZIP layout, and then runs
this setup workflow.

## 3. Install VB-CABLE

If VB-CABLE is absent, setup stages its official driver package and prints the directory. Right-click `VBCABLE_Setup_x64.exe`, choose **Run as administrator**, install, and reboot. This driver step cannot be completed safely by a non-elevated source checkout.

In DSDPlus, send decoded audio to **CABLE Input**. XScan records **CABLE Output** through Windows WASAPI at 48 kHz.

## 4. Add a scan list

Copy `examples\FMP24.ScanList.example` to `FMP24.ScanList` in the DSDPlus directory, then replace the example with frequencies, modes, bandwidths, and delays for your area. Do not commit your operational scan list if it exposes information you do not want public.

The default DSDPlus argument `-m2` passes analog source audio when there is no digital sync while preserving decoded supported digital audio. Encrypted calls cannot be decrypted.

## 5. Cut over to the radio hardware

The first setup is deliberately side-by-side and does not take the SDR. When the driver, SDR, and scan list are ready:

```powershell
.\setup.ps1 -Cutover
```

Cutover stops conflicting receiver processes, enables XScan hardware control, registers the per-user logon task, and opens `http://127.0.0.1:8890`. On first use, create the administrator password.

## 6. Optional public HTTPS

Public exposure is optional. First own a domain, direct its DNS to the host, and forward TCP ports 80/443 to that host. Reinstall with the HTTPS URL and then run the reverse-proxy installer from an elevated PowerShell:

```powershell
.\setup.ps1 -Cutover -PublicUrl https://scanner.example.com
.\v2\deploy\install-public-https.ps1 -Domain scanner.example.com
```

Never expose ports 8890 or 8891 directly. Caddy should be the only public listener.

## Troubleshooting

- **No receiver:** confirm `DSDPlus.exe` and `FMP24.exe` exist in `-DsdPlusRoot` and that the RTL-SDR driver works with FMP24 directly.
- **No recordings:** confirm Windows exposes `CABLE Output (VB-Audio Virtual Cable)` and that DSDPlus audio is routed to CABLE Input.
- **No live stream:** check the configured `tools.ffmpeg` and `tools.mediamtx` paths in `%LOCALAPPDATA%\XScan\settings.json`.
- **Wrong SDR:** change `-i0` in `runtime.fmp24_args` to the correct device index.
- **Public site fails:** verify DNS, router forwarding, Windows Firewall, and the Caddy service. Local XScan should still answer on loopback.
