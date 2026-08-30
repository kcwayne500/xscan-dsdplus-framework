# XScan V2 Real-Machine Acceptance Checklist

Run this checklist only after automated tests pass. Do not run the legacy recorder and V2
hardware pipeline at the same time.

- [ ] Side-by-side host starts on 8891 and cannot start hardware while the safety lock is active.
- [ ] Localhost first-run setup and subsequent login/logout work.
- [ ] Stop the legacy system, enable hardware control in an isolated acceptance settings file,
      and verify DSDPlus/FMP24 start without terminal windows.
- [ ] Show/hide native windows works from the tray and dashboard.
- [ ] VB-Cable is resolved by name even if its numeric device index changed.
- [ ] Analog and NXDN transmissions include 0.5 seconds of pre-roll and are not clipped.
- [ ] NXDN calls correlate RID, alias, RAN, and decoder event text.
- [ ] Multiple calls completing while FFmpeg is busy are all stored and playable.
- [ ] Desktop and mobile browsers can play live WebRTC audio and seek recorded MP3s.
- [ ] Killing DSDPlus, FMP24, FFmpeg, and MediaMTX produces truthful status within five seconds
      and the expected bounded recovery behavior.
- [ ] Disconnecting/reconnecting VB-Cable reports a fault and recovers after restart.
- [ ] Scanlist edits create backups, reject stale revisions, and apply after explicit restart.
- [ ] DSDPlus file edits preserve untouched lines and can be restored from backup.
- [ ] Disk warning, trash, restore, permanent purge confirmation, and support bundle work.
- [ ] The configured `https://scanner.example.com` address presents a trusted certificate and HTTP redirects to HTTPS.
- [ ] Port 8890 listens only on loopback; public API, PWA, HLS, and Android pairing work through Caddy.
- [ ] Reboot/logon starts XScan without terminal windows.
- [ ] `rollback.ps1 -StartLegacy` restores legacy operation without restoring DSDPlus data files.
