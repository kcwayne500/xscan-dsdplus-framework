from pathlib import Path

from xscan.paths import AppPaths
from xscan.streaming import _find_tool


def test_discover_uses_portable_defaults(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("XSCAN_STATE_DIR", raising=False)
    monkeypatch.delenv("XSCAN_DSDPLUS_ROOT", raising=False)
    paths = AppPaths.discover()
    assert paths.state == tmp_path / "XScan"
    assert paths.dsdplus == tmp_path / "Programs" / "DSDPlus"


def test_discover_and_tool_lookup_honor_explicit_configuration(tmp_path: Path):
    state = tmp_path / "state elsewhere"
    dsdplus = tmp_path / "radio programs"
    tool = tmp_path / "dependencies" / "ffmpeg.exe"
    tool.parent.mkdir(parents=True)
    tool.write_bytes(b"test")

    paths = AppPaths.discover(state_dir=state, dsdplus_root=dsdplus)
    assert paths.state == state
    assert paths.dsdplus == dsdplus
    assert _find_tool(paths, "ffmpeg", "ffmpeg.exe", str(tool)) == tool
