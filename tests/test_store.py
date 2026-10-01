import concurrent.futures
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from applybot.application_store import (
    ApplicationStore,
    StoreError,
    application_input,
    version_input,
)


def draft(**changes):
    return {
        "url": "https://jobs.example.test/role/1",
        "company": "Example company",
        "title": "Junior engineer",
        "status": "queued",
        "notes": "Practice transactions",
        **changes,
    }


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "tracker.sqlite3"
        self.store = ApplicationStore(self.path)

    def tearDown(self):
        self.temporary.cleanup()

    def test_crud_preserves_identity_and_retains_delete_audit(self):
        created = self.store.create(draft())
        updated = self.store.update(
            created["id"], 1, draft(title="Mid-level engineer", status="interview")
        )
        self.assertEqual(updated["id"], created["id"])
        self.assertEqual(updated["version"], 2)
        self.assertEqual(updated["created_at"], created["created_at"])
        self.assertEqual(
            ApplicationStore(self.path).get(created["id"])["title"],
            "Mid-level engineer",
        )
        self.store.delete(created["id"], 2)
        with self.assertRaises(StoreError) as error:
            self.store.get(created["id"])
        self.assertEqual(error.exception.status, 404)
        self.assertEqual(
            [row["action"] for row in self.store.audit(created["id"])],
            ["DELETE", "UPDATE", "CREATE"],
        )

    def test_two_concurrent_writers_have_one_winner(self):
        created = self.store.create(draft())

        def update(title):
            try:
                return self.store.update(created["id"], 1, draft(title=title))
            except StoreError as error:
                return error.status

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(update, ["First proposal", "Second proposal"]))
        self.assertEqual(sum(isinstance(value, dict) for value in results), 1)
        self.assertIn(409, results)
        self.assertEqual(self.store.get(created["id"])["version"], 2)
        self.assertEqual(len(self.store.audit(created["id"])), 2)

    def test_stale_delete_does_not_remove_updated_record(self):
        created = self.store.create(draft())
        self.store.update(created["id"], 1, draft(notes="Accepted edit"))
        with self.assertRaises(StoreError) as error:
            self.store.delete(created["id"], 1)
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.store.get(created["id"])["notes"], "Accepted edit")

    def test_audit_failure_rolls_back_update(self):
        created = self.store.create(draft())
        with self.store.connection() as db:
            db.execute(
                "CREATE TRIGGER reject_audit BEFORE INSERT ON application_audit BEGIN SELECT RAISE(ABORT,'injected audit failure'); END"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.update(created["id"], 1, draft(title="Must roll back"))
        self.assertEqual(self.store.get(created["id"]), created)
        self.assertEqual(len(self.store.audit(created["id"])), 1)

    def test_audit_failure_rolls_back_create(self):
        with self.store.connection() as db:
            db.execute(
                "CREATE TRIGGER reject_audit BEFORE INSERT ON application_audit BEGIN SELECT RAISE(ABORT,'injected audit failure'); END"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create(draft())
        self.assertEqual(self.store.list()["total"], 0)

    def test_audit_failure_rolls_back_delete(self):
        created = self.store.create(draft())
        with self.store.connection() as db:
            db.execute(
                "CREATE TRIGGER reject_audit BEFORE INSERT ON application_audit BEGIN SELECT RAISE(ABORT,'injected audit failure'); END"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.delete(created["id"], 1)
        self.assertEqual(self.store.get(created["id"]), created)

    def test_search_treats_sql_wildcards_as_literal_text(self):
        matching = self.store.create(draft(title="100%_match"))
        self.store.create(draft(title="100XXmatch"))
        self.assertEqual(
            [row["id"] for row in self.store.list(query="%_")["items"]],
            [matching["id"]],
        )
        self.assertEqual(self.store.list(query="' OR 1=1 --")["total"], 0)

    def test_paging_and_status_filters_cover_each_record_once(self):
        for index in range(23):
            self.store.create(draft(title=f"Edition {index}", status="applied"))
        self.store.create(draft(status="queued"))
        first = self.store.list(status="applied", page=1)
        second = self.store.list(status="applied", page=2)
        self.assertEqual(first["total"], 23)
        self.assertEqual(len(first["items"]), 20)
        self.assertEqual(len(second["items"]), 3)
        self.assertEqual(
            len({row["id"] for row in first["items"] + second["items"]}), 23
        )

    def test_unknown_database_is_preserved(self):
        path = Path(self.temporary.name) / "other.sqlite3"
        with closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE personal_data(value TEXT)")
            db.execute("INSERT INTO personal_data VALUES('keep')")
            db.commit()
        before = path.read_bytes()
        with self.assertRaises(StoreError):
            ApplicationStore(path)
        self.assertEqual(path.read_bytes(), before)

    def test_future_database_version_is_rejected(self):
        with self.store.connection() as db:
            db.execute("PRAGMA user_version=99")
        with self.assertRaises(StoreError):
            ApplicationStore(self.path)
        with self.store.connection() as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 99)

    def test_invalid_input_is_rejected_before_any_insert(self):
        invalid = [
            draft(title=" "),
            draft(company="x" * 121),
            draft(title="line\nbreak"),
            draft(status="sent-maybe"),
            draft(url="javascript:alert(1)"),
            draft(url="https://name:password@example.test"),
            draft(url="https://example.test:bad"),
            draft(url="https://example.test/a b"),
            draft(notes="x" * 5001),
            {**draft(), "version": 99},
        ]
        for value in invalid:
            with self.subTest(value=list(value)):
                with self.assertRaises(StoreError):
                    self.store.create(value)
        self.assertEqual(self.store.list()["total"], 0)

    def test_normalization_does_not_modify_the_input(self):
        value = draft(title="  A role  ", notes="A note\nwith a second line")
        self.assertEqual(application_input(value)["title"], "A role")
        self.assertEqual(value["title"], "  A role  ")

    def test_version_and_list_boundaries(self):
        for value in [True, False, 0, -1, 1.5, "1", 2**53]:
            with self.assertRaises(StoreError):
                version_input(value)
        for options in [
            {"page": 0},
            {"page": True},
            {"page": 100001},
            {"query": "x" * 121},
            {"status": []},
        ]:
            with self.assertRaises(StoreError):
                self.store.list(**options)


if __name__ == "__main__":
    unittest.main()
