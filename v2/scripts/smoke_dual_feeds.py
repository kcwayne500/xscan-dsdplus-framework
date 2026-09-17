"""Isolated packaged smoke test. Does not read or modify installed scanner state."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import sys
import wave

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xscan.paths import AppPaths
from xscan.database import Database
from xscan.settings import SettingsStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--executable', required=True)
    parser.add_argument('--port', type=int, default=8892)
    parser.add_argument('--serve-seconds', type=int, default=0)
    args = parser.parse_args()
    executable = Path(args.executable).resolve(strict=True)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    root = Path(tempfile.mkdtemp(prefix='xscan-dual-smoke-'))
    paths = AppPaths.discover(state_dir=root / 'state', dsdplus_root=root / 'dsdplus')
    paths.ensure()
    settings = SettingsStore(paths)
    settings.update({'server':{'port':args.port}, 'runtime':{'hardware_control_enabled':False,
        'desired_running':False}, 'streaming':{'enabled':False},
        'feeds':{'feed2':{'enabled':True}}})
    for key in ('feed1','feed2'):
        feed_paths = paths.for_feed(key)
        feed_paths.ensure()
        with wave.open(str(feed_paths.recordings / 'fixture.wav'), 'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(48000)
            wav.writeframes(b'\x00\x00' * 48000)
        Database(feed_paths, key).add_call({'id':key+'-fixture', 'label':key+' TEST ONLY',
            'audio_file':'fixture.wav', 'duration_seconds':1})
    process = subprocess.Popen([str(executable), '--no-tray', '--state-dir', str(paths.state),
        '--dsdplus-root', str(paths.dsdplus), '--port', str(args.port)],
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        base = f'http://127.0.0.1:{args.port}'
        with httpx.Client(base_url=base, timeout=3, trust_env=False) as client:
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('Packaged host exited before binding')
                try:
                    if client.get('/api/m2/feeds').status_code == 200: break
                except httpx.HTTPError:
                    pass
                time.sleep(.25)
            else:
                raise RuntimeError('Packaged host did not bind in 40 seconds')
            for url in ('/', '/m2/', '/feeds.js', '/api/m2/status', '/api/m2/feeds'):
                assert client.get(url).status_code == 200, url
            for key in ('feed1','feed2'):
                assert client.get(f'/api/m2/feeds/{key}/calls').json()['items'][0]['id'] == key+'-fixture'
                assert client.get(f'/api/m2/feeds/{key}/status').json()['feed_id'] == key
            assert client.get('/api/m2/feeds/feed2/calls/feed1-fixture/audio').status_code == 404
            assert client.get('/api/v1/feeds/feed2/settings').status_code == 428
            with client.stream('GET', '/api/m2/feeds/feed2/events') as response:
                lines = response.iter_lines()
                assert next(lines) == 'event: snapshot'
                assert json.loads(next(lines)[6:])['feed_id'] == 'feed2'
            print(json.dumps({'result':'PASS', 'url':base, 'pid':process.pid,
                              'temporary_state':str(root), 'hardware_enabled':False}), flush=True)
            if args.serve_seconds:
                time.sleep(args.serve_seconds)
    finally:
        process.terminate()
        process.wait(timeout=15)


if __name__ == '__main__':
    main()
