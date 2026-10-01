"""SQLite application-tracking domain. No browser automation or model calls."""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

STATUSES = frozenset(
    {"queued", "applied", "interview", "offer", "rejected", "abandoned", "incomplete"}
)
APPLICATION_ID = 0x41504254
EDITABLE = frozenset({"url", "company", "title", "status", "notes"})


class StoreError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def clean_text(value: object, name: str, maximum: int, *, empty: bool = False) -> str:
    if not isinstance(value, str):
        raise StoreError(f"{name} must be text.")
    result = value.strip()
    if len(result) > maximum or (not empty and not result):
        raise StoreError(
            f"{name} must contain {'0' if empty else '1'}–{maximum} characters."
        )
    if any(
        ord(char) < 32 and not (name == "Notes" and char in "\n\t") for char in result
    ):
        raise StoreError(f"{name} contains unsupported control characters.")
    return result


def application_input(value: object) -> dict:
    if not isinstance(value, dict) or set(value) - EDITABLE:
        raise StoreError("Supply only url, company, title, status and notes.")
    result = {
        "url": clean_text(value.get("url"), "URL", 2048),
        "company": clean_text(value.get("company"), "Company", 120),
        "title": clean_text(value.get("title"), "Title", 160),
        "notes": clean_text(value.get("notes", ""), "Notes", 5000, empty=True),
    }
    try:
        url = urlsplit(result["url"])
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
        ):
            raise ValueError()
        _ = url.port
        if any(char.isspace() for char in result["url"]):
            raise ValueError()
    except ValueError:
        raise StoreError(
            "URL must be an HTTP(S) address without credentials or whitespace."
        ) from None
    status = value.get("status")
    if not isinstance(status, str) or status not in STATUSES:
        raise StoreError("Choose a supported application status.")
    result["status"] = status
    return result


def version_input(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value < 2**53 - 1
    ):
        raise StoreError("Version must be a positive bounded integer.")
    return value


class ApplicationStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            self._check_identity(db)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            try:
                self._check_identity(db)
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, 1):
                    raise StoreError(
                        "Unsupported tracker database version. Preserve a backup before upgrading.",
                        503,
                    )
                db.execute(
                    """CREATE TABLE IF NOT EXISTS applications (
                    id TEXT PRIMARY KEY, url TEXT NOT NULL CHECK(length(url) BETWEEN 1 AND 2048),
                    company TEXT NOT NULL CHECK(length(company) BETWEEN 1 AND 120),
                    title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 160),
                    status TEXT NOT NULL CHECK(status IN ('queued','applied','interview','offer','rejected','abandoned','incomplete')),
                    notes TEXT NOT NULL DEFAULT '' CHECK(length(notes)<=5000),
                    version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"""
                )
                db.execute(
                    "CREATE INDEX IF NOT EXISTS applications_order ON applications(created_at DESC, id)"
                )
                db.execute(
                    """CREATE TABLE IF NOT EXISTS application_audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, application_id TEXT NOT NULL,
                    action TEXT NOT NULL, version INTEGER NOT NULL, recorded_at TEXT NOT NULL)"""
                )
                db.execute(
                    "CREATE INDEX IF NOT EXISTS application_audit_record ON application_audit(application_id,sequence)"
                )
                db.execute(
                    """CREATE TABLE IF NOT EXISTS legacy_imports (
                    receipt TEXT PRIMARY KEY, application_id TEXT NOT NULL, imported_at TEXT NOT NULL)"""
                )
                db.execute(f"PRAGMA application_id={APPLICATION_ID}")
                db.execute("PRAGMA user_version=1")
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    @staticmethod
    def _check_identity(db):
        identity = db.execute("PRAGMA application_id").fetchone()[0]
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if identity == APPLICATION_ID and version != 1:
            raise StoreError(
                "Unsupported tracker database version. Preserve a backup before upgrading.",
                503,
            )
        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        if identity not in (0, APPLICATION_ID) or (
            identity == 0 and (tables or version != 0)
        ):
            raise StoreError(
                "This file is not an ApplyBot tracker database. Choose a separate tracker path.",
                503,
            )

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    @staticmethod
    def _audit(db, identity, action, version):
        db.execute(
            "INSERT INTO application_audit(application_id,action,version,recorded_at) VALUES(?,?,?,?)",
            (identity, action, version, utc_now()),
        )

    @staticmethod
    def _get(db, identity):
        row = db.execute(
            "SELECT * FROM applications WHERE id=?", (identity,)
        ).fetchone()
        if row is None:
            raise StoreError("Application not found.", 404)
        return dict(row)

    def get(self, identity: str) -> dict:
        with self.connection() as db:
            return self._get(db, identity)

    def create(self, value: object) -> dict:
        data = application_input(value)
        identity, timestamp = str(uuid.uuid4()), utc_now()
        with self.transaction() as db:
            db.execute(
                """INSERT INTO applications(id,url,company,title,status,notes,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                (
                    identity,
                    data["url"],
                    data["company"],
                    data["title"],
                    data["status"],
                    data["notes"],
                    timestamp,
                    timestamp,
                ),
            )
            self._audit(db, identity, "CREATE", 1)
            return self._get(db, identity)

    def update(self, identity: str, version: object, value: object) -> dict:
        expected, data = version_input(version), application_input(value)
        with self.transaction() as db:
            current = self._get(db, identity)
            if current["version"] != expected:
                raise StoreError(
                    "Application changed. Keep your draft and load the current record before retrying.",
                    409,
                )
            changed = db.execute(
                """UPDATE applications SET url=?,company=?,title=?,status=?,notes=?,
                                    version=version+1,updated_at=? WHERE id=? AND version=?""",
                (
                    data["url"],
                    data["company"],
                    data["title"],
                    data["status"],
                    data["notes"],
                    utc_now(),
                    identity,
                    expected,
                ),
            )
            if changed.rowcount != 1:
                raise StoreError("Application changed while saving.", 409)
            self._audit(db, identity, "UPDATE", expected + 1)
            return self._get(db, identity)

    def delete(self, identity: str, version: object) -> dict:
        expected = version_input(version)
        with self.transaction() as db:
            current = self._get(db, identity)
            if current["version"] != expected:
                raise StoreError(
                    "Application changed. Review the current record before deleting.",
                    409,
                )
            removed = db.execute(
                "DELETE FROM applications WHERE id=? AND version=?",
                (identity, expected),
            )
            if removed.rowcount != 1:
                raise StoreError("Application changed while deleting.", 409)
            self._audit(db, identity, "DELETE", expected + 1)
            return {"id": identity}

    def list(self, *, query: str = "", status: str = "", page: int = 1) -> dict:
        query = clean_text(query, "Search", 120, empty=True)
        if not isinstance(status, str) or (status and status not in STATUSES):
            raise StoreError("Unknown application status.")
        if (
            isinstance(page, bool)
            or not isinstance(page, int)
            or not 1 <= page <= 100000
        ):
            raise StoreError("Page must be between 1 and 100000.")
        clauses, parameters = [], []
        if query:
            escaped = (
                query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            clauses.append(
                "(company LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\' OR notes LIKE ? ESCAPE '\\')"
            )
            parameters.extend(["%" + escaped + "%"] * 3)
        if status:
            clauses.append("status=?")
            parameters.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connection() as db:
            db.execute("BEGIN")
            try:
                total = db.execute(
                    "SELECT count(*) FROM applications" + where, parameters
                ).fetchone()[0]
                rows = db.execute(
                    "SELECT * FROM applications"
                    + where
                    + " ORDER BY created_at DESC,id LIMIT 20 OFFSET ?",
                    [*parameters, (page - 1) * 20],
                ).fetchall()
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return {
            "items": [dict(row) for row in rows],
            "total": total,
            "page": page,
            "pageSize": 20,
        }

    def audit(self, identity: str) -> list[dict]:
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM application_audit WHERE application_id=? ORDER BY sequence DESC LIMIT 30",
                    (identity,),
                )
            ]

    def import_history(self, records) -> dict:
        """Import a validated legacy batch atomically; retained receipts prevent resurrection."""
        from .legacy_history import LegacyRecord

        validated = []
        if len(records) > 10000:
            raise StoreError("Import is limited to 10,000 records.")
        for record in records:
            if not isinstance(record, LegacyRecord):
                raise StoreError("Import requires validated legacy records.")
            validated.append((record, application_input(record.data)))
        created = skipped = 0
        with self.transaction() as db:
            for record, data in validated:
                if db.execute(
                    "SELECT 1 FROM legacy_imports WHERE receipt=?", (record.receipt,)
                ).fetchone():
                    skipped += 1
                    continue
                identity = str(
                    uuid.uuid5(uuid.NAMESPACE_URL, "applybot-history:" + record.receipt)
                )
                db.execute(
                    """INSERT INTO applications(id,url,company,title,status,notes,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        identity,
                        data["url"],
                        data["company"],
                        data["title"],
                        data["status"],
                        data["notes"],
                        record.timestamp,
                        record.timestamp,
                    ),
                )
                self._audit(db, identity, "IMPORT", 1)
                db.execute(
                    "INSERT INTO legacy_imports(receipt,application_id,imported_at) VALUES(?,?,?)",
                    (record.receipt, identity, utc_now()),
                )
                created += 1
        return {"created": created, "skipped": skipped, "total": len(records)}

    def imported_receipts(self) -> set[str]:
        with self.connection() as db:
            return {row[0] for row in db.execute("SELECT receipt FROM legacy_imports")}

    def find_by_url(self, url: str, status: str = "applied") -> list[dict]:
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM applications WHERE url=? AND status=? ORDER BY created_at DESC,id LIMIT 20",
                    (url, status),
                )
            ]

    def backup(self, destination: str | Path) -> None:
        """Use SQLite backup so committed WAL contents are included."""
        import os

        target = Path(destination).resolve()
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        try:
            with self.connection() as source:
                backup = sqlite3.connect(target)
                try:
                    source.backup(backup)
                finally:
                    backup.close()
        except BaseException:
            target.unlink(
                missing_ok=True
            )  # This call exclusively created this new file.
            raise
