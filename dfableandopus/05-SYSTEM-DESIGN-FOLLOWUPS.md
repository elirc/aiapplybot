# Now scale it

Each answer starts from what ApplyBot does today. Refusing to invent a distributed system where the code has a single loopback process is part of the answer.

## Today's shape

```
  browser (app.js)                 one Python process
  +----------------+        +--------------------------------+
  | editor (base)  |  HTTP  |  TrackerHandler                |
  |  version: N    | -----> |   _host -> _authorize -> _body |
  |  draft fields  |        |                |               |
  +----------------+        |        ApplicationStore        |
          ^                 |   validate  |  BEGIN IMMEDIATE |
          |  adopt(result)  |             v                  |
          +-----------------|  applications + application_audit
                            |         (one SQLite file, WAL)  |
                            +--------------------------------+
   token: one per process, printed in the URL fragment
   no owner column anywhere
```

## 1. Two people, not two tabs. How do you make this multi-tenant?

Today there is no tenancy at all: `TrackerServer.__init__` (`applybot/dashboard.py:23`) mints one `secrets.token_urlsafe(32)` per process and `_authorize` compares every request against it. A UUID id is unguessable but it is not a permission.

The change is an `owner_id` column on all three tables and an owner predicate in every store method: `list`, `get`, `update`, `delete`, `audit`, `import_history`, `find_by_url`, `backup`. The trade-off: an explicit WHERE-clause predicate is greppable but relies on every future method remembering it, while a row-level-security policy (Postgres) or a global query filter (EF Core) cannot be forgotten yet hides the rule from the reader. At this size I would take the explicit predicate plus a test that enumerates the store's public methods and fails when a new one appears without a cross-owner test.

Note the ordering trap in `_handle`: static assets are served before `_authorize` runs (`applybot/dashboard.py:136`). That is fine for three fixed files and wrong the moment any per-user asset is served from the same branch.

## 2. A million applications. What happens to the list route?

`list` (`applybot/application_store.py:263`) does `SELECT count(*)` and `SELECT ... ORDER BY created_at DESC,id LIMIT 20 OFFSET ?` inside one `BEGIN`, with a page cap of 100000. Two things break at a million rows. The unfiltered `count(*)` scans the table on every page load. And `OFFSET 19980` makes SQLite produce and discard 19980 rows.

Keyset pagination fixes the second at no cost, because the index that already exists, `applications_order ON applications(created_at DESC, id)` (`applybot/application_store.py:113`), is exactly the composite key needed: `WHERE (created_at, id) < (?, ?) ORDER BY created_at DESC, id LIMIT 20`. The trade is losing "jump to page 40"; you get next and previous only. For the count the options are an approximate total, a cached total invalidated on write, or no total at all. The current code buys a consistent count-and-page snapshot with a full scan, and at a million rows you give up either the consistency or the exact number.

The `q` search is the harder half. `company LIKE '%text%'` cannot use any index at all, so search is a full scan regardless of pagination. That is where SQLite FTS5, or Postgres `tsvector` with a GIN index, or a trigram index earns its complexity.

## 3. Concurrent writers at scale: does the version scheme survive?

The contract survives; the lock model does not. `transaction()` issues `BEGIN IMMEDIATE`, which takes a database-wide write lock, so two writers touching unrelated applications still serialise, with a 5-second `busy_timeout` (`applybot/application_store.py:155`) before one fails. That is correct and cheap for one user and unacceptable for a hundred.

On Postgres the same `UPDATE ... WHERE id=? AND version=?` with `rowcount != 1` becomes genuinely optimistic: writers to different rows never block each other, and the conditional statement is what makes writers to the *same* row safe. Retry policy is the new decision: a 409 here goes to the human because a note is content that cannot be merged automatically. For a status-only change you could retry server-side, but then you must define what the user's intent was, and the current design refuses to guess.

## 4. Background jobs: follow-up reminders that survive a restart

There is no job runner today; every write is synchronous inside the request. The design that fits this codebase is the transactional outbox, because the transaction already exists: write the reminder intent in the same `BEGIN IMMEDIATE` as the application change and its audit row, exactly as `import_history` (`applybot/application_store.py:319`) already writes a row and its receipt together. A separate worker claims pending intents and calls a delivery adapter.

