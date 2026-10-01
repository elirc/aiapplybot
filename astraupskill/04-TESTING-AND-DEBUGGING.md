# Test guarantees and investigate the failed boundary

Install [development requirements](../requirements-dev.txt), then run `python scripts/verify.py` from the project folder. It parses project Python modules, runs the unittest suite, and writes `.verification/model.json`. Fixtures use temporary files, local sockets, and fictional profiles. They do not load your personal profile, submit applications, or call providers.

[Store tests](../tests/test_store.py) exercise actual SQLite behavior. The concurrent-writer test submits two updates with the same baseline and requires one winner. Audit-failure tests install a trigger that aborts insertion, then inspect the record and history. These assertions establish persisted outcomes rather than checking whether a mocked commit was called. An unrelated-database test verifies that the store refuses another application's database instead of assuming every SQLite file belongs to ApplyBot.

[HTTP tests](../tests/test_http.py) run the real server on an ephemeral loopback port. They check statuses, response headers, access protection, body shape, literal search, conflicts, and database failures. A response request ID helps identify an operation without logging raw SQL or sensitive search text. [Import tests](../tests/test_legacy.py) cover malformed lines, repeated imports, identical occurrences, rollback, and delete-then-reimport behavior.

[Profile and CLI tests](../tests/test_profile_and_cli.py) cover unknown booleans, relative document paths, malformed YAML, bounded structures, standard-library help, backups, and cleanup. The browser-launch and logging-failure tests replace automation boundaries with controlled fakes. They establish resource lifecycle behavior, not compatibility with real career sites or paid model endpoints.

Install Chromium with `python -m playwright install chromium`, then run `python scripts/verify_browser.py`. This drives the actual dashboard against temporary HTTP and SQLite instances. It checks conflicts, draft export, error preservation, deletion, pagination, token lifetime, literal rendering, and layouts. Screenshots go to `astraupskill/images`. An optional `PLAYWRIGHT_CHROMIUM_EXECUTABLE` environment variable selects an already installed executable; normal use can rely on Playwright's matching installation.

When a test fails, narrow the question. If the row changed but the library reports an outage, inspect the save and refresh responses separately. If a stale update succeeds, inspect the version in the request before modifying SQL. If a temporary database cannot be deleted on Windows, check that every connection and server thread closed. Leaving a SQLite transaction context is not the same as explicitly closing its connection.

Write a regression note containing the smallest reproduction, violated guarantee, responsible boundary, and test that would fail without the fix. Prefer assertions about accepted data, retained drafts, and observable status codes over incidental HTML formatting or private helper call counts. Good tests remain useful when the code is reorganized while preserving its contract.

As a debugging exercise, temporarily change the expected version in a synthetic request and predict the result. Then make the audit trigger fail and compare the response. Both operations fail, but for different reasons; a useful UI and test report should preserve that distinction.

## The concurrent-writer case

Here is the store test named above, from `tests/test_store.py:57-71`. Two threads submit the same baseline version; the assertions cover the winner, the 409, the stored version and the audit length together.

```python
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
```
