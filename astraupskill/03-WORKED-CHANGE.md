# Worked change: edit without losing another draft

The original JSONL history recorded events but supplied no editable record identity, notes, or concurrency contract. The improvement introduces a SQLite aggregate and makes the browser wait for accepted writes. Study this as one complete change across database, HTTP, and user interaction.

The editable value stays deliberately small:

```json
{
  "url": "https://jobs.example.test/web-developer",
  "company": "Cedar Labs",
  "title": "Web Developer",
  "status": "interview",
  "notes": "Prepare an example about debugging a failed save."
}
```

On update, the client wraps this in an object containing `version` and `application`. The client cannot put server-generated identity or timestamps inside the editable value. This separates business input from concurrency metadata. [The adapter](../applybot/dashboard.py) requires the exact wrapper keys before passing expected version and application data to the store.

Read `ApplicationStore.update` in [the store](../applybot/application_store.py). It validates before opening the write transaction. Inside, it loads the current row and compares the expected version. The SQL update additionally uses `WHERE id=? AND version=?`, checks that one row changed, inserts the audit event, and reads the accepted result. Returning inside the context manager still runs its exit: commit must finish before the caller receives the result. A failed audit or commit raises instead of returning success.

Now inspect the submit handler in [the browser](../applybot/web/app.js). It captures `base = editor` and the current field values before making the request. The `run` wrapper disables conflicting editor actions and catches failures. Success goes through `adopt`, advancing the shown version and marking the draft saved. Failure leaves the fields available. The user can export them, load the current accepted record with confirmation, and manually reconcile. The browser never silently changes the expected version and retries a stale draft.

Reproduce the regression with two tabs. Load one fictional record in both. Save a new note in the first tab. Enter a different note in the second and save. Verify that the second displays an error, still contains its note, and exports the old baseline version with that note. Load current values only after exporting. The accepted first note should then appear with the newer version.

Next inject an audit failure using the existing test fixture. Observe that neither the row version nor audit count advances. Contrast that with the browser test where the save succeeds but the following list refresh fails: the saved notice stays visible, the accepted editor version advances, and the library reports its separate problem.

Your review should explain each causal link. Stable identity preserves references; captured versions detect stale intent; the transaction keeps data and audit consistent; retaining fields protects unsaved reasoning; explicit reconciliation lets the user choose the final content. A single-tab happy-path demonstration would miss most of this feature's value.
