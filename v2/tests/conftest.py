from __future__ import annotations

from pathlib import Path

import pytest

from xscan.paths import AppPaths


@pytest.fixture
def app_paths(tmp_path: Path) -> AppPaths:
    bundle = tmp_path / "bundle"
    state = tmp_path / "state"
    dsdplus = tmp_path / "DSDPlusFastlane"
    recordings = dsdplus / "recordings"
    web = bundle / "xscan" / "web"
    web.mkdir(parents=True)
    recordings.mkdir(parents=True)
    paths = AppPaths(
        bundle=bundle,
        state=state,
        dsdplus=dsdplus,
        recordings=recordings,
        backups=state / "backups",
        logs=state / "logs",
        trash=recordings / ".xscan-trash",
        web=web,
        database=state / "xscan.db",
        settings=state / "settings.json",
        auth=state / "auth.json",
        migration=state / "migration.json",
    )
    paths.ensure()
    return paths
