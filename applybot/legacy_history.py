"""Validate legacy JSONL without modifying or silently skipping its records."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .application_store import StoreError, application_input

MAX_HISTORY_BYTES = 10 * 1024 * 1024
MAX_HISTORY_RECORDS = 10000


@dataclass(frozen=True)
class LegacyRecord:
    receipt: str
    timestamp: str
    data: dict


def parse_legacy_history(path: str | Path) -> list[LegacyRecord]:
    with Path(path).open("rb") as source:
        raw = source.read(MAX_HISTORY_BYTES + 1)
    if len(raw) > MAX_HISTORY_BYTES:
        raise StoreError(
            "Legacy history exceeds 10 MiB. Split a copy into smaller validated files."
        )
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise StoreError(
            "Legacy history must be UTF-8. The original file was not changed."
        ) from None
    output, occurrences = [], Counter()
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        if len(line.encode("utf-8")) > 32768:
            raise StoreError(f"Legacy history line {number} exceeds 32 KiB.")
        try:
            value = json.loads(line)
            if not isinstance(value, dict) or set(value) != {
                "timestamp",
                "url",
                "company",
                "title",
                "status",
            }:
                raise StoreError("Expected timestamp, url, company, title and status.")
            if not isinstance(value["timestamp"], str):
                raise StoreError("Timestamp must be ISO text with a timezone.")
            timestamp = datetime.fromisoformat(value["timestamp"])
            if timestamp.tzinfo is None or not 1970 <= timestamp.year <= 2100:
                raise StoreError(
                    "Timestamp needs a timezone and a year between 1970 and 2100."
                )
            timestamp_text = timestamp.astimezone(timezone.utc).isoformat(
                timespec="microseconds"
            )
            company, title = value["company"], value["title"]
            if not isinstance(company, str) or not isinstance(title, str):
                raise StoreError(
                    "Company and title must be text, including empty legacy values."
                )
            data = application_input(
                {
                    "url": value["url"],
                    "company": company.strip() or "Unknown company",
                    "title": title.strip() or "Untitled role",
                    "status": value["status"],
                    "notes": "",
                }
            )
            canonical = json.dumps(
                {"timestamp": timestamp_text, **data},
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            occurrences[fingerprint] += 1
            receipt = f"{fingerprint}:{occurrences[fingerprint]}"
            output.append(LegacyRecord(receipt, timestamp_text, data))
            if len(output) > MAX_HISTORY_RECORDS:
                raise StoreError(
                    "History exceeds 10,000 records. Split a copy before importing."
                )
        except (ValueError, TypeError, OverflowError) as error:
            detail = (
                str(error)
                if isinstance(error, StoreError)
                else "Invalid JSON or timestamp."
            )
            raise StoreError(
                f"Legacy history line {number}: {detail} No records were imported."
            ) from None
    return output
