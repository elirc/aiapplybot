import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from applybot.application_store import ApplicationStore, StoreError
from applybot.legacy_history import parse_legacy_history


def legacy(**changes):
    return {
        "timestamp": "2026-09-09T12:00:00+00:00",
        "url": "https://jobs.example.test/1",
        "company": "Example",
        "title": "Engineer",
        "status": "applied",
        **changes,
    }


class LegacyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "history.jsonl"
        self.store = ApplicationStore(Path(self.temp.name) / "tracker.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def write(self, values):
        self.path.write_text(
            "\n".join(json.dumps(value) for value in values) + "\n", encoding="utf-8"
        )
        return parse_legacy_history(self.path)

    def test_import_repeat_and_appended_record_preserve_existing_rows(self):
        records = self.write([legacy(), legacy(title="Second role")])
        original = self.path.read_bytes()
        self.assertEqual(self.store.import_history(records)["created"], 2)
        rows = self.store.list()["items"]
        self.store.update(
            rows[0]["id"],
            1,
            {
                key: ("Reviewed notes" if key == "notes" else rows[0][key])
                for key in ("url", "company", "title", "status", "notes")
            },
        )
        self.assertEqual(self.store.import_history(records)["skipped"], 2)
        appended = self.write(
            [legacy(), legacy(title="Second role"), legacy(title="Third role")]
        )
        self.assertEqual(
            self.store.import_history(appended),
            {"created": 1, "skipped": 2, "total": 3},
        )
        self.assertTrue(
            any(row["notes"] == "Reviewed notes" for row in self.store.list()["items"])
        )
        self.assertTrue(self.path.read_bytes().startswith(original))

    def test_duplicate_legacy_occurrences_are_distinct_and_repeatable(self):
        records = self.write([legacy(), legacy()])
        self.assertNotEqual(records[0].receipt, records[1].receipt)
        self.assertEqual(self.store.import_history(records)["created"], 2)
        self.assertEqual(self.store.import_history(records)["skipped"], 2)

    def test_deleted_import_is_not_resurrected(self):
        records = self.write([legacy()])
        self.store.import_history(records)
        row = self.store.list()["items"][0]
        self.store.delete(row["id"], row["version"])
        self.assertEqual(self.store.import_history(records)["skipped"], 1)
        self.assertEqual(self.store.list()["total"], 0)

    def test_malformed_line_is_reported_without_skipping_or_mutating(self):
        self.path.write_text(json.dumps(legacy()) + "\n{broken\n", encoding="utf-8")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(StoreError, "line 2"):
            parse_legacy_history(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.store.list()["total"], 0)

    def test_invalid_record_shapes_and_timestamps_fail(self):
        for value in [
            [],
            {**legacy(), "extra": 1},
            legacy(timestamp="2026-09-09"),
            legacy(timestamp="not a date"),
            legacy(company=[]),
            legacy(status="probably sent"),
        ]:
            with self.subTest(kind=type(value).__name__):
                with self.assertRaises(StoreError):
                    self.write([value])

    def test_empty_legacy_labels_have_explicit_defaults(self):
        records = self.write([legacy(company="", title="")])
        self.assertEqual(records[0].data["company"], "Unknown company")
        self.assertEqual(records[0].data["title"], "Untitled role")

    def test_audit_failure_rolls_back_entire_import_and_receipts(self):
        records = self.write([legacy(), legacy(title="Second")])
        with self.store.connection() as db:
            db.execute(
                "CREATE TRIGGER reject_import BEFORE INSERT ON application_audit BEGIN SELECT RAISE(ABORT,'injected'); END"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.import_history(records)
        self.assertEqual(self.store.list()["total"], 0)
        self.assertEqual(self.store.imported_receipts(), set())

    def test_invalid_utf8_and_oversized_file_are_rejected(self):
        self.path.write_bytes(b"\xff")
        with self.assertRaisesRegex(StoreError, "UTF-8"):
            parse_legacy_history(self.path)
        self.path.write_bytes(b" " * (10 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(StoreError, "10 MiB"):
            parse_legacy_history(self.path)
