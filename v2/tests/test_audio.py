from xscan.audio import TriggerSegmenter
from xscan.audio import AudioEngine
from xscan.database import Database
from xscan.settings import SettingsStore
import queue
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np


def test_trigger_segmenter_includes_preroll_and_stops_after_silence():
    segmenter = TriggerSegmenter(sample_rate=100, blocksize=10, bytes_per_frame=2, threshold=.1, silence_hang=.2, pre_roll=.2)
    quiet_a = np.zeros(10, dtype=np.int16).tobytes()
    quiet_b = np.full(10, 20, dtype=np.int16).tobytes()
    loud = np.full(10, 20_000, dtype=np.int16).tobytes()
    assert segmenter.feed(quiet_a, .01, 0.0) == []
    assert segmenter.feed(quiet_b, .01, 0.1) == []
    events = segmenter.feed(loud, .2, 0.2)
    assert events[0].type == "start"
    assert events[0].data == quiet_a + quiet_b + loud
    assert [event.type for event in segmenter.feed(b"d" * 20, .01, .41)] == ["data", "stop"]


def test_audio_callback_drops_oldest_chunk_instead_of_blocking():
    class Streamer:
        def __init__(self): self.data = []
        def write(self, data): self.data.append(data)
    class State:
        def heartbeat(self, name): self.name = name
    class Logger:
        def warning(self, *args): pass
    engine = object.__new__(AudioEngine)
    engine._queue = queue.Queue(maxsize=1)
    engine._queue.put_nowait(b"old")
    engine.streaming, engine.state, engine.logger = Streamer(), State(), Logger()
    engine._callback(b"new", 1, None, None)
    assert engine._queue.get_nowait() == b"new"
    assert engine.streaming.data == [b"new"]


def test_duplicate_device_name_prefers_stable_wasapi_endpoint(app_paths):
    engine = object.__new__(AudioEngine)
    engine.settings = SettingsStore(app_paths)
    engine.devices = lambda: [
        {"index": 6, "name": "CABLE Output (VB-Audio Virtual Cable)", "host_api": "Windows DirectSound", "is_default_input": False},
        {"index": 12, "name": "CABLE Output (VB-Audio Virtual Cable)", "host_api": "Windows WASAPI", "is_default_input": True},
    ]
    assert engine.resolve_device()["index"] == 12


def test_vb_cable_driver_rename_is_matched_by_stable_name_words(app_paths):
    engine = object.__new__(AudioEngine)
    engine.settings = SettingsStore(app_paths)
    engine.logger = SimpleNamespace(warning=lambda *args: None)
    engine.devices = lambda: [
        {"index": 0, "name": "CABLE Output (VB-Audio Point)", "host_api": "Windows WDM-KS", "is_default_input": True},
        {"index": 2, "name": "Input (VB-Audio Point)", "host_api": "Windows WDM-KS", "is_default_input": False},
    ]
    selected = engine.resolve_device()
    assert selected["index"] == 0
    assert engine.settings.section("audio")["device_name"] == "CABLE Output (VB-Audio Point)"


def test_vb_cable_driver_rename_prefers_wasapi_over_wdm_ks(app_paths):
    engine = object.__new__(AudioEngine)
    engine.settings = SettingsStore(app_paths)
    engine.logger = SimpleNamespace(warning=lambda *args: None)
    engine.devices = lambda: [
        {"index": 13, "name": "CABLE Output (VB-Audio Point)", "host_api": "Windows WDM-KS", "is_default_input": True},
        {"index": 12, "name": "CABLE Output (VB-Audio Point)", "host_api": "Windows WASAPI", "is_default_input": False},
    ]
    selected = engine.resolve_device()
    assert selected["index"] == 12
    assert engine.settings.section("audio")["device_host_api"] == "Windows WASAPI"


def test_partial_recordings_are_recovered_on_startup(app_paths):
    part = app_paths.recordings / "call.part.wav"
    part.write_bytes(b"RIFF")
    engine = object.__new__(AudioEngine)
    engine.paths = app_paths
    assert engine.recover_partials() == ["call.recovered.wav"]
    assert (app_paths.recordings / "call.recovered.wav").exists()


def test_conversion_failure_keeps_recoverable_wav_and_call_record(app_paths, monkeypatch):
    class State:
        def correlate_dsd(self, *args): return None
        def update_component(self, *args, **kwargs): pass
    class Events:
        def publish(self, *args): pass
    class Logger:
        def error(self, *args): pass

    fake_ffmpeg = app_paths.bundle / "ffmpeg.exe"
    fake_ffmpeg.write_bytes(b"fake")
    wav_part = app_paths.recordings / "failed.part.wav"
    wav_part.write_bytes(b"RIFF generated PCM")
    now = datetime.now().astimezone()
    engine = object.__new__(AudioEngine)
    engine.paths, engine.database = app_paths, Database(app_paths)
    engine.settings, engine.state = SettingsStore(app_paths), State()
    engine.events, engine.logger, engine.ffmpeg = Events(), Logger(), fake_ffmpeg
    monkeypatch.setattr("xscan.audio.subprocess.run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr=b"encoder failed"))
    engine._finalize({
        "id": "failed-conversion", "wav_part": wav_part, "base": "failed",
        "metadata": {"label": "Dispatch", "frequency": "155.000", "mode": "FM", "options": {}, "raw": "line"},
        "device": "VB-Cable", "started_at": now, "ended_at": now + timedelta(seconds=1),
        "duration": 1.0, "reason": "Silence detected",
    })
    assert (app_paths.recordings / "failed.wav").read_bytes() == b"RIFF generated PCM"
    call = engine.database.get_call("failed-conversion")
    assert call["audio_codec"] == "wav"
    assert call["audio_file"] == "failed.wav"


def test_successful_conversion_atomically_publishes_mp3(app_paths, monkeypatch):
    class State:
        def correlate_dsd(self, *args): return None
        def update_component(self, *args, **kwargs): pass
    class Events:
        def publish(self, *args): pass
    class Logger:
        def error(self, *args): pass

    fake_ffmpeg = app_paths.bundle / "ffmpeg.exe"
    fake_ffmpeg.write_bytes(b"fake")
    wav_part = app_paths.recordings / "success.part.wav"
    wav_part.write_bytes(b"RIFF generated PCM")
    now = datetime.now().astimezone()
    engine = object.__new__(AudioEngine)
    engine.paths, engine.database = app_paths, Database(app_paths)
    engine.settings, engine.state = SettingsStore(app_paths), State()
    engine.events, engine.logger, engine.ffmpeg = Events(), Logger(), fake_ffmpeg

    def convert(command, **kwargs):
        Path(command[-1]).write_bytes(b"encoded mp3")
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr("xscan.audio.subprocess.run", convert)
    engine._finalize({
        "id": "success-conversion", "wav_part": wav_part, "base": "success",
        "metadata": {"label": "Dispatch", "frequency": "155.000", "mode": "FM", "options": {}, "raw": "line"},
        "device": "VB-Cable", "started_at": now, "ended_at": now + timedelta(seconds=1),
        "duration": 1.0, "reason": "Silence detected",
    })
    assert not wav_part.exists()
    assert (app_paths.recordings / "success.mp3").read_bytes() == b"encoded mp3"
    assert engine.database.get_call("success-conversion")["audio_codec"] == "mp3"
