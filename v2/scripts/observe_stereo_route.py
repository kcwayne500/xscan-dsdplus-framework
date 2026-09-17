"""Read-only stereo lane measurement during a ONE-receiver live test."""
import argparse
import json
import time
from urllib.request import urlopen
import numpy as np
import sounddevice as sd


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--input', default='CABLE Output (VB-Audio Virtual Cable)')
    parser.add_argument('--port', type=int, default=8890)
    args = parser.parse_args()
    apis = sd.query_hostapis()
    found = [i for i,d in enumerate(sd.query_devices()) if d['name'] == args.input
             and apis[d['hostapi']]['name'] == 'Windows WASAPI' and d['max_input_channels'] >= 2]
    if len(found) != 1: raise RuntimeError('Exact stereo endpoint not found')
    peaks = np.zeros(2)
    squares = np.zeros(2)
    frames_total = 0
    strong = np.zeros(2, dtype=int)
    statuses = []
    def receive(indata, frames, info, status):
        nonlocal peaks, squares, frames_total, strong
        pcm = np.frombuffer(indata, dtype='<i2').reshape(-1,2).astype(float)
        if status: statuses.append(str(status))
        peaks = np.maximum(peaks, np.max(np.abs(pcm),axis=0))
        squares += np.sum(pcm*pcm,axis=0)
        strong += (np.sqrt(np.mean(pcm*pcm,axis=0)) > 70)
        frames_total += frames
    observations = []
    with sd.RawInputStream(device=found[0], channels=2, samplerate=48000,
            dtype='int16', blocksize=2048, callback=receive,
            extra_settings=sd.WasapiSettings(exclusive=False)):
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            with urlopen(f'http://127.0.0.1:{args.port}/api/m2/feeds', timeout=3) as response:
                feeds = json.load(response)['items']
            observations.append([dict(id=f['id'], running=f['status']['running'],
                recording=f['status']['recording'], now=f['status']['now_playing']['display'],
                mode=f['status']['now_playing']['mode']) for f in feeds])
            time.sleep(.5)
    active = sorted({f['id'] for row in observations for f in row if f['running']})
    channels = sorted({f['now'] for row in observations for f in row if f['running']})
    print(json.dumps(dict(active_feeds=active, observed_channels=channels,
        recorded_channels=sorted({f['now'] for row in observations for f in row if f['recording']}),
        frames=frames_total, peak_pcm16=peaks.tolist(),
        rms_pcm16=np.sqrt(squares/max(1,frames_total)).tolist(),
        strong_blocks=strong.tolist(), statuses=statuses), indent=2))


if __name__ == '__main__': main()
