from __future__ import annotations

import ctypes
import logging
import os
import queue
import re
import subprocess
import threading
import time
import wave
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np
import sounddevice as sd

from .database import Database
from .events import EventBus
from .paths import AppPaths
from .settings import SettingsStore
from .state import RuntimeState
from .streaming import StreamingManager, _find_tool
from .windows import WINDOW_CREATION_FLAGS


def _initialise_windows_com() -> bool:
    """Initialize COM on FastAPI/worker threads before PortAudio opens WASAPI.

    Windows main threads are normally initialized by the GUI runtime, but
    AnyIO worker threads are not. PortAudio can enumerate a WASAPI endpoint in
    that state yet fail at Pa_StartStream with a misleading WDM-KS -9999 error.
    """
    if os.name != "nt":
        return False
    result = int(ctypes.windll.ole32.CoInitializeEx(None, 0))  # COINIT_MULTITHREADED
    return result in (0, 1)  # S_OK or S_FALSE; both require CoUninitialize.


def _uninitialise_windows_com(initialised: bool) -> None:
    if initialised:
        ctypes.windll.ole32.CoUninitialize()


@dataclass(slots=True)
class TriggerEvent:
    type: str
    data: bytes = b""
    reason: str = ""


class TriggerSegmenter:
    def __init__(self, sample_rate: int, blocksize: int, bytes_per_frame: int, threshold: float, silence_hang: float, pre_roll: float):
        self.sample_rate = sample_rate
        self.blocksize = blocksize
        self.bytes_per_frame = bytes_per_frame
        blocks = max(1, int(np.ceil(pre_roll * sample_rate / blocksize)))
        self.pre_roll: deque[bytes] = deque(maxlen=blocks)
        self.threshold = threshold
        self.silence_hang = silence_hang
        self.active = False
        self.started_at = 0.0
        self.last_loud = 0.0

    def feed(self, data: bytes, level: float, now: float) -> list[TriggerEvent]:
        if not self.active:
            if level >= self.threshold:
                payload = b"".join(self.pre_roll) + data
                self.pre_roll.clear()
                self.active = True
                self.started_at = now
                self.last_loud = now
                return [TriggerEvent("start", payload)]
            self.pre_roll.append(data)
            return []
        events = [TriggerEvent("data", data)]
        if level >= self.threshold:
            self.last_loud = now
        elif now - self.last_loud >= self.silence_hang:
            self.active = False
            events.append(TriggerEvent("stop", reason="Silence detected"))
        return events

    def stop(self) -> list[TriggerEvent]:
        if not self.active:
            return []
        self.active = False
        return [TriggerEvent("stop", reason="Monitoring stopped")]


