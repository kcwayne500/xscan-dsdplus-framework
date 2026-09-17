"""Per-decoder Windows session panning, including DSDPlus analog monitoring.

DSDPlus -oL/-oR alone does not pan source-monitor audio in 2.523. Apply
IChannelAudioVolume before starting FMP; never change endpoint/global volume.
COM objects stay on the thread that created them.
"""
from __future__ import annotations

import ctypes
import os
import time

if os.name == "nt":
    # Import on the host main thread. comtypes initializes its importing
    # thread as STA; lazy-importing it after our worker's MTA initialization
    # causes RPC_E_CHANGED_MODE in the packaged application.
    from pycaw.utils import AudioUtilities, AudioSession
    from pycaw.api.audiopolicy import IAudioSessionControl2


def lane_levels(lane: str, count: int) -> tuple[float, float]:
    if lane not in ("left", "right") or count != 2:
        raise RuntimeError(f"Stereo isolation requires a two-channel session, got {lane}/{count}")
    return (1.0, 0.0) if lane == "left" else (0.0, 1.0)


def _route_owned_sessions(pid: int, lane: str, apply: bool) -> list[str]:
    found = []
    for device in AudioUtilities.GetAllDevices(data_flow=0, device_state=1):
        sessions = device.AudioSessionManager.GetSessionEnumerator()
        for index in range(sessions.GetCount()):
            session = AudioSession(sessions.GetSession(index).QueryInterface(IAudioSessionControl2))
            if session.ProcessId != pid or session.State == 2:  # expired
                continue
            volume = session.channelAudioVolume()
            levels = lane_levels(lane, volume.GetChannelCount())
            if apply:
                # Silence the unwanted lane before enabling the intended lane.
                for channel in sorted(range(2), key=lambda i: levels[i]):
                    volume.SetChannelVolume(channel, levels[channel], None)
            actual = tuple(volume.GetChannelVolume(i) for i in range(2))
            if actual != levels:
                raise RuntimeError(f"Decoder {pid} lost {lane} audio isolation: {actual}")
            found.append(str(device.FriendlyName))
    return found


def route_decoder_audio(process, lane: str, *, apply: bool = True, timeout: float = 10) -> list[str]:
    if lane == "mono":
        return []
    if os.name != "nt":
        raise RuntimeError("Shared stereo decoder isolation requires Windows Core Audio")
    lane_levels(lane, 2)
    result = int(ctypes.windll.ole32.CoInitializeEx(None, 0))
    if result not in (0, 1, -2147417850):  # allow an existing STA apartment
        raise RuntimeError(f"Cannot initialize decoder audio routing: {result}")
    try:
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            devices = _route_owned_sessions(process.pid, lane, apply)
            if devices:
                return devices
            if not apply or time.monotonic() >= deadline:
                break
            time.sleep(.1)
        raise RuntimeError(f"No isolated stereo audio session for decoder {process.pid}; tuner not started")
    finally:
        if result in (0, 1):
            ctypes.windll.ole32.CoUninitialize()
