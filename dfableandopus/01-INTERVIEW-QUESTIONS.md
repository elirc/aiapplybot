# Interview questions on the ApplyBot tracker

Answers cite real locations. Python is the implementation language; where the panel is hiring for Node or .NET, the answer names the equivalent.

## Screening

### S1 (junior). Describe the data model in three sentences.

Three tables, all created in `ApplicationStore.__init__` (`applybot/application_store.py:87`). `applications` holds `id TEXT PRIMARY KEY`, the five editable columns `url, company, title, status, notes`, a `version INTEGER NOT NULL DEFAULT 1 CHECK(version>0)` and `created_at`/`updated_at`. `application_audit` holds `sequence INTEGER PRIMARY KEY AUTOINCREMENT, application_id, action, version, recorded_at`, one row per accepted CREATE, UPDATE, DELETE or IMPORT. `legacy_imports` maps a `receipt TEXT PRIMARY KEY` to the application it produced, so importing the old JSONL twice does not duplicate rows.

### S2 (junior). Which field is identity and which is state?

`id` is a `uuid.uuid4()` string minted once in `create` (`applybot/application_store.py:197`) and never rewritten. `version` is the state marker: it starts at 1 and the `UPDATE` sets `version=version+1`. `test_crud_preserves_identity_and_retains_delete_audit` in `tests/test_store.py:36` asserts exactly that separation: the id and `created_at` survive an edit while `version` becomes 2.

### S3 (junior). Trace a create from the browser to the database.

The submit handler at `applybot/web/app.js:299` captures `base = editor` and `application = values()`, then POSTs to `/api/applications`. `TrackerHandler._handle` checks the Host header, authorises the bearer token, reads a bounded JSON body through `_body` (`applybot/dashboard.py:77`), and calls `store.create`. The store validates, opens `BEGIN IMMEDIATE`, inserts the row plus a `CREATE` audit row, commits, and returns the reloaded row. The adapter replies 201 with `Location: /api/applications/<id>` (`applybot/dashboard.py:181`). Only then does the client call `adopt(result)`.

### S4 (junior). Where is the validation boundary?

`application_input` at `applybot/application_store.py:44`. It rejects any key outside `EDITABLE = {"url","company","title","status","notes"}` with `set(value) - EDITABLE`, trims and length-bounds every string through `clean_text`, parses the URL with `urlsplit` and refuses a non-http(s) scheme, a missing hostname, embedded credentials or whitespace, and requires `status` to be a member of `STATUSES` (`applybot/application_store.py:12`). The HTML `required` attributes and the table CHECK constraints are the other two layers, not the boundary.

### S5 (mid). Why are there three validation layers if one would do?

They defend different callers. HTML `required` only helps someone using the form. `application_input` is the only layer any HTTP caller must pass, so it is the real contract. The CHECK constraints on `applications` (length bounds and the literal status list) defend against a future code path that writes without going through the store, and against a database edited by hand. The strong answer names the cost too: the status list is written twice, in `STATUSES` and in the CHECK clause, and they can drift.

### S6 (mid). What does the HTTP adapter own that the store does not?

Framing and transport policy only: single-Host check (`_host`, `applybot/dashboard.py:50`), constant-time bearer comparison with `secrets.compare_digest` and an Origin check (`_authorize`, `applybot/dashboard.py:62`), `Content-Length` required (411), 64 KiB cap (413), `application/json` required (415), the static allowlist of exactly three files, and response headers including `Cache-Control: no-store` and a CSP. It maps `StoreError.status` to the response code and nothing else. The store never imports `http`.

### S7 (mid). In Node or .NET, where would each of those live?

In Express: the transport checks become middleware (helmet for the headers, `express.json({ limit: "64kb" })` for the cap), `application_input` becomes a Zod schema with `.strict()` so unknown keys fail, and `StoreError` becomes a typed error mapped in one error handler. In ASP.NET Core: model binding plus data annotations or FluentValidation for the DTO, middleware for headers, and an `IExceptionHandler` mapping a domain exception to `Results.Conflict` or `Results.Problem`.

## Deep dive

### D1 (mid). What was the actual bug the improvement fixed?

The original stored history as appended JSONL: no editable identity, no notes, and no concurrency contract, so two editors of the same application silently overwrote each other and the loser lost their typing. The fix is the pair described in `astraupskill/03-WORKED-CHANGE.md`: a version the client must echo, and a client that never replaces its draft with anything but an accepted server response.

### D2 (mid). Walk through `update` line by line.

`applybot/application_store.py:217`. Line 218 is `expected, data = version_input(version), application_input(value)` and it runs *before* `with self.transaction()`. Inside the transaction it calls `self._get(db, identity)`, which raises a 404 `StoreError` if the row is gone. It compares `current["version"] != expected` and raises a 409 with the message "Application changed. Keep your draft and load the current record before retrying." The `UPDATE` sets the five columns, `version=version+1` and `updated_at`, with `WHERE id=? AND version=?`. Then `if changed.rowcount != 1` raises a second 409, "Application changed while saving." (`applybot/application_store.py:241`). Then `_audit(db, identity, "UPDATE", expected + 1)` and a re-read of the row, returned from inside the context manager so the `COMMIT` in `transaction()` still runs before the caller sees it.

