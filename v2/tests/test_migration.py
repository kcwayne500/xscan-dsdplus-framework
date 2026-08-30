import json

from xscan.database import Database
from xscan.migration import Migrator
from xscan.settings import SettingsStore


class Logger:
    def warning(self, *args, **kwargs): pass


def test_recording_log_import_is_idempotent(app_paths):
    log = app_paths.recordings / "recordings_log.json"
    log.write_text(json.dumps([{"started_at": "2026-01-01T00:00:00", "label": "Dispatch", "audio_file": "missing.mp3"}]), encoding="utf-8")
    settings = SettingsStore(app_paths)
    database = Database(app_paths)
    migrator = Migrator(app_paths, settings, database, Logger())
    migrator.run(); migrator.run()
    assert database.list_calls()["total"] == 1
