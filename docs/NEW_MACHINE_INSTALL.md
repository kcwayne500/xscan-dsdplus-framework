# Complete new-machine installation

This procedure rebuilds the XScan/DSDPlus host from a Git clone plus a private
DSDPlus Fast Lane transfer ZIP. The proprietary ZIP remains outside Git.

## What to bring to the new machine

- this Git repository
- `DSDPlusFastLane-manual-transfer-YYYYMMDD.zip`
- the SHA-256 value recorded when the ZIP was created
- one RTL-SDR and its antenna
- network/domain details only if public HTTPS will be restored later

The DSDPlus archive contains the operational scan list and DSDPlus/FMP24 files.
It does not contain XScan passwords. Create a fresh XScan administrator password
on the new machine.

## 1. Verify the transfer

```powershell
Get-FileHash 'D:\Transfer\DSDPlusFastLane-manual-transfer-YYYYMMDD.zip' -Algorithm SHA256
```

Compare the result with the value from the old machine. Do not continue after a
hash mismatch.

## 2. Install without claiming the radio

Open PowerShell as Administrator in the repository:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install-new-machine.ps1 `
  -DsdPlusArchive 'D:\Transfer\DSDPlusFastLane-manual-transfer-YYYYMMDD.zip'
```

This restores DSDPlus to `C:\DSDPlusFastLane`, installs Python if necessary,
downloads FFmpeg and MediaMTX, stages the official VB-CABLE package, runs all
tests, builds XScan, and starts the safe side-by-side dashboard on port 8891.

## 3. Install the two device drivers

### RTL-SDR

Connect only the intended RTL-SDR. Use Zadig to replace the driver for the
receiver's bulk interface with **WinUSB**. Confirm the USB ID/device before
pressing Replace Driver; choosing the wrong device can disable another USB
peripheral. Unplug/replug the receiver and confirm FMP24 can open it.

### VB-CABLE

The first installer pass prints the staged VB-CABLE directory. Right-click
`VBCABLE_Setup_x64.exe`, choose **Run as administrator**, install, and reboot.
After reboot, Windows Sound settings must show both **CABLE Input** and
**CABLE Output**.

In DSDPlus, decoded audio goes to CABLE Input. XScan captures CABLE Output via
Windows WASAPI at 48 kHz.

## 4. Validate before cutover

```powershell
.\scripts\test-new-machine.ps1
```

Also open `http://127.0.0.1:8891`, create the administrator password, and verify
that the page loads. Side-by-side mode intentionally does not control the SDR.

## 5. Cut over

When the driver, receiver, antenna, scan list, and VB-CABLE are ready:

```powershell
.\scripts\install-new-machine.ps1 `
  -DsdPlusArchive 'D:\Transfer\DSDPlusFastLane-manual-transfer-YYYYMMDD.zip' `
  -Cutover
.\scripts\test-new-machine.ps1 -Cutover
```

Cutover stops conflicting DSDPlus/FMP24/XScan processes, enables hardware
control, registers the per-user `XScan V2` logon task, and uses port 8890.

## 6. Acceptance test

1. Open `http://127.0.0.1:8890`.
2. Confirm FMP24 and DSDPlus are running and the displayed frequency changes.
3. Listen to a known active channel and confirm intelligible audio.
4. Confirm a completed call appears in rewind/history and produces a recording.
5. Inspect `%LOCALAPPDATA%\XScan\logs` and the DSDPlus event log for I/Q loss,
   USB drops, audio overruns, or repeated process restarts.
6. Leave gain and PPM at the transferred values initially. Change one setting at
   a time only if the spectrum/BER/log evidence supports it.

## Optional public HTTPS

Local-only access is the default. Restore public access only after the new host
has the intended static address, DNS, TCP 80/443 forwarding, and firewall rules:

```powershell
.\setup.ps1 -Cutover -DsdPlusRoot C:\DSDPlusFastLane `
  -PublicUrl https://scanner.example.com
.\v2\deploy\install-public-https.ps1 -Domain scanner.example.com
```

Never forward ports 8890 or 8891 directly to the internet.

## Recovery and reruns

- Re-running the installer reuses a valid `C:\DSDPlusFastLane` and rebuilds
  XScan. It does not overwrite that directory from the ZIP.
- If the destination exists but is incomplete, move it to a clearly named
  backup directory and rerun. The script refuses to merge an archive into an
  unknown non-empty directory.
- XScan settings are backed up by `v2\install.ps1` before updates.
- Keep the transfer ZIP offline as the licensed DSDPlus recovery copy.
