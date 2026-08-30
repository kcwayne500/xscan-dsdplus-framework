from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


FMP_TUNING_RE = re.compile(r"^Tuning to\s+(?P<frequency>[\d.]+)\s+(?P<mode>\S+)\s+(?P<body>.*?)\s*$", re.I)
OPTION_RE = re.compile(r"^(?P<key>BW|DELAY|PL|DPL|RAN|NAC)=(?P<value>\S+)$", re.I)
DSD_EVENT_RE = re.compile(
    r"^(?P<date>\d{4}/\d{2}/\d{2})\s+(?P<time>\d{2}:\d{2}:\d{2})\s+"
    r"Freq=(?P<frequency>[\d.]+)\s+(?P<body>.*)$"
)
RID_RE = re.compile(r"\bRID=(?P<rid>\d+)\s*(?:\[(?P<alias>[^]]*)\])?", re.I)
DURATION_RE = re.compile(r"\s+(?P<duration>\d+)s\s*$", re.I)
RAN_NAC_RE = re.compile(r"\b(?P<key>RAN|NAC)=\s*(?P<value>\S+)", re.I)


def revision_for(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class FmpMetadata:
    frequency: str
    mode: str
    label: str
    options: dict[str, str]
    raw: str

    @property
    def display(self) -> str:
        return f"{self.frequency} {self.label}".strip()


def parse_fmp_line(line: str) -> FmpMetadata | None:
    match = FMP_TUNING_RE.match(line.strip())
    if not match:
        return None
    tokens = match.group("body").split()
    options: dict[str, str] = {}
    label_tokens: list[str] = []
    still_options = True
    for token in tokens:
        option = OPTION_RE.match(token) if still_options else None
        if option:
            options[option.group("key").upper()] = option.group("value")
        else:
            still_options = False
            label_tokens.append(token)
    return FmpMetadata(
        frequency=match.group("frequency"),
        mode=match.group("mode"),
        label=" ".join(label_tokens).strip() or "Unknown Channel",
        options=options,
        raw=line.strip(),
    )


@dataclass(slots=True)
class DsdEvent:
    occurred_at: datetime
    frequency: str
    call_type: str
    radio_id: str
    radio_alias: str
    ran_nac: str
    decoder_duration: float | None
    raw: str


def parse_dsd_event(line: str) -> DsdEvent | None:
    match = DSD_EVENT_RE.match(line.strip())
    if not match:
        return None
    body = match.group("body").strip()
    rid = RID_RE.search(body)
    ran_nac = RAN_NAC_RE.search(body)
    duration = DURATION_RE.search(body)
    call_type = body.split(";")[0].strip()
    if ran_nac:
        call_type = body[ran_nac.end() :].split(";")[0].strip()
    occurred_at = datetime.strptime(f"{match.group('date')} {match.group('time')}", "%Y/%m/%d %H:%M:%S")
    return DsdEvent(
        occurred_at=occurred_at,
        frequency=match.group("frequency"),
        call_type=call_type,
        radio_id=rid.group("rid") if rid else "",
        radio_alias=(rid.group("alias") or "").strip(" .") if rid else "",
        ran_nac=f"{ran_nac.group('key').upper()}={ran_nac.group('value')}" if ran_nac else "",
        decoder_duration=float(duration.group("duration")) if duration else None,
        raw=line.strip(),
    )


@dataclass(slots=True)
class ScanListEntry:
    line_number: int
    group: str
    enabled: bool
    frequency: str
    mode: str
    options: list[str]
    label: str
    raw: str


def parse_scanlist(text: str) -> list[ScanListEntry]:
    entries: list[ScanListEntry] = []
    current_group = "Ungrouped"
    for number, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith(";"):
            heading = stripped.lstrip(";").strip()
            if heading and set(heading) != {"="} and heading.upper() == heading and not re.match(r"^\d", heading):
                current_group = heading
        if stripped.startswith("; ==="):
            continue
        enabled = not stripped.startswith(";")
        candidate = stripped if enabled else stripped[1:].lstrip()
        parts = candidate.split()
        if len(parts) < 3 or not re.fullmatch(r"\d+(?:\.\d+)?", parts[0]):
            continue
        option_tokens: list[str] = []
        label_index = 2
        for index, token in enumerate(parts[2:], 2):
            if "=" in token:
                option_tokens.append(token)
                label_index = index + 1
            else:
                label_index = index
                break
        entries.append(
            ScanListEntry(number, current_group, enabled, parts[0], parts[1], option_tokens, " ".join(parts[label_index:]), raw)
        )
    return entries


def validate_scanlist(text: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for entry in parse_scanlist(text):
        frequency = float(entry.frequency)
        if not 10 <= frequency <= 2000:
            issues.append({"line": entry.line_number, "level": "error", "message": "Frequency is outside 10-2000 MHz"})
        if entry.enabled and not entry.label:
            issues.append({"line": entry.line_number, "level": "warning", "message": "Enabled channel has no label"})
        if entry.enabled and entry.frequency in seen:
            issues.append(
                {"line": entry.line_number, "level": "warning", "message": f"Duplicate enabled frequency (first on line {seen[entry.frequency]})"}
            )
        elif entry.enabled:
            seen[entry.frequency] = entry.line_number
    return issues


DSD_SCHEMAS: dict[str, list[str]] = {
    "DSDPlus.frequencies": ["protocol", "network_id", "site", "channel", "tx_frequency", "rx_frequency", "sort_order"],
    "DSDPlus.networks": ["protocol", "network_id", "name"],
    "DSDPlus.sites": ["protocol", "network_id", "site", "name"],
    "DSDPlus.groups": ["protocol", "network_id", "group", "priority", "override", "hits", "timestamp", "alias"],
    "DSDPlus.radios": ["protocol", "network_id", "group", "radio", "priority", "override", "hits", "timestamp", "alias"],
    "DSDPlus.siteLoader": ["protocol", "network_id", "site", "name"],
}


def parse_dsd_records(name: str, text: str) -> list[dict[str, Any]]:
    schema = DSD_SCHEMAS.get(name, [])
    records: list[dict[str, Any]] = []
    for line_number, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith(";") or stripped.startswith("-"):
            continue
        try:
            values = next(csv.reader([raw], skipinitialspace=True))
        except csv.Error:
            continue
        if len(values) < 2:
            continue
        fields = {schema[index] if index < len(schema) else f"extra_{index + 1}": value.strip() for index, value in enumerate(values)}
        records.append({"line_number": line_number, "raw": raw, "fields": fields})
    return records


def validate_dsd_document(name: str, text: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    try:
        text.encode("ascii")
    except UnicodeEncodeError as exc:
        issues.append({"line": text[: exc.start].count("\n") + 1, "level": "error", "message": "DSDPlus files must use ASCII characters"})
    minimum = len(DSD_SCHEMAS.get(name, []))
    for record in parse_dsd_records(name, text):
        count = len(record["fields"])
        if minimum and count < minimum:
            issues.append(
                {"line": record["line_number"], "level": "error", "message": f"Expected at least {minimum} comma-separated fields, found {count}"}
            )
    return issues


def serialise(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return value
