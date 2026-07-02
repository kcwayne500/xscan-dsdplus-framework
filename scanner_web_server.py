import json
import ipaddress
import mimetypes
import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_UI_DIR = os.path.join(SCRIPT_DIR, "webui")
RECORDINGS_DIR = r"C:\DSDPlusFastlane\recordings"
RECORDINGS_LOG_FILE = os.path.join(RECORDINGS_DIR, "recordings_log.json")
DISCONNECT_ERRORS = (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)


def _parse_datetime(value):
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _load_recordings_log():
    if not os.path.exists(RECORDINGS_LOG_FILE):
        return []
    try:
        with open(RECORDINGS_LOG_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _build_audio_url(filename):
    return "/audio/" + urllib.parse.quote(filename)


def _normalize_recording(entry):
    if not isinstance(entry, dict):
        return None
    started_at = entry.get("started_at") or ""
    ended_at = entry.get("ended_at") or ""
    duration = _safe_float(entry.get("duration_seconds"))
    audio_candidates = [
        entry.get("audio_file"),
        entry.get("mp3_file"),
        entry.get("wav_file"),
    ]
    audio_filename = next((name for name in audio_candidates if isinstance(name, str) and name.strip()), "")
    audio_path = os.path.join(RECORDINGS_DIR, audio_filename) if audio_filename else ""
    file_exists = bool(audio_filename and os.path.isfile(audio_path))
    started_dt = _parse_datetime(started_at)
    started_label = started_dt.strftime("%Y-%m-%d %H:%M:%S") if started_dt else (started_at or "Unknown")
    normalized = {
        "id": audio_filename or f"{started_at}-{entry.get('frequency', 'unknown')}-{entry.get('label', 'Unknown_Channel')}",
        "started_at": started_at,
        "ended_at": ended_at,
        "started_at_label": started_label,
        "duration_seconds": round(duration, 3) if duration is not None else None,
        "frequency": str(entry.get("frequency") or "unknown"),
        "mode": str(entry.get("mode") or "unknown"),
        "label": str(entry.get("label") or "Unknown_Channel"),
        "raw_log_line": str(entry.get("raw_log_line") or ""),
        "stop_reason": str(entry.get("stop_reason") or ""),
        "audio_device": str(entry.get("audio_device") or ""),
        "audio_file": audio_filename,
        "wav_file": str(entry.get("wav_file") or ""),
        "mp3_file": str(entry.get("mp3_file") or ""),
        "playable_url": _build_audio_url(audio_filename) if file_exists else "",
        "file_exists": file_exists,
    }
    normalized["_started_dt"] = started_dt
    return normalized


def _filter_recordings(items, params):
    search_text = (params.get("search", [""])[0] or "").strip().lower()
    frequency_filter = (params.get("frequency", [""])[0] or "").strip().lower()
    mode_filter = (params.get("mode", [""])[0] or "").strip().lower()
    label_filter = (params.get("label", [""])[0] or "").strip().lower()
    started_from = _parse_datetime((params.get("started_from", [""])[0] or "").strip())
    started_to = _parse_datetime((params.get("started_to", [""])[0] or "").strip())
    min_duration = _safe_float((params.get("min_duration", [""])[0] or "").strip())
    max_duration = _safe_float((params.get("max_duration", [""])[0] or "").strip())
    offset = max(0, int((params.get("offset", ["0"])[0] or "0")))
    limit = min(500, max(1, int((params.get("limit", ["250"])[0] or "250"))))

    filtered = []
    for item in items:
        haystack = " ".join(
            [
                item["label"],
                item["frequency"],
                item["mode"],
                item["raw_log_line"],
                item["audio_device"],
                item["stop_reason"],
            ]
        ).lower()
        if search_text and search_text not in haystack:
            continue
        if frequency_filter and frequency_filter not in item["frequency"].lower():
            continue
        if mode_filter and mode_filter not in item["mode"].lower():
            continue
        if label_filter and label_filter not in item["label"].lower():
            continue
        item_dt = item.get("_started_dt")
        if started_from and (item_dt is None or item_dt < started_from):
            continue
        if started_to and (item_dt is None or item_dt > started_to):
            continue
        duration = item.get("duration_seconds")
        if min_duration is not None and (duration is None or duration < min_duration):
            continue
        if max_duration is not None and (duration is None or duration > max_duration):
            continue
        filtered.append(item)

    filtered.sort(key=lambda item: item.get("_started_dt") or datetime.min, reverse=True)
    total = len(filtered)
    page_items = filtered[offset : offset + limit]
    for item in page_items:
        item.pop("_started_dt", None)
    return {
        "items": page_items,
        "total": total,
        "offset": offset,
        "limit": limit,
    }


def _get_recordings_payload(params):
    normalized = [entry for entry in (_normalize_recording(item) for item in _load_recordings_log()) if entry]
    return _filter_recordings(normalized, params)


def _get_recent_calls_payload(params):
    normalized = [entry for entry in (_normalize_recording(item) for item in _load_recordings_log()) if entry]
    normalized.sort(key=lambda item: item.get("_started_dt") or datetime.min, reverse=True)
    limit = min(100, max(1, int((params.get("limit", ["40"])[0] or "40"))))
    items = normalized[:limit]
    for item in items:
        item.pop("_started_dt", None)
    return {
        "items": items,
        "total": len(normalized),
        "limit": limit,
    }


def _is_allowed_audio_file(filename):
    safe_name = os.path.basename(filename)
    if not safe_name or safe_name != filename:
        return False
    normalized = [entry for entry in (_normalize_recording(item) for item in _load_recordings_log()) if entry]
    return any(item.get("audio_file") == safe_name for item in normalized)


def _is_allowed_client(host):
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


class _ThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, DISCONNECT_ERRORS):
            return
        super().handle_error(request, client_address)


