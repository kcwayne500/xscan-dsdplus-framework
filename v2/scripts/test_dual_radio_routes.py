"""Opt-in real-radio stereo isolation test; no production recordings/settings."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
import numpy as np
import sounddevice as sd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xscan.session_audio import route_decoder_audio
from xscan.supervisor import _fmp_command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared-runtime', required=True)
    parser.add_argument('--serial-left', required=True)
    parser.add_argument('--serial-right', required=True)
    parser.add_argument('--frequency', type=float, required=True)
    parser.add_argument('--output-number', type=int, required=True)
    args = parser.parse_args()
    assert args.serial_left != args.serial_right
    with urlopen('http://127.0.0.1:8890/api/m2/feeds', timeout=5) as response:
        assert not any(f['status']['running'] or f['status']['recording'] for f in json.load(response)['items'])
    result = subprocess.run(['powershell.exe','-NoProfile','-Command',
        '@(Get-Process DSDPlus,FMP24 -ErrorAction SilentlyContinue).Count'],
        capture_output=True,text=True,check=True,creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.stdout.strip() == '0', 'Receivers must be stopped'
    root = Path(tempfile.mkdtemp(prefix='xscan-dual-radio-test-'))
    source = Path(args.prepared_runtime)
    apis = sd.query_hostapis()
    devices = [i for i,d in enumerate(sd.query_devices()) if d['name']=='CABLE Output (VB-Audio Virtual Cable)'
        and apis[d['hostapi']]['name']=='Windows WASAPI' and d['max_input_channels']>=2]
    assert len(devices)==1
    processes, logs = {}, []
    def start(lane, serial, link):
        directory = root / lane
        directory.mkdir()
        for name in ['DSDPlus.exe','DSDPlus.bin','FMP24.exe','FMP24.cfg']:
            shutil.copy2(source/name,directory/name)
        for path in source.glob('*.dll'): shutil.copy2(path,directory/path.name)
        for suffix in ('networks','sites','frequencies','groups','radios','P25data','siteLoader'):
            (directory/('DSDPlus.'+suffix)).write_text('; Isolated radio test\n')
        log = (directory/'decoder.log').open('wb'); logs.append(log)
        startup = subprocess.STARTUPINFO(); startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        kwargs = dict(cwd=directory,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
            startupinfo=startup,creationflags=subprocess.CREATE_NO_WINDOW)
        dsd = subprocess.Popen([str(directory/'DSDPlus.exe'),'-r1',f'-i{link}',
            f'-o{args.output_number}{lane[0].upper()}','-O','NUL'], **kwargs)
        processes[lane] = [dsd]
        print(lane, route_decoder_audio(dsd,lane),flush=True)
        fmp = subprocess.Popen(_fmp_command(directory/'FMP24.exe',
            [f'-i"{serial}"',f'-o{link}',f'-f{args.frequency}']),**kwargs)
        processes[lane].append(fmp)
        time.sleep(30)  # First-run FFT planning and direct-link connection.
    def stop(lane):
        for process in reversed(processes.pop(lane, [])):
            if process.poll() is None: process.terminate(); process.wait(timeout=5)
    def measure(label, expected):
        for lane, pair in processes.items():
            assert all(p.poll() is None for p in pair)
            route_decoder_audio(pair[0],lane,apply=False)
        pcm = sd.rec(6*48000,samplerate=48000,channels=2,dtype='int16',device=devices[0],
            blocking=True,extra_settings=sd.WasapiSettings(exclusive=False)).astype(float)
        rms = np.sqrt(np.mean(pcm*pcm,axis=0))
        print(json.dumps(dict(phase=label,rms=rms.tolist(),peak=np.max(np.abs(pcm),axis=0).tolist())),flush=True)
        for index, audible in enumerate(expected):
            assert rms[index] > 100 if audible else rms[index] < 2, f'{label}: unexpected audio {rms}'
    try:
        print(root,flush=True)
        start('right',args.serial_right,20042)
        measure('right only',[False,True])
        start('left',args.serial_left,20041)
        measure('both',[True,True])
        stop('right')
        time.sleep(1)
        measure('right stopped; left survives',[True,False])
        print('PASS: independent real-radio lanes and stop',flush=True)
    finally:
        stop('left'); stop('right')
        for log in logs: log.close()


if __name__ == '__main__': main()
