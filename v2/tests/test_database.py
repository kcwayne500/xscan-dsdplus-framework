from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from xscan.database import Database


def test_call_lifecycle_and_audio_file_moves(app_paths):
    database = Database(app_paths)
    audio = app_paths.recordings / "call.mp3"
    audio.write_bytes(b"audio")
    call_id = database.add_call({"id": "one", "started_at": "2026-01-01T00:00:00+00:00", "label": "Dispatch", "audio_file": audio.name, "audio_bytes": 5})
    assert call_id == "one"
    assert database.list_calls(search="Disp")["total"] == 1
    database.update_call("one", {"favorite": True, "tags": ["important"], "note": "test"})
    assert database.get_call("one")["favorite"] is True
    assert database.trash_calls(["one"]) == 1
    assert not audio.exists()
    assert (app_paths.trash / "call.mp3").exists()
    assert database.restore_calls(["one"]) == 1
    assert audio.exists()
    database.trash_calls(["one"])
    assert database.purge_calls(["one"]) == 1
    assert database.get_call("one") is None


def test_legacy_source_reference_is_idempotent(app_paths):
    database = Database(app_paths)
    call = {"source_ref": "legacy:1", "started_at": "2026-01-01T00:00:00", "label": "One"}
    database.add_call(call)
    database.add_call(call)
    assert database.list_calls()["total"] == 1


def test_wal_database_keeps_all_concurrent_call_writes(app_paths):
    database = Database(app_paths)

    def insert(number: int) -> None:
        database.add_call({"id": f"call-{number}", "started_at": f"2026-01-01T00:00:{number:02d}", "label": "Concurrent"})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(insert, range(40)))
    assert database.list_calls(limit=100)["total"] == 40
