from fastapi.testclient import TestClient
import httpx
import base64
import secrets
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from xscan.api import create_app


def test_auth_csrf_status_and_range_audio(app_paths):
    app = create_app(app_paths)
    with TestClient(app) as client:
        state = client.get("/api/v1/auth/state").json()
        assert state["configured"] is False
        response = client.post("/api/v1/auth/setup", json={"password": "LongScannerPassword12"})
        assert response.status_code == 200
        csrf = client.cookies.get("xscan_csrf")
        assert client.get("/api/v1/status").status_code == 200
        assert client.post("/api/v1/system/stop").status_code == 403
        assert client.post("/api/v1/system/stop", headers={"X-CSRF-Token": csrf}).status_code == 200
        context = app.state.context
        audio = app_paths.recordings / "sample.mp3"
        audio.write_bytes(b"0123456789")
        context.database.add_call({"id": "range", "started_at": "2026-01-01T00:00:00", "audio_file": audio.name, "audio_bytes": 10})
        ranged = client.get("/api/v1/calls/range/audio", headers={"Range": "bytes=2-5"})
        assert ranged.status_code == 206
        assert ranged.content == b"2345"
        assert ranged.headers["content-range"] == "bytes 2-5/10"


def test_config_revision_conflict_returns_409(app_paths):
    (app_paths.dsdplus / "FMP24.ScanList").write_text("155.000 FM DELAY=2 Dispatch\n", encoding="utf-8")
    app = create_app(app_paths)
    with TestClient(app) as client:
        client.post("/api/v1/auth/setup", json={"password": "LongScannerPassword12"})
        csrf = client.cookies.get("xscan_csrf")
        opened = client.get("/api/v1/config/scanlist").json()
        (app_paths.dsdplus / "FMP24.ScanList").write_text("155.500 FM DELAY=2 Channel Two\n", encoding="utf-8")
        response = client.put("/api/v1/config/scanlist", json={"revision": opened["revision"], "text": opened["text"]}, headers={"X-CSRF-Token": csrf})
        assert response.status_code == 409


def test_complete_whep_session_lifecycle(app_paths, monkeypatch):
    class FakeAsyncClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            return httpx.Response(201, content=b"answer", headers={"Content-Type": "application/sdp", "Location": "session/abc"})
        async def request(self, method, *args, **kwargs):
            return httpx.Response(204 if method == "DELETE" else 200, content=b"candidate")

    monkeypatch.setattr("xscan.api.httpx.AsyncClient", FakeAsyncClient)
    app = create_app(app_paths)
    with TestClient(app) as client:
        public_created = client.post("/api/m2/whep", content=b"offer", headers={"Content-Type": "application/sdp"})
        assert public_created.status_code == 201
        public_location = public_created.headers["location"]
        assert public_location.startswith("/api/m2/whep/")
        assert client.patch(public_location, content=b"candidate").status_code == 200
        assert client.delete(public_location).status_code == 204
        client.post("/api/v1/auth/setup", json={"password": "LongScannerPassword12"})
        csrf = client.cookies.get("xscan_csrf")
        headers = {"X-CSRF-Token": csrf, "Content-Type": "application/sdp"}
        created = client.post("/api/v1/stream/whep", content=b"offer", headers=headers)
        assert created.status_code == 201
        location = created.headers["location"]
        patched = client.patch(location, content=b"candidate", headers={"X-CSRF-Token": csrf})
        assert patched.status_code == 200
        assert client.delete(location, headers={"X-CSRF-Token": csrf}).status_code == 204
        assert client.delete(location, headers={"X-CSRF-Token": csrf}).status_code == 204
        assert client.patch(location, content=b"late", headers={"X-CSRF-Token": csrf}).status_code == 404


