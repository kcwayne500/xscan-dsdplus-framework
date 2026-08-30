# Third-party notices

XScan invokes separate third-party programs. Their binaries are intentionally not committed to this repository and their licenses are not replaced by XScan's MIT License.

- **DSDPlus and FMP24** — downloaded from [DSDPlus.com](https://www.dsdplus.com/). The setup script downloads only the public release from the publisher. Do not redistribute Fast Lane or other licensed files through this repository.
- **FFmpeg** — downloaded as an LGPL Windows build from [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds). FFmpeg's exact licensing depends on its build options; see [FFmpeg Legal](https://ffmpeg.org/legal.html) and the license files in the downloaded archive.
- **MediaMTX** — downloaded from [bluenviron/mediamtx releases](https://github.com/bluenviron/mediamtx/releases) and licensed under the [MIT License](https://mediamtx.org/docs/misc/license).
- **Caddy** — downloaded only when the optional public HTTPS installer is run, from [Caddy releases](https://github.com/caddyserver/caddy/releases), under the [Apache License 2.0](https://github.com/caddyserver/caddy/blob/master/LICENSE).
- **VB-CABLE** — the official driver package is staged from [VB-Audio](https://vb-audio.com/Cable/index.htm). It is donationware and remains subject to [VB-Audio licensing](https://vb-audio.com/Services/licensing.htm).
- **hls.js** — the minified browser client under `v2/xscan/web/vendor` is distributed under the Apache License 2.0; see the [hls.js repository](https://github.com/video-dev/hls.js).
- **Android Gradle wrapper** — used under the terms supplied by the Android/Gradle projects.

When preparing a public release, retain this file and all license files shipped inside downloaded dependency packages.
