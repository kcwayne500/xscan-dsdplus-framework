from __future__ import annotations

import csv
import difflib
import io
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .parsers import (
    DSD_SCHEMAS,
    parse_dsd_records,
    parse_scanlist,
    revision_for,
    serialise,
    validate_dsd_document,
    validate_scanlist,
)
from .paths import AppPaths


class RevisionConflict(RuntimeError):
    pass


class ConfigValidationError(ValueError):
    def __init__(self, issues: list[dict[str, Any]]):
        super().__init__("Configuration validation failed")
        self.issues = issues


class ConfigManager:
    WHITELIST = {
        "scanlist": "FMP24.ScanList",
        "frequencies": "DSDPlus.frequencies",
        "networks": "DSDPlus.networks",
        "sites": "DSDPlus.sites",
        "groups": "DSDPlus.groups",
        "radios": "DSDPlus.radios",
        "site-loader": "DSDPlus.siteLoader",
    }

    def __init__(self, paths: AppPaths):
        self.paths = paths

    def resolve(self, key: str) -> Path:
        name = self.WHITELIST.get(key)
        if not name:
            raise KeyError(key)
        return self.paths.dsdplus / name

    def read(self, key: str) -> dict[str, Any]:
        path = self.resolve(key)
        text = path.read_text(encoding="utf-8", errors="strict") if path.exists() else ""
        name = path.name
        if key == "scanlist":
            entries = [serialise(entry) for entry in parse_scanlist(text)]
            records: list[dict[str, Any]] = []
            issues = validate_scanlist(text)
        else:
            entries = []
            records = parse_dsd_records(name, text)
            issues = validate_dsd_document(name, text)
        return {
            "key": key,
            "name": name,
            "path": str(path),
            "revision": revision_for(text),
            "text": text,
            "entries": entries,
            "records": records,
            "schema": DSD_SCHEMAS.get(name, []),
            "issues": issues,
        }

    def save_text(self, key: str, text: str, expected_revision: str) -> dict[str, Any]:
        path = self.resolve(key)
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if revision_for(current) != expected_revision:
            raise RevisionConflict("The file changed after it was opened")
        issues = validate_scanlist(text) if key == "scanlist" else validate_dsd_document(path.name, text)
        errors = [issue for issue in issues if issue["level"] == "error"]
        if errors:
            raise ConfigValidationError(issues)
        backup = self._backup(path, current)
        self._atomic_write(path, text)
        result = self.read(key)
        result["backup"] = str(backup)
        result["diff"] = "\n".join(
            difflib.unified_diff(current.splitlines(), text.splitlines(), fromfile=f"{path.name}.before", tofile=path.name, lineterm="")
        )
        return result

    def patch_lines(self, key: str, patches: list[dict[str, Any]], expected_revision: str) -> dict[str, Any]:
        document = self.read(key)
        if document["revision"] != expected_revision:
            raise RevisionConflict("The file changed after it was opened")
        lines = document["text"].splitlines(keepends=True)
        for patch in sorted(patches, key=lambda item: int(item["line_number"])):
            index = int(patch["line_number"]) - 1
            if key == "scanlist" and index >= len(lines):
                if lines and not lines[-1].endswith(("\n", "\r")):
                    lines[-1] += "\n"
                options = " ".join(str(value).strip() for value in patch.get("options", []) if str(value).strip())
                pieces = [str(patch["frequency"]).strip(), str(patch["mode"]).strip(), options, str(patch.get("label", "")).strip()]
                replacement = "  ".join(piece for piece in pieces if piece)
                if not patch.get("enabled", True):
                    replacement = ";" + replacement
                lines.append(replacement + "\n")
                continue
            if not 0 <= index < len(lines):
                raise ValueError(f"Invalid line number {index + 1}")
            ending = "\r\n" if lines[index].endswith("\r\n") else "\n" if lines[index].endswith("\n") else ""
            if key == "scanlist":
                options = " ".join(str(value).strip() for value in patch.get("options", []) if str(value).strip())
                pieces = [str(patch["frequency"]).strip(), str(patch["mode"]).strip(), options, str(patch.get("label", "")).strip()]
                replacement = "  ".join(piece for piece in pieces if piece)
                if not patch.get("enabled", True):
                    replacement = ";" + replacement
            else:
                fields = patch.get("fields") or {}
                output = io.StringIO()
                writer = csv.writer(output, lineterminator="")
                schema = document.get("schema") or []
                ordered = [fields.get(name, "") for name in schema]
                extras = sorted((key for key in fields if key.startswith("extra_")), key=lambda item: int(item.split("_")[1]))
                ordered.extend(fields[key] for key in extras)
                writer.writerow(ordered)
                replacement = output.getvalue()
            lines[index] = replacement + ending
        return self.save_text(key, "".join(lines), expected_revision)

    def backups(self, key: str) -> list[dict[str, Any]]:
        path = self.resolve(key)
        folder = self.paths.backups / path.name
        if not folder.exists():
            return []
        return [
            {"name": item.name, "path": str(item), "bytes": item.stat().st_size, "modified_at": datetime.fromtimestamp(item.stat().st_mtime).isoformat()}
            for item in sorted(folder.glob("*.bak"), key=lambda value: value.stat().st_mtime, reverse=True)
        ]

    def restore(self, key: str, backup_name: str, expected_revision: str) -> dict[str, Any]:
        path = self.resolve(key)
        candidate = (self.paths.backups / path.name / Path(backup_name).name).resolve()
        root = (self.paths.backups / path.name).resolve()
        if candidate.parent != root or not candidate.is_file():
            raise FileNotFoundError(backup_name)
        return self.save_text(key, candidate.read_text(encoding="utf-8"), expected_revision)

    def _backup(self, path: Path, text: str) -> Path:
        folder = self.paths.backups / path.name
        folder.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = folder / f"{timestamp}.bak"
        backup.write_text(text, encoding="utf-8", newline="")
        return backup

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
