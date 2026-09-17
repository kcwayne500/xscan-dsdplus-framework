"""Opt-in local hardware test. Stop receivers first; no production recordings.

Writes known tones ONLY to the explicitly named virtual cable, opens two shared
WASAPI readers, and checks left/right isolation and independent reader lifetime.
It never changes endpoint defaults, gains, driver settings or scanner state.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen

import numpy as np
import sounddevice as sd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xscan.audio import select_capture_channel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, help='Exact WASAPI capture endpoint name')
    parser.add_argument('--output', required=True, help='Exact WASAPI playback endpoint name')
    parser.add_argument('--port', type=int, default=8890)
    args = parser.parse_args()
    with urlopen(f'http://127.0.0.1:{args.port}/api/m2/feeds', timeout=5) as response:
        feeds = json.load(response)['items']
    if any(item['status']['running'] or item['status']['recording'] for item in feeds):
        raise RuntimeError('Stop both scanners in the dashboard before injecting test audio')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-Command',
        "@(Get-Process DSDPlus,FMP24 -ErrorAction SilentlyContinue).Count"],
        capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.stdout.strip() != '0':
        raise RuntimeError('Receiver processes still exist; test refused')
    apis = sd.query_hostapis()
    devices = sd.query_devices()
    def find(name, direction):
        found = [i for i, d in enumerate(devices) if d['name'] == name
                 and apis[d['hostapi']]['name'] == 'Windows WASAPI' and d[f'max_{direction}_channels'] >= 2]
        if len(found) != 1:
            raise RuntimeError(f'Expected exactly one stereo WASAPI endpoint: {name}')
        return found[0]
    capture, output = find(args.input, 'input'), find(args.output, 'output')
    rate = int(devices[capture]['default_samplerate'])
    data = [[], []]
    statuses = []
    readers = []
    def callback(index):
        def receive(indata, frames, info, status):
            if status: statuses.append(str(status))
            data[index].append(select_capture_channel(bytes(indata), ('left','right')[index]))
        return receive
    try:
        for index in range(2):
            reader = sd.RawInputStream(device=capture, channels=2, samplerate=rate,
                dtype='int16', blocksize=2048, extra_settings=sd.WasapiSettings(exclusive=False),
                callback=callback(index))
            readers.append(reader)
            reader.start()
        time.sleep(.25)
        t = np.arange(rate * 2) / rate
        tones = (np.sin(2*np.pi*613*t)*9000, np.sin(2*np.pi*1237*t)*9000)
        reports = []
        with sd.RawOutputStream(device=output, channels=2, samplerate=rate, dtype='int16',
                                extra_settings=sd.WasapiSettings(exclusive=False)) as writer:
            for active in [(True,False), (False,True), (True,True)]:
                offsets = [len(chunks) for chunks in data]
                pcm = np.column_stack([tone if enabled else np.zeros_like(tone)
                                       for tone, enabled in zip(tones, active)]).astype('<i2')
                writer.write(pcm.tobytes())
                writer.write(np.zeros((rate//2,2), dtype='<i2').tobytes())
                time.sleep(.25)
                rms = [float(np.sqrt(np.mean(np.frombuffer(b''.join(chunks[offset:]), dtype='<i2').astype(float)**2)))
                       for chunks, offset in zip(data, offsets)]
                for index, enabled in enumerate(active):
                    if enabled and not rms[index] > 100:
                        raise AssertionError(f'Missing lane {index}: {rms}')
                    if not enabled and not rms[index] < max(3.0, rms[1-index]*.001):
                        raise AssertionError(f'Cross-feed audio detected: {rms}')
                reports.append({'active':active, 'rms_pcm16':rms})
            readers[0].stop()
            readers[0].close()
            offsets = [len(chunks) for chunks in data]
            writer.write(np.column_stack((np.zeros_like(tones[1]), tones[1])).astype('<i2').tobytes())
            writer.write(np.zeros((rate//2,2), dtype='<i2').tobytes())
            time.sleep(.25)
            assert len(data[0]) == offsets[0] and len(data[1]) > offsets[1]
            assert readers[1].active
        if statuses:
            raise AssertionError(f'Capture overruns/underflows: {statuses}')
        print(json.dumps({'passed':True, 'sample_rate':rate, 'phases':reports,
                          'independent_reader_stop':True}, indent=2))
    finally:
        for reader in readers:
            reader.close()


if __name__ == '__main__':
    main()