### D3 (mid). There is a read-and-compare *and* a conditional WHERE. Is one redundant?

No, and this is the question I would expect. The read-and-compare produces the good error message and distinguishes 404 from 409. The `WHERE id=? AND version=?` plus `rowcount != 1` is the one that is actually correct under concurrency: it is a single atomic statement, so nothing can slip between the check and the write. Under `BEGIN IMMEDIATE` the window is already closed here, but if this moved to Postgres with row locks and a shorter transaction, the conditional write is the part that would still hold.

### D4 (mid). What breaks if I delete the `rowcount` check?

Nothing visible in the current single-writer SQLite path, because `BEGIN IMMEDIATE` serialises writers and the earlier compare already ran. That is precisely why removing it is dangerous: it is a belt that only matters the day the lock model changes. The honest answer is "no current test fails, and that is the argument for keeping it, not removing it." The test that would catch a broader regression is `test_two_concurrent_writers_have_one_winner` (`tests/test_store.py:57`), which runs two `update` calls through a `ThreadPoolExecutor` and asserts exactly one dict result, a 409 among the results, a final version of 2, and exactly two audit rows.

### D5 (mid). Why is validation outside the transaction?

`BEGIN IMMEDIATE` takes SQLite's database-wide write lock. Every microsecond spent parsing, normalising or raising inside that transaction is a microsecond every other writer waits, and a validation failure raised inside would have taken the lock for nothing. `astraupskill/02-CONCEPTS.md` makes this the rule. On Postgres the reason changes (row locks, not a database lock) but the rule survives: a validation error inside a transaction is a wasted round trip on a held pooled connection.

### D6 (mid). How do you know the audit row and the data row cannot diverge?

Because they are in one transaction, and the test proves it by breaking the audit deliberately. `test_audit_failure_rolls_back_update` (`tests/test_store.py:81`) installs `CREATE TRIGGER reject_audit BEFORE INSERT ON application_audit BEGIN SELECT RAISE(ABORT,'injected audit failure'); END`, calls `update`, expects `sqlite3.IntegrityError`, and then asserts the record equals the pre-update dict and the audit still has exactly one row. There are sibling tests for create and delete at `tests/test_store.py:92` and `:101`.

### D7 (mid). A save times out. What does the user see and why is that the right design?

`request` in `applybot/web/app.js:55` aborts after 15000 ms with the message "The request timed out and may already have completed. Refresh current records before retrying." `run` (`applybot/web/app.js:97`) appends "Your editor remains available. For an uncertain new-record save, refresh the library before retrying." That wording is honest because the code cannot know: there are no durable idempotency receipts for new POSTs. `astraupskill/VERIFICATION.md` states that limit, and `astraupskill/05-PRACTICE.md` task 3 is the design that would remove it.

### D8 (mid). Then why is the JSONL import idempotent but POST is not?

The importer has a natural key. `import_history` (`applybot/application_store.py:319`) checks `SELECT 1 FROM legacy_imports WHERE receipt=?` inside the same transaction, skips on a hit, and derives the new id as `uuid.uuid5(uuid.NAMESPACE_URL, "applybot-history:" + record.receipt)` so a re-import is deterministic. Receipts are kept after a delete, which is why `test_deleted_import_is_not_resurrected` (`tests/test_legacy.py:69`) passes. A fresh browser POST has no such key, so the client would have to mint one.

### D9 (mid). The search box takes user text into a LIKE. Why is parameterisation not enough?

Parameterisation protects the query *structure*, not the pattern *semantics*. A user typing `100%` would otherwise get a wildcard. `list` (`applybot/application_store.py:263`) escapes backslash, percent and underscore and then uses `LIKE ? ESCAPE '\'`, which restores literal-substring behaviour. `test_search_treats_sql_wildcards_as_literal_text` (`tests/test_store.py:111`) and its HTTP twin `test_sql_like_search_is_literal_over_http` (`tests/test_http.py:169`) cover it.

### D10 (mid). A SQLite error escapes. What does the client get?

A 503 with the fixed text "The local tracker could not save or read data. Keep your draft and retry after checking the local database." plus the `requestId`, from the `except (sqlite3.Error, OSError)` arm at `applybot/dashboard.py:225`. Driver text never reaches the client; `test_store_failure_has_no_partial_write_or_internal_error_text` (`tests/test_http.py:136`) asserts `b"internal SQL"` is absent from the body and that the record is byte-for-byte unchanged afterwards.

### D11 (mid). Is the bearer token authorisation?

No, and saying so is the point. `TrackerServer.__init__` (`applybot/dashboard.py:23`) generates one `secrets.token_urlsafe(32)` per process and prints it in the URL fragment. It gates the whole loopback server; it does not identify a user and there is no owner column on `applications`. Adding real multi-user access means an owner predicate on every read and write path, which is exercise 4 in `astraupskill/05-PRACTICE.md`.

