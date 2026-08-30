from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import mimetypes
import os
import re
import threading
import time
import zipfile
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urljoin

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .auth import AuthManager, RateLimited
from .audio import AudioEngine
from .config_manager import ConfigManager, ConfigValidationError, RevisionConflict
from .database import Database
from .events import EventBus
from .logging_setup import configure_logging
from .migration import Migrator
from .paths import AppPaths
from .runtime import HostRuntime
from .settings import SettingsStore
from .state import RuntimeState
from .streaming import StreamingManager, local_ipv4_addresses
from .supervisor import HardwareControlDisabled, ProcessSupervisor


SESSION_COOKIE = "xscan_session"
CSRF_COOKIE = "xscan_csrf"


class AppContext:
    def __init__(self, paths: AppPaths, verbose: bool = False):
        paths.ensure()
        self.paths = paths
        self.logger = configure_logging(paths, verbose)
        self.settings = SettingsStore(paths)
        self.events = EventBus()
        self.database = Database(paths)
        self.state = RuntimeState(paths, self.events)
        self.config = ConfigManager(paths)
        self.auth = AuthManager(paths, self.database)
        self.streaming = StreamingManager(paths, self.settings, self.state, self.events, self.logger)
        self.supervisor = ProcessSupervisor(paths, self.settings, self.state, self.events, self.logger)
        self.audio = AudioEngine(paths, self.settings, self.database, self.state, self.events, self.streaming, self.logger)
        self.runtime = HostRuntime(
            self.settings, self.state, self.events, self.supervisor, self.audio, self.streaming, self.logger
        )
        self.migrator = Migrator(paths, self.settings, self.database, self.logger)
        self.whep_sessions: dict[str, str] = {}
        self.whep_lock = threading.Lock()

    def initialise(self) -> None:
        result = self.migrator.run()
        recovered = self.audio.recover_partials()
        self.logger.info("Migration complete: %s; recovered=%s", result, recovered)
        self.state.update_component("web", "running", message="API is serving")
        self.runtime.auto_start()

    def close(self) -> None:
        self.runtime.close()


def _is_local(request: Request) -> bool:
    host = _client_address(request)
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


def _client_address(request: Request) -> str:
    """Trust proxy client headers only when the direct peer is loopback."""
    direct = request.client.host if request.client else ""
    if direct in {"127.0.0.1", "::1", "localhost", "testclient"}:
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if forwarded:
            return forwarded
    return direct


