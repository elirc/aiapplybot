# Progressive practice toward mid-level CRUD work

Use fictional records and a separate practice database. Start each task with a design note and acceptance criteria. Preserve the existing conflict, import, backup, and failed-draft guarantees. Read [solution reasoning](06-SOLUTIONS-AND-REVIEW.md) after making your own decisions.

## 1. Add a follow-up date

Introduce optional `follow_up_on`. Choose whether empty input becomes `null` or an empty string and use that choice consistently. Validate real calendar dates, including leap days, rather than only a string pattern. Add a control, library display, and explicit database migration. Acceptance: valid dates round-trip; impossible dates fail before mutation; stale edits cannot replace a newer date; an existing database upgrades without losing records or audit history. Do not recreate the database to avoid migration work.

## 2. Add status counts

Display counts above the library. Define whether they use search text, status filter, both, or neither, and make the wording clear. Use an aggregate SQL query rather than downloading every row. If counts and rows promise one snapshot, compute them within one read transaction. Acceptance: creation, status change, and deletion update counts; zero values remain clear; a failed count refresh does not relabel a successful save as failed.

## 3. Make create retries safe

Add an idempotency key generated once for a new browser draft. The server records a normalized payload fingerprint and accepted result in the same transaction as the record and audit. Acceptance: the same key and payload return the same identity; a changed payload conflicts; concurrent identical requests create one application. Define behavior after the record is edited or deleted. Legacy import receipts identify old events, so avoid mixing their contract with new HTTP request identity accidentally.

## 4. Design owner-scoped access

Plan two users with private libraries. Identify every path needing an owner predicate: list, get, update, delete, audit, imports, and backups. Explain why a UUID is not permission and why the shared local token cannot distinguish users. Implement a small repository exercise with cross-owner read and write tests before adding a complete login system. Keep the current personal tracker behavior until the new authorization boundary is coherent.

## 5. Add durable reminder intent

Design follow-up reminders that survive process restarts. Save a pending intent transactionally with the application change, then process it using a fake local delivery adapter. Acceptance: adapter failure leaves retryable work; changing the date supersedes old intent; deletion cancels pending work; repeated delivery has a documented deduplication strategy. An outbox does not automatically guarantee exactly-once effects in an arbitrary external service.

For each exercise, deliver a small patch, example request and response, compatibility explanation, and evidence for success and failure. Your reviewer should understand the behavioral guarantee before reading implementation details. More buttons are not the target; justified behavior under competing edits and partial failure is.
