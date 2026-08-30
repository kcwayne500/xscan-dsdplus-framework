from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from .paths import AppPaths


SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
  id TEXT PRIMARY KEY,
  source_ref TEXT UNIQUE,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  duration_seconds REAL,
  frequency TEXT NOT NULL DEFAULT '',
  mode TEXT NOT NULL DEFAULT '',
  label TEXT NOT NULL DEFAULT '',
  protocol TEXT NOT NULL DEFAULT '',
  ran_nac TEXT NOT NULL DEFAULT '',
  radio_id TEXT NOT NULL DEFAULT '',
  radio_alias TEXT NOT NULL DEFAULT '',
  call_type TEXT NOT NULL DEFAULT '',
  decoder_duration REAL,
  audio_device TEXT NOT NULL DEFAULT '',
  trigger_level REAL,
  stop_reason TEXT NOT NULL DEFAULT '',
  audio_file TEXT NOT NULL DEFAULT '',
  audio_codec TEXT NOT NULL DEFAULT '',
  audio_bytes INTEGER NOT NULL DEFAULT 0,
  raw_fmp_line TEXT NOT NULL DEFAULT '',
  raw_dsd_line TEXT NOT NULL DEFAULT '',
  favorite INTEGER NOT NULL DEFAULT 0,
  tags TEXT NOT NULL DEFAULT '[]',
  note TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT 'active',
  trashed_at TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calls_started_at ON calls(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_calls_frequency ON calls(frequency);
CREATE INDEX IF NOT EXISTS idx_calls_state ON calls(state);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  csrf TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mobile_devices (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  public_key TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_seen_at TEXT,
  revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS mobile_tokens (
  token_hash TEXT PRIMARY KEY,
  device_id TEXT NOT NULL REFERENCES mobile_devices(id) ON DELETE CASCADE,
  scopes TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mobile_tokens_expiry ON mobile_tokens(expires_at);
CREATE TABLE IF NOT EXISTS mobile_nonces (
  device_id TEXT NOT NULL REFERENCES mobile_devices(id) ON DELETE CASCADE,
  nonce TEXT NOT NULL,
  used_at TEXT NOT NULL,
  PRIMARY KEY(device_id, nonce)
);
CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('version', '2');
"""


class Database:
    def __init__(self, paths: AppPaths):
        self.paths = paths
        self._write_lock = threading.RLock()
        self.paths.state.mkdir(parents=True, exist_ok=True)
        self._initialise()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.paths.database, timeout=15, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    def _initialise(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    @staticmethod
    def _normalise_call(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["favorite"] = bool(item.get("favorite"))
        try:
            item["tags"] = json.loads(item.get("tags") or "[]")
        except json.JSONDecodeError:
            item["tags"] = []
        return item

    def add_call(self, call: dict[str, Any]) -> str:
        now = datetime.now(UTC).isoformat()
        call_id = str(call.get("id") or secrets.token_hex(16))
        values = {
            "id": call_id,
            "source_ref": call.get("source_ref"),
            "started_at": call.get("started_at") or now,
            "ended_at": call.get("ended_at"),
            "duration_seconds": call.get("duration_seconds"),
            "frequency": str(call.get("frequency") or ""),
            "mode": str(call.get("mode") or ""),
            "label": str(call.get("label") or "Unknown Channel"),
            "protocol": str(call.get("protocol") or ""),
            "ran_nac": str(call.get("ran_nac") or ""),
            "radio_id": str(call.get("radio_id") or ""),
            "radio_alias": str(call.get("radio_alias") or ""),
            "call_type": str(call.get("call_type") or ""),
            "decoder_duration": call.get("decoder_duration"),
            "audio_device": str(call.get("audio_device") or ""),
            "trigger_level": call.get("trigger_level"),
            "stop_reason": str(call.get("stop_reason") or ""),
            "audio_file": str(call.get("audio_file") or ""),
            "audio_codec": str(call.get("audio_codec") or ""),
            "audio_bytes": int(call.get("audio_bytes") or 0),
            "raw_fmp_line": str(call.get("raw_fmp_line") or call.get("raw_log_line") or ""),
            "raw_dsd_line": str(call.get("raw_dsd_line") or ""),
            "favorite": 1 if call.get("favorite") else 0,
            "tags": json.dumps(call.get("tags") or []),
            "note": str(call.get("note") or ""),
            "state": str(call.get("state") or "active"),
            "trashed_at": call.get("trashed_at"),
            "created_at": now,
        }
        columns = ", ".join(values)
        placeholders = ", ".join(f":{key}" for key in values)
        with self._write_lock, self.connect() as connection:
            connection.execute(f"INSERT OR IGNORE INTO calls ({columns}) VALUES ({placeholders})", values)
        return call_id

    def list_calls(
        self,
        *,
        search: str = "",
        state: str = "active",
        frequency: str = "",
        mode: str = "",
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        clauses = ["state = ?"]
        params: list[Any] = [state]
        if search:
            clauses.append("(label LIKE ? OR frequency LIKE ? OR radio_alias LIKE ? OR radio_id LIKE ? OR note LIKE ?)")
            needle = f"%{search}%"
            params.extend([needle] * 5)
        if frequency:
            clauses.append("frequency LIKE ?")
            params.append(f"%{frequency}%")
        if mode:
            clauses.append("mode LIKE ?")
            params.append(f"%{mode}%")
        where = " AND ".join(clauses)
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        with self.connect() as connection:
            total = connection.execute(f"SELECT COUNT(*) FROM calls WHERE {where}", params).fetchone()[0]
            rows = connection.execute(
                f"SELECT * FROM calls WHERE {where} ORDER BY started_at DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return {"items": [self._normalise_call(row) for row in rows], "total": total, "offset": offset, "limit": limit}

    def get_call(self, call_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
        return self._normalise_call(row) if row else None

    def update_call(self, call_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {"favorite", "tags", "note"}
        values = {key: patch[key] for key in allowed if key in patch}
        if "favorite" in values:
            values["favorite"] = 1 if values["favorite"] else 0
        if "tags" in values:
            values["tags"] = json.dumps([str(tag).strip() for tag in values["tags"] if str(tag).strip()])
        if values:
            assignments = ", ".join(f"{key} = ?" for key in values)
            with self._write_lock, self.connect() as connection:
                connection.execute(f"UPDATE calls SET {assignments} WHERE id = ?", [*values.values(), call_id])
        return self.get_call(call_id)

    def trash_calls(self, call_ids: Iterable[str]) -> int:
        moved = 0
        self.paths.trash.mkdir(parents=True, exist_ok=True)
        with self._write_lock, self.connect() as connection:
            for call_id in call_ids:
                row = connection.execute("SELECT audio_file, state FROM calls WHERE id = ?", (call_id,)).fetchone()
                if not row or row["state"] != "active":
                    continue
                name = Path(row["audio_file"]).name
                source = self.paths.recordings / name
                target = self.paths.trash / name
                if source.is_file():
                    if target.exists():
                        target = self.paths.trash / f"{call_id}-{name}"
                    shutil.move(str(source), str(target))
                    stored_name = target.name
                else:
                    stored_name = name
                connection.execute(
                    "UPDATE calls SET state='trashed', trashed_at=?, audio_file=? WHERE id=?",
                    (datetime.now(UTC).isoformat(), stored_name, call_id),
                )
                moved += 1
        return moved

    def restore_calls(self, call_ids: Iterable[str]) -> int:
        restored = 0
        with self._write_lock, self.connect() as connection:
            for call_id in call_ids:
                row = connection.execute("SELECT audio_file, state FROM calls WHERE id = ?", (call_id,)).fetchone()
                if not row or row["state"] != "trashed":
                    continue
                source = self.paths.trash / Path(row["audio_file"]).name
                name = source.name
                if name.startswith(f"{call_id}-"):
                    name = name[len(call_id) + 1 :]
                target = self.paths.recordings / name
                if source.is_file():
                    if target.exists():
                        stem, suffix = target.stem, target.suffix
                        target = target.with_name(f"{stem}-restored{suffix}")
                    shutil.move(str(source), str(target))
                    name = target.name
                connection.execute(
                    "UPDATE calls SET state='active', trashed_at=NULL, audio_file=? WHERE id=?", (name, call_id)
                )
                restored += 1
        return restored

    def purge_calls(self, call_ids: Iterable[str]) -> int:
        purged = 0
        with self._write_lock, self.connect() as connection:
            for call_id in call_ids:
                row = connection.execute("SELECT audio_file, state FROM calls WHERE id = ?", (call_id,)).fetchone()
                if not row or row["state"] != "trashed":
                    continue
                path = self.paths.trash / Path(row["audio_file"]).name
                if path.is_file():
                    path.unlink()
                connection.execute("DELETE FROM calls WHERE id = ?", (call_id,))
                purged += 1
        return purged

    def create_session(self, hours: int) -> tuple[str, str, str]:
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        csrf = secrets.token_urlsafe(24)
        now = datetime.now(UTC)
        expires = now + timedelta(hours=hours)
        with self._write_lock, self.connect() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at < ?", (now.isoformat(),))
            connection.execute(
                "INSERT INTO sessions(token_hash, csrf, expires_at, created_at) VALUES (?, ?, ?, ?)",
                (token_hash, csrf, expires.isoformat(), now.isoformat()),
            )
        return token, csrf, expires.isoformat()

    def validate_session(self, token: str) -> dict[str, str] | None:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT csrf, expires_at FROM sessions WHERE token_hash=? AND expires_at>?", (token_hash, now)
            ).fetchone()
        return dict(row) if row else None

    def revoke_session(self, token: str) -> None:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with self._write_lock, self.connect() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))

    def register_mobile_device(self, name: str, public_key: str) -> dict[str, Any]:
        device_id = secrets.token_hex(16)
        now = datetime.now(UTC).isoformat()
        with self._write_lock, self.connect() as connection:
            connection.execute(
                "INSERT INTO mobile_devices(id,name,public_key,created_at) VALUES (?,?,?,?)",
                (device_id, name, public_key, now),
            )
        return {"id": device_id, "name": name, "created_at": now, "last_seen_at": None, "revoked_at": None}

    def list_mobile_devices(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id,name,created_at,last_seen_at,revoked_at FROM mobile_devices ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_mobile_device(self, device_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM mobile_devices WHERE id=? AND revoked_at IS NULL", (device_id,)).fetchone()
        return dict(row) if row else None

    def revoke_mobile_device(self, device_id: str) -> bool:
        now = datetime.now(UTC).isoformat()
        with self._write_lock, self.connect() as connection:
            changed = connection.execute(
                "UPDATE mobile_devices SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (now, device_id)
            ).rowcount
            connection.execute("DELETE FROM mobile_tokens WHERE device_id=?", (device_id,))
        return bool(changed)

    def consume_mobile_nonce(self, device_id: str, nonce: str) -> bool:
        now = datetime.now(UTC)
        cutoff = (now - timedelta(minutes=10)).isoformat()
        with self._write_lock, self.connect() as connection:
            connection.execute("DELETE FROM mobile_nonces WHERE used_at<?", (cutoff,))
            try:
                connection.execute(
                    "INSERT INTO mobile_nonces(device_id,nonce,used_at) VALUES (?,?,?)",
                    (device_id, nonce, now.isoformat()),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def create_mobile_token(self, device_id: str, minutes: int = 5) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = datetime.now(UTC)
        expires = now + timedelta(minutes=minutes)
        scopes = "status:read events:read stream:read app:read"
        with self._write_lock, self.connect() as connection:
            connection.execute("DELETE FROM mobile_tokens WHERE expires_at<?", (now.isoformat(),))
            connection.execute(
                "INSERT INTO mobile_tokens(token_hash,device_id,scopes,expires_at,created_at) VALUES (?,?,?,?,?)",
                (token_hash, device_id, scopes, expires.isoformat(), now.isoformat()),
            )
            connection.execute("UPDATE mobile_devices SET last_seen_at=? WHERE id=?", (now.isoformat(), device_id))
        return token, expires.isoformat()

    def validate_mobile_token(self, token: str, scope: str) -> dict[str, str] | None:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            row = connection.execute(
                """SELECT t.device_id,t.scopes,t.expires_at FROM mobile_tokens t
                   JOIN mobile_devices d ON d.id=t.device_id
                   WHERE t.token_hash=? AND t.expires_at>? AND d.revoked_at IS NULL""",
                (token_hash, now),
            ).fetchone()
        if not row or scope not in str(row["scopes"]).split():
            return None
        return dict(row)
