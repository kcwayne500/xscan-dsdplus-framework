import json
import os
import re
import shutil
import subprocess
import threading
import time
import wave
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Callable, Optional

import numpy as np
import sounddevice as sd


AUDIO_CHANNELS = 1
AUDIO_DTYPE = "int16"
AUDIO_BLOCKSIZE = 2048
AUDIO_TRIGGER_LEVEL = 0.0021
SILENCE_HANG_SEC = 1.75
MIN_RECORD_SEC = 0.90
PRE_ROLL_SEC = 0.45
AUDIO_START_BLOCKS = 3
LIKELY_NOISE_ACTIVE_SEC = 0.50
POLL_INTERVAL_SEC = 0.02
LOG_POLL_SEC = 0.05

TUNING_REGEX = re.compile(r"Tuning to\s+([\d.]+)\s+(\w+)\s+BW=.*?DELAY=\d+\s+(.*)")
DSD_CALL_REGEX = re.compile(r"Freq=([\d.]+)\s+(.*)")
BRACKET_LABEL_REGEX = re.compile(r"\[([^\]]+)\]")


@dataclass(frozen=True)
class ScannerMetadata:
    raw: str = ""
    frequency: str = "unknown"
    mode: str = "unknown"
    label: str = "Unknown_Channel"
    display: str = "Unknown Channel"


@dataclass(frozen=True)
class RecordingLogEntry:
    started_at: str
    ended_at: str
    duration_seconds: float
    frequency: str
    mode: str
    label: str
    raw_log_line: str
    audio_device: str
    trigger_level: float
    silence_hang_sec: float
    stop_reason: str
    wav_file: str
    mp3_file: str
    audio_file: str
    active_audio_seconds: float = 0.0
    peak_dbfs: float = -120.0
    likely_noise: bool = False


@dataclass
class MonitorCallbacks:
    log: Callable[[str], None]
    metadata: Callable[[ScannerMetadata], None]
    status: Callable[[str], None]
    recording: Callable[[bool], None]
    meter: Callable[[int], None]


class SettingsStore:
    DEFAULTS = {
        "auto_start_on_open": False,
        "minimize_to_tray": True,
        "audio_device_name": "",
        "audio_device_index": None,
        "streaming_enabled": False,
    }

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.values = self.load()

    def load(self):
        settings = dict(self.DEFAULTS)
        if not os.path.exists(self.path):
            return settings
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                settings.update(loaded)
        except (OSError, json.JSONDecodeError):
            pass
        return settings

    def save(self):
        with self.lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            temp_path = self.path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(self.values, handle, indent=2)
            os.replace(temp_path, self.path)

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value
        self.save()

    def snapshot(self):
        return dict(self.values)


class AudioDeviceCatalog:
    def list_inputs(self):
        devices = []
        for index, device in enumerate(sd.query_devices()):
            if device.get("max_input_channels", 0) > 0:
                devices.append((index, device.get("name", "")))
        return devices

    def select(self, settings):
        devices = self.list_inputs()
        preferred_name = settings.get("audio_device_name", "")
        preferred_index = settings.get("audio_device_index")
        for index, name in devices:
            if preferred_name and name == preferred_name:
                return index, name
        for index, name in devices:
            if preferred_index is not None and index == preferred_index:
                return index, name
        return devices[0] if devices else (None, None)

    def samplerate(self, device_index):
        device_info = sd.query_devices(device_index)
        return int(device_info.get("default_samplerate") or 48000)


