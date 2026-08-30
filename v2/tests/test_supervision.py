import io
import threading
from pathlib import Path

from xscan.events import EventBus
from xscan.settings import DEFAULT_SETTINGS, SettingsStore
from xscan.state import RuntimeState
from xscan.supervisor import ProcessSupervisor, _dsd_command_args, _fmp_command
from xscan.windows import external_program_dll_search


class Logger:
    def info(self, *args): pass
    def error(self, *args): pass
    def warning(self, *args): pass
    def exception(self, *args): pass


class FakeProcess:
    _next_pid = 500_000

    def __init__(self):
        FakeProcess._next_pid += 1
        self.pid = FakeProcess._next_pid
        self.stdout = io.BytesIO()
        self.stderr = io.BytesIO()
        self._code = None

    def poll(self): return self._code
    def terminate(self): self._code = 0
    def kill(self): self._code = -9
    def wait(self, timeout=None): return self._code


def test_default_dsdplus_args_enable_mixed_analog_digital_audio():
    assert "-m2" in DEFAULT_SETTINGS["runtime"]["dsdplus_args"]


def test_dsdplus_launch_always_forces_monitor_if_no_sync():
    assert _dsd_command_args(["-r1", "-m0", "-i20001"]) == ["-r1", "-m2", "-i20001"]
    assert _dsd_command_args(["-r1", "-i20001"]) == ["-r1", "-m2", "-i20001"]
    assert _dsd_command_args(["-r1", "-m1", "-m4", "-i20001"]).count("-m2") == 1


def test_fmp_serial_argument_keeps_raw_quotes_on_windows(monkeypatch, tmp_path):
    monkeypatch.setattr("xscan.supervisor.os.name", "nt")
    command = _fmp_command(
        tmp_path / "FMP24.exe",
        ['-i"00000001"', "-o20001", "-s1"],
    )
    assert isinstance(command, str)
    assert '-i"00000001"' in command
    assert r'-i\"00000001\"' not in command


def test_fmp_output_is_consumed_one_line_at_a_time():
    process = FakeProcess()
    process.stdout = io.BytesIO(b"first line\nsecond line\n")
    supervisor = object.__new__(ProcessSupervisor)
    received = []
    supervisor._handle_fmp_line = received.append

    supervisor._read_fmp_output(process)

    assert received == ["first line", "second line"]


def test_external_program_restores_pyinstaller_dll_directory(monkeypatch):
    calls = []
    monkeypatch.setattr("xscan.windows.sys._MEIPASS", Path(r"C:\XScan\_internal"), raising=False)
    monkeypatch.setattr("xscan.windows._set_dll_directory", calls.append)

    with external_program_dll_search():
        calls.append("launched")

    assert calls == [None, "launched", r"C:\XScan\_internal"]


def test_receiver_pair_crash_is_detected_and_restarted_as_a_pair(app_paths, monkeypatch):
    (app_paths.dsdplus / "DSDPlus.exe").write_bytes(b"dummy")
    (app_paths.dsdplus / "FMP24.exe").write_bytes(b"dummy")
    settings = SettingsStore(app_paths)
    settings.update({"runtime": {"hardware_control_enabled": True, "max_restart_attempts": 3}})
    state = RuntimeState(app_paths, EventBus())
    created = []

    def popen(*args, **kwargs):
        process = FakeProcess()
        created.append(process)
        return process

    monkeypatch.setattr("xscan.supervisor.subprocess.Popen", popen)
    supervisor = ProcessSupervisor(app_paths, settings, state, state.events, Logger())
    try:
        supervisor.start(persist=False)
        assert len(created) == 2
        created[0]._code = 17
        for _ in range(60):
            if len(created) >= 4:
                break
            threading.Event().wait(0.1)
        assert len(created) >= 4
        assert created[1].poll() == 0
        assert supervisor.dsd_process is created[2]
        assert supervisor.fmp_process is created[3]
        assert state.components["dsdplus"].restarts == 1
        assert state.components["fmp24"].restarts == 1
    finally:
        supervisor.close()
