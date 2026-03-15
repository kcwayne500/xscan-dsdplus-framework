import json
import mimetypes
import os
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_UI_DIR = os.path.join(SCRIPT_DIR, "webui")


class _Handler(BaseHTTPRequestHandler):
    server_version = "ScannerMetadataServer/1.0"

    def _send_bytes(self, status, content_type, data, cache_control="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", cache_control)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

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
        with open(file_path, "rb") as handle:
            self._send_bytes(200, content_type, handle.read(), cache_control=cache_control)

    def _resolve_webui_path(self, request_path):
        relative_path = request_path.split("?", 1)[0].lstrip("/")
        if not relative_path:
            relative_path = "index.html"
        candidate = os.path.normpath(os.path.join(WEB_UI_DIR, relative_path))
        webui_root = os.path.normcase(os.path.abspath(WEB_UI_DIR))
        candidate_abs = os.path.normcase(os.path.abspath(candidate))
        if candidate_abs == webui_root or candidate_abs.startswith(webui_root + os.sep):
            return candidate
        return None

    def do_GET(self):
        if self.path == "/api/status":
            payload = self.server.payload_func()
            data = json.dumps(payload).encode("utf-8")
            self._send_bytes(200, "application/json; charset=utf-8", data)
            return
        if self.path in ("/", "/index.html") or not self.path.startswith("/api/"):
            file_path = self._resolve_webui_path(self.path)
            if file_path:
                self._send_file(file_path)
                return
        self._send_bytes(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self):
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
        self.httpd = ThreadingHTTPServer((self.host, self.port), _Handler)
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
