"""Temporary DSDPlus WAV/source-monitor routing diagnostic, with receivers stopped."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import sys
from urllib.request import urlopen
import wave
import numpy as np
import sounddevice as sd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xscan.supervisor import _fmp_command
from xscan.session_audio import route_decoder_audio


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dsdplus-root', required=True)
    parser.add_argument('--output-number', type=int, required=True)
    parser.add_argument('--lane', choices=['L','R'], required=True)
    parser.add_argument('--session-pan', action='store_true', help='Enforce per-process Windows stereo routing before starting the tuner')
    parser.add_argument('--copy-settings', action='store_true', help='Copy the existing native decoder settings for this disposable test only')
    parser.add_argument('--frequency', type=float, help='Optional known broadcast frequency for a real receiver test instead of offline WAV')
    parser.add_argument('--serial', help='Verified idle SDR serial; required with --frequency')
    parser.add_argument('--seconds', type=float, default=12)
    parser.add_argument('--input', default='CABLE Output (VB-Audio Virtual Cable)')
    args = parser.parse_args()
    with urlopen('http://127.0.0.1:8890/api/m2/feeds', timeout=5) as response:
        assert not any(f['status']['running'] or f['status']['recording'] for f in json.load(response)['items'])
    result = subprocess.run(['powershell.exe','-NoProfile','-Command',
        '@(Get-Process DSDPlus,FMP24 -ErrorAction SilentlyContinue).Count'],
        capture_output=True,text=True,check=True,creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.stdout.strip() == '0', 'Receivers must be stopped'
    directory = Path(tempfile.mkdtemp(prefix='xscan-dsd-stereo-'))
    source = Path(args.dsdplus_root)
    for path in [source / 'DSDPlus.exe', *source.glob('*.dll')]:
        shutil.copy2(path, directory / path.name)
    for suffix in ('networks','sites','frequencies','groups','radios','P25data','siteLoader'):
        (directory / ('DSDPlus.'+suffix)).write_text('; Isolated routing diagnostic\n', encoding='ascii')
    if args.copy_settings:
        shutil.copy2(source / 'DSDPlus.bin', directory / 'DSDPlus.bin')
    if args.frequency:
        assert args.serial
        for name in ('FMP24.exe','FMP24.cfg'):
            shutil.copy2(source / name, directory / name)
    raw = (np.sin(np.arange(48000*5) / 48000 * 2*np.pi*613)*8000).astype('<i2')
    wav_path = directory / 'source-tone.wav'
    with wave.open(str(wav_path), 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(48000); wav.writeframes(raw.tobytes())
    apis = sd.query_hostapis()
    devices = [i for i,d in enumerate(sd.query_devices()) if d['name'] == args.input
               and apis[d['hostapi']]['name'] == 'Windows WASAPI' and d['max_input_channels'] >= 2]
    assert len(devices) == 1
    frames = []
    def capture(indata, count, info, status): frames.append(bytes(indata))
    process = None
    tuner = None
    try:
        with sd.RawInputStream(device=devices[0], channels=2, samplerate=48000, dtype='int16',
                callback=capture, extra_settings=sd.WasapiSettings(exclusive=False)), (directory / 'decoder.log').open('wb') as log:
            start = subprocess.STARTUPINFO()
            start.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            start.wShowWindow = 0
            inputs = ['-r1','-i20042'] if args.frequency else ['?',str(wav_path)]
            process = subprocess.Popen([str(directory / 'DSDPlus.exe'),
                f'-o{args.output_number}{args.lane}', '-O', 'NUL', *inputs],
                cwd=directory, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                startupinfo=start, creationflags=subprocess.CREATE_NO_WINDOW)
            if args.frequency:
                time.sleep(1)
                if args.session_pan:
                    print('Verified session route:', route_decoder_audio(process, 'left' if args.lane == 'L' else 'right'), flush=True)
                tuner = subprocess.Popen(_fmp_command(directory / 'FMP24.exe',
                    [f'-i"{args.serial}"','-o20042',f'-f{args.frequency}']), cwd=directory,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    startupinfo=start, creationflags=subprocess.CREATE_NO_WINDOW)
            deadline = time.monotonic()+args.seconds
            while process.poll() is None and time.monotonic() < deadline: time.sleep(.2)
    finally:
        if tuner and tuner.poll() is None:
            tuner.terminate(); tuner.wait(timeout=5)
        if process and process.poll() is None:
            process.terminate(); process.wait(timeout=5)
    pcm = np.frombuffer(b''.join(frames), dtype='<i2').reshape(-1,2).astype(float)
    print(json.dumps(dict(directory=str(directory), lane=args.lane,
        rms=np.sqrt(np.mean(pcm*pcm,axis=0)).tolist(), peak=np.max(np.abs(pcm),axis=0).tolist()), indent=2))
    print((directory / 'decoder.log').read_text(errors='replace')[-4500:])


if __name__ == '__main__': main()
