# Read from the boundaries inward

[The CLI](../applybot/cli.py) selects `dashboard`, `history`, `import-history`, `backup`, or the existing `apply` workflow. Browser and model imports are delayed until the automation command needs them. This makes help and tracker commands usable without installing provider SDKs. The dashboard uses Python's standard library; profile and automation regression tests additionally use the development requirements.

The tracker has three main layers. [The HTTP adapter](../applybot/dashboard.py) handles framing, access-token and origin checks, status codes, bounded JSON, and a static-file allowlist. [The store](../applybot/application_store.py) owns application validation, SQLite transactions, stable IDs, versions, audit events, queries, import receipts, and backups. [The browser script](../applybot/web/app.js) owns editable drafts, pending actions, list requests, and visible messages. HTML and CSS supply the form and responsive layout without a framework or build step.

Trace creation through those layers. The browser reads five editable fields and posts JSON. The adapter bounds and parses the body, then calls `ApplicationStore.create`. The store normalizes the values, generates a UUID, begins a transaction, inserts the application and audit event, and commits. Only then does the adapter return the accepted record. The browser's `adopt` function replaces the draft with that result. A following library refresh can fail independently; its message must not claim that the already accepted save failed.

[Legacy history parsing](../applybot/legacy_history.py) is a separate boundary. It validates bounded JSONL records and creates deterministic receipts. The store imports those records atomically. [The compatibility tracker](../applybot/tracker.py) combines current SQLite entries with unimported legacy entries for CLI history and prior-application warnings. This explains why an old entry may appear in `history` before it appears in the dashboard. Import is explicit and leaves the original file untouched.

The automation path still uses [profile loading](../applybot/profile.py), [planner adapters](../applybot/llm.py), and [browser lifecycle code](../applybot/browser.py). It is not exposed as a dashboard route. The static server serves only its three known assets and cannot browse the project directory. Personal YAML, browser sessions, and model calls are outside the dashboard's operation.

For a source-reading exercise, draw both the success and failure arrows for each operation. Invalid status fails before a transaction. A failed audit insert rolls the transaction back. A stale version becomes a conflict response. A network timeout may occur after a create committed, leaving the client uncertain. Label each arrow with the test that demonstrates it. Then explain why the same generic retry button would be inappropriate for all four cases.

Use the map to locate changes before writing them. A new editable field affects domain validation, SQL, HTTP representation, browser controls, compatibility, and tests. A new display label may affect only HTML. Scope follows behavior, not the number of files you initially expect to edit.

## Out of scope for this course

`applybot/browser.py`, `llm.py`, `ats.py` and `tracker.py` (the Playwright automation, the LLM adapter, the ATS detectors and the legacy tracker) are not part of this course. The four modules to read are `application_store.py`, `web/app.js`, `web/` route wiring in `dashboard.py`, and `cli.py` only where it calls the store. Ignore the rest until the practice exercises send you there.
