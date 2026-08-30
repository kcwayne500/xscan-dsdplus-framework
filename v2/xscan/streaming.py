from __future__ import annotations

import logging
import os
import queue
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import psutil

from .events import EventBus
from .paths import AppPaths
from .settings import SettingsStore
from .state import RuntimeState
from .windows import WINDOW_CREATION_FLAGS, terminate_process


def _find_tool(paths: AppPaths, folder: str, executable: str, configured: str = "") -> Path | None:
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend([
        paths.bundle / folder / executable,
        paths.bundle.parent / folder / executable,
    ])
    command = shutil.which(Path(executable).stem)
    if command:
        candidates.append(Path(command))
    return next((path for path in candidates if path.is_file()), None)


def local_ipv4_addresses() -> list[str]:
    addresses = ["127.0.0.1"]
    for values in psutil.net_if_addrs().values():
        for value in values:
            if value.family.name == "AF_INET" and value.address and value.address not in addresses and not value.address.startswith("169.254."):
                addresses.append(value.address)
    return addresses


class StreamingManager:
    def __init__(self, paths: AppPaths, settings: SettingsStore, state: RuntimeState, events: EventBus, logger: logging.Logger):
        self.paths = paths
        self.settings = settings
        self.state = state
        self.events = events
        self.logger = logger
        tools = settings.section("tools")
        self.ffmpeg = _find_tool(paths, "ffmpeg", "ffmpeg.exe", str(tools.get("ffmpeg") or ""))
        self.mediamtx = _find_tool(paths, "mediamtx", "mediamtx.exe", str(tools.get("mediamtx") or ""))
        self.mediamtx_process: subprocess.Popen | None = None
        self.ffmpeg_process: subprocess.Popen | None = None
        self._queue: queue.Queue[bytes | None] = queue.Queue(maxsize=256)
        self._writer: threading.Thread | None = None
        self._lock = threading.RLock()

    @property
    def available(self) -> bool:
        return bool(self.ffmpeg and self.mediamtx)

    def start(self, sample_rate: int) -> bool:
        config = self.settings.section("streaming")
        if not config["enabled"]:
            self.state.update_component("ffmpeg", "disabled", message="Live streaming is disabled")
            self.state.update_component("mediamtx", "disabled", message="Live streaming is disabled")
            return False
        if not self.available:
            missing = "FFmpeg" if not self.ffmpeg else "MediaMTX"
            self.state.update_component("ffmpeg" if not self.ffmpeg else "mediamtx", "fault", message=f"{missing} was not found")
            return False
        self.stop()
        self._queue = queue.Queue(maxsize=256)
        rtsp_port = int(config["rtsp_port"])
        webrtc_port = int(config["webrtc_port"])
        webrtc_media_port = int(config["webrtc_media_port"])
        hls_port = int(config["hls_port"])
        stream_name = str(config["stream_name"])
        media_config = self.paths.state / "mediamtx.generated.yml"
        advertised_hosts = local_ipv4_addresses()
        public_host = urlparse(str(self.settings.section("server").get("public_url") or "")).hostname
        if public_host and public_host not in advertised_hosts:
            advertised_hosts.append(public_host)
        hosts = "\n".join(f"  - {address}" for address in advertised_hosts)
        media_config.write_text(
            "\n".join(
                [
                    "logLevel: warn",
                    f"rtspAddress: :{rtsp_port}",
                    f"webrtcAddress: :{webrtc_port}",
                    f"webrtcLocalUDPAddress: :{webrtc_media_port}",
                    f"webrtcLocalTCPAddress: :{webrtc_media_port}",
                    "hls: yes",
                    f"hlsAddress: 127.0.0.1:{hls_port}",
                    "hlsAlwaysRemux: yes",
                    "hlsVariant: lowLatency",
                    "hlsSegmentCount: 7",
                    "hlsSegmentDuration: 1s",
                    "hlsPartDuration: 200ms",
                    "rtmp: no",
                    "api: no",
                    "metrics: no",
                    "pprof: no",
                    "webrtcAdditionalHosts:",
                    hosts,
                    "paths:",
                    f"  {stream_name}: {{}}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        try:
            self.mediamtx_process = subprocess.Popen(
                [str(self.mediamtx), str(media_config)],
                cwd=self.mediamtx.parent,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=WINDOW_CREATION_FLAGS,
            )
            self.state.update_component("mediamtx", "running", pid=self.mediamtx_process.pid, message=f"WebRTC :{webrtc_port}/{webrtc_media_port}; HLS :{hls_port}")
            self._pump_errors(self.mediamtx_process, "MediaMTX")
            time.sleep(0.4)
            if self.mediamtx_process.poll() is not None:
                raise RuntimeError("MediaMTX exited during startup")
            command = [
                str(self.ffmpeg), "-hide_banner", "-loglevel", "error", "-fflags", "nobuffer",
                "-f", "s16le", "-ar", str(sample_rate), "-ac", "1", "-i", "pipe:0",
                "-map", "0:a:0", "-c:a", "libopus", "-application", "lowdelay", "-frame_duration", "20",
                "-b:a", str(config["audio_bitrate"]), "-f", "rtsp", "-rtsp_transport", "tcp",
                f"rtsp://127.0.0.1:{rtsp_port}/{stream_name}",
            ]
            self.ffmpeg_process = subprocess.Popen(
                command,
                cwd=self.ffmpeg.parent,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=WINDOW_CREATION_FLAGS,
            )
            self.state.update_component("ffmpeg", "running", pid=self.ffmpeg_process.pid, message="Publishing Opus audio")
            self._pump_errors(self.ffmpeg_process, "FFmpeg stream")
            self._writer = threading.Thread(target=self._write_loop, name="stream-writer", daemon=True)
            self._writer.start()
            self.events.publish("stream", {"state": "live"})
            return True
        except Exception as exc:
            self.logger.exception("Streaming startup failed")
            self.state.update_component("ffmpeg", "fault", message=str(exc))
            self.stop()
            return False

    def write(self, data: bytes) -> None:
        process = self.ffmpeg_process
        if process is None or process.poll() is not None:
            return
        try:
            self._queue.put_nowait(data)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(data)
            except (queue.Empty, queue.Full):
                pass

    def _write_loop(self) -> None:
        while True:
            data = self._queue.get()
            if data is None:
                break
            with self._lock:
                process = self.ffmpeg_process
            if process is None or process.stdin is None or process.poll() is not None:
                self.state.update_component("ffmpeg", "fault", message="Streaming publisher stopped")
                break
            try:
                process.stdin.write(data)
            except (BrokenPipeError, OSError, ValueError) as exc:
                self.state.update_component("ffmpeg", "fault", message=str(exc))
                break

    def _pump_errors(self, process: subprocess.Popen, prefix: str) -> None:
        def worker() -> None:
            if process.stderr is None:
                return
            for raw in iter(process.stderr.readline, b""):
                if raw:
                    self.logger.warning("%s: %s", prefix, raw.decode(errors="replace").strip())
        threading.Thread(target=worker, name=f"{prefix}-stderr", daemon=True).start()

    def stop(self) -> None:
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        with self._lock:
            ffmpeg, mediamtx = self.ffmpeg_process, self.mediamtx_process
            self.ffmpeg_process = None
            self.mediamtx_process = None
        terminate_process(ffmpeg)
        terminate_process(mediamtx)
        self.state.update_component("ffmpeg", "stopped", message="Publisher stopped")
        self.state.update_component("mediamtx", "stopped", message="Media server stopped")

    def health(self) -> dict[str, bool]:
        config = self.settings.section("streaming")

        def listening(port: int) -> bool:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    return True
            except OSError:
                return False

        return {
            "ffmpeg": bool(self.ffmpeg_process and self.ffmpeg_process.poll() is None),
            "mediamtx": bool(
                self.mediamtx_process
                and self.mediamtx_process.poll() is None
                and listening(int(config["webrtc_port"]))
                and listening(int(config["webrtc_media_port"]))
                and listening(int(config["rtsp_port"]))
                and listening(int(config["hls_port"]))
            ),
        }
