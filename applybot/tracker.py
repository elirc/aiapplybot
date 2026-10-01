"""SQLite tracking plus visible, explicitly imported legacy JSONL history."""

from __future__ import annotations

from pathlib import Path

from .application_store import ApplicationStore, StoreError
from .legacy_history import parse_legacy_history

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
HISTORY_FILE = DATA_DIR / "applications.jsonl"
DATABASE_FILE = DATA_DIR / "applications.sqlite3"


def log_application(
    url: str, company: str | None, title: str | None, status: str
) -> None:
    ApplicationStore(DATABASE_FILE).create(
        {
            "url": url,
            "company": company or "Unknown company",
            "title": title or "Untitled role",
            "status": status,
            "notes": "",
        }
    )


def _as_history(row: dict) -> dict:
    return {**row, "timestamp": row["created_at"]}


def read_history(limit: int = 1000) -> list[dict]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
        raise StoreError("History limit must be between 1 and 10000.")
    records, imported = [], set()
    if DATABASE_FILE.exists():
        store = ApplicationStore(DATABASE_FILE)
        imported = store.imported_receipts()
        with store.connection() as db:
            records.extend(
                _as_history(dict(row))
                for row in db.execute(
                    "SELECT * FROM applications ORDER BY created_at DESC,id LIMIT ?",
                    (limit,),
                )
            )
    if HISTORY_FILE.exists():
        records.extend(
            {**record.data, "timestamp": record.timestamp, "legacy": True}
            for record in parse_legacy_history(HISTORY_FILE)
            if record.receipt not in imported
        )
    records.sort(key=lambda record: record["timestamp"], reverse=True)
    return records[:limit]


def prior_applications(url: str) -> list[dict]:
    records, imported = [], set()
    if DATABASE_FILE.exists():
        store = ApplicationStore(DATABASE_FILE)
        records.extend(_as_history(row) for row in store.find_by_url(url))
        imported = store.imported_receipts()
    if HISTORY_FILE.exists():
        records.extend(
            {**record.data, "timestamp": record.timestamp}
            for record in parse_legacy_history(HISTORY_FILE)
            if record.receipt not in imported
            and record.data["url"] == url
            and record.data["status"] == "applied"
        )
    return sorted(records, key=lambda record: record["timestamp"])


def print_history(limit: int = 200) -> None:
    records = read_history(limit)
    if not records:
        print("No applications logged yet.")
        return
    print(
        f"Showing up to {limit} most recent records (SQLite plus unimported legacy history)."
    )
    print(f"{'When':<22} {'Status':<12} {'Company':<28} Title / URL")
    print("-" * 100)
    for record in records:
        when = record["timestamp"][:19].replace("T", " ")
        print(
            f"{when:<22} {record['status']:<12} {record['company'][:27]:<28} {record['title'][:60]}"
        )
    if any(record.get("legacy") for record in records):
        print(
            "Legacy rows are visible here. Use import-history to add them to the web dashboard."
        )
    print(f"{len(records)} records shown.")
