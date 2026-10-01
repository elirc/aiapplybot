"""Real Chromium / HTTP / SQLite checks using only synthetic local records."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from applybot.application_store import ApplicationStore
from applybot.dashboard import TrackerServer
from playwright.sync_api import sync_playwright, expect

TOKEN = "synthetic-browser-check-token-" + "x" * 32
IMAGE_DIR = ROOT / "astraupskill" / "images"


def draft(**changes):
    return {
        "url": "https://jobs.example.test/engineer",
        "company": "Cedar Labs",
        "title": "Software Engineer",
        "status": "queued",
        "notes": "Prepare an example of a difficult bug and how I verified the fix.",
        **changes,
    }


class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        options = {"headless": True}
        if os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"):
            options["executable_path"] = os.environ["PLAYWRIGHT_CHROMIUM_EXECUTABLE"]
        try:
            cls.browser = cls.playwright.chromium.launch(**options)
        except BaseException:
            cls.playwright.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="applybot-browser-")
        self.store = ApplicationStore(Path(self.temp.name) / "applications.sqlite3")
        self.server = TrackerServer(self.store, 0, token=TOKEN)
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True
        )
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.context = self.browser.new_context(
            viewport={"width": 1440, "height": 1100}
        )
        self.page = self.open_page()

    def tearDown(self):
        self.context.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.assertFalse(self.thread.is_alive())
        self.temp.cleanup()

    def open_page(self):
        page = self.context.new_page()
        page.goto(self.base + "/#token=" + TOKEN)
        expect(page.locator("#workspace")).to_be_visible()
        expect(page.locator("#new")).to_be_enabled()
        return page

    def fill(self, page, **changes):
        for key, value in draft(**changes).items():
            target = page.locator(f'#editor [name="{key}"]')
            if key == "status":
                target.select_option(value)
            else:
                target.fill(value)

    def review(self, page, title="Software Engineer"):
        page.locator("#refresh").click()
        page.get_by_role("button", name="Review " + title, exact=True).click()
        expect(page.locator("#save")).to_be_enabled()
        expect(page.locator("#version-label")).to_contain_text("Version")

    def test_crud_token_memory_reload_and_cancelled_delete(self):
        page = self.page
        self.assertNotIn("#", page.url)
        self.assertEqual(
            page.evaluate("[localStorage.length, sessionStorage.length]"), [0, 0]
        )
        page.locator("#new").click()
        self.fill(page)
        page.locator("#save").click()
        expect(page.locator("#notice")).to_contain_text("version 1")
        row = self.store.list()["items"][0]
        page.locator('[name="status"]').select_option("interview")
        page.locator("#save").click()
        expect(page.locator("#notice")).to_contain_text("version 2")
        self.assertEqual(self.store.get(row["id"])["status"], "interview")
        page.reload()
        expect(page.locator("#login")).to_be_visible()
        page.locator("#token").fill(TOKEN)
        page.locator("#login-form button").click()
        self.review(page)
        page.once("dialog", lambda dialog: dialog.dismiss())
        page.locator("#delete").click()
        self.assertEqual(self.store.list()["total"], 1)
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator("#delete").click()
        expect(page.locator("#notice")).to_have_text("Application deleted.")
        self.assertEqual(self.store.list()["total"], 0)
        self.assertEqual(len(self.store.audit(row["id"])), 3)

    def test_two_tabs_preserve_stale_draft_and_export_before_reload(self):
        row = self.store.create(draft())
        self.review(self.page)
        other = self.open_page()
        self.review(other)
        self.page.locator('[name="notes"]').fill("First editor's accepted note")
        self.page.locator("#save").click()
        expect(self.page.locator("#notice")).to_contain_text("version 2")
        other.locator('[name="notes"]').fill("Second editor's unsaved reasoning")
        other.locator("#save").click()
        expect(other.locator("#error")).to_be_visible()
        expect(other.locator('[name="notes"]')).to_have_value(
            "Second editor's unsaved reasoning"
        )
        self.assertEqual(
            self.store.get(row["id"])["notes"], "First editor's accepted note"
        )
        with other.expect_download() as pending:
            other.locator("#export-draft").click()
        data = json.loads(Path(pending.value.path()).read_text(encoding="utf-8"))
        self.assertEqual(data["version"], 1)
        self.assertEqual(
            data["application"]["notes"], "Second editor's unsaved reasoning"
        )
        other.once("dialog", lambda dialog: dialog.accept())
        other.locator("#load-current").click()
        expect(other.locator('[name="notes"]')).to_have_value(
            "First editor's accepted note"
        )

    def test_real_database_failure_keeps_draft_and_rolls_back(self):
        row = self.store.create(draft())
        self.review(self.page)
        with self.store.connection() as db:
            db.execute(
                "CREATE TRIGGER reject_audit BEFORE INSERT ON application_audit BEGIN SELECT RAISE(ABORT,'synthetic internal detail'); END"
            )
        self.page.locator('[name="notes"]').fill("Keep this draft after failure")
        self.page.locator("#save").click()
        expect(self.page.locator("#error")).to_be_visible()
        expect(self.page.locator("#error")).not_to_contain_text(
            "synthetic internal detail"
        )
        expect(self.page.locator('[name="notes"]')).to_have_value(
            "Keep this draft after failure"
        )
        self.assertEqual(self.store.get(row["id"])["version"], 1)
        self.assertEqual(len(self.store.audit(row["id"])), 1)

    def test_literal_html_and_filtered_search(self):
        title = '<img src=x onerror="window.injected=true">'
        self.store.create(draft(title=title, company="100%_ Labs"))
        self.store.create(draft(title="Other role", company="Ordinary company"))
        self.page.locator("#search").fill("100%_")
        expect(self.page.locator("#records article")).to_have_count(1)
        expect(self.page.locator("#records h3")).to_have_text(title)
        self.assertEqual(self.page.locator("#records img").count(), 0)
        self.assertIsNone(self.page.evaluate("window.injected"))
        self.page.locator("#status-filter").select_option("offer")
        expect(self.page.locator("#records article")).to_have_count(0)

    def test_lock_requires_discard_and_removes_record_content(self):
        self.store.create(draft())
        self.review(self.page)
        self.page.locator('[name="notes"]').fill("Private pending note")
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.page.locator("#logout").click()
        expect(self.page.locator("#workspace")).to_be_visible()
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator("#logout").click()
        expect(self.page.locator("#login")).to_be_visible()
        expect(self.page.locator("#records article")).to_have_count(0)
        self.assertEqual(self.page.locator('[name="notes"]').input_value(), "")
        self.assertEqual(self.page.locator("#token").input_value(), "")
        self.assertEqual(self.page.locator("#audit li").count(), 0)
        self.assertIsNone(self.page.locator("#job-link").get_attribute("href"))
        expect(self.page.locator("#editor-heading")).to_have_text("New application")

    def test_deleting_only_last_page_record_clamps_pagination(self):
        for index in range(21):
            self.store.create(draft(title=f"Role {index:02d}"))
        self.page.locator("#refresh").click()
        expect(self.page.locator("#records article")).to_have_count(20)
        self.page.locator("#next").click()
        expect(self.page.locator("#page-label")).to_have_text("Page 2 of 2")
        self.page.locator("#records button").click()
        expect(self.page.locator("#save")).to_be_enabled()
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator("#delete").click()
        expect(self.page.locator("#page-label")).to_have_text("Page 1 of 1")
        expect(self.page.locator("#records article")).to_have_count(20)
        expect(self.page.locator("#next")).to_be_disabled()

    def test_failed_refresh_keeps_saved_notice_and_previous_list(self):
        self.store.create(draft())
        self.review(self.page)
        self.page.route(
            "**/api/applications?*",
            lambda route: route.fulfill(
                status=503, json={"error": "Synthetic list outage"}
            ),
        )
        self.page.locator('[name="notes"]').fill("Accepted even though refresh fails")
        self.page.locator("#save").click()
        expect(self.page.locator("#notice")).to_contain_text("version 2")
        expect(self.page.locator("#list-error")).to_have_text("Synthetic list outage")
        expect(self.page.locator("#list-status")).to_contain_text("last loaded list")
        expect(self.page.locator("#draft-state")).to_have_text("Showing saved values")

    def test_desktop_and_narrow_layout(self):
        self.store.create(draft(status="interview"))
        self.store.create(
            draft(
                company="Maple Studio",
                title="Junior Web Developer",
                status="applied",
                notes="Sent portfolio. Follow up next Tuesday.",
            )
        )
        self.store.create(
            draft(
                company="Northstar Tools",
                title="Backend Engineer",
                notes="Review SQL transactions and prepare a concurrency example.",
            )
        )
        self.review(self.page)
        expect(self.page.locator("#audit li")).to_have_count(1)
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        for name, width, height in [("desktop", 1440, 1200), ("narrow", 390, 1000)]:
            self.page.set_viewport_size({"width": width, "height": height})
            self.assertTrue(
                self.page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            )
            self.page.screenshot(path=str(IMAGE_DIR / f"{name}.png"), full_page=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", default=str(ROOT / ".verification" / "browser.json")
    )
    args = parser.parse_args()
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(BrowserTests)
    )
    report = {
        "passed": result.wasSuccessful(),
        "tests": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "browser": "Chromium",
        "syntheticLocalDataOnly": True,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    raise SystemExit(0 if result.wasSuccessful() else 1)
