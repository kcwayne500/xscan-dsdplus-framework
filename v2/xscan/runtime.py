from __future__ import annotations

import logging
import threading
import time
from collections import deque

from .audio import AudioEngine
from .events import EventBus
from .settings import SettingsStore
from .state import RuntimeState
from .streaming import StreamingManager
from .supervisor import HardwareControlDisabled, ProcessSupervisor


class HostRuntime:
    def __init__(
        self,
        settings: SettingsStore,
        state: RuntimeState,
        events: EventBus,
        supervisor: ProcessSupervisor,
        audio: AudioEngine,
        streaming: StreamingManager,
        logger: logging.Logger,
    ):
        self.settings = settings
        self.state = state
        self.events = events
        self.supervisor = supervisor
        self.audio = audio
        self.streaming = streaming
        self.logger = logger
        self._lock = threading.RLock()
        self._closing = threading.Event()
        self._restart_times: dict[str, deque[float]] = {"audio": deque(), "streaming": deque()}
        self._retry_after = {"audio": 0.0, "streaming": 0.0}
        self._fault_latched = {"audio": False, "streaming": False}
        self._monitor_thread = threading.Thread(target=self._monitor, name="component-recovery", daemon=True)
        self._monitor_thread.start()

    def _reset_recovery(self) -> None:
        for name in self._restart_times:
            self._restart_times[name].clear()
            self._retry_after[name] = 0.0
            self._fault_latched[name] = False

    def start(self, persist: bool = True) -> dict:
        with self._lock:
            self._reset_recovery()
            runtime = self.settings.section("runtime")
            if not runtime["hardware_control_enabled"]:
                raise HardwareControlDisabled("Side-by-side safety lock is enabled; use the cutover installer to enable hardware control")
            if not self.audio.start():
                raise RuntimeError("Audio capture could not start; check the configured device")
            try:
                self.supervisor.start(persist=persist)
            except Exception:
                self.audio.stop()
                raise
            self.state.desired_running = True
            self.events.publish("system", {"state": "starting"})
            return self.state.snapshot()

    def stop(self, persist: bool = True) -> dict:
        with self._lock:
            self._reset_recovery()
            self.supervisor.stop(persist=persist)
            self.audio.stop()
            self.state.desired_running = False
            self.events.publish("system", {"state": "stopped"})
            return self.state.snapshot()

    def restart(self) -> dict:
        with self._lock:
            self._reset_recovery()
            runtime = self.settings.section("runtime")
            if not runtime["hardware_control_enabled"]:
                raise HardwareControlDisabled("Side-by-side safety lock is enabled; use the cutover installer to enable hardware control")
            self.audio.stop()
            self.supervisor.restart()
            if not self.audio.start():
                self.supervisor.stop(persist=False)
                raise RuntimeError("Receiver restarted, but audio capture failed")
            self.events.publish("system", {"state": "restarted"})
            return self.state.snapshot()

    def auto_start(self) -> None:
        runtime = self.settings.section("runtime")
        self.state.desired_running = bool(runtime["desired_running"])
        if runtime["hardware_control_enabled"] and runtime["desired_running"]:
            try:
                self.start(persist=False)
            except Exception:
                self.logger.exception("Automatic system start failed")

    def close(self) -> None:
        self._closing.set()
        with self._lock:
            self.audio.close()
            self.supervisor.close()
        self._monitor_thread.join(timeout=3)

    def _schedule_recovery(self, name: str, message: str) -> bool:
        if self._fault_latched[name]:
            return False
        runtime = self.settings.section("runtime")
        now = time.monotonic()
        attempts = self._restart_times[name]
        window = float(runtime["restart_window_seconds"])
        while attempts and now - attempts[0] > window:
            attempts.popleft()
        if len(attempts) >= int(runtime["max_restart_attempts"]):
            self._fault_latched[name] = True
            if name == "audio":
                self.state.update_component("audio", "fault", message="Audio restart limit reached; use manual retry")
            else:
                self.state.update_component("ffmpeg", "fault", message="Streaming restart limit reached; use manual retry")
                self.state.update_component("mediamtx", "fault", message="Streaming restart limit reached; use manual retry")
            self.logger.error("%s restart limit reached: %s", name, message)
            return False
        if self._retry_after[name] == 0.0:
            delay = min(30.0, float(2 ** len(attempts)))
            attempts.append(now)
            self._retry_after[name] = now + delay
            self.logger.warning("%s fault; retry %d in %.0fs: %s", name, len(attempts), delay, message)
            return False
        if now < self._retry_after[name]:
            return False
        self._retry_after[name] = 0.0
        return True

    def _monitor(self) -> None:
        while not self._closing.wait(1.0):
            if not self.state.desired_running or not self.supervisor.desired:
                continue
            with self._lock:
                if not self.audio.is_healthy():
                    if self._schedule_recovery("audio", "capture callback stopped"):
                        self.audio.stop()
                        if self.audio.start():
                            self.state.update_component("audio", "running", message="Audio capture recovered", restart=True)
                        else:
                            self.state.update_component("audio", "fault", message="Audio recovery failed")
                    continue
                self.state.heartbeat("audio")
                if not self.settings.section("streaming")["enabled"]:
                    continue
                health = self.streaming.health()
                if all(health.values()):
                    recovered = bool(self._restart_times["streaming"] or self._fault_latched["streaming"] or self._retry_after["streaming"])
                    self._restart_times["streaming"].clear()
                    self._retry_after["streaming"] = 0.0
                    self._fault_latched["streaming"] = False
                    self.state.update_component(
                        "ffmpeg", "running", pid=self.streaming.ffmpeg_process.pid if self.streaming.ffmpeg_process else None,
                        message="Streaming publisher recovered" if recovered else "Publishing Opus audio",
                    )
                    self.state.update_component(
                        "mediamtx", "running", pid=self.streaming.mediamtx_process.pid if self.streaming.mediamtx_process else None,
                        message="Media server recovered" if recovered else "Live audio server ready",
                    )
                    continue
                failed = ", ".join(key for key, healthy in health.items() if not healthy)
                self.state.update_component("ffmpeg", "fault" if not health["ffmpeg"] else "running", pid=self.streaming.ffmpeg_process.pid if self.streaming.ffmpeg_process else None, message=f"Streaming fault: {failed}")
                self.state.update_component("mediamtx", "fault" if not health["mediamtx"] else "running", pid=self.streaming.mediamtx_process.pid if self.streaming.mediamtx_process else None, message=f"Streaming fault: {failed}")
                if self._schedule_recovery("streaming", failed) and self.audio.sample_rate:
                    if self.streaming.start(self.audio.sample_rate):
                        self.state.update_component("ffmpeg", "running", pid=self.streaming.ffmpeg_process.pid if self.streaming.ffmpeg_process else None, message="Streaming publisher recovered", restart=True)
                        self.state.update_component("mediamtx", "running", pid=self.streaming.mediamtx_process.pid if self.streaming.mediamtx_process else None, message="Media server recovered", restart=True)
