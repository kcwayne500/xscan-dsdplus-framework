"""Exercise the PowerShell upgrade against disposable trees, never installed files."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'upgrade-dual-feed.ps1'
PWSH = shutil.which('pwsh') or shutil.which('powershell')
pytestmark = pytest.mark.skipif(os.name != 'nt' or not PWSH, reason='Windows upgrade script')


def quote(path):
    return "'" + str(path).replace("'", "''") + "'"


def run_upgrade(install, state, dsd, candidate, rollback=None, busy=False):
    owner = "[pscustomobject]@{ExecutablePath=" + quote(install / 'XScanV2.exe') + ";Name='XScanV2.exe';CommandLine=''}" if busy else '@()'
    command = (f"function Get-CimInstance {{ {owner} }}; function Get-NetTCPConnection {{ @() }}; "
        f"& {quote(SCRIPT)} -InstallRoot {quote(install)} -StateRoot {quote(state)} "
        f"-DsdPlusRoot {quote(dsd)} -Candidate {quote(candidate)}")
    if rollback:
        command += f' -Rollback {quote(rollback)}'
    return subprocess.run([PWSH, '-NoProfile', '-Command', command], capture_output=True, text=True, timeout=30)


def fixture_trees(tmp_path):
    install, state, dsd, candidate = [tmp_path / name for name in ('XScan','state','dsd','candidate')]
    for folder in (install, state, dsd, candidate): folder.mkdir()
    (install / 'XScanV2.exe').write_bytes(b'old fake executable')
    (candidate / 'XScanV2.exe').write_bytes(b'new fake executable')
    (state / 'settings.json').write_text(json.dumps({'server':{'port':19999}}))
    (state / 'xscan.db').write_bytes(b'old fake database')
    return install, state, dsd, candidate


def test_versioned_upgrade_and_rollback_preserve_newer_state(tmp_path):
    install, state, dsd, candidate = fixture_trees(tmp_path)
    result = run_upgrade(install,state,dsd,candidate)
    assert result.returncode == 0, result.stderr + result.stdout
    assert (install / 'XScanV2.exe').read_bytes() == b'new fake executable'
    checkpoint = next((tmp_path / 'XScan-upgrades').iterdir())
    (state / 'xscan.db').write_bytes(b'new fake database')
    recordings = state / 'feeds' / 'feed2' / 'dsdplus' / 'recordings'
    recordings.mkdir(parents=True)
    (recordings / 'preserve.wav').write_bytes(b'new recording')
    result = run_upgrade(install,state,dsd,candidate,checkpoint)
    assert result.returncode == 0, result.stderr + result.stdout
    assert (install / 'XScanV2.exe').read_bytes() == b'old fake executable'
    assert (state / 'xscan.db').read_bytes() == b'old fake database'
    assert (recordings / 'preserve.wav').read_bytes() == b'new recording'
    assert any(path.read_bytes() == b'new fake database'
               for path in (tmp_path / 'XScan-upgrades').glob('*/state/xscan.db'))


def test_upgrade_refuses_running_owned_process(tmp_path):
    install, state, dsd, candidate = fixture_trees(tmp_path)
    result = run_upgrade(install,state,dsd,candidate,busy=True)
    assert result.returncode != 0
    assert (install / 'XScanV2.exe').read_bytes() == b'old fake executable'
    assert not (tmp_path / 'XScan-upgrades').exists()
