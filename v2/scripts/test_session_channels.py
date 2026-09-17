"""Inspect or pan only the explicitly selected diagnostic decoder PID."""
import argparse
import json
from pycaw.utils import AudioUtilities, AudioSession
from pycaw.api.audiopolicy import IAudioSessionControl2

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--pid', type=int, required=True)
parser.add_argument('--lane', choices=['left', 'right'])
args = parser.parse_args()
for device in AudioUtilities.GetAllDevices(data_flow=0, device_state=1):
    sessions = device.AudioSessionManager.GetSessionEnumerator()
    for index in range(sessions.GetCount()):
        session = AudioSession(sessions.GetSession(index).QueryInterface(IAudioSessionControl2))
        if session.ProcessId != args.pid:
            continue
        volume = session.channelAudioVolume()
        count = volume.GetChannelCount()
        before = [volume.GetChannelVolume(i) for i in range(count)]
        if args.lane:
            assert device.FriendlyName == 'CABLE Input (VB-Audio Virtual Cable)'
            assert session.Process.name().lower() == 'dsdplus.exe'
            assert 'xscan-dsd-stereo-' in session.Process.exe().lower()
            assert count == 2, f'Cannot isolate a {count}-channel session'
            lane = 0 if args.lane == 'left' else 1
            volume.SetChannelVolume(1-lane, 0.0, None)
            volume.SetChannelVolume(lane, 1.0, None)
        print(json.dumps(dict(device=device.FriendlyName,pid=args.pid,channels=count,
            before=before,after=[volume.GetChannelVolume(i) for i in range(count)])))