class MetadataReader:
    def __init__(self, log_file):
        self.log_file = log_file
        self._last_line = None

    def read_last_line(self):
        if not os.path.exists(self.log_file):
            return None
        try:
            with open(self.log_file, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                pos = handle.tell()
                chunk = b""
                while pos > 0:
                    step = min(4096, pos)
                    pos -= step
                    handle.seek(pos)
                    chunk = handle.read(step) + chunk
                    lines = [line.strip() for line in chunk.splitlines() if line.strip()]
                    if len(lines) >= 2 or pos == 0:
                        return lines[-1].decode("utf-8", errors="ignore") if lines else None
        except OSError:
            return None
        return None

    def poll(self):
        line = self.read_last_line()
        if line == self._last_line:
            return None
        self._last_line = line
        return parse_metadata(line)


def parse_metadata(line):
    if not line:
        return None
    match = TUNING_REGEX.search(line)
    if match:
        frequency, mode, label = match.groups()
        clean_label = label.strip() or "Unknown_Channel"
        return ScannerMetadata(
            raw=line,
            frequency=frequency,
            mode=mode,
            label=clean_label,
            display=f"{frequency} {clean_label}",
        )

    match = DSD_CALL_REGEX.search(line)
    if match:
        frequency, details = match.groups()
        label_match = BRACKET_LABEL_REGEX.search(details)
        label = label_match.group(1).strip() if label_match else details.strip()
        label = re.sub(r"\s+\d+s$", "", label).strip(" ;") or "Unknown_Channel"
        mode = details.split("Group call", 1)[0].split("Private call", 1)[0].strip(" ;") or "DSD"
        frequency = frequency.rstrip("0").rstrip(".")
        return ScannerMetadata(
            raw=line,
            frequency=frequency,
            mode=mode,
            label=label,
            display=f"{frequency} {label}",
        )

    return None


def metadata_is_known(metadata):
    return bool(metadata and metadata.frequency != "unknown" and metadata.label != "Unknown_Channel")


def sanitize_label(label):
    return re.sub(r"[^a-zA-Z0-9]", "_", label).strip("_") or "Unknown_Channel"


class RecordingCatalog:
    def __init__(self, output_dir, ffmpeg_dir, log_file):
        self.output_dir = output_dir
        self.ffmpeg_dir = ffmpeg_dir
        self.log_file = log_file
        self.lock = threading.Lock()

    def build_paths(self, metadata):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"{timestamp}_{metadata.frequency}_{sanitize_label(metadata.label)}"
        return (
            os.path.join(self.output_dir, base_name + ".wav"),
            os.path.join(self.output_dir, base_name + ".mp3"),
        )

    def ffmpeg_exe(self):
        ffmpeg_exe = os.path.join(self.ffmpeg_dir, "ffmpeg.exe")
        if os.path.exists(ffmpeg_exe):
            return ffmpeg_exe
        return shutil.which("ffmpeg")

    def start_mp3_encoder(self, mp3_path, metadata, samplerate):
        ffmpeg_exe = self.ffmpeg_exe()
        if not ffmpeg_exe:
            return None, "ffmpeg not available"
        env = os.environ.copy()
        if os.path.isdir(self.ffmpeg_dir):
            env["PATH"] = self.ffmpeg_dir + os.pathsep + env.get("PATH", "")
        cmd = [
            ffmpeg_exe,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "s16le",
            "-ar",
            str(samplerate),
            "-ac",
            "1",
            "-i",
            "pipe:0",
            "-metadata",
            f"title={metadata.label}",
            "-metadata",
            f"artist={metadata.frequency}",
            "-metadata",
            f"album={metadata.mode}",
            "-metadata",
            f"comment={metadata.raw}",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "96k",
            mp3_path,
        ]
        try:
            process = subprocess.Popen(
                cmd,
                cwd=self.ffmpeg_dir if os.path.isdir(self.ffmpeg_dir) else os.getcwd(),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            return None, str(exc)
        return process, None

    def append(self, entry):
        os.makedirs(self.output_dir, exist_ok=True)
        with self.lock:
            data = []
            if os.path.exists(self.log_file):
                try:
                    with open(self.log_file, "r", encoding="utf-8") as handle:
                        loaded = json.load(handle)
                    if isinstance(loaded, list):
                        data = loaded
                except (OSError, json.JSONDecodeError):
                    data = []
            data.append(asdict(entry))
            temp_path = self.log_file + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
            os.replace(temp_path, self.log_file)

    def convert_to_mp3(self, wav_path, mp3_path, metadata):
        ffmpeg_exe = self.ffmpeg_exe()
        if not ffmpeg_exe:
            return False, "ffmpeg not available"
        env = os.environ.copy()
        if os.path.isdir(self.ffmpeg_dir):
            env["PATH"] = self.ffmpeg_dir + os.pathsep + env.get("PATH", "")
        cmd = [
            ffmpeg_exe,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            wav_path,
            "-metadata",
            f"title={metadata.label}",
            "-metadata",
            f"artist={metadata.frequency}",
            "-metadata",
            f"album={metadata.mode}",
            "-metadata",
            f"comment={metadata.raw}",
            "-acodec",
            "libmp3lame",
            "-ab",
            "96k",
            mp3_path,
        ]
        try:
            proc = subprocess.run(
                cmd,
                cwd=self.ffmpeg_dir if os.path.isdir(self.ffmpeg_dir) else os.getcwd(),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)
        if proc.returncode != 0:
            message = proc.stderr.strip().splitlines()[-1] if proc.stderr else f"ffmpeg exit {proc.returncode}"
            return False, message
        return True, None


class RecordingSession:
    def __init__(self, catalog, metadata, samplerate, device_name):
        os.makedirs(catalog.output_dir, exist_ok=True)
        self.catalog = catalog
        self.metadata = metadata
        self.device_name = device_name
        self.wav_path, self.mp3_path = catalog.build_paths(metadata)
        self.encoder_process = None
        self.encoder_error = None
        self.wave_file = None
        self.started_at_iso = datetime.now().isoformat()
        self.started_at_monotonic = time.monotonic()
        self.last_audio_at = self.started_at_monotonic
        self.samplerate = samplerate
        self.active_audio_seconds = 0.0
        self.peak_level = 0.0
        self.encoder_process, self.encoder_error = catalog.start_mp3_encoder(self.mp3_path, metadata, samplerate)
        if self.encoder_process is None:
            self.wave_file = wave.open(self.wav_path, "wb")
            self.wave_file.setnchannels(AUDIO_CHANNELS)
            self.wave_file.setsampwidth(2)
            self.wave_file.setframerate(samplerate)

    @property
    def recording_format(self):
        return "mp3" if self.encoder_process is not None else "wav"

    @property
    def active_path(self):
        return self.mp3_path if self.encoder_process is not None else self.wav_path

    def write(self, data):
        if self.encoder_process is not None:
            if self.encoder_process.stdin is None or self.encoder_process.poll() is not None:
                self.encoder_error = "MP3 encoder stopped"
                return
            try:
                self.encoder_process.stdin.write(data)
            except (BrokenPipeError, OSError, ValueError) as exc:
                self.encoder_error = str(exc)
            return
        self.wave_file.writeframes(data)

    def mark_audio(self, moment):
        self.last_audio_at = moment

    def observe_level(self, level, block_count=1):
        self.peak_level = max(self.peak_level, float(level))
        if level >= AUDIO_TRIGGER_LEVEL:
            self.active_audio_seconds += (AUDIO_BLOCKSIZE * block_count) / self.samplerate

    def observe_trigger_run(self, block_count, peak_level):
        self.peak_level = max(self.peak_level, float(peak_level))
        self.active_audio_seconds += (AUDIO_BLOCKSIZE * block_count) / self.samplerate

    def should_stop_for_silence(self, moment):
        return moment - self.last_audio_at >= SILENCE_HANG_SEC

    def close(self):
        ended_at = time.monotonic()
        ended_at_iso = datetime.now().isoformat()
        if self.encoder_process is not None:
            try:
                if self.encoder_process.stdin is not None:
                    self.encoder_process.stdin.close()
            except Exception:
                pass
            try:
                self.encoder_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.encoder_process.kill()
                self.encoder_error = "MP3 encoder timed out"
            stderr = ""
            try:
                if self.encoder_process.stderr is not None:
                    stderr = self.encoder_process.stderr.read().decode("utf-8", errors="ignore").strip()
            except Exception:
                stderr = ""
            if self.encoder_process.returncode not in (0, None):
                self.encoder_error = stderr or f"MP3 encoder exit {self.encoder_process.returncode}"
        elif self.wave_file is not None:
            try:
                self.wave_file.close()
            except Exception:
                pass
        return ended_at, ended_at_iso, max(0.0, ended_at - self.started_at_monotonic)


class AudioMonitor:
    def __init__(
        self,
        device_catalog,
        metadata_reader,
        recording_catalog,
        streaming_manager,
        callbacks,
        settings_func,
    ):
        self.device_catalog = device_catalog
        self.metadata_reader = metadata_reader
        self.recording_catalog = recording_catalog
        self.streaming_manager = streaming_manager
        self.callbacks = callbacks
        self.settings_func = settings_func
        self.stop_event = threading.Event()
        self.thread = None
        self.current_metadata = ScannerMetadata()
        self.finalizer_threads = []
        self.lock = threading.Lock()

    def is_running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        if self.is_running():
            return False
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return True

    def stop(self):
        self.stop_event.set()
        self.streaming_manager.stop(log_message=False)

    def _finalize(self, session, ended_at, ended_at_iso, duration, reason):
        metadata = session.metadata
        if duration < MIN_RECORD_SEC:
            for path in (session.wav_path, session.mp3_path):
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            self.callbacks.log(f"Clip discarded (< {MIN_RECORD_SEC:.2f}s)")
            return
        is_mp3 = session.recording_format == "mp3" and not session.encoder_error and os.path.exists(session.mp3_path)
        audio_file = os.path.basename(session.mp3_path if is_mp3 else session.wav_path)
        active_audio_seconds = round(session.active_audio_seconds, 3)
        peak_dbfs = round(float(20.0 * np.log10(max(session.peak_level, 1e-6))), 1)
        likely_noise = active_audio_seconds < LIKELY_NOISE_ACTIVE_SEC
        entry = RecordingLogEntry(
            started_at=session.started_at_iso,
            ended_at=ended_at_iso,
            duration_seconds=round(duration, 3),
            frequency=metadata.frequency,
            mode=metadata.mode,
            label=metadata.label,
            raw_log_line=metadata.raw,
            audio_device=session.device_name,
            trigger_level=AUDIO_TRIGGER_LEVEL,
            silence_hang_sec=SILENCE_HANG_SEC,
            stop_reason=reason,
            wav_file="" if is_mp3 else os.path.basename(session.wav_path),
            mp3_file=os.path.basename(session.mp3_path),
            audio_file=audio_file,
            active_audio_seconds=active_audio_seconds,
            peak_dbfs=peak_dbfs,
            likely_noise=likely_noise,
        )
        if is_mp3:
            suffix = " (brief/noise-like trigger)" if likely_noise else ""
            self.callbacks.log(f"Saved MP3 -> {os.path.basename(session.mp3_path)}{suffix}")
        else:
            if session.encoder_error:
                self.callbacks.log(f"MP3 recording failed: {session.encoder_error}")
            if os.path.exists(session.wav_path):
                self.callbacks.log(f"Saved WAV -> {os.path.basename(session.wav_path)}")
            else:
                self.callbacks.log("Recording was not saved because the encoder stopped before a file was written")
                return
        self.recording_catalog.append(entry)

    def _finish_session(self, session, reason):
        ended_at, ended_at_iso, duration = session.close()
        self.callbacks.recording(False)
        self.callbacks.status("RUNNING")
        worker = threading.Thread(
            target=self._finalize,
            args=(session, ended_at, ended_at_iso, duration, reason),
            daemon=True,
        )
        self.finalizer_threads.append(worker)
        worker.start()

    def _open_stream(self, device_index, samplerate):
        return sd.RawInputStream(
            samplerate=samplerate,
            blocksize=AUDIO_BLOCKSIZE,
            device=device_index,
            channels=AUDIO_CHANNELS,
            dtype=AUDIO_DTYPE,
        )

    def _run(self):
        settings = self.settings_func()
        device_index, device_name = self.device_catalog.select(settings)
        if device_index is None:
            self.callbacks.log("No audio input device is available")
            self.callbacks.status("ERROR")
            return

        try:
            samplerate = self.device_catalog.samplerate(device_index)
            stream = self._open_stream(device_index, samplerate)
            stream.start()
        except Exception as exc:
            self.callbacks.log(f"Audio monitor failed: {exc}")
            self.callbacks.status("ERROR")
            return

        self.callbacks.status("RUNNING")
        self.callbacks.recording(False)
        self.callbacks.log(f"Monitoring audio on {device_name}")
        if settings.get("streaming_enabled"):
            self.streaming_manager.start(samplerate)

        active_session = None
        last_log_poll = 0.0
        high_audio_blocks = 0
        high_audio_peak = 0.0
        last_unknown_audio_log = 0.0
        pre_roll_blocks = max(1, int((samplerate * PRE_ROLL_SEC) / AUDIO_BLOCKSIZE))
        pre_roll = deque(maxlen=pre_roll_blocks)
        try:
            while not self.stop_event.is_set():
                now = time.monotonic()
                if now - last_log_poll >= LOG_POLL_SEC:
                    metadata = self.metadata_reader.poll()
                    if metadata is not None:
                        with self.lock:
                            self.current_metadata = metadata
                        self.callbacks.metadata(metadata)
                    last_log_poll = now

                try:
                    data, overflowed = stream.read(AUDIO_BLOCKSIZE)
                except Exception as exc:
                    self.callbacks.log(f"Audio read error: {exc}")
                    self.callbacks.status("ERROR")
                    break

                samples = np.frombuffer(data, dtype=np.int16)
                level = 0.0 if samples.size == 0 else float(np.sqrt(np.mean(np.square(samples.astype(np.float32))))) / 32768.0
                self.callbacks.meter(int(min(level * 2400, 100)))
                if overflowed:
                    self.callbacks.log("Audio overflow detected")

                if self.settings_func().get("streaming_enabled"):
                    self.streaming_manager.write(data)

                if active_session is not None:
                    active_session.write(data)
                    active_session.observe_level(level)
                    if level >= AUDIO_TRIGGER_LEVEL:
                        active_session.mark_audio(now)
                    elif active_session.should_stop_for_silence(now):
                        self._finish_session(active_session, "Silence detected")
                        active_session = None
                        high_audio_blocks = 0
                        high_audio_peak = 0.0
                        pre_roll.clear()
                else:
                    pre_roll.append(bytes(data))
                    if level >= AUDIO_TRIGGER_LEVEL:
                        high_audio_blocks += 1
                        high_audio_peak = max(high_audio_peak, level)
                    else:
                        high_audio_blocks = 0
                        high_audio_peak = 0.0

                    if high_audio_blocks < AUDIO_START_BLOCKS:
                        continue

                    with self.lock:
                        metadata = self.current_metadata
                    if not metadata_is_known(metadata):
                        if now - last_unknown_audio_log >= 5.0:
                            self.callbacks.log("Audio ignored until channel metadata is available")
                            last_unknown_audio_log = now
                        high_audio_blocks = 0
                        continue

                    try:
                        active_session = RecordingSession(self.recording_catalog, metadata, samplerate, device_name)
                        for chunk in pre_roll:
                            active_session.write(chunk)
                        pre_roll.clear()
                        active_session.observe_trigger_run(high_audio_blocks, high_audio_peak)
                        active_session.mark_audio(now)
                        self.callbacks.recording(True)
                        self.callbacks.status("RECORDING")
                        self.callbacks.log(f"Audio detected -> {metadata.display}")
                        self.callbacks.log(
                            f"Recording {active_session.recording_format.upper()} -> {os.path.basename(active_session.active_path)}"
                        )
                    except Exception as exc:
                        self.callbacks.log(f"Recording start failed: {exc}")
                        active_session = None
                        pre_roll.clear()
                        time.sleep(0.2)
                    finally:
                        high_audio_blocks = 0
                        high_audio_peak = 0.0

                time.sleep(POLL_INTERVAL_SEC)
        finally:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
            self.streaming_manager.stop(log_message=False)
            if active_session is not None:
                self._finish_session(active_session, "Monitoring stopped")
            self.callbacks.meter(0)
            self.callbacks.recording(False)
            if self.stop_event.is_set():
                self.callbacks.status("STOPPED")
