from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .database import Database
from .paths import AppPaths
from .settings import SettingsStore, find_legacy_settings


class Migrator:
    def __init__(self, paths: AppPaths, settings: SettingsStore, database: Database, logger):
        self.paths = paths
        self.settings = settings
        self.database = database
        self.logger = logger

    def run(self) -> dict[str, Any]:
        state = self._load_state()
        imported_settings = False
        imported_calls = 0
        if not state.get("legacy_settings_imported"):
            source = find_legacy_settings(self.paths)
            if source:
                try:
                    legacy = json.loads(source.read_text(encoding="utf-8"))
                    patch = {
                        "audio": {"device_name": legacy.get("audio_device_name") or self.settings.section("audio")["device_name"]},
                        "runtime": {"desired_running": bool(legacy.get("auto_start_on_open", True))},
                        "streaming": {"enabled": bool(legacy.get("streaming_enabled", True))},
                    }
                    self.settings.update(patch)
                    imported_settings = True
                    state["legacy_settings_source"] = str(source)
                except (OSError, json.JSONDecodeError) as exc:
                    self.logger.warning("Legacy settings import failed: %s", exc)
            state["legacy_settings_imported"] = True
        log_path = self.paths.recordings / "recordings_log.json"
        if log_path.is_file():
            try:
                entries = json.loads(log_path.read_text(encoding="utf-8"))
                if isinstance(entries, list):
                    for index, entry in enumerate(entries):
                        if not isinstance(entry, dict):
                            continue
                        audio_file = str(entry.get("audio_file") or entry.get("mp3_file") or entry.get("wav_file") or "")
                        audio_path = self.paths.recordings / Path(audio_file).name if audio_file else None
                        call = dict(entry)
                        call.update(
                            {
                                "source_ref": f"legacy:{log_path}:{index}",
                                "audio_file": Path(audio_file).name if audio_file else "",
                                "audio_codec": audio_path.suffix.lstrip(".").lower() if audio_path else "",
                                "audio_bytes": audio_path.stat().st_size if audio_path and audio_path.is_file() else 0,
                            }
                        )
                        before = self.database.list_calls(search="", state="active", offset=0, limit=1)["total"]
                        self.database.add_call(call)
                        after = self.database.list_calls(search="", state="active", offset=0, limit=1)["total"]
                        imported_calls += max(0, after - before)
            except (OSError, json.JSONDecodeError) as exc:
                self.logger.warning("Legacy recording log import failed: %s", exc)
        state.update({"last_run_at": datetime.now(UTC).isoformat(), "last_imported_calls": imported_calls})
        self.paths.migration.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        return {"settings": imported_settings, "calls": imported_calls, "state": state}

    def _load_state(self) -> dict[str, Any]:
        if self.paths.migration.is_file():
            try:
                value = json.loads(self.paths.migration.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    return value
            except (OSError, json.JSONDecodeError):
                pass
        return {}
