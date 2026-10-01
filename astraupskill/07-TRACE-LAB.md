# Trace one accepted update and one rejected update

Start `python -m applybot dashboard --data-dir .\trace-data --port 8422`. Open the private URL and create a fictional Cedar Labs application using a reserved example job URL. Open developer tools and the Network panel. Keep the access token private: it appears in request headers even though the bootstrap fragment is removed from the address bar.

Find the POST to `/api/applications`. Record the five editable fields, response identity, version, timestamps, and Location header. Explain which values come from the user and which come from the server. Change the title and verify that the identity stays unchanged. This demonstrates why references should use stable IDs instead of mutable display names.

Open the same record in another tab. Both should load the current version. In tab A, change notes to “Accepted note from tab A” and save. Inspect the PUT body: expected `version` sits beside `application`, not inside it. The accepted response advances the version. In tab B, enter “Pending note from tab B” and save. Expect HTTP 409 and retained input. Export that draft before loading current values, then open the JSON and identify its old baseline version.

Trace the server path in [the store](../applybot/application_store.py). Put a breakpoint before comparing versions. Describe the database version and expected client version. Follow the exception through the transaction context into [the adapter](../applybot/dashboard.py). Verify that the response contains a public message and request ID rather than raw SQL. The audit should include the accepted update and no event for the rejected stale attempt.

Next create a company called “100% Labs” and search for the percent sign. Explain why LIKE escaping matters even with parameterized SQL. Create enough fictional records for a second page, then delete its only remaining row. The library should return to the last valid page. Inspect the follow-up request and explain why pagination controls are disabled while results are pending.

Finish with a backup rehearsal. Run `python -m applybot backup --data-dir .\trace-data --output .\trace-backup.sqlite3` using a new filename. Copy that backup into a new directory as `applications.sqlite3`, then start a second dashboard on a different port using that directory. Verify the accepted note and audit history. Keep the original trace data intact. Explain why the supplied backup API is preferable to copying only the main database file while committed changes may still be in SQLite's WAL.

Your deliverable is a short narrative with request examples, one conflict screenshot, and a transaction diagram. Include the uncertain case: a browser timeout after POST does not prove rejection, because the server may already have committed. The current UI asks the user to inspect before retrying. The idempotency practice task proposes a stronger future guarantee; distinguish that proposed design from behavior already implemented.

## The breakpoint for the stale PUT

Set your breakpoint on the comparison in `update`; tab B arrives here with the version it loaded, while `current["version"]` has already advanced. From `applybot/application_store.py:217-225`:

```python
    def update(self, identity: str, version: object, value: object) -> dict:
        expected, data = version_input(version), application_input(value)
        with self.transaction() as db:
            current = self._get(db, identity)
            if current["version"] != expected:
                raise StoreError(
                    "Application changed. Keep your draft and load the current record before retrying.",
                    409,
                )
```