## Behavioral

### B1 (mid). Tell me about a time you found a correctness problem that was not a crash.

**Situation.** The tracker's predecessor appended each application event to a JSONL file. Nothing crashed; the data was quietly wrong when the same record was open twice. **Task.** Make concurrent edits detectable rather than last-write-wins. **Action.** I added a `version` column, required the client to send the version it loaded in a `{version, application}` wrapper enforced at `applybot/dashboard.py:207`, and made the write conditional on that version. **Result.** `test_two_concurrent_writers_have_one_winner` runs two real threads and asserts one accepted dict, one 409, final version 2, exactly two audit rows.

### B2 (junior). A time you chose the harder testing path.

**Situation.** I claimed the audit row and the data row were atomic. **Task.** Prove it rather than assert it. **Action.** Instead of mocking, the tests install a real `BEFORE INSERT` ABORT trigger on `application_audit` and then inspect persisted state through a fresh connection. **Result.** Three tests (`tests/test_store.py:81`, `:92`, `:101`) each show the version and audit count unchanged after the failure, and the HTTP twin shows a 503 with no driver text leaking.

### B3 (mid). A time you designed for a failure a user would actually hit.

**Situation.** A save can succeed while the following list refresh fails. **Task.** Stop the UI calling that a failed save. **Action.** In `app.js` the submit handler adopts the accepted record and posts "Application saved at version N." *before* `await loadList()`, so a refresh failure raises separately into `run`'s catch. **Result.** `astraupskill/VERIFICATION.md` records this as one of the eight Chromium scenarios, all eight passing: "successful save followed by failed list refresh".

### B4 (mid). A time you left something unfinished on purpose.

**Situation.** New POSTs still have no durable idempotency receipt. **Task.** Decide between shipping a half-idempotency and documenting the gap. **Action.** I wrote the ambiguity into the user-facing timeout text and into `VERIFICATION.md`'s limits paragraph, and specified the real fix as a practice exercise with acceptance criteria (same key and payload return the same identity; a changed payload conflicts; concurrent identical requests create one row). **Result.** No user is told a retry is safe when the code cannot know that.

## Follow-ups the interviewer will ask next

### F1 (mid). You said you use `version`. Would you use a timestamp instead?

No. Two writes inside the same clock tick compare equal, and clocks move backwards. An integer that only the accepted `UPDATE` increments has neither problem. If the panel wants an HTTP-native shape, the same integer becomes a weak `ETag` and the client sends `If-Match`, which is the mapping in `astraupskill/08-TRANSLATION.md`.

### F2 (mid). You return 409 twice in `update`. Should they be the same code?

They are the same code with different messages, and I would keep it. The first ("Application changed. Keep your draft and load the current record before retrying.") is the expected user-facing case. The second ("Application changed while saving.") means the row moved between the read and the write, which under `BEGIN IMMEDIATE` should be unreachable; distinct text makes it findable in a log. If I had metrics I would count them separately.

### F3 (mid). Your `delete` also takes a version. Why?

Because deleting a record another editor just changed destroys work you never saw. `delete` (`applybot/application_store.py:245`) does the same compare plus `DELETE ... WHERE id=? AND version=?` with a `rowcount` check, and writes a `DELETE` audit row at `expected + 1` so history survives the row. `test_stale_delete_does_not_remove_updated_record` (`tests/test_store.py:73`) asserts the 409 and that the accepted edit is still there.

### F4 (mid). Pagination is `LIMIT 20 OFFSET ?`. Defend it.

At this size it is correct and readable, and the count and the page are read inside one `BEGIN` block (`applybot/application_store.py:263` onwards) so the total and the rows agree. It is a deliberate trade: OFFSET scans and discards, so deep pages degrade, and a row inserted between page loads shifts the window. The page cap is 100000. At a scale where that matters I would move to keyset pagination on `(created_at, id)`, which is exactly the index that already exists.

### F5 (junior). Why `uuid4` and not an autoincrementing integer?

The id appears in a URL and in an exported draft, so a sequential integer would leak how many applications exist and make neighbouring records guessable. The cost is index locality, which is irrelevant at this size. The adapter validates the shape with `uuid.UUID(identity)` and turns a bad one into 404 rather than 400 (`applybot/dashboard.py:188`), so a scanner cannot distinguish malformed from missing.

### F6 (mid). How would you move this to Postgres and Express without rewriting the client?

Keep the wire contract: same `{version, application}` wrapper, same 201 with `Location`, same 409. Behind it, `application_input` becomes a Zod schema with `.strict()`, and `update` becomes one `prisma.application.updateMany({ where: { id, version }, data: { ...fields, version: { increment: 1 } } })` where `count === 0` is the 409, wrapped in `prisma.$transaction` with the audit insert. The client's `adopt` rule needs no change at all, which is the argument that the concurrency contract belonged on the wire rather than in the store.