The trade-off, and the thing an interviewer wants you to say: an outbox gives you at-least-once delivery, not exactly-once. Deduplication has to live at the destination or in a delivery-receipt table. The acceptance criteria in `astraupskill/05-PRACTICE.md` exercise 5 state it as: adapter failure leaves retryable work, changing the date supersedes old intent, deletion cancels pending work, and repeated delivery has a documented deduplication strategy.

## 5. Idempotency: how do you make a retried create safe?

Today you cannot, and the code says so. `request` in `applybot/web/app.js:55` aborts at 15 seconds with "The request timed out and may already have completed", and `VERIFICATION.md` lists the absence of durable idempotency receipts as a known limit.

The mechanism already exists one layer over. The legacy importer is idempotent because each event has a `receipt` that is the primary key of `legacy_imports`, checked inside the transaction, with the record id derived as `uuid.uuid5(uuid.NAMESPACE_URL, "applybot-history:" + record.receipt)` so a repeat is deterministic. Generalise it: the client mints a key per new draft, sends it as a header, and the server stores key, a normalised payload fingerprint and the accepted response in the same transaction as the record. Same key and payload replay the stored response; same key, different payload is a 409. The trade-off is retention: those keys grow forever unless aged out, and the expiry window is the honest maximum retry window you can promise.

Do not merge the two contracts. Import receipts identify a historical event; request keys identify a client attempt. Mixing them means a re-import could return a cached HTTP response.

## 6. Auth: from one process token to real accounts

The current token gates a process, not a person. Moving to accounts changes three things at once and they must move together: identity (sessions or short-lived tokens), the owner predicate from question 1, and the audit row, which currently records `application_id, action, version, recorded_at` and would need an actor.

The trade-off: session cookies with `SameSite=Strict` give you revocation and a CSRF story that pairs with the existing Origin check at `applybot/dashboard.py:73`, at the cost of server-side session state. A stateless bearer JWT removes that state and removes revocation with it. For an application whose whole value is a record you can edit and delete, revocation matters more, so cookies.

One existing detail generalises well: the token is delivered in the URL *fragment*, never sent to the server and absent from server logs. That stops being adequate the moment there is more than one user, because a shared link still carries it.

## 7. Observability: what would you add first?

Today `log_message` is overridden to a no-op with the comment "Request URLs can contain search text. Do not log their contents." (`applybot/dashboard.py:46`), and every response carries an `X-Request-Id` that is also echoed in every error body (`applybot/dashboard.py:120`). So the correlation identifier exists and the log does not.

First addition is a structured log line per request: request id, method, route *pattern* (not the path, which carries the id and the search text), status, duration. Second is a counter split by outcome, because the two 409 sites mean different things: "Application changed" at `applybot/application_store.py:223` is a normal user event whose rate tells you how often people edit in two places, while "Application changed while saving." at `:241` should be approximately never and a non-zero rate is a bug signal. Third is the 503 arm at `applybot/dashboard.py:225`, the only place a real infrastructure failure surfaces. The trade-off is privacy: what makes a search bug debuggable is the query text, and that is exactly what the code refuses to record.

## 8. Migrating to Node or .NET: what moves and what does not?

The wire contract does not move, and that is the argument for having put it on the wire. `{version, application}` on a PUT, 201 with `Location: /api/applications/<id>`, 409 with a human message, 400 for an unknown key: a rewritten server that keeps those needs no client change at all, and `applybot/web/app.js` could be dropped onto it unmodified.

What moves: `application_input` becomes a Zod schema with `.strict()` or a C# DTO with validation attributes; `StoreError(message, status)` becomes a typed error handled in one Express error middleware or an `IExceptionHandler`; `transaction()` becomes `prisma.$transaction` or an EF transaction scope. In EF Core the conditional UPDATE is declared as `[ConcurrencyCheck] int Version`, EF emits the same `WHERE ... AND Version = @p`, and a zero row count surfaces as `DbUpdateConcurrencyException`, mapped to 409. That mapping is the one in `astraupskill/08-TRANSLATION.md`.

What you lose is worth naming: with EF's declarative token the check is invisible in the C# source, so a reviewer cannot see the guarantee by reading the method. The Python version is more code and more legible. I would still take the declarative version, with an integration test asserting the 409, because the test is what a maintainer actually reads.