class AudioEngine:
    def __init__(
        self,
        paths: AppPaths,
        settings: SettingsStore,
        database: Database,
        state: RuntimeState,
        events: EventBus,
        streaming: StreamingManager,
        logger: logging.Logger,
    ):
        self.paths = paths
        self.settings = settings
        self.database = database
        self.state = state
        self.events = events
        self.streaming = streaming
        self.logger = logger
        self.stream: sd.RawInputStream | None = None
        self._queue: queue.Queue[bytes | None] = queue.Queue(maxsize=256)
        self._conversion_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._processor: threading.Thread | None = None
        self._converter = threading.Thread(target=self._conversion_loop, name="recording-converter", daemon=True)
        self._converter.start()
        self._running = threading.Event()
        self._session: dict[str, Any] | None = None
        self._segmenter: TriggerSegmenter | None = None
        self._last_meter_event = 0.0
        self._channel_overrides: dict[str, dict[str, float]] = {}
        self._default_threshold = 0.0021
        self._default_silence_hang = 1.0
        self.sample_rate = 0
        tools = settings.section("tools")
        self.ffmpeg = _find_tool(paths, "ffmpeg", "ffmpeg.exe", str(tools.get("ffmpeg") or ""))

    def is_healthy(self) -> bool:
        if not self._running.is_set() or self.stream is None:
            return False
        try:
            return bool(self.stream.active)
        except Exception:
            return False

    def devices(self) -> list[dict[str, Any]]:
        result = []
        host_apis = sd.query_hostapis()
        for index, device in enumerate(sd.query_devices()):
            if int(device["max_input_channels"]) > 0:
                host_api_index = int(device["hostapi"])
                host_api = host_apis[host_api_index]
                result.append(
                    {
                        "index": index,
                        "name": str(device["name"]),
                        "host_api": str(host_api["name"]),
                        "is_default_input": int(host_api.get("default_input_device", -1)) == index,
                        "input_channels": int(device["max_input_channels"]),
                        "default_sample_rate": int(device["default_samplerate"]),
                    }
                )
        return result

    def resolve_device(self) -> dict[str, Any] | None:
        config = self.settings.section("audio")
        preferred = config["device_name"].casefold()
        preferred_host_api = str(config.get("device_host_api") or "Windows WASAPI").casefold()
        devices = self.devices()
        matches = [device for device in devices if device["name"].casefold() == preferred]
        exact = (
            next((device for device in matches if device["host_api"].casefold() == preferred_host_api), None)
            or next((device for device in matches if device["host_api"].casefold() == "windows wasapi"), None)
            or next((device for device in matches if device["is_default_input"]), None)
            or (matches[0] if matches else None)
        )
        if exact:
            return exact
        # Driver revisions can change a VB-Cable descriptive suffix (for
        # example "Virtual Cable" to "Point") and its PortAudio host API.
        # Match the endpoint's stable words instead of a numeric device index.
        def words(value: str) -> set[str]:
            return set(re.findall(r"[a-z0-9]+", value.casefold()))

        wanted = words(preferred)
        ranked = sorted(
            (
                (
                    len(wanted & words(device["name"])) / max(1, len(wanted | words(device["name"]))),
                    device["host_api"].casefold() == preferred_host_api,
                    device["host_api"].casefold() == "windows wasapi",
                    device,
                )
                for device in devices
            ),
            key=lambda item: item[:3], reverse=True,
        )
        if ranked and ranked[0][0] >= 0.55:
            selected = ranked[0][3]
            self.settings.update({"audio": {"device_name": selected["name"], "device_host_api": selected["host_api"]}})
            self.logger.warning("Audio endpoint description changed; matched %s to %s", config["device_name"], selected["name"])
            return selected
        return None

    def start(self) -> bool:
        if self._running.is_set():
            return True
        com_initialised = _initialise_windows_com()
        try:
            device = self.resolve_device()
            if not device:
                self.state.update_component("audio", "fault", message="Configured audio device is unavailable")
                return False
            config = self.settings.section("audio")
            self._queue = queue.Queue(maxsize=256)
            self._channel_overrides = dict(config.get("per_channel") or {})
            self._default_threshold = float(config["trigger_level"])
            self._default_silence_hang = float(config["silence_hang_seconds"])
            sample_rate = int(device["default_sample_rate"] or 48000)
            self.sample_rate = sample_rate
            self._segmenter = TriggerSegmenter(
                sample_rate, int(config["blocksize"]), 2, float(config["trigger_level"]),
                float(config["silence_hang_seconds"]), float(config["pre_roll_seconds"]),
            )
            self.stream = sd.RawInputStream(
                samplerate=sample_rate,
                blocksize=int(config["blocksize"]),
                device=int(device["index"]),
                channels=1,
                dtype="int16",
                callback=self._callback,
            )
            self._running.set()
            self.stream.start()
            self._processor = threading.Thread(target=self._process_loop, args=(sample_rate, device["name"]), name="audio-processor", daemon=True)
            self._processor.start()
            self.streaming.start(sample_rate)
            self.state.update_component("audio", "running", message=f"{device['name']} @ {sample_rate} Hz")
            self.state.update_component("recorder", "ready", message="Waiting for audio")
            return True
        except Exception as exc:
            self._running.clear()
            self.state.update_component("audio", "fault", message=str(exc))
            self.logger.exception("Audio capture failed to start")
            return False
        finally:
            _uninitialise_windows_com(com_initialised)

    def stop(self) -> None:
        self._running.clear()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        self.streaming.stop()
        self.state.update_component("audio", "stopped", message="Audio capture stopped")
        self.state.update_component("recorder", "stopped", message="Recorder stopped")

    def close(self) -> None:
        self.stop()
        self._conversion_queue.put(None)

    def _callback(self, indata, frames, time_info, status) -> None:
        data = bytes(indata)
        if status:
            self.logger.warning("Audio callback status: %s", status)
        try:
            self._queue.put_nowait(data)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(data)
            except (queue.Empty, queue.Full):
                pass
        self.streaming.write(data)
        self.state.heartbeat("audio")

    def _process_loop(self, sample_rate: int, device_name: str) -> None:
        assert self._segmenter is not None
        while self._running.is_set():
            try:
                data = self._queue.get(timeout=1)
            except queue.Empty:
                self.state.update_component("audio", "fault", message="No audio callback heartbeat")
                self._running.clear()
                break
            if data is None:
                break
            samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
            level = float(np.sqrt(np.mean(samples * samples)) / 32768.0) if samples.size else 0.0
            self.state.set_audio_level(level)
            now = time.monotonic()
            frequency = self.state.metadata().get("frequency", "")
            override = self._channel_overrides.get(frequency, {})
            self._segmenter.threshold = float(override.get("trigger_level", self._default_threshold))
            self._segmenter.silence_hang = float(override.get("silence_hang_seconds", self._default_silence_hang))
            if now - self._last_meter_event >= 0.2:
                self.events.publish("audio-level", {"level": level})
                self._last_meter_event = now
            for event in self._segmenter.feed(data, level, now):
                self._handle_trigger(event, sample_rate, device_name, now)
        for event in self._segmenter.stop():
            self._handle_trigger(event, sample_rate, device_name, time.monotonic())

    def _handle_trigger(self, event: TriggerEvent, sample_rate: int, device_name: str, now: float) -> None:
        if event.type == "start":
            metadata = self.state.metadata()
            started = datetime.now().astimezone()
            safe = (re.sub(r"[^A-Za-z0-9]+", "_", metadata["label"]).strip("_") or "Unknown_Channel")[:80]
            base = f"{started.strftime('%Y%m%d_%H%M%S_%f')[:-3]}_{metadata['frequency'] or 'unknown'}_{safe}"
            wav_part = self.paths.recordings / f"{base}.part.wav"
            wave_file = wave.open(str(wav_part), "wb")
            wave_file.setnchannels(1)
            wave_file.setsampwidth(2)
            wave_file.setframerate(sample_rate)
            wave_file.writeframes(event.data)
            call_id = os.urandom(16).hex()
            self._session = {
                "id": call_id,
                "wave": wave_file,
                "wav_part": wav_part,
                "base": base,
                "metadata": metadata,
                "device": device_name,
                "sample_rate": sample_rate,
                "started_at": started,
                "started_monotonic": now,
            }
            self.state.set_recording(call_id)
            self.state.update_component("recorder", "running", message=metadata["display"])
        elif event.type == "data" and self._session:
            self._session["wave"].writeframes(event.data)
        elif event.type == "stop" and self._session:
            session, self._session = self._session, None
            session["ended_at"] = datetime.now().astimezone()
            session["duration"] = max(0.0, now - session["started_monotonic"])
            session["reason"] = event.reason
            session["wave"].close()
            self.state.set_recording(None)
            minimum = float(self.settings.section("audio")["minimum_seconds"])
            if session["duration"] < minimum:
                session["wav_part"].unlink(missing_ok=True)
                self.state.update_component("recorder", "ready", message=f"Discarded clip under {minimum:.2f}s")
            else:
                self._conversion_queue.put(session)
                self.state.update_component("recorder", "ready", message="Queued recording conversion")

    def _conversion_loop(self) -> None:
        while True:
            session = self._conversion_queue.get()
            if session is None:
                return
            try:
                self._finalize(session)
            except Exception as exc:
                self.logger.exception("Recording finalization failed")
                self.state.update_component("recorder", "fault", message=str(exc))

    def _finalize(self, session: dict[str, Any]) -> None:
        wav_part: Path = session["wav_part"]
        mp3_part = self.paths.recordings / f"{session['base']}.part.mp3"
        mp3_final = self.paths.recordings / f"{session['base']}.mp3"
        wav_final = self.paths.recordings / f"{session['base']}.wav"
        metadata = session["metadata"]
        audio_path = wav_final
        codec = "wav"
        if self.ffmpeg:
            command = [
                str(self.ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav_part),
                "-metadata", f"title={metadata['label']}", "-metadata", f"artist={metadata['frequency']}",
                "-metadata", f"album={metadata['mode']}", "-acodec", "libmp3lame", "-b:a", "96k", str(mp3_part),
            ]
            result = subprocess.run(command, cwd=self.ffmpeg.parent, capture_output=True, creationflags=WINDOW_CREATION_FLAGS, timeout=120)
            if result.returncode == 0 and mp3_part.is_file() and mp3_part.stat().st_size > 0:
                os.replace(mp3_part, mp3_final)
                wav_part.unlink(missing_ok=True)
                audio_path, codec = mp3_final, "mp3"
            else:
                self.logger.error("MP3 conversion failed: %s", result.stderr.decode(errors="replace")[-500:])
                os.replace(wav_part, wav_final)
        else:
            os.replace(wav_part, wav_final)
        dsd = self.state.correlate_dsd(session["started_at"], metadata["frequency"])
        call = {
            "id": session["id"],
            "started_at": session["started_at"].isoformat(),
            "ended_at": session["ended_at"].isoformat(),
            "duration_seconds": round(session["duration"], 3),
            "frequency": metadata["frequency"],
            "mode": metadata["mode"],
            "label": metadata["label"],
            "protocol": metadata["mode"],
            "ran_nac": dsd.ran_nac if dsd else next((f"{key}={metadata['options'][key]}" for key in ("RAN", "NAC") if key in metadata["options"]), ""),
            "radio_id": dsd.radio_id if dsd else "",
            "radio_alias": dsd.radio_alias if dsd else "",
            "call_type": dsd.call_type if dsd else "",
            "decoder_duration": dsd.decoder_duration if dsd else None,
            "audio_device": session["device"],
            "trigger_level": self.settings.section("audio")["trigger_level"],
            "stop_reason": session["reason"],
            "audio_file": audio_path.name,
            "audio_codec": codec,
            "audio_bytes": audio_path.stat().st_size,
            "raw_fmp_line": metadata["raw"],
            "raw_dsd_line": dsd.raw if dsd else "",
        }
        self.database.add_call(call)
        self.events.publish("call-completed", call)
        self.state.update_component("recorder", "ready", message=f"Saved {audio_path.name}")

    def recover_partials(self) -> list[str]:
        recovered: list[str] = []
        for part in self.paths.recordings.glob("*.part.wav"):
            target = part.with_name(part.name.replace(".part.wav", ".recovered.wav"))
            os.replace(part, target)
            recovered.append(target.name)
        for part in self.paths.recordings.glob("*.part.mp3"):
            target = part.with_name(part.name.replace(".part.mp3", ".recovered.mp3"))
            os.replace(part, target)
            recovered.append(target.name)
        return recovered
