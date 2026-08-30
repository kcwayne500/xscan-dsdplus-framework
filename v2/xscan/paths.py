from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def _bundle_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class AppPaths:
    bundle: Path
    state: Path
    dsdplus: Path
    recordings: Path
    backups: Path
    logs: Path
    trash: Path
    web: Path
    database: Path
    settings: Path
    auth: Path
    migration: Path

    @classmethod
    def discover(
        cls,
        *,
        state_dir: str | Path | None = None,
        dsdplus_root: str | Path | None = None,
    ) -> "AppPaths":
        bundle = _bundle_root()
        local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        state = Path(state_dir or os.environ.get("XSCAN_STATE_DIR") or local_app_data / "XScan")
        configured_dsdplus = dsdplus_root or os.environ.get("XSCAN_DSDPLUS_ROOT")
        if configured_dsdplus:
            dsdplus = Path(configured_dsdplus)
        else:
            dsdplus = local_app_data / "Programs" / "DSDPlus"
        recordings = dsdplus / "recordings"
        return cls(
            bundle=bundle,
            state=state,
            dsdplus=dsdplus,
            recordings=recordings,
            backups=state / "backups",
            logs=state / "logs",
            trash=recordings / ".xscan-trash",
            web=bundle / "xscan" / "web",
            database=state / "xscan.db",
            settings=state / "settings.json",
            auth=state / "auth.json",
            migration=state / "migration.json",
        )

    def ensure(self) -> None:
        for path in (self.state, self.backups, self.logs, self.recordings, self.trash):
            path.mkdir(parents=True, exist_ok=True)
