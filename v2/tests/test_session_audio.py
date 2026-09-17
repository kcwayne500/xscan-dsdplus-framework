from types import SimpleNamespace
import pytest
from xscan import session_audio


def test_stereo_lanes_reject_unsafe_formats():
    assert session_audio.lane_levels("left", 2) == (1, 0)
    assert session_audio.lane_levels("right", 2) == (0, 1)
    for lane, count in [("mono", 2), ("left", 1), ("right", 16)]:
        with pytest.raises(RuntimeError):
            session_audio.lane_levels(lane, count)


def test_mono_does_not_touch_windows_sessions(monkeypatch):
    monkeypatch.setattr(session_audio, "_route_owned_sessions", lambda *a: pytest.fail("mono must not pan"))
    assert session_audio.route_decoder_audio(None, "mono") == []


@pytest.mark.skipif(session_audio.os.name != "nt", reason="Windows COM")
def test_missing_or_dead_decoder_fails_closed(monkeypatch):
    monkeypatch.setattr(session_audio, "_route_owned_sessions", lambda *a: [])
    with pytest.raises(RuntimeError, match="tuner not started"):
        session_audio.route_decoder_audio(SimpleNamespace(pid=1, poll=lambda:None), "left", timeout=0)
    with pytest.raises(RuntimeError, match="tuner not started"):
        session_audio.route_decoder_audio(SimpleNamespace(pid=1, poll=lambda:0), "right", timeout=0)


@pytest.mark.skipif(session_audio.os.name != "nt", reason="Windows COM")
def test_only_owned_sessions_are_panned_and_readback_is_checked(monkeypatch):
    from pycaw import utils
    class Volume:
        def __init__(self): self.levels = [1.0, 1.0]; self.writes = []
        def GetChannelCount(self): return 2
        def GetChannelVolume(self, index): return self.levels[index]
        def SetChannelVolume(self, index, value, context):
            self.writes.append((index, value)); self.levels[index] = value
    ours, other = Volume(), Volume()
    def session(pid, volume):
        value = SimpleNamespace(ProcessId=pid, State=1, channelAudioVolume=lambda:volume)
        value.QueryInterface = lambda interface:value
        return value
    sessions = [session(10, other), session(20, ours)]
    enumerator = SimpleNamespace(GetCount=lambda:len(sessions), GetSession=lambda i:sessions[i])
    device = SimpleNamespace(FriendlyName="Test cable", AudioSessionManager=SimpleNamespace(GetSessionEnumerator=lambda:enumerator))
    monkeypatch.setattr(utils.AudioUtilities, "GetAllDevices", lambda **kwargs:[device])
    monkeypatch.setattr(session_audio, "AudioSession", lambda value:value)
    assert session_audio._route_owned_sessions(20, "right", True) == ["Test cable"]
    assert ours.writes == [(0, 0.0), (1, 1.0)]
    assert other.writes == []
    assert session_audio._route_owned_sessions(20, "right", False) == ["Test cable"]
    ours.levels[0] = 1.0
    with pytest.raises(RuntimeError, match="lost right audio isolation"):
        session_audio._route_owned_sessions(20, "right", False)
