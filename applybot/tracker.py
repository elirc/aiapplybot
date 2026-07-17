"""Application history: one JSON line per logged application."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

# Anchored to the project root so history lands in the same place regardless
# of the directory the tool is launched from.
HISTORY_FILE = Path(__file__).resolve().parent.parent / "data" / "applications.jsonl"


def log_application(url: str, company: str | None, title: str | None, status: str) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "url": url,
        "company": company or "",
        "title": title or "",
        "status": status,
    }
    with HISTORY_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    records = []
    with HISTORY_FILE.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


def print_history() -> None:
    records = read_history()
    if not records:
        print("No applications logged yet.")
        return
    print(f"{'When':<22} {'Status':<10} {'Company':<28} Title / URL")
    print("-" * 100)
    for r in records:
        when = r["timestamp"][:19].replace("T", " ")
        label = r["title"] or r["url"]
        print(f"{when:<22} {r['status']:<10} {r['company'][:27]:<28} {label[:60]}")
    counts: dict[str, int] = {}
    for r in records:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    breakdown = ", ".join(f"{n} {status}" for status, n in sorted(counts.items()))
    print(f"\n{len(records)} total ({breakdown})")
