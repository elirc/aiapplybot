# Flashcards

Cover the right column. Say the answer aloud before uncovering.

| Question | Answer |
|---|---|
| Which frozenset names the five editable fields? | `EDITABLE = frozenset({"url", "company", "title", "status", "notes"})`, `applybot/application_store.py:16` |
| The seven allowed statuses | queued, applied, interview, offer, rejected, abandoned, incomplete (`STATUSES`, `applybot/application_store.py:12`) |
| Which function rejects an unknown key, and with what expression? | `application_input`, via `set(value) - EDITABLE` |
| Status code for a stale PUT | 409 |
| Exact 409 message from the version compare | "Application changed. Keep your draft and load the current record before retrying." |
| Exact 409 message from the rowcount guard | "Application changed while saving." |
| What does `update` do before opening the transaction? | `expected, data = version_input(version), application_input(value)` |
| Why validate outside the transaction here? | `BEGIN IMMEDIATE` holds a database-wide write lock; parsing inside makes every other writer wait, and a rejection would take the lock for nothing |
| The conditional write, exactly | `UPDATE applications SET ... version=version+1,updated_at=? WHERE id=? AND version=?` |
| Which value is the verdict on that write? | `changed.rowcount != 1` |
| Where does `version` start and what is its CHECK? | `version INTEGER NOT NULL DEFAULT 1 CHECK(version>0)` |
| Does `delete` require a version? | Yes; `delete(identity, version)` compares and uses `WHERE id=? AND version=?` |
| What version is written to the audit row on a delete? | `expected + 1` |
| Three tables in the schema | `applications`, `application_audit`, `legacy_imports` |
| Columns of `application_audit` | `sequence` (AUTOINCREMENT PK), `application_id`, `action`, `version`, `recorded_at` |
| The four audit actions | CREATE, UPDATE, DELETE, IMPORT |
| How is a delete's history preserved after the row is gone? | The audit row is written in the same transaction and is not deleted with the record |
| How is the audit atomicity proved? | A test creates `CREATE TRIGGER reject_audit BEFORE INSERT ON application_audit ... RAISE(ABORT,...)`, `tests/test_store.py:81` |
| Test name for the two-writer race | `test_two_concurrent_writers_have_one_winner`, `tests/test_store.py:57` |
| What does that test assert about the final state? | Exactly one dict result, a 409 present, final version 2, exactly 2 audit rows |
| Test name for the stale delete | `test_stale_delete_does_not_remove_updated_record`, `tests/test_store.py:73` |
| Status code returned when a `sqlite3.Error` escapes | 503, from `except (sqlite3.Error, OSError)` at `applybot/dashboard.py:225` |
| What must never appear in that 503 body? | Driver or SQL text; `tests/test_http.py:136` asserts `b"internal SQL"` is absent |
| Exact request-body key set required by a PUT | `{"version", "application"}`, compared with `set(value) != expected` |
| Key set required by a DELETE body | `{"version"}` only |
| Status for `Content-Type: text/plain` on a POST | 415 |
| Status for a missing or non-numeric `Content-Length` | 411 |
| Status for a body over 64 KiB | 413 (`MAX_BODY = 65536`) |
| Status for `GET /api/applications/not-a-uuid` | 404, not 400: `uuid.UUID(identity)` failure maps to "Application not found." |
| Status for a query string on a POST or PUT | 400, "Mutation routes do not accept query parameters." |
| Allowed query keys on the list route | `q`, `status`, `page`, exactly once each |
| Which header does a 201 carry, and with what value? | `Location: /api/applications/<id>` |
| Which three static paths are served, and before what check? | `/`, `/app.js`, `/styles.css`, served before `_authorize` runs |
| How is the bearer token compared? | `secrets.compare_digest` on the bytes after `"Bearer "` |
| Where is the token delivered to the browser? | In the URL fragment, `#token=...`, which is never sent to the server |
| What makes LIKE search literal? | Escaping `\`, `%`, `_` in that order, then `LIKE ? ESCAPE '\'` |
| Why is parameterisation alone insufficient for search? | It protects query structure, not pattern semantics; `100%` would still wildcard |
| Page size and page cap on the list route | 20 rows per page; page must be 1 to 100000 |
| What makes the JSONL import idempotent? | A `receipt` primary key checked inside the transaction, and ids derived by `uuid.uuid5(NAMESPACE_URL, "applybot-history:" + receipt)` |
| Why are receipts kept after a delete? | So a re-import cannot resurrect a deleted record (`test_deleted_import_is_not_resurrected`) |
| Import batch limit | 10,000 records |
| What does `adopt(record)` do to the client state? | Sets `editor = record` and rewrites every form field from the server's response |
| Why does the submit handler capture `base = editor` first? | `adopt` reassigns `editor`; the request must carry the version the user actually edited |
| Client request timeout, and its message | 15000 ms; "The request timed out and may already have completed. Refresh current records before retrying." |
| Why is "saved at version N" shown before `loadList()`? | So a list-refresh failure cannot be reported as a failed save |
| Is the loopback token authorisation? | No: one token per process, no accounts, no owner column |
| Which pragma identifies the database, and to what value? | `PRAGMA application_id` set to `0x41504254`; `PRAGMA user_version=1` |
| What happens when the file is not an ApplyBot database? | `_check_identity` raises a 503, "This file is not an ApplyBot tracker database. Choose a separate tracker path." |
| Why does `backup` use SQLite's backup API rather than a file copy? | To include committed WAL contents; it also creates the target with `O_EXCL` and never overwrites |
| Node equivalent of the conditional UPDATE | `prisma.application.updateMany({ where: { id, version }, data: { ...fields, version: { increment: 1 } } })` with `count === 0` as the 409 |
| .NET equivalent | `[ConcurrencyCheck] int Version`; EF emits the same predicate and throws `DbUpdateConcurrencyException`, mapped to 409 |
| Named limit to mention in an interview | New POSTs have no durable idempotency receipt, so a timed-out create is genuinely ambiguous |