def _secure_request(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"


def _set_auth_cookies(response: Response, token: str, csrf: str, request: Request, max_age: int) -> None:
    secure = _secure_request(request)
    response.set_cookie(SESSION_COOKIE, token, max_age=max_age, httponly=True, secure=secure, samesite="strict", path="/")
    response.set_cookie(CSRF_COOKIE, csrf, max_age=max_age, httponly=False, secure=secure, samesite="strict", path="/")


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


def create_app(paths: AppPaths | None = None, verbose: bool = False) -> FastAPI:
    context = AppContext(paths or AppPaths.discover(), verbose)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        context.events.bind_loop()
        await asyncio.to_thread(context.initialise)
        yield
        await asyncio.to_thread(context.close)

    app = FastAPI(title="XScan V2", version=__version__, lifespan=lifespan)
    app.state.context = context

    def require_session(request: Request) -> dict[str, str]:
        token = request.cookies.get(SESSION_COOKIE, "")
        session = context.database.validate_session(token) if token else None
        if not context.auth.is_configured:
            raise HTTPException(428, "Administrator setup is required")
        if not session:
            raise HTTPException(401, "Authentication required")
        return {**session, "token": token}

    def _require_read_auth(request: Request, scope: str) -> dict[str, str]:
        token = request.cookies.get(SESSION_COOKIE, "")
        session = context.database.validate_session(token) if token else None
        if session:
            return {**session, "token": token, "kind": "session"}
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            mobile = context.database.validate_mobile_token(authorization[7:].strip(), scope)
            if mobile:
                return {**mobile, "kind": "mobile"}
        raise HTTPException(401, "Authentication required")

    def require_status_auth(request: Request) -> dict[str, str]:
        return _require_read_auth(request, "status:read")

    def require_event_auth(request: Request) -> dict[str, str]:
        return _require_read_auth(request, "events:read")

    def require_stream_auth(request: Request) -> dict[str, str]:
        return _require_read_auth(request, "stream:read")

    def require_app_auth(request: Request) -> dict[str, str]:
        return _require_read_auth(request, "app:read")

    def require_csrf(request: Request, session: dict[str, str] = Depends(require_session)) -> dict[str, str]:
        header = request.headers.get("x-csrf-token", "")
        cookie = request.cookies.get(CSRF_COOKIE, "")
        if not header or not cookie or header != cookie or header != session["csrf"]:
            raise HTTPException(403, "CSRF validation failed")
        return session

    @app.exception_handler(RevisionConflict)
    async def revision_conflict_handler(_request: Request, exc: RevisionConflict):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ConfigValidationError)
    async def validation_handler(_request: Request, exc: ConfigValidationError):
        return JSONResponse(status_code=422, content={"detail": str(exc), "issues": exc.issues})

    @app.exception_handler(HardwareControlDisabled)
    async def hardware_lock_handler(_request: Request, exc: HardwareControlDisabled):
        return JSONResponse(status_code=423, content={"detail": str(exc)})

    @app.get("/api/v1/auth/state")
    def auth_state(request: Request):
        token = request.cookies.get(SESSION_COOKIE, "")
        return {
            "configured": context.auth.is_configured,
            "authenticated": bool(token and context.database.validate_session(token)),
            "setup_allowed": _is_local(request),
        }

    @app.post("/api/v1/auth/setup")
    def auth_setup(request: Request, payload: dict[str, Any] = Body(...)):
        if not _is_local(request):
            raise HTTPException(403, "First-time setup is allowed only from localhost")
        try:
            context.auth.setup(str(payload.get("password") or ""))
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        hours = int(context.settings.section("server")["session_hours"])
        token, csrf, expires = context.database.create_session(hours)
        response = JSONResponse({"authenticated": True, "expires_at": expires})
        _set_auth_cookies(response, token, csrf, request, hours * 3600)
        return response

    @app.post("/api/v1/auth/login")
    def auth_login(request: Request, payload: dict[str, Any] = Body(...)):
        key = _client_address(request) or "unknown"
        try:
            valid = context.auth.verify(str(payload.get("password") or ""), key)
        except RateLimited as exc:
            raise HTTPException(429, str(exc)) from exc
        if not valid:
            raise HTTPException(401, "Invalid password")
        hours = int(context.settings.section("server")["session_hours"])
        token, csrf, expires = context.database.create_session(hours)
        response = JSONResponse({"authenticated": True, "expires_at": expires})
        _set_auth_cookies(response, token, csrf, request, hours * 3600)
        return response

    @app.post("/api/v1/auth/logout")
    def auth_logout(session: dict[str, str] = Depends(require_csrf)):
        context.database.revoke_session(session["token"])
        response = JSONResponse({"authenticated": False})
        _clear_auth_cookies(response)
        return response

    @app.get("/api/v1/status")
    def status(_session=Depends(require_status_auth)):
        payload = context.state.snapshot()
        settings = context.settings.snapshot()
        payload.update(
            {
                "version": __version__,
                "hardware_control_enabled": settings["runtime"]["hardware_control_enabled"],
                "streaming_enabled": settings["streaming"]["enabled"],
                "lan_http_warning": bool(settings["server"]["lan_http_enabled"]),
                "web_port": settings["server"]["port"],
                "audio_trigger_level": settings["audio"]["trigger_level"],
                "audio_device_name": settings["audio"]["device_name"],
            }
        )
        warning_threshold = float(settings["storage"]["warning_free_gb"])
        payload["storage"]["warning"] = payload["storage"]["free_gb"] < warning_threshold
        payload["storage"]["warning_threshold_gb"] = warning_threshold
        return payload

    def m2_status_payload(request: Request | None = None) -> dict[str, Any]:
        """Small, intentionally public status surface for the M2 listener."""
        snapshot = context.state.snapshot()
        settings = context.settings.snapshot()
        components = snapshot.get("components") or {}
        ffmpeg = components.get("ffmpeg") or {}
        mediamtx = components.get("mediamtx") or {}
        stream_ready = bool(
            settings["streaming"]["enabled"]
            and ffmpeg.get("pid_alive") is not False
            and mediamtx.get("pid_alive") is not False
            and ffmpeg.get("state") in {"running", "ready", "live"}
            and mediamtx.get("state") in {"running", "ready", "live"}
        )
        client_address = _client_address(request) if request else ""
        local_playback_blocked = bool(
            request and (_is_local(request) or client_address in set(local_ipv4_addresses()))
        )
        return {
            "running": bool(snapshot.get("running")),
            "recording": bool(snapshot.get("recording")),
            "audio_level": float(snapshot.get("audio_level") or 0),
            "audio_trigger_level": float(settings["audio"]["trigger_level"]),
            "audio_device_name": str(settings["audio"].get("device_name") or "Scanner audio"),
            "now_playing": snapshot.get("now_playing") or {},
            "streaming_enabled": bool(settings["streaming"]["enabled"]),
            "stream_ready": stream_ready,
            "local_playback_blocked": local_playback_blocked,
            "updated_at": datetime.now(UTC).isoformat(),
        }

    def m2_call_payload(item: dict[str, Any]) -> dict[str, Any]:
        audio_name = Path(str(item.get("audio_file") or "")).name
        playable = bool(audio_name and (context.paths.recordings / audio_name).is_file())
        return {
            "id": str(item.get("id") or ""),
            "started_at": item.get("started_at"),
            "duration_seconds": item.get("duration_seconds"),
            "frequency": str(item.get("frequency") or ""),
            "mode": str(item.get("mode") or ""),
            "label": str(item.get("label") or item.get("frequency") or "Scanner call"),
            "radio_alias": str(item.get("radio_alias") or ""),
            "talkgroup_alias": str(item.get("talkgroup_alias") or ""),
            "playable": playable,
            "audio_url": f"/api/m2/calls/{item.get('id')}/audio" if playable else "",
        }

    @app.get("/api/m2/status")
    def m2_status(request: Request):
        return m2_status_payload(request)

    @app.get("/api/m2/calls")
    def m2_calls(limit: int = Query(40, ge=1, le=100)):
        result = context.database.list_calls(state="active", limit=limit)
        return {
            "items": [m2_call_payload(item) for item in result["items"]],
            "total": result["total"],
        }

    @app.get("/api/m2/events")
    async def m2_events(request: Request):
        queue = context.events.subscribe()
        allowed = {"audio-level", "now-playing", "recording", "call-completed", "component", "stream", "system"}

        async def stream() -> AsyncIterator[str]:
            try:
                yield f"event: snapshot\ndata: {json.dumps(m2_status_payload(request), default=str)}\n\n"
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                        if event.type in allowed:
                            yield f"event: {event.type}\ndata: {json.dumps(event.data, default=str)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": heartbeat\n\n"
            finally:
                context.events.unsubscribe(queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/v1/mobile/devices")
    def register_mobile_device(payload: dict[str, Any] = Body(...), _session=Depends(require_csrf)):
        name = str(payload.get("name") or "Android device").strip()[:80]
        public_key = str(payload.get("public_key") or "").strip()
        try:
            raw_key = base64.b64decode(public_key, validate=True)
            Ed25519PublicKey.from_public_bytes(raw_key)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, "public_key must be a base64 Ed25519 public key") from exc
        if len(raw_key) != 32:
            raise HTTPException(422, "public_key must be a 32-byte Ed25519 key")
        return context.database.register_mobile_device(name, public_key)

    @app.get("/api/v1/mobile/devices")
    def mobile_devices(_session=Depends(require_session)):
        return {"items": context.database.list_mobile_devices()}

    @app.delete("/api/v1/mobile/devices/{device_id}")
    def revoke_mobile_device(device_id: str, _session=Depends(require_csrf)):
        if not context.database.revoke_mobile_device(device_id):
            raise HTTPException(404, "Mobile device not found")
        return {"revoked": True, "id": device_id}

    @app.post("/api/v1/mobile/token")
    def mobile_token(payload: dict[str, Any] = Body(...)):
        device_id = str(payload.get("device_id") or "")
        nonce = str(payload.get("nonce") or "")
        signature = str(payload.get("signature") or "")
        try:
            timestamp = int(payload.get("timestamp"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, "timestamp is required") from exc
        if abs(int(time.time()) - timestamp) > 60:
            raise HTTPException(401, "Mobile signature timestamp is stale")
        if not 16 <= len(nonce) <= 128:
            raise HTTPException(422, "nonce must be 16 to 128 characters")
        device = context.database.get_mobile_device(device_id)
        if not device:
            raise HTTPException(401, "Mobile device is not registered")
        message = f"POST\n/api/v1/mobile/token\n{timestamp}\n{nonce}".encode()
        try:
            key = Ed25519PublicKey.from_public_bytes(base64.b64decode(device["public_key"], validate=True))
            key.verify(base64.b64decode(signature, validate=True), message)
        except (ValueError, TypeError, InvalidSignature) as exc:
            raise HTTPException(401, "Mobile signature is invalid") from exc
        if not context.database.consume_mobile_nonce(device_id, nonce):
            raise HTTPException(409, "Mobile nonce has already been used")
        token, expires = context.database.create_mobile_token(device_id)
        return {"access_token": token, "token_type": "bearer", "expires_at": expires, "expires_in": 300}

    @app.get("/api/v1/mobile/bootstrap")
    def mobile_bootstrap(request: Request, _auth=Depends(require_status_auth)):
        settings = context.settings.snapshot()
        public_url = settings["server"]["public_url"]
        return {
            "server_id": hashlib.sha256(str(context.paths.state).encode()).hexdigest()[:16],
            "api_version": "v1",
            "host_version": __version__,
            "public_url": public_url,
            "hls_path": f"/api/v1/stream/hls/{settings['streaming']['stream_name']}/index.m3u8",
        }

    def _android_apk() -> Path:
        return context.paths.web / "downloads" / "XScan-Android-1.0.4.apk"

    @app.get("/api/v1/mobile/release")
    def mobile_release(_auth=Depends(require_app_auth)):
        path = _android_apk()
        return {
            "available": path.is_file(), "version_name": "1.0.4", "version_code": 5,
            "bytes": path.stat().st_size if path.is_file() else 0,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "",
            "download_url": "/api/v1/mobile/release/apk",
        }

    @app.get("/api/v1/mobile/release/apk")
    def mobile_apk(_auth=Depends(require_app_auth)):
        path = _android_apk()
        if not path.is_file():
            raise HTTPException(404, "Android release is not installed")
        return FileResponse(path, media_type="application/vnd.android.package-archive", filename=path.name)

    @app.post("/api/v1/system/{action}")
    def system_action(action: str, _session=Depends(require_csrf)):
        if action == "start":
            return context.runtime.start()
        if action == "stop":
            return context.runtime.stop()
        if action == "restart":
            return context.runtime.restart()
        raise HTTPException(404, "Unknown system action")

    @app.post("/api/v1/system/windows/{action}")
    def native_windows(action: str, _session=Depends(require_csrf)):
        if action not in {"show", "hide"}:
            raise HTTPException(404, "Unknown window action")
        count = context.supervisor.set_native_windows_visible(action == "show")
        return {"action": action, "windows": count}

    @app.get("/api/v1/calls")
    def calls(
        search: str = "", state: str = "active", frequency: str = "", mode: str = "",
        offset: int = 0, limit: int = Query(100, ge=1, le=500), _session=Depends(require_session),
    ):
        if state not in {"active", "trashed"}:
            raise HTTPException(400, "state must be active or trashed")
        return context.database.list_calls(search=search, state=state, frequency=frequency, mode=mode, offset=offset, limit=limit)

    @app.get("/api/v1/calls/{call_id}")
    def call_detail(call_id: str, _session=Depends(require_session)):
        item = context.database.get_call(call_id)
        if not item:
            raise HTTPException(404, "Call not found")
        return item

    @app.patch("/api/v1/calls/{call_id}")
    def update_call(call_id: str, payload: dict[str, Any] = Body(...), _session=Depends(require_csrf)):
        item = context.database.update_call(call_id, payload)
        if not item:
            raise HTTPException(404, "Call not found")
        return item

    @app.post("/api/v1/calls/trash")
    def trash_calls(payload: dict[str, Any] = Body(...), _session=Depends(require_csrf)):
        return {"moved": context.database.trash_calls(payload.get("ids") or [])}

    @app.post("/api/v1/calls/restore")
    def restore_calls(payload: dict[str, Any] = Body(...), _session=Depends(require_csrf)):
        return {"restored": context.database.restore_calls(payload.get("ids") or [])}

    @app.post("/api/v1/calls/purge")
    def purge_calls(payload: dict[str, Any] = Body(...), _session=Depends(require_csrf)):
        if payload.get("confirm") != "PURGE":
            raise HTTPException(400, "Permanent purge requires confirm=PURGE")
        return {"purged": context.database.purge_calls(payload.get("ids") or [])}

    def call_audio_response(call_id: str, request: Request, download: bool = False, active_only: bool = False):
        item = context.database.get_call(call_id)
        if not item or active_only and item.get("state") != "active":
            raise HTTPException(404, "Call not found")
        folder = context.paths.trash if item["state"] == "trashed" else context.paths.recordings
        name = Path(item["audio_file"]).name
        path = folder / name
        if not path.is_file():
            raise HTTPException(404, "Audio file is missing")
        size = path.stat().st_size
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        disposition = "attachment" if download else "inline"
        headers = {"Accept-Ranges": "bytes", "Content-Disposition": f'{disposition}; filename="{path.name}"'}
        range_header = request.headers.get("range")
        if not range_header:
            return FileResponse(path, media_type=media_type, headers=headers)
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
        if not match:
            raise HTTPException(416, "Invalid byte range", headers={"Content-Range": f"bytes */{size}"})
        start_text, end_text = match.groups()
        if not start_text and not end_text:
            raise HTTPException(416, "Invalid byte range", headers={"Content-Range": f"bytes */{size}"})
        if start_text:
            start = int(start_text)
            end = int(end_text) if end_text else size - 1
        else:
            length = int(end_text)
            start, end = max(0, size - length), size - 1
        if start >= size or start > end:
            raise HTTPException(416, "Range outside file", headers={"Content-Range": f"bytes */{size}"})
        end = min(end, size - 1)

        def reader():
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = handle.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        headers.update({"Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(end - start + 1)})
        return StreamingResponse(reader(), status_code=206, media_type=media_type, headers=headers)

    @app.get("/api/v1/calls/{call_id}/audio")
    def call_audio(call_id: str, request: Request, download: bool = False, _session=Depends(require_session)):
        return call_audio_response(call_id, request, download)

    @app.get("/api/m2/calls/{call_id}/audio")
    def m2_call_audio(call_id: str, request: Request):
        return call_audio_response(call_id, request, active_only=True)

    @app.get("/api/v1/devices")
    def devices(_session=Depends(require_session)):
        audio_settings = context.settings.section("audio")
        selected = audio_settings["device_name"]
        try:
            items = context.audio.devices()
        except Exception as exc:
            items = []
            context.logger.warning("Audio device query failed: %s", exc)
        return {"items": items, "selected_name": selected, "selected_host_api": audio_settings.get("device_host_api", ""), "level": context.state.audio_level}

    @app.put("/api/v1/devices/selected")
    def select_device(payload: dict[str, Any] = Body(...), _session=Depends(require_csrf)):
        name = str(payload.get("name") or "").strip()
        host_api = str(payload.get("host_api") or "").strip()
        if not name:
            raise HTTPException(400, "Device name is required")
        context.settings.update({"audio": {"device_name": name, "device_host_api": host_api}})
        return {"selected_name": name, "selected_host_api": host_api, "restart_required": True}

    @app.post("/api/v1/devices/calibrate")
    async def calibrate_device(seconds: float = Query(3.0, ge=1.0, le=10.0), _session=Depends(require_csrf)):
        samples: list[float] = []
        deadline = asyncio.get_running_loop().time() + seconds
        while asyncio.get_running_loop().time() < deadline:
            samples.append(context.state.audio_level)
            await asyncio.sleep(0.05)
        ordered = sorted(samples)
        noise_floor = ordered[min(len(ordered) - 1, int(len(ordered) * .95))] if ordered else 0.0
        recommended = max(0.0001, round(noise_floor * 2.5, 6))
        return {"samples": len(samples), "noise_floor_p95": noise_floor, "recommended_trigger": recommended}

    @app.get("/api/v1/settings")
    def get_settings(_session=Depends(require_session)):
        return context.settings.snapshot()

    @app.put("/api/v1/settings")
    def put_settings(payload: dict[str, Any] = Body(...), _session=Depends(require_csrf)):
        runtime_patch = payload.get("runtime")
        if isinstance(runtime_patch, dict) and "hardware_control_enabled" in runtime_patch:
            raise HTTPException(403, "Hardware control can only be enabled by the cutover installer")
        try:
            result = context.settings.update(payload)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        context.events.publish("settings", {"updated": True})
        return result

    @app.get("/api/v1/settings/backups")
    def settings_backups(_session=Depends(require_session)):
        return {"items": context.settings.backups()}

    @app.post("/api/v1/settings/restore")
    def settings_restore(payload: dict[str, Any] = Body(...), _session=Depends(require_csrf)):
        try:
            result = context.settings.restore(str(payload.get("backup") or ""))
        except FileNotFoundError as exc:
            raise HTTPException(404, "Settings backup not found") from exc
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        context.events.publish("settings", {"restored": True})
        return result

    @app.get("/api/v1/config")
    def config_index(_session=Depends(require_session)):
        return {"items": [{"key": key, "name": value} for key, value in context.config.WHITELIST.items()]}

    @app.get("/api/v1/config/{key}")
    def config_read(key: str, _session=Depends(require_session)):
        try:
            return context.config.read(key)
        except KeyError as exc:
            raise HTTPException(404, "Configuration resource is not editable") from exc

    @app.put("/api/v1/config/{key}")
    def config_write(key: str, payload: dict[str, Any] = Body(...), _session=Depends(require_csrf)):
        try:
            if "patches" in payload:
                result = context.config.patch_lines(key, payload["patches"], str(payload.get("revision") or ""))
            else:
                result = context.config.save_text(key, str(payload.get("text") or ""), str(payload.get("revision") or ""))
            context.events.publish("configuration", {"key": key, "revision": result["revision"]})
            return result
        except KeyError as exc:
            raise HTTPException(404, "Configuration resource is not editable") from exc

    @app.get("/api/v1/config/{key}/backups")
    def config_backups(key: str, _session=Depends(require_session)):
        try:
            return {"items": context.config.backups(key)}
        except KeyError as exc:
            raise HTTPException(404, "Configuration resource is not editable") from exc

    @app.post("/api/v1/config/{key}/restore")
    def config_restore(key: str, payload: dict[str, Any] = Body(...), _session=Depends(require_csrf)):
        try:
            return context.config.restore(key, str(payload.get("backup") or ""), str(payload.get("revision") or ""))
        except FileNotFoundError as exc:
            raise HTTPException(404, "Backup not found") from exc

    @app.get("/api/v1/diagnostics")
    def diagnostics(_session=Depends(require_session)):
        return {
            "status": context.state.snapshot(),
            "processes": context.supervisor.process_snapshot(),
            "streaming": context.streaming.health(),
            "paths": {"state": str(context.paths.state), "dsdplus": str(context.paths.dsdplus), "recordings": str(context.paths.recordings)},
            "tools": {"ffmpeg": str(context.streaming.ffmpeg or ""), "mediamtx": str(context.streaming.mediamtx or "")},
        }

    @app.get("/api/v1/diagnostics/logs")
    def diagnostics_logs(limit: int = Query(300, ge=1, le=2000), _session=Depends(require_session)):
        path = context.paths.logs / "xscan.log"
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:] if path.is_file() else []
        return {"items": lines}

    @app.get("/api/v1/diagnostics/bundle")
    def diagnostics_bundle(_session=Depends(require_session)):
        output = io.BytesIO()
        settings = context.settings.snapshot()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("status.json", json.dumps(context.state.snapshot(), indent=2, default=str))
            archive.writestr("settings.json", json.dumps(settings, indent=2))
            archive.writestr("processes.json", json.dumps(context.supervisor.process_snapshot(), indent=2))
            log_path = context.paths.logs / "xscan.log"
            if log_path.is_file():
                archive.writestr("xscan.log", log_path.read_bytes()[-2_000_000:])
            manifest = {}
            for key, name in context.config.WHITELIST.items():
                path = context.paths.dsdplus / name
                if path.is_file():
                    manifest[name] = {"bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            archive.writestr("config-manifest.json", json.dumps(manifest, indent=2))
        output.seek(0)
        return StreamingResponse(output, media_type="application/zip", headers={"Content-Disposition": "attachment; filename=xscan-support.zip"})

    @app.get("/api/v1/events")
    async def events(request: Request, _session=Depends(require_event_auth)):
        queue = context.events.subscribe()

        async def stream() -> AsyncIterator[str]:
            try:
                yield f"event: snapshot\ndata: {json.dumps(context.state.snapshot(), default=str)}\n\n"
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                        yield f"event: {event.type}\ndata: {json.dumps(context.events.serialise(event), default=str)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": heartbeat\n\n"
            finally:
                context.events.unsubscribe(queue)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/api/v1/stream/hls/{asset_path:path}")
    async def hls_proxy(request: Request, asset_path: str, _auth=Depends(require_stream_auth)):
        stream = context.settings.section("streaming")
        clean = asset_path.strip("/")
        stream_name = str(stream["stream_name"])
        if not clean.startswith(f"{stream_name}/") or ".." in clean.split("/"):
            raise HTTPException(404, "HLS asset not found")
        target = f"http://127.0.0.1:{int(stream['hls_port'])}/{clean}"
        if request.url.query:
            target += f"?{request.url.query}"
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                upstream = await client.get(target)
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"HLS stream unavailable: {exc}") from exc
        if upstream.status_code >= 400:
            raise HTTPException(upstream.status_code, "HLS asset unavailable")
        content_type = upstream.headers.get("content-type", "application/octet-stream")
        headers = {"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*"}
        return Response(content=upstream.content, media_type=content_type, headers=headers)

    def whep_base() -> str:
        stream = context.settings.section("streaming")
        return f"http://127.0.0.1:{stream['webrtc_port']}/{stream['stream_name']}/whep"

    async def whep_create_response(request: Request, public_prefix: str) -> Response:
        body = await request.body()
        async with httpx.AsyncClient(timeout=15) as client:
            try:
                upstream = await client.post(whep_base(), content=body, headers={"Content-Type": request.headers.get("content-type", "application/sdp")})
            except httpx.HTTPError as exc:
                raise HTTPException(502, f"WebRTC publisher unavailable: {exc}") from exc
        headers = {"Content-Type": upstream.headers.get("content-type", "application/sdp"), "Cache-Control": "no-store"}
        location = upstream.headers.get("location")
        if location:
            target = urljoin(whep_base(), location)
            token = os.urandom(16).hex()
            with context.whep_lock:
                context.whep_sessions[token] = target
            headers["Location"] = f"{public_prefix}/{token}"
        return Response(content=upstream.content, status_code=upstream.status_code, headers=headers)

    async def whep_session_response(token: str, request: Request) -> Response:
        with context.whep_lock:
            target = context.whep_sessions.get(token)
        if not target:
            if request.method == "DELETE":
                return Response(status_code=204)
            raise HTTPException(404, "WebRTC session not found")
        async with httpx.AsyncClient(timeout=15) as client:
            try:
                upstream = await client.request(
                    request.method, target, content=await request.body(),
                    headers={"Content-Type": request.headers.get("content-type", "application/trickle-ice-sdpfrag")},
                )
            except httpx.HTTPError as exc:
                if request.method == "DELETE":
                    with context.whep_lock:
                        context.whep_sessions.pop(token, None)
                    return Response(status_code=204)
                raise HTTPException(502, f"WebRTC session unavailable: {exc}") from exc
        if request.method == "DELETE":
            with context.whep_lock:
                context.whep_sessions.pop(token, None)
            return Response(status_code=204)
        return Response(content=upstream.content, status_code=upstream.status_code, headers={"Content-Type": upstream.headers.get("content-type", "text/plain"), "Cache-Control": "no-store"})

    @app.post("/api/v1/stream/whep")
    async def whep_create(request: Request, _session=Depends(require_csrf)):
        return await whep_create_response(request, "/api/v1/stream/whep")

    @app.api_route("/api/v1/stream/whep/{token}", methods=["PATCH", "DELETE"])
    async def whep_session(token: str, request: Request, _session=Depends(require_csrf)):
        return await whep_session_response(token, request)

    @app.post("/api/m2/whep")
    async def m2_whep_create(request: Request):
        return await whep_create_response(request, "/api/m2/whep")

    @app.api_route("/api/m2/whep/{token}", methods=["PATCH", "DELETE"])
    async def m2_whep_session(token: str, request: Request):
        return await whep_session_response(token, request)

    if not context.paths.web.is_dir():
        @app.get("/")
        def missing_web():
            return JSONResponse({"detail": f"Web assets missing at {context.paths.web}"}, status_code=503)
    else:
        app.mount("/", StaticFiles(directory=context.paths.web, html=True), name="web")
    return app
