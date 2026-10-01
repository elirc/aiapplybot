import argparse
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from applybot import cli, tracker
from applybot.application_store import ApplicationStore
from applybot.browser import Browser
from applybot.llm import LocalBackend
from applybot.profile import (
    ProfileError,
    load_profile,
    profile_as_yaml,
    validate_profile,
)

ROOT = Path(__file__).resolve().parents[1]


class ProfileTests(unittest.TestCase):
    def test_bad_sections_recursion_and_unbounded_numbers_are_rejected(self):
        cycle = {}
        cycle["self"] = cycle
        for value in [
            [],
            {"personal": []},
            {"documents": {"resume": 12}},
            {"work_authorization": {"require_sponsorship": "false"}},
            {"preferences": {"desired_salary": 100000}},
            {"writing_style": 5},
            {"number": float("nan")},
            {"number": 10**1000},
            cycle,
        ]:
            with self.subTest(kind=type(value).__name__):
                with self.assertRaises(ProfileError):
                    validate_profile(value)

    def test_document_paths_are_relative_to_profile_and_serialization_is_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "resume.txt").write_text("Synthetic resume", encoding="utf-8")
            path = root / "profile.yaml"
            path.write_text(
                "documents:\n  resume: resume.txt\npersonal:\n  first_name: Example\n",
                encoding="utf-8",
            )
            value = load_profile(path)
            self.assertEqual(
                value["documents"]["resume"], str((root / "resume.txt").resolve())
            )
            self.assertEqual(profile_as_yaml(value), profile_as_yaml(value))

    def test_malformed_and_oversized_yaml_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.yaml"
            for raw in [b"[broken", b"\xff", b" " * (256 * 1024 + 1)]:
                path.write_bytes(raw)
                with self.assertRaises(ProfileError):
                    load_profile(path)
                self.assertEqual(path.read_bytes(), raw)

    def test_offline_boolean_answers_keep_unknown_distinct_from_false(self):
        backend = LocalBackend()
        field = {
            "id": "sponsor",
            "type": "radio",
            "label": "Do you require sponsorship?",
            "options": ["Yes", "No"],
        }
        for value, expected in [
            (None, "skip"),
            (True, "Yes"),
            (False, "No"),
            ("false", "skip"),
        ]:
            profile = {"work_authorization": {"require_sponsorship": value}}
            action = backend._plan_field(field, profile)
            self.assertEqual(
                action.action if expected == "skip" else action.value, expected
            )

    def test_offline_consent_and_blank_canned_answers_require_manual_review(self):
        backend = LocalBackend()
        consent = backend._plan_field(
            {
                "id": "consent",
                "type": "checkbox",
                "label": "I certify these answers are accurate",
            },
            {},
        )
        self.assertEqual(consent.action, "skip")
        empty = backend._plan_field(
            {"id": "text", "type": "text", "label": "Custom question"},
            {"canned_answers": {"Custom question": ""}},
        )
        self.assertEqual(empty.action, "skip")

    def test_example_profile_contains_no_default_claims(self):
        example = load_profile(ROOT / "profile.example.yaml")
        self.assertEqual(example["personal"]["email"], "")
        self.assertIsNone(example["work_authorization"]["authorized_to_work_us"])
        self.assertEqual(example["background"]["work_history"], [])


class CliTests(unittest.TestCase):
    def test_help_works_without_site_packages(self):
        result = subprocess.run(
            [sys.executable, "-S", "-m", "applybot", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dashboard", result.stdout)
        self.assertIn("import-history", result.stdout)

    def test_dry_run_does_not_create_a_database_or_data_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "history.jsonl"
            destination = root / "absent"
            source.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-09-09T00:00:00Z",
                        "url": "https://jobs.example.test/1",
                        "company": "Example",
                        "title": "Engineer",
                        "status": "applied",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                cli.cmd_import_history(
                    argparse.Namespace(
                        file=str(source), data_dir=str(destination), dry_run=True
                    )
                )
            self.assertFalse(destination.exists())

    def test_backup_includes_committed_data_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ApplicationStore(root / "source.sqlite3")
            row = store.create(
                {
                    "url": "https://jobs.example.test/1",
                    "company": "Example",
                    "title": "Engineer",
                    "status": "queued",
                    "notes": "Committed WAL data",
                }
            )
            backup = root / "backup.sqlite3"
            store.backup(backup)
            self.assertEqual(ApplicationStore(backup).get(row["id"]), row)
            before = backup.read_bytes()
            with self.assertRaises(FileExistsError):
                store.backup(backup)
            self.assertEqual(backup.read_bytes(), before)

    def test_history_keeps_unimported_legacy_visible_without_duplicates_after_import(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = root / "applications.jsonl"
            database = root / "applications.sqlite3"
            history.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-09-09T00:00:00Z",
                        "url": "https://jobs.example.test/old",
                        "company": "Example",
                        "title": "Old role",
                        "status": "applied",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with patch.object(tracker, "HISTORY_FILE", history), patch.object(
                tracker, "DATABASE_FILE", database
            ):
                tracker.log_application(
                    "https://jobs.example.test/new", "Example", "New role", "applied"
                )
                self.assertEqual(len(tracker.read_history()), 2)
                self.assertEqual(
                    len(tracker.prior_applications("https://jobs.example.test/old")), 1
                )
                with contextlib.redirect_stdout(io.StringIO()):
                    cli.cmd_import_history(
                        argparse.Namespace(
                            file=str(history), data_dir=str(root), dry_run=False
                        )
                    )
                self.assertEqual(len(tracker.read_history()), 2)

    def test_logging_failure_closes_browser_and_does_not_log_a_false_fallback(self):
        browser = Mock()
        browser.page_html_head.return_value = "<html></html>"
        args = argparse.Namespace(
            url="https://jobs.example.test/1",
            profile="synthetic.yaml",
            llm="local",
            model=None,
            headless=True,
            browser_profile="synthetic",
        )
        with patch("applybot.profile.load_profile", return_value={}), patch(
            "applybot.profile.profile_as_yaml", return_value="{}"
        ), patch(
            "applybot.llm.build_backend",
            return_value=SimpleNamespace(name="Synthetic", model="offline"),
        ), patch(
            "applybot.browser.Browser", return_value=browser
        ), patch.object(
            tracker, "prior_applications", return_value=[]
        ), patch.object(
            cli, "_scan_and_fill", return_value=("Example", "Engineer")
        ), patch(
            "builtins.input", return_value="d"
        ), patch.object(
            tracker, "log_application", side_effect=OSError("synthetic write failure")
        ) as log, contextlib.redirect_stdout(
            io.StringIO()
        ):
            with self.assertRaises(OSError):
                cli.cmd_apply(args)
            log.assert_called_once_with(args.url, "Example", "Engineer", "applied")
            browser.close.assert_called_once()

    def test_failed_browser_launch_stops_playwright(self):
        runtime = Mock()
        runtime.chromium.launch_persistent_context.side_effect = RuntimeError(
            "synthetic launch failure"
        )
        with patch("applybot.browser.sync_playwright") as factory:
            factory.return_value.start.return_value = runtime
            with self.assertRaises(RuntimeError):
                Browser(profile_dir="synthetic-unused-profile")
            runtime.stop.assert_called_once()
