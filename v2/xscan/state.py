from __future__ import annotations

import shutil
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import psutil

from .events import EventBus
from .parsers import DsdEvent, FmpMetadata, serialise
from .paths import AppPaths


@dataclass(slots=True)
class Component:
    name: str
    state: str = "stopped"
    pid: int | None = None
    message: str = ""
    heartbeat: float = 0.0
    restarts: int = 0

    def payload(self) -> dict[str, Any]:
        data = asdict(self)
        age = max(0.0, time.monotonic() - self.heartbeat) if self.heartbeat else None
        pid_alive = psutil.pid_exists(self.pid) if self.pid else None
        heartbeat_required = self.name in {"dsdplus", "fmp24", "audio", "ffmpeg", "mediamtx"}
        fresh = age is not None and age <= 5.0 if heartbeat_required else True
        data["pid_alive"] = pid_alive
        data["healthy"] = self.state in {"running", "ready", "live"} and pid_alive is not False and fresh
        data["heartbeat_age_seconds"] = round(age, 2) if age is not None else None
        return data


class RuntimeState:
    COMPONENTS = ("host", "web", "dsdplus", "fmp24", "audio", "recorder", "ffmpeg", "mediamtx", "storage")

    def __init__(self, paths: AppPaths, events: EventBus):
        self.paths = paths
        self.events = events
        self._lock = threading.RLock()
        self.components = {name: Component(name) for name in self.COMPONENTS}
        self.current_fmp: FmpMetadata | None = None
        self.dsd_events: deque[DsdEvent] = deque(maxlen=500)
        self.audio_level = 0.0
        self.audio_levels: deque[float] = deque(maxlen=1000)
        self._last_audio_event = 0.0
        self.recording_call_id: str | None = None
        self.desired_running = False
        self.last_error = ""
        self.update_component("host", "running", message="XScan host is running")
        self.refresh_storage()

    def update_component(self, name: str, state: str, *, pid: int | None = None, message: str = "", restart: bool = False) -> None:
        with self._lock:
            component = self.components.setdefault(name, Component(name))
            changed = (component.state, component.pid, component.message) != (state, pid, message)
            component.state = state
            component.pid = pid
            component.message = message
            component.heartbeat = time.monotonic()
            if restart:
                component.restarts += 1
            payload = component.payload()
        if changed:
            self.events.publish("component", payload)

    def heartbeat(self, name: str, *, message: str | None = None) -> None:
        with self._lock:
            component = self.components.setdefault(name, Component(name))
            component.heartbeat = time.monotonic()
            if message is not None:
                component.message = message

    def set_fmp(self, metadata: FmpMetadata) -> None:
        with self._lock:
            self.current_fmp = metadata
        self.events.publish("now-playing", serialise(metadata))

    def add_dsd_event(self, event: DsdEvent) -> None:
        with self._lock:
            self.dsd_events.append(event)
        self.events.publish("decoder-event", serialise(event))

    def set_audio_level(self, level: float) -> None:
        publish = False
        with self._lock:
            self.audio_level = level
            self.audio_levels.append(level)
            now = time.monotonic()
            if now - self._last_audio_event >= 0.1:
                self._last_audio_event = now
                publish = True
        if publish:
            self.events.publish("audio-level", {"level": level})

    def recent_audio_levels(self, count: int = 100) -> list[float]:
        with self._lock:
            return list(self.audio_levels)[-count:]

    def set_recording(self, call_id: str | None) -> None:
        with self._lock:
            self.recording_call_id = call_id
        self.events.publish("recording", {"active": bool(call_id), "id": call_id})

    def metadata(self) -> dict[str, Any]:
        with self._lock:
            value = self.current_fmp
        if value is None:
            return {"frequency": "", "mode": "", "label": "Unknown Channel", "options": {}, "raw": "", "display": "Unknown Channel"}
        data = serialise(value)
        data["display"] = value.display
        return data

    def correlate_dsd(self, started_at: datetime, frequency: str, window_seconds: float = 30.0) -> DsdEvent | None:
        try:
            target_frequency = float(frequency)
        except (TypeError, ValueError):
            target_frequency = -1
        if started_at.tzinfo is not None:
            started_at = started_at.astimezone().replace(tzinfo=None)
        with self._lock:
            candidates = list(self.dsd_events)
        best: tuple[float, DsdEvent] | None = None
        for event in candidates:
            try:
                same_frequency = abs(float(event.frequency) - target_frequency) < 0.0002
            except ValueError:
                same_frequency = False
            delta = abs((event.occurred_at - started_at).total_seconds())
            if same_frequency and delta <= window_seconds and (best is None or delta < best[0]):
                best = (delta, event)
        return best[1] if best else None

    def refresh_storage(self) -> dict[str, Any]:
        usage = shutil.disk_usage(self.paths.recordings)
        free_gb = usage.free / (1024**3)
        message = f"{free_gb:.1f} GB free"
        self.update_component("storage", "ready", message=message)
        return {"total_bytes": usage.total, "used_bytes": usage.used, "free_bytes": usage.free, "free_gb": round(free_gb, 2)}

    def snapshot(self) -> dict[str, Any]:
        storage = self.refresh_storage()
        with self._lock:
            components = {name: component.payload() for name, component in self.components.items()}
            metadata = self.metadata()
            return {
                "desired_running": self.desired_running,
                "running": components["dsdplus"]["healthy"] and components["fmp24"]["healthy"] and components["audio"]["healthy"],
                "recording": bool(self.recording_call_id),
                "recording_call_id": self.recording_call_id,
                "audio_level": self.audio_level,
                "now_playing": metadata,
                "components": components,
                "storage": storage,
                "last_error": self.last_error,
            }
