from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from .events import EventBus
from .parsers import parse_dsd_event, parse_fmp_line
from .paths import AppPaths
from .settings import SettingsStore
from .state import RuntimeState
from .windows import WINDOW_CREATION_FLAGS, external_program_dll_search, set_process_windows_visible, terminate_process


class HardwareControlDisabled(RuntimeError):
    pass


_FMP_SERIAL_ARGUMENT = re.compile(r'-i"[A-Za-z0-9]{1,8}"')
_DSD_MONITOR_ARGUMENT = re.compile(r"-m[0-4]", re.IGNORECASE)


def _dsd_command_args(arguments: list[str]) -> list[str]:
    """Force the mixed analog/digital source-monitor mode on every launch."""
    normalized = [str(value) for value in arguments if not _DSD_MONITOR_ARGUMENT.fullmatch(str(value))]
    insert_at = 1 if normalized and normalized[0].lower().startswith("-r") else 0
    normalized.insert(insert_at, "-m2")
    return normalized


def _fmp_command(executable: Path, arguments: list[str]) -> list[str] | str:
    """Preserve FMP24's quoted serial syntax on Windows.

    FMP24 distinguishes ``-i1`` (numeric device index) from ``-i"00000001"``
    (dongle serial) by inspecting the raw Windows command line.  Python's
    normal sequence-to-command-line conversion escapes embedded quotes, so the
    latter must be rendered deliberately.
    """
    values = [str(executable), *map(str, arguments)]
    if os.name != "nt" or not any(_FMP_SERIAL_ARGUMENT.fullmatch(value) for value in values[1:]):
        return values
    return " ".join(
        value if _FMP_SERIAL_ARGUMENT.fullmatch(value) else subprocess.list2cmdline([value])
        for value in values
    )


