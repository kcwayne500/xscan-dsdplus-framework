"""Synthetic two-tone RTSP/mix integration test; no SDR or audio device access."""
import argparse
import logging
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xscan.feeds import FeedManager
from xscan.paths import AppPaths
from xscan.settings import SettingsStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ffmpeg', required=True)
    parser.add_argument('--mediamtx', required=True)
    args = parser.parse_args()
    for port in (18554, 18888, 18889, 18189):
        with socket.socket() as probe: probe.bind(('127.0.0.1', port))
    root = Path(tempfile.mkdtemp(prefix='xscan-mix-smoke-'))
    paths = AppPaths.discover(state_dir=root / 'state', dsdplus_root=root / 'empty-dsdplus')
    paths.ensure()
    settings = SettingsStore(paths)
    settings.update({'tools':{'ffmpeg':str(Path(args.ffmpeg).resolve(strict=True)),
        'mediamtx':str(Path(args.mediamtx).resolve(strict=True))},
        'runtime':{'desired_running':False,'hardware_control_enabled':True},
        'feeds':{'feed2':{'enabled':True}}, 'streaming':{'rtsp_port':18554,
        'webrtc_port':18889,'webrtc_media_port':18189,'hls_port':18888}})
    manager = FeedManager(paths, settings, logging.getLogger('synthetic-mix'))
    stop = threading.Event()
    active = {'feed1','feed2'}
    def signal():
        phase = np.arange(960)
        deadline = time.monotonic()
        while not stop.is_set():
            for key, hz in (('feed1',400),('feed2',1200)):
                if key not in active:
                    continue
                pcm = (12000 * np.sin(2 * np.pi * hz * phase / 48000)).astype('<i2')
                manager.get(key).streaming.write(pcm.tobytes())
            deadline += .02
            stop.wait(max(0, deadline - time.monotonic()))
    worker = threading.Thread(target=signal, daemon=True)
    try:
        # Deliberately never initialise/start HostRuntime or AudioEngine.
        assert manager.get('feed1').streaming.start(48000)
        assert manager.get('feed2').streaming.start(48000)
        manager.mixer.start()
        worker.start()
        time.sleep(2)
        command = [args.ffmpeg, '-hide_banner','-loglevel','error','-rtsp_transport','tcp',
            '-i','rtsp://127.0.0.1:18554/scanner-both','-t','2','-ar','48000','-ac','1','-f','s16le','pipe:1']
        result = subprocess.run(command, capture_output=True, timeout=20,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        assert result.returncode == 0, result.stderr.decode(errors='replace')
        samples = np.frombuffer(result.stdout, dtype='<i2').astype(float)
        assert samples.size >= 48000
        bins = np.fft.rfftfreq(samples.size, 1/48000)
        spectrum = np.abs(np.fft.rfft(samples * np.hanning(samples.size)))
        peaks = []
        for hz in (400,1200):
            window = np.abs(bins-hz) < 10
            peak = float(spectrum[window].max())
            assert peak > float(np.median(spectrum)) * 100, (hz,peak)
            peaks.append(hz)
        assert max(abs(samples)) < 32767
        server_pid = manager.server.process.pid
        active.discard('feed2')
        manager.get('feed2').streaming.stop()
        manager.mixer.clear('feed2')
        time.sleep(1)
        assert manager.server.process.pid == server_pid and manager.server.process.poll() is None
        assert all(manager.get('feed1').streaming.health().values())
        remaining = subprocess.run(command, capture_output=True, timeout=20,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        assert remaining.returncode == 0
        single = np.frombuffer(remaining.stdout, dtype='<i2').astype(float)
        bins = np.fft.rfftfreq(single.size, 1/48000)
        spectrum = np.abs(np.fft.rfft(single * np.hanning(single.size)))
        first = spectrum[np.abs(bins-400) < 10].max()
        second = spectrum[np.abs(bins-1200) < 10].max()
        assert second < first * .05
        assert not any(feed.supervisor.desired for feed in manager.feeds.values())
        print(f'PASS: Both RTSP decoded {samples.size} samples; tones {peaks}; no clipping; Feed 1 and Both survive Feed 2 stopping; no receiver/capture started.')
    finally:
        stop.set()
        if worker.is_alive(): worker.join(timeout=2)
        manager.close()


if __name__ == '__main__': main()
