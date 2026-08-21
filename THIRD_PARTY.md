# Third-party runtime notes

This private migration repository carries an exact snapshot of software already used by the owner's scanner system.

| Component | Bundled version/snapshot | Purpose |
|---|---|---|
| DSDPlus Fast Lane | Existing owner-provided runtime | Digital voice decoder |
| FMP24 | Existing owner-provided runtime | RTL-SDR tuner/scanner |
| FFmpeg | Existing tested shared Windows build | MP3 conversion and RTSP publisher |
| MediaMTX | v1.16.3 | WebRTC/RTSP media server |
| Caddy | v2.11.2 | Public HTTPS reverse proxy |
| VB-CABLE | Installed from the vendor during setup | Virtual audio handoff |

The bundled binaries retain their original ownership and licenses. They are not relicensed by this repository.

- Keep the repository private.
- Confirm the owner's DSDPlus Fast Lane entitlement on the replacement machine.
- Do not redistribute this payload to third parties.
- VB-CABLE is not bundled; `INSTALL.ps1` downloads its official donationware driver directly from VB-Audio.

Official project pages:

- VB-CABLE: https://vb-audio.com/Cable/
- MediaMTX: https://github.com/bluenviron/mediamtx
- Caddy: https://caddyserver.com/
- FFmpeg: https://ffmpeg.org/
