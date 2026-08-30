from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .paths import AppPaths


DEFAULT_SETTINGS: dict[str, Any] = {
    "schema_version": 2,
    "server": {
        # The public reverse proxy is the only network-facing listener. Keep
        # the application backend on loopback so port 8890 cannot bypass TLS.
        "host": "127.0.0.1",
        "port": 8891,
        "lan_http_enabled": False,
        "public_https_enabled": False,
        "public_url": "",
        "session_hours": 12,
    },
    "runtime": {
        "desired_running": True,
        "hardware_control_enabled": False,
        "hide_native_windows": True,
        "auto_restart": True,
        "max_restart_attempts": 5,
        "restart_window_seconds": 600,
        # Mode 2 passes source audio whenever digital sync is absent. This is
        # the DSDPlus "Monitor Source Audio if No Sync" menu setting and keeps
        # analog channels audible while decoded digital voice remains intact.
        "dsdplus_args": ["-r1", "-m2", "-i20001"],
        "fmp24_args": ["-i0", "-o20001", "-s1", "-g32.8", "-e1", "-P0.0", "-f162.5500"],
    },
    "audio": {
        "device_name": "CABLE Output (VB-Audio Virtual Cable)",
        "device_host_api": "Windows WASAPI",
        "trigger_level": 0.0021,
        "silence_hang_seconds": 1.5,
        "minimum_seconds": 0.5,
        "pre_roll_seconds": 0.5,
        "channels": 1,
        "dtype": "int16",
        "blocksize": 2048,
        "per_channel": {},
    },
    "streaming": {
        "enabled": True,
        "rtsp_port": 8554,
        "webrtc_port": 8889,
        "webrtc_media_port": 8189,
        "hls_port": 8888,
        "stream_name": "scanner",
        "audio_bitrate": "48k",
    },
    "tools": {
        "ffmpeg": "",
        "mediamtx": "",
    },
    "storage": {
        "warning_free_gb": 10,
        "keep_all": True,
    },
}


def _merge(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value
    return target


def _normalise_network_settings(data: dict[str, Any]) -> dict[str, Any]:
    """Remove the retired Tailscale route while loading older settings files."""
    server = data.get("server")
    if isinstance(server, dict):
        server.pop("tailscale_https_enabled", None)
        server.pop("tailscale_url", None)
    return data


class SettingsStore:
    def __init__(self, paths: AppPaths):
        self.paths = paths
        self._lock = threading.RLock()
        self._data = deepcopy(DEFAULT_SETTINGS)
        self.load()

    def load(self) -> dict[str, Any]:
        with self._lock:
            data = deepcopy(DEFAULT_SETTINGS)
            if self.paths.settings.exists():
                try:
                    loaded = json.loads(self.paths.settings.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        _merge(data, _normalise_network_settings(loaded))
                except (OSError, json.JSONDecodeError):
                    pass
            self._data = data
            return deepcopy(self._data)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._data)

    def section(self, name: str) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._data[name])

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            patch = deepcopy(patch)
            candidate = deepcopy(self._data)
            per_channel = None
            if isinstance(patch.get("audio"), dict) and "per_channel" in patch["audio"]:
                per_channel = patch["audio"].pop("per_channel")
            _merge(candidate, patch)
            if per_channel is not None:
                candidate["audio"]["per_channel"] = deepcopy(per_channel)
            self._validate(candidate)
            self._backup_current()
            self._data = candidate
            self._atomic_write(candidate)
            return deepcopy(self._data)

    def replace(self, data: dict[str, Any]) -> dict[str, Any]:
        merged = _merge(deepcopy(DEFAULT_SETTINGS), _normalise_network_settings(deepcopy(data)))
        self._validate(merged)
        with self._lock:
            self._backup_current()
            self._data = merged
            self._atomic_write(self._data)
            return deepcopy(self._data)

    def backups(self) -> list[dict[str, Any]]:
        folder = self.paths.backups / "settings"
        if not folder.is_dir():
            return []
        return [
            {"name": path.name, "bytes": path.stat().st_size, "modified_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat()}
            for path in sorted(folder.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        ]

    def restore(self, backup_name: str) -> dict[str, Any]:
        folder = (self.paths.backups / "settings").resolve()
        candidate = (folder / Path(backup_name).name).resolve()
        if candidate.parent != folder or not candidate.is_file():
            raise FileNotFoundError(backup_name)
        data = json.loads(candidate.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Settings backup is invalid")
        # A web restore must never cross the installer-controlled hardware
        # safety boundary or silently move the currently bound web port.
        data.setdefault("runtime", {})["hardware_control_enabled"] = self._data["runtime"]["hardware_control_enabled"]
        data.setdefault("server", {})["port"] = self._data["server"]["port"]
        return self.replace(data)

    def _backup_current(self) -> None:
        if not self.paths.settings.is_file():
            return
        folder = self.paths.backups / "settings"
        folder.mkdir(parents=True, exist_ok=True)
        name = datetime.now().strftime("%Y%m%d-%H%M%S-%f.json")
        (folder / name).write_bytes(self.paths.settings.read_bytes())

    def _atomic_write(self, data: dict[str, Any]) -> None:
        self.paths.state.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="settings-", suffix=".tmp", dir=self.paths.state)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(data, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.paths.settings)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    @staticmethod
    def _validate(data: dict[str, Any]) -> None:
        port = int(data["server"]["port"])
        if not 1 <= port <= 65535:
            raise ValueError("server.port must be between 1 and 65535")
        public_url = str(data["server"].get("public_url") or "")
        parsed_public_url = urlparse(public_url)
        if public_url and (parsed_public_url.scheme != "https" or not parsed_public_url.hostname):
            raise ValueError("server.public_url must be empty or an HTTPS URL")
        if bool(data["server"].get("public_https_enabled")) and not public_url:
            raise ValueError("server.public_url is required when public HTTPS is enabled")
        audio = data["audio"]
        if float(audio["trigger_level"]) <= 0:
            raise ValueError("audio.trigger_level must be positive")
        for key in ("silence_hang_seconds", "minimum_seconds", "pre_roll_seconds"):
            if float(audio[key]) < 0:
                raise ValueError(f"audio.{key} cannot be negative")
        if int(audio["channels"]) != 1:
            raise ValueError("XScan V2 currently supports one audio channel")
        streaming = data["streaming"]
        for key in ("rtsp_port", "webrtc_port", "webrtc_media_port", "hls_port"):
            if not 1 <= int(streaming[key]) <= 65535:
                raise ValueError(f"streaming.{key} must be between 1 and 65535")
        tools = data.get("tools") or {}
        for key in ("ffmpeg", "mediamtx"):
            if tools.get(key) is not None and not isinstance(tools.get(key), str):
                raise ValueError(f"tools.{key} must be a file path or empty")
        for frequency, override in (audio.get("per_channel") or {}).items():
            try:
                if float(override.get("trigger_level", audio["trigger_level"])) <= 0:
                    raise ValueError
            except (AttributeError, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid per-channel trigger settings for {frequency}") from exc


def find_legacy_settings(paths: AppPaths) -> Path | None:
    candidates = [
        paths.bundle.parent / "dist" / "DSDPlusScannerRecorder" / "_internal" / "scanner_gui_recorder_settings.json",
        paths.bundle.parent / "scanner_gui_recorder_settings.json",
    ]
    return next((path for path in candidates if path.is_file()), None)
