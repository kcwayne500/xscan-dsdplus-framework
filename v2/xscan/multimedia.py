"""Host-owned MediaMTX and an optional bounded two-feed PCM mixer."""
from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from collections import deque
from urllib.parse import urlparse

import numpy as np

from .streaming import _find_tool, local_ipv4_addresses
from .windows import WINDOW_CREATION_FLAGS, terminate_process


def stream_names(settings):
    name = settings.section("streaming")["stream_name"]
    return {"feed1": name, "feed2": name + "-feed2", "both": name + "-both"}


class MediaServer:
    def __init__(self, paths, settings, logger):
        self.paths, self.settings, self.logger = paths, settings, logger
        self.process = None
        self.lock = threading.RLock()
        self.configuration = None

    def ensure(self):
        with self.lock:
            configuration = (self.settings.section("streaming"), self.settings.section("tools"),
                             self.settings.section("server").get("public_url", ""))
            if self.process and self.process.poll() is None and configuration == self.configuration:
                return self.process
            terminate_process(self.process)
            self.process = None
            self.configuration = configuration
            exe = _find_tool(self.paths, "mediamtx", "mediamtx.exe", self.settings.section("tools")["mediamtx"])
            if not exe:
                raise RuntimeError("MediaMTX is unavailable")
            cfg = self.settings.section("streaming")
            hosts = local_ipv4_addresses()
            public = urlparse(self.settings.section("server").get("public_url", "")).hostname
            if public and public not in hosts:
                hosts.append(public)
            # JSON is valid YAML and safely quotes stream names and host strings.
            config = {
                "logLevel": "warn", "rtspAddress": f"127.0.0.1:{cfg['rtsp_port']}",
                "rtspTransports": ["tcp"], "srt": False, "moq": False, "playback": False,
                "webrtcAddress": f"127.0.0.1:{cfg['webrtc_port']}",
                "webrtcLocalUDPAddress": f":{cfg['webrtc_media_port']}",
                "webrtcLocalTCPAddress": f":{cfg['webrtc_media_port']}",
                "webrtcAdditionalHosts": hosts, "hls": True,
                "hlsAddress": f"127.0.0.1:{cfg['hls_port']}", "hlsAlwaysRemux": True,
                "hlsVariant": "lowLatency", "hlsSegmentCount": 7,
                "hlsSegmentDuration": "1s", "hlsPartDuration": "200ms",
                "rtmp": False, "api": False, "metrics": False, "pprof": False,
                "paths": {name: {} for name in stream_names(self.settings).values()},
            }
            path = self.paths.state / "mediamtx.generated.yml"
            path.write_text(json.dumps(config, indent=2), encoding="utf-8")
            self.process = subprocess.Popen([str(exe), str(path)], cwd=exe.parent,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=WINDOW_CREATION_FLAGS)
            process = self.process
            def errors():
                for line in iter(process.stdout.readline, b""):
                    self.logger.warning("MediaMTX: %s", line.decode(errors="replace").strip())
            threading.Thread(target=errors, daemon=True, name="shared-media-log").start()
            time.sleep(0.4)
            if process.poll() is not None:
                raise RuntimeError("MediaMTX failed to start; check ports and logs")
            return process

    def close(self):
        with self.lock:
            terminate_process(self.process)
            self.process = None


class PcmMixer:
    """Wall-clock-paced audio; input queues cannot block either recorder.

    Drift is bounded by trimming old buffered samples and filling underruns
    with silence. Inputs are resampled only for listening, never recording.
    """
    rate = 48000
    frames = 960

    def __init__(self, settings, publisher):
        self.settings, self.publisher = settings, publisher
        self.queues = {key: queue.Queue(maxsize=8) for key in ("feed1", "feed2")}
        self.buffers = {key: np.empty(0, dtype=np.float32) for key in self.queues}
        self.drops = {key: 0 for key in self.queues}
        self.closing = threading.Event()
        self.thread = None
        self.last_error = ""
        self.restarts = 0
        self._clear = {key: threading.Event() for key in self.queues}

    def put(self, feed_id, data, sample_rate):
        pending = self.queues[feed_id]
        try:
            pending.put_nowait((time.monotonic(), data, sample_rate))
        except queue.Full:
            self.drops[feed_id] += 1

    def clear(self, feed_id):
        pending = self.queues[feed_id]
        while True:
            try:
                pending.get_nowait()
            except queue.Empty:
                break
        # The marker cannot be lost to a concurrent input callback.
        self._clear[feed_id].set()

    def render(self):
        output = np.zeros(self.frames, dtype=np.float32)
        weights = self.settings.section("mix")
        divisor = max(1.0, sum(float(weights[key]) for key in self.queues))
        for key, pending in self.queues.items():
            if self._clear[key].is_set():
                self.buffers[key] = np.empty(0, dtype=np.float32)
                self._clear[key].clear()
            chunks = [self.buffers[key]]
            while True:
                try:
                    stamp, data, rate = pending.get_nowait()
                except queue.Empty:
                    break
                if not data:
                    chunks = [np.empty(0, dtype=np.float32)]
                    continue
                if time.monotonic() - stamp > 0.4:
                    self.drops[key] += 1
                    continue
                samples = np.frombuffer(data, dtype="<i2").astype(np.float32)
                if rate != self.rate and samples.size:
                    count = max(1, round(samples.size * self.rate / rate))
                    samples = np.interp(np.arange(count) * rate / self.rate, np.arange(samples.size), samples)
                chunks.append(samples)
            buffered = np.concatenate(chunks)
            if buffered.size > self.frames * 6:
                buffered = buffered[-self.frames * 3:]
                self.drops[key] += 1
            count = min(self.frames, buffered.size)
            output[:count] += buffered[:count] * (float(weights[key]) / divisor)
            self.buffers[key] = buffered[count:]
        return np.clip(output, -32768, 32767).astype("<i2").tobytes()

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.closing.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="both-audio-mixer")
        self.thread.start()

    def _run(self):
        attempts = deque()
        retry_at = 0.0
        check_at = 0.0
        deadline = time.monotonic()
        while not self.closing.is_set():
            now = time.monotonic()
            enabled = (self.settings.section("feeds")["feed2"]["enabled"]
                       and self.settings.section("runtime")["hardware_control_enabled"])
            if enabled and self.settings.section("streaming")["enabled"]:
                if now >= check_at:
                    check_at = now + 2.0
                    while attempts and now - attempts[0] > 600:
                        attempts.popleft()
                    try:
                        if not all(self.publisher.health().values()) and now >= retry_at and len(attempts) < 5:
                            attempts.append(now)
                            self.restarts += 1
                            self.publisher.start(self.rate)
                            retry_at = now + min(30, 2 ** len(attempts))
                        if len(attempts) >= 5:
                            self.last_error = "Mix retry budget exhausted; retry after the 10-minute window"
                    except Exception as exc:
                        self.last_error = str(exc)
                self.publisher.write(self.render())
            else:
                if self.publisher.ffmpeg_process:
                    self.publisher.stop()
                self.render()  # discard pending input while the mix is disabled
                attempts.clear()
            deadline += self.frames / self.rate
            if deadline < time.monotonic() - 0.1:
                deadline = time.monotonic()
            self.closing.wait(max(0, deadline - time.monotonic()))

    def close(self):
        self.closing.set()
        if self.thread:
            self.thread.join(timeout=10)
        self.publisher.stop()