class ProcessSupervisor:
    def __init__(self, paths: AppPaths, settings: SettingsStore, state: RuntimeState, events: EventBus, logger: logging.Logger):
        self.paths = paths
        self.settings = settings
        self.state = state
        self.events = events
        self.logger = logger
        self.dsd_process: subprocess.Popen | None = None
        self.fmp_process: subprocess.Popen | None = None
        self._desired = False
        self._closing = threading.Event()
        self._lock = threading.RLock()
        self._restart_times: deque[float] = deque()
        self._monitor_thread = threading.Thread(target=self._monitor, name="process-supervisor", daemon=True)
        self._event_thread = threading.Thread(target=self._tail_dsd_events, name="dsd-event-tailer", daemon=True)
        self._monitor_thread.start()
        self._event_thread.start()

    @property
    def desired(self) -> bool:
        return self._desired

    def start(self, persist: bool = True) -> None:
        runtime = self.settings.section("runtime")
        if not runtime["hardware_control_enabled"]:
            raise HardwareControlDisabled("Side-by-side safety lock is enabled; hardware control is disabled")
        self._desired = True
        self.state.desired_running = True
        if persist:
            self.settings.update({"runtime": {"desired_running": True}})
        self._start_pair()

    def stop(self, persist: bool = True) -> None:
        self._desired = False
        self.state.desired_running = False
        if persist:
            self.settings.update({"runtime": {"desired_running": False}})
        self._stop_pair()

    def restart(self) -> None:
        if not self.settings.section("runtime")["hardware_control_enabled"]:
            raise HardwareControlDisabled("Side-by-side safety lock is enabled; hardware control is disabled")
        self._desired = True
        self.state.desired_running = True
        self._stop_pair()
        self._start_pair(restart=True)

    def close(self) -> None:
        self._closing.set()
        self._desired = False
        self._stop_pair()

    def _start_pair(self, restart: bool = False) -> None:
        with self._lock:
            if self._pair_alive():
                return
            self._stop_pair_locked()
            runtime = self.settings.section("runtime")
            dsd_exe = self.paths.dsdplus / "DSDPlus.exe"
            fmp_exe = self.paths.dsdplus / "FMP24.exe"
            if not dsd_exe.is_file() or not fmp_exe.is_file():
                missing = dsd_exe if not dsd_exe.is_file() else fmp_exe
                self.state.update_component("dsdplus" if missing == dsd_exe else "fmp24", "fault", message=f"Missing {missing}")
                raise FileNotFoundError(missing)
            dsd_args = _dsd_command_args(runtime["dsdplus_args"])
            self.logger.info("Starting DSDPlus: %s", dsd_args)
            with external_program_dll_search():
                self.dsd_process = subprocess.Popen(
                    [str(dsd_exe), *dsd_args], cwd=self.paths.dsdplus,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=WINDOW_CREATION_FLAGS,
                )
            self.state.update_component("dsdplus", "running", pid=self.dsd_process.pid, message="Decoder running", restart=restart)
            if runtime["hide_native_windows"]:
                threading.Timer(1.0, set_process_windows_visible, args=(self.dsd_process.pid, False)).start()
            time.sleep(1.0)
            self.logger.info("Starting FMP24: %s", runtime["fmp24_args"])
            with external_program_dll_search():
                self.fmp_process = subprocess.Popen(
                    _fmp_command(fmp_exe, runtime["fmp24_args"]), cwd=self.paths.dsdplus,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    creationflags=WINDOW_CREATION_FLAGS, bufsize=0,
                )
            self.state.update_component("fmp24", "running", pid=self.fmp_process.pid, message="Scanner running", restart=restart)
            threading.Thread(target=self._read_fmp_output, args=(self.fmp_process,), name="fmp-output", daemon=True).start()
            if runtime["hide_native_windows"]:
                threading.Timer(1.0, set_process_windows_visible, args=(self.fmp_process.pid, False)).start()
            self.events.publish("system", {"state": "running"})

    def _stop_pair(self) -> None:
        with self._lock:
            self._stop_pair_locked()

    def _stop_pair_locked(self) -> None:
        fmp, dsd = self.fmp_process, self.dsd_process
        self.fmp_process = None
        self.dsd_process = None
        terminate_process(fmp)
        terminate_process(dsd)
        self.state.update_component("fmp24", "stopped", message="Scanner stopped")
        self.state.update_component("dsdplus", "stopped", message="Decoder stopped")

    def _pair_alive(self) -> bool:
        return bool(
            self.dsd_process and self.fmp_process and self.dsd_process.poll() is None and self.fmp_process.poll() is None
        )

    def _monitor(self) -> None:
        while not self._closing.wait(1.0):
            if not self._desired:
                continue
            with self._lock:
                alive = self._pair_alive()
                dsd_code = self.dsd_process.poll() if self.dsd_process else None
                fmp_code = self.fmp_process.poll() if self.fmp_process else None
            if alive:
                self.state.heartbeat("dsdplus")
                self.state.heartbeat("fmp24")
                continue
            runtime = self.settings.section("runtime")
            self.state.last_error = f"Receiver pair stopped (DSDPlus={dsd_code}, FMP24={fmp_code})"
            self.logger.error(self.state.last_error)
            self._stop_pair()
            if not runtime["auto_restart"]:
                self._desired = False
                continue
            now = time.monotonic()
            window = float(runtime["restart_window_seconds"])
            while self._restart_times and now - self._restart_times[0] > window:
                self._restart_times.popleft()
            if len(self._restart_times) >= int(runtime["max_restart_attempts"]):
                self._desired = False
                self.state.desired_running = False
                self.state.update_component("dsdplus", "fault", message="Restart limit reached")
                self.state.update_component("fmp24", "fault", message="Restart limit reached")
                continue
            delay = min(30, 2 ** len(self._restart_times))
            self._restart_times.append(now)
            if self._closing.wait(delay) or not self._desired:
                continue
            try:
                self._start_pair(restart=True)
            except Exception:
                self.logger.exception("Receiver pair restart failed")

    def _read_fmp_output(self, process: subprocess.Popen) -> None:
        if process.stdout is None:
            return
        while True:
            raw = process.stdout.readline()
            if not raw:
                break
            self._handle_fmp_line(raw.decode("utf-8", errors="replace").rstrip("\r\n"))

    def _handle_fmp_line(self, line: str) -> None:
        if not line:
            return
        self._append_compat_log(line)
        metadata = parse_fmp_line(line)
        if metadata:
            self.state.set_fmp(metadata)
        self.state.heartbeat("fmp24", message=line[-180:])

    def _append_compat_log(self, line: str) -> None:
        path = self.paths.dsdplus / "startup" / "fmp24_scan.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if path.exists() and path.stat().st_size > 10 * 1024 * 1024:
                oldest = path.with_suffix(".log.5")
                if oldest.exists():
                    oldest.unlink()
                for number in range(4, 0, -1):
                    source = path.with_suffix(f".log.{number}") if number > 1 else path
                    target = path.with_suffix(f".log.{number + 1}")
                    if source.exists():
                        os.replace(source, target)
            with path.open("a", encoding="utf-8", errors="replace") as handle:
                handle.write(line + "\n")
        except OSError as exc:
            self.logger.warning("Compatibility log write failed: %s", exc)

    def _tail_dsd_events(self) -> None:
        path = self.paths.dsdplus / "1R-DSDPlus.event"
        position = 0
        identity: tuple[int, int] | None = None
        while not self._closing.wait(0.5):
            try:
                stat = path.stat()
                current_identity = (stat.st_dev, stat.st_ino)
                if identity != current_identity or stat.st_size < position:
                    position = max(0, stat.st_size - 65536)
                    identity = current_identity
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(position)
                    for line in handle:
                        event = parse_dsd_event(line)
                        if event:
                            self.state.add_dsd_event(event)
                    position = handle.tell()
            except FileNotFoundError:
                continue
            except OSError as exc:
                self.logger.warning("DSD event tail failed: %s", exc)

    def set_native_windows_visible(self, visible: bool) -> int:
        count = 0
        with self._lock:
            for process in (self.dsd_process, self.fmp_process):
                if process and process.poll() is None:
                    count += set_process_windows_visible(process.pid, visible)
        return count

    def process_snapshot(self) -> list[dict[str, int | str | None]]:
        with self._lock:
            return [
                {"name": "DSDPlus", "pid": self.dsd_process.pid if self.dsd_process else None, "exit_code": self.dsd_process.poll() if self.dsd_process else None},
                {"name": "FMP24", "pid": self.fmp_process.pid if self.fmp_process else None, "exit_code": self.fmp_process.poll() if self.fmp_process else None},
            ]
