# Stories, bullets and the spoken summary

## Story 1: the silent overwrite

**Situation.** ApplyBot recorded job applications by appending events to a JSONL file: no editable identity, no notes, no way for the system to notice the same application was open twice. Nothing errored; the second save simply won.

**Task.** Make competing edits a detectable event with one accepted winner, without asking the user to lock anything.

**Action.** I added a `version` column to `applications`, defaulted to 1 with `CHECK(version>0)`. The edit contract became `{version, application}`, and the adapter rejects any other key set (`applybot/dashboard.py:207`). `ApplicationStore.update` (`applybot/application_store.py:217`) compares the expected version against the loaded row, then writes with `WHERE id=? AND version=?` and `version=version+1`, treating `rowcount != 1` as a second 409. The browser keeps the loaded record as `editor` and sends `base.version`, never a freshly fetched one.

**Result.** `tests/test_store.py:57` runs two `update` calls on one record through a two-worker `ThreadPoolExecutor`: exactly one returns a record, a 409 is among the results, the final version is 2, the audit table has exactly 2 rows. `tests/test_http.py:120` covers the boundary: the loser gets 409 and a later GET still returns the winner's title.

## Story 2: proving atomicity instead of asserting it

**Situation.** Every accepted change writes both a data row and an audit row. I had claimed they could not diverge.

**Task.** Get evidence a reviewer would accept, without mocking the database.

**Action.** The tests create a real `BEFORE INSERT` trigger on `application_audit` that calls `RAISE(ABORT, ...)`, then run each operation and inspect persisted state through a new connection rather than the one under test. I also checked the HTTP behaviour, because a rollback that leaks driver text is still a defect.

**Result.** Three store tests (`tests/test_store.py:81`, `:92`, `:101`) show version and audit count unchanged after a forced failure on update, create and delete. `tests/test_http.py:136` asserts a 503, that the body omits the trigger's `internal SQL` text, and that a GET returns the record byte-for-byte as before.

## Story 3: not lying to the user about an uncertain save

**Situation.** Two adjacent half-truths: a POST that times out may already have committed, and a list refresh failing after a successful save is not a failed save.

**Task.** Make the interface say only what the code can know.

**Action.** `request` in `applybot/web/app.js:55` aborts at 15 seconds saying the request may already have completed and to refresh before retrying; `run` (`applybot/web/app.js:97`) appends the same caution to every caught error. The submit handler adopts the accepted record and shows "Application saved at version N." before awaiting `loadList()`, so a refresh failure surfaces as its own message. I then wrote the missing idempotency receipt into the limits section of the verification document rather than pretending it existed.

**Result.** `astraupskill/VERIFICATION.md` records eight Chromium scenarios, all passing in the implementation review, including the two-tab conflict with draft export and the successful-save-then-failed-refresh case.

## Resume bullets

- Replaced an append-only JSONL history with a versioned SQLite CRUD store, adding optimistic concurrency via a conditional `UPDATE ... WHERE id=? AND version=?`; a two-thread test proves one of two competing writers wins and the final version is 2.
- Wrote every accepted change and its audit row inside one `BEGIN IMMEDIATE` transaction and proved rollback with an injected `RAISE(ABORT)` trigger across create, update and delete: 3 tests, zero partial writes.
- Built a loopback HTTP adapter on the Python standard library with a constant-time bearer check, Host and Origin validation, a 64 KiB body cap, a three-file static allowlist and a no-store CSP; 10 tests cover the rejection paths.
- Made user search literal by escaping `%`, `_` and `\` and using `LIKE ? ESCAPE '\'`, closing a wildcard gap parameterised SQL alone does not cover; verified at the store and HTTP layers.
- Built an idempotent JSONL importer keyed on a per-event receipt with `uuid5`-derived ids, so repeat imports skip and deleted records are not resurrected; 8 tests across repeat, duplicate, malformed and rollback cases.

## Sixty-second spoken summary

"ApplyBot started as a script that filled job forms and appended what it did to a JSONL file. I turned the tracking half into a real CRUD application. The interesting part is not the create-read-update-delete, it is what happens when two tabs touch the same record. Each row carries a version. When you edit, the client sends back the version it loaded, and the update is one conditional statement: set the fields, bump the version, where id and version both match. If no row changed you get a 409, your typing stays on screen, and you reconcile by hand. The data row and its audit row go in one transaction, and I proved the rollback with a trigger that deliberately breaks the audit insert. I was careful about what I did not build: new creates have no idempotency receipt, so a timed-out save is genuinely ambiguous, and the interface says so rather than offering a retry that could duplicate a record."

## What I would do next

The first item is durable idempotency for creates, specified in `astraupskill/05-PRACTICE.md`: the client mints a key once per new draft, the server stores that key with a normalised payload fingerprint and the accepted result in the same transaction as the record and the audit, and the acceptance criteria are that the same key and payload return the same identity, a changed payload conflicts, and concurrent identical requests produce one row. That turns the honest "may already have completed" message into a safe retry.

Second is owner scoping. Today there is one process-wide token and no owner column, so a UUID is unguessable but not a permission. The work is an owner predicate on every path: list, get, update, delete, audit, import and backup, with cross-owner read and write tests written before any login screen exists.
