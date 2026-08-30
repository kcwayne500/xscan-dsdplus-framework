from pathlib import Path


WEB = Path(__file__).resolve().parents[1] / "xscan" / "web"


def test_login_page_does_not_poll_protected_status_or_reload():
    source = (WEB / "app.js").read_text(encoding="utf-8")
    assert "setInterval(() => { if (state.authenticated) refreshStatus(); }, 5000);" in source
    assert "if (error.status === 401) location.reload();" not in source


def test_service_worker_uses_network_first_and_current_cache():
    source = (WEB / "sw.js").read_text(encoding="utf-8")
    assert "xscan-v2-shell-8" in source
    assert "fetch(event.request).then" in source
    assert "self.skipWaiting()" in source
    assert "url.pathname.startsWith('/api/')" in source


def test_dashboard_distinguishes_scanner_controls_from_browser_audio():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    index = (WEB / "index.html").read_text(encoding="utf-8")
    assert '>Start</button>' in app
    assert '>Stop</button>' in app
    assert "Start or stop the receiver and recorder together." in app
    assert 'data-action="player-primary"' in index
    assert '<span class="player-primary-label">Listen Live</span>' in index
    assert "Play or pause audio in this browser" not in index


def test_live_player_falls_back_and_never_exposes_raw_stream_json():
    player = (WEB / "player.js").read_text(encoding="utf-8")
    assert "WebRTC live audio unavailable; trying fallback" not in player
    assert "[['WebRTC', () => this.liveWebRtc(autoplay)], ['HLS', () => this.liveHls(autoplay)]]" in player
    assert "Live audio is reconnecting. Please try again in a few seconds." in player


def test_mobile_uses_drawer_and_dashboard_player_without_horizontal_health_grid():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    index = (WEB / "index.html").read_text(encoding="utf-8")
    styles = (WEB / "styles.css").read_text(encoding="utf-8")
    assert 'id="mobileDrawer"' in index
    assert 'class="mobile-listen-panel"' in app
    assert ".dashboard-grid > .health-card { display: none; }" in styles
    assert ".health-grid { grid-template-columns: 1fr; }" in styles


def test_dashboard_has_live_audio_meter_and_stopped_lcd_state():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    styles = (WEB / "styles.css").read_text(encoding="utf-8")
    assert "source.addEventListener('audio-level'" in app
    assert 'id="dashboardAudioMeter"' in app
    assert "lcd ${stopped?'stopped':''}" in app
    assert ".lcd.stopped" in styles


def test_mobile_manifest_and_android_release_project_are_pinned():
    import json

    manifest = json.loads((WEB / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert manifest["id"] == "/xscan-mobile"
    assert {icon["sizes"] for icon in manifest["icons"]} == {"192x192", "512x512"}
    assert any(icon.get("purpose") == "maskable" for icon in manifest["icons"])
    root = WEB.parents[1]
    android = (root / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
    top = (root / "android" / "build.gradle.kts").read_text(encoding="utf-8")
    assert 'compileSdk = 36' in android and 'minSdk = 26' in android and 'targetSdk = 36' in android
    assert 'androidx.media3:media3-session:1.11.0' in android
    assert 'versionName = "1.0.4"' in android
    assert 'DEFAULT_PUBLIC_URL' in android and 'xscanPublicUrl' in android
    assert 'scanner.example.com' in android
    assert 'com.android.application") version "9.3.0"' in top
    assert 'org.jetbrains.kotlin.android") version "2.3.21"' in top
    proguard = (root / "android" / "app" / "proguard-rules.pro").read_text(encoding="utf-8")
    assert "-keep class org.bouncycastle.** { *; }" in proguard
    native_manifest = (root / "android" / "app" / "src" / "main" / "AndroidManifest.xml").read_text(encoding="utf-8")
    identity = (root / "android" / "app" / "src" / "main" / "java" / "com" / "xscan" / "radio" / "DeviceIdentity.kt").read_text(encoding="utf-8")
    assert 'android:allowBackup="false"' in native_manifest
    assert 'remove("device_id").remove(REGISTERED_PUBLIC_KEY)' in identity
    assert 'putString(REGISTERED_PUBLIC_KEY,currentPublicKey)' in identity


def test_windows_package_excludes_downloaded_tools_and_private_apks():
    spec = (WEB.parents[1] / "XScanV2.spec").read_text(encoding="utf-8")
    assert 'binaries = []' in spec
    assert 'repo / "ffmpeg"' not in spec
    assert 'repo / "mediamtx"' not in spec
    assert '{"downloads", "screenshots"}' in spec


def test_m2_is_a_standalone_public_webrtc_pwa_with_call_tape():
    import json

    m2 = WEB / "m2"
    index = (m2 / "index.html").read_text(encoding="utf-8")
    script = (m2 / "m2.js").read_text(encoding="utf-8")
    styles = (m2 / "m2.css").read_text(encoding="utf-8")
    compact = (m2 / "compact.css").read_text(encoding="utf-8")
    worker = (m2 / "sw.js").read_text(encoding="utf-8")
    manifest = json.loads((m2 / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert manifest["id"] == "/m2/" and manifest["display"] == "standalone"
    assert "RTCPeerConnection" in script and "fetch('/api/m2/whep'" in script
    assert "Hls" not in script and "/api/v1/mobile/token" not in script
    assert "navigator.mediaSession" in script and "setActionHandler" in script
    assert 'id="callTape"' in index and 'class="call-tape sequence-tape"' in index
    assert 'id="historySlider"' not in index and "historySlider" not in script
    assert "durationClass" in script and "call-notch" in script
    assert "PixelSplitter-Bold.ttf" not in styles and 'id="audioMeter"' in index
    assert "url.pathname.startsWith('/api/')" in worker
    assert "hostPlaybackBlocked" in script and "prevent VB-CABLE feedback" in script
    assert "xscan-m2-shell-7" in worker
    assert ".lcd-panel.recording" in compact and "#e36b76" in compact
    assert ".lcd-panel.replay" in compact and "#aaa1e3" in compact
    assert ".call-notch.selected" in compact and ".recent-call.selected" in compact
    assert "playIntent" in script and "recoverLivePlayback" in script
    assert "document.addEventListener('resume'" in script and "window.addEventListener('pageshow'" in script
    assert "Live audio stalled. Reconnecting" in script and "event.track.addEventListener('ended'" in script
    assert "/m2/compact.css" in worker