class _Handler(BaseHTTPRequestHandler):
    server_version = "ScannerMetadataServer/1.0"

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except DISCONNECT_ERRORS:
            self.close_connection = True

    def _client_allowed(self):
        host = self.client_address[0] if self.client_address else ""
        if _is_allowed_client(host):
            return True
        self.close_connection = True
        self._send_bytes(403, "text/plain; charset=utf-8", b"forbidden")
        return False

    def _send_bytes(self, status, content_type, data, cache_control="no-store"):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", cache_control)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except DISCONNECT_ERRORS:
            self.close_connection = True

    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        return self.rfile.read(length) if length > 0 else b""

    def _get_whep_target(self):
        payload = self.server.payload_func()
        raw_stream_url = str(payload.get("raw_stream_url") or "").strip()
        if not raw_stream_url or raw_stream_url.lower() == "streaming disabled":
            return None
        return raw_stream_url.rstrip("/") + "/whep"

    def _send_file(self, file_path, cache_control="public, max-age=3600"):
        if not os.path.isfile(file_path):
            self._send_bytes(404, "text/plain; charset=utf-8", b"not found")
            return
        content_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        file_size = os.path.getsize(file_path)
        range_header = self.headers.get("Range", "")
        start = 0
        end = file_size - 1
        status = 200
        if range_header.startswith("bytes="):
            range_value = range_header.split("=", 1)[1].split(",", 1)[0].strip()
            raw_start, _, raw_end = range_value.partition("-")
            try:
                if raw_start:
                    start = int(raw_start)
                    end = int(raw_end) if raw_end else file_size - 1
                elif raw_end:
                    suffix_size = int(raw_end)
                    start = max(0, file_size - suffix_size)
                    end = file_size - 1
                if start < 0 or end < start or start >= file_size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{file_size}")
                    self.end_headers()
                    return
                end = min(end, file_size - 1)
                status = 206
            except ValueError:
                start = 0
                end = file_size - 1
                status = 200
        content_length = max(0, end - start + 1)
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", cache_control)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(content_length))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.end_headers()
            with open(file_path, "rb") as handle:
                handle.seek(start)
                remaining = content_length
                while remaining > 0:
                    chunk = handle.read(min(1024 * 256, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except DISCONNECT_ERRORS:
            self.close_connection = True

    def _resolve_webui_path(self, request_path):
        relative_path = urllib.parse.urlsplit(request_path).path.lstrip("/")
        if not relative_path:
            relative_path = "index.html"
        candidate = os.path.normpath(os.path.join(WEB_UI_DIR, relative_path))
        webui_root = os.path.normcase(os.path.abspath(WEB_UI_DIR))
        candidate_abs = os.path.normcase(os.path.abspath(candidate))
        if not (candidate_abs == webui_root or candidate_abs.startswith(webui_root + os.sep)):
            return None
        if os.path.isdir(candidate):
            candidate = os.path.join(candidate, "index.html")
        return candidate

    def do_GET(self):
        if not self._client_allowed():
            return
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)
        if path == "/recordings":
            self.send_response(302)
            self.send_header("Location", "/recordings/")
            self.end_headers()
            return
        if path == "/api/status":
            payload = self.server.payload_func()
            data = json.dumps(payload).encode("utf-8")
            self._send_bytes(200, "application/json; charset=utf-8", data)
            return
        if path == "/api/recordings":
            payload = _get_recordings_payload(params)
            data = json.dumps(payload).encode("utf-8")
            self._send_bytes(200, "application/json; charset=utf-8", data)
            return
        if path == "/api/radio-calls":
            payload = _get_recent_calls_payload(params)
            data = json.dumps(payload).encode("utf-8")
            self._send_bytes(200, "application/json; charset=utf-8", data)
            return
        if path.startswith("/audio/"):
            filename = urllib.parse.unquote(path.split("/audio/", 1)[1])
            if not _is_allowed_audio_file(filename):
                self._send_bytes(404, "text/plain; charset=utf-8", b"not found")
                return
            self._send_file(os.path.join(RECORDINGS_DIR, filename), cache_control="public, max-age=300")
            return
        if path in ("/", "/index.html") or path.startswith("/recordings/") or not path.startswith("/api/"):
            file_path = self._resolve_webui_path(path)
            if file_path:
                self._send_file(file_path)
                return
        self._send_bytes(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self):
        if not self._client_allowed():
            return
        if self.path != "/api/whep":
            self._send_bytes(404, "text/plain; charset=utf-8", b"not found")
            return
        target = self._get_whep_target()
        if not target:
            self._send_bytes(503, "text/plain; charset=utf-8", b"stream not available")
            return
        body = self._read_body()
        request = urllib.request.Request(
            target,
            data=body,
            headers={
                "Content-Type": self.headers.get("Content-Type", "application/sdp"),
                "Accept": "application/sdp",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                data = response.read()
                self._send_bytes(
                    getattr(response, "status", 200),
                    response.headers.get("Content-Type", "application/sdp"),
                    data,
                )
        except urllib.error.HTTPError as exc:
            data = exc.read() if hasattr(exc, "read") else str(exc).encode("utf-8")
            self._send_bytes(exc.code, exc.headers.get("Content-Type", "text/plain; charset=utf-8"), data)
        except OSError as exc:
            self._send_bytes(502, "text/plain; charset=utf-8", str(exc).encode("utf-8"))

    def log_message(self, fmt, *args):
        return


class MetadataWebServer:
    def __init__(self, host, port, payload_func):
        self.host = host
        self.port = port
        self.payload_func = payload_func
        self.httpd = None
        self.thread = None

    def start(self):
        if self.httpd is not None:
            return
        self.httpd = _ThreadingHTTPServer((self.host, self.port), _Handler)
        self.httpd.payload_func = self.payload_func
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        if self.httpd is None:
            return
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        finally:
            self.httpd = None
            self.thread = None

