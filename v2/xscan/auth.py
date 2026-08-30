from __future__ import annotations

import json
import threading
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from .database import Database
from .paths import AppPaths


class RateLimited(RuntimeError):
    pass


class AuthManager:
    def __init__(self, paths: AppPaths, database: Database):
        self.paths = paths
        self.database = database
        self.hasher = PasswordHasher()
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    @property
    def is_configured(self) -> bool:
        return bool(self._load().get("password_hash"))

    def setup(self, password: str) -> None:
        if self.is_configured:
            raise RuntimeError("Administrator password is already configured")
        self._validate_password(password)
        self.paths.auth.write_text(
            json.dumps({"password_hash": self.hasher.hash(password), "configured_at": datetime.now(UTC).isoformat()}, indent=2) + "\n",
            encoding="utf-8",
        )

    def verify(self, password: str, client_key: str) -> bool:
        self._check_rate(client_key)
        password_hash = self._load().get("password_hash")
        if not password_hash:
            return False
        try:
            valid = self.hasher.verify(password_hash, password)
            if valid and self.hasher.check_needs_rehash(password_hash):
                data = self._load()
                data["password_hash"] = self.hasher.hash(password)
                self.paths.auth.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            if valid:
                with self._lock:
                    self._attempts.pop(client_key, None)
            return bool(valid)
        except VerifyMismatchError:
            self._record_failure(client_key)
            return False

    def _check_rate(self, client_key: str) -> None:
        now = time.monotonic()
        with self._lock:
            attempts = self._attempts[client_key]
            while attempts and now - attempts[0] > 300:
                attempts.popleft()
            if len(attempts) >= 5:
                raise RateLimited("Too many login attempts; try again later")
    def _record_failure(self, client_key: str) -> None:
        with self._lock:
            self._attempts[client_key].append(time.monotonic())

    def _load(self) -> dict[str, Any]:
        if not self.paths.auth.is_file():
            return {}
        try:
            value = json.loads(self.paths.auth.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _validate_password(password: str) -> None:
        if len(password) < 12:
            raise ValueError("Password must contain at least 12 characters")
        if password.lower() == password or password.upper() == password:
            raise ValueError("Password must include mixed case characters")
        if not any(character.isdigit() for character in password):
            raise ValueError("Password must include a number")