def test_m2_status_calls_and_audio_are_public_but_minimal(app_paths):
    app = create_app(app_paths)
    audio = app_paths.recordings / "m2-sample.mp3"
    audio.write_bytes(b"0123456789")
    app.state.context.database.add_call({
        "id": "m2-call", "started_at": "2026-01-01T00:00:00", "audio_file": audio.name,
        "audio_bytes": 10, "duration_seconds": 4.2, "frequency": "155.0000", "mode": "FM", "label": "Dispatch",
    })
    with TestClient(app) as client:
        assert client.get("/api/v1/status").status_code == 401
        status = client.get("/api/m2/status")
        assert status.status_code == 200
        assert {"now_playing", "audio_level", "stream_ready", "audio_trigger_level", "local_playback_blocked"} <= set(status.json())
        assert status.json()["local_playback_blocked"] is True
        calls = client.get("/api/m2/calls").json()
        assert calls["items"][0]["label"] == "Dispatch"
        assert calls["items"][0]["audio_url"] == "/api/m2/calls/m2-call/audio"
        assert "audio_file" not in calls["items"][0]
        ranged = client.get("/api/m2/calls/m2-call/audio", headers={"Range": "bytes=3-6"})
        assert ranged.status_code == 206
        assert ranged.content == b"3456"


def test_favicon_is_served(app_paths):
    (app_paths.web / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    app = create_app(app_paths)
    with TestClient(app) as client:
        response = client.get("/favicon.svg")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/svg+xml")


def test_proxy_client_is_not_treated_as_localhost(app_paths):
    app = create_app(app_paths)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/setup",
            json={"password": "LongScannerPassword12"},
            headers={"X-Forwarded-For": "203.0.113.20", "X-Forwarded-Proto": "https"},
        )
        assert response.status_code == 403


def test_mobile_bootstrap_allows_local_only_install(app_paths):
    app = create_app(app_paths)
    with TestClient(app) as client:
        client.post("/api/v1/auth/setup", json={"password": "LongScannerPassword12"})
        response = client.get("/api/v1/mobile/bootstrap")
        assert response.status_code == 200
        assert response.json()["public_url"] == ""
        assert "tailscale_url" not in response.json()
        assert "lan_url" not in response.json()


def test_mobile_device_signature_token_and_replay_protection(app_paths):
    app = create_app(app_paths)
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    with TestClient(app) as client:
        client.post("/api/v1/auth/setup", json={"password": "LongScannerPassword12"})
        csrf = client.cookies.get("xscan_csrf")
        registered = client.post(
            "/api/v1/mobile/devices",
            json={"name": "Test phone", "public_key": base64.b64encode(public).decode()},
            headers={"X-CSRF-Token": csrf},
        )
        assert registered.status_code == 200
        device_id = registered.json()["id"]
        timestamp, nonce = int(time.time()), secrets.token_urlsafe(18)
        message = f"POST\n/api/v1/mobile/token\n{timestamp}\n{nonce}".encode()
        payload = {
            "device_id": device_id, "timestamp": timestamp, "nonce": nonce,
            "signature": base64.b64encode(private.sign(message)).decode(),
        }
        issued = client.post("/api/v1/mobile/token", json=payload)
        assert issued.status_code == 200
        token = issued.json()["access_token"]
        client.cookies.clear()
        assert client.get("/api/v1/status", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        assert client.post("/api/v1/mobile/token", json=payload).status_code == 409


def test_hls_proxy_is_authenticated_and_restricts_path(app_paths, monkeypatch):
    class FakeAsyncClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url):
            assert url.endswith("/scanner/index.m3u8?session=test-session")
            return httpx.Response(200, content=b"#EXTM3U", headers={"Content-Type": "application/vnd.apple.mpegurl"})

    monkeypatch.setattr("xscan.api.httpx.AsyncClient", FakeAsyncClient)
    app = create_app(app_paths)
    with TestClient(app) as client:
        assert client.get("/api/v1/stream/hls/scanner/index.m3u8").status_code == 401
        client.post("/api/v1/auth/setup", json={"password": "LongScannerPassword12"})
        response = client.get("/api/v1/stream/hls/scanner/index.m3u8?session=test-session")
        assert response.status_code == 200
        assert response.content == b"#EXTM3U"
        assert client.get("/api/v1/stream/hls/not-scanner/index.m3u8").status_code == 404
