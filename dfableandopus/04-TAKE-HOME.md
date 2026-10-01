# Take-home: follow-up dates on the application tracker

Budget 3 to 4 hours. The repository is `aiapplybot` as it stands. Do not rewrite the architecture; extend it the way the existing code extends.

## The brief

Our users track job applications and keep losing track of when to chase one. Add an optional follow-up date to an application, end to end: validation, persistence, HTTP and the browser form. Then add one read route that answers "what is due".

### Part A: the field

`follow_up_on` is optional and holds a calendar date, not a timestamp. Decide whether absent means SQL `NULL` or an empty string, write the decision down in one sentence, and apply it consistently in the store, the JSON representation and the form. Validate that the value is a real date, so `2025-02-30` and `2025-13-01` are rejected before anything is written; a regular-expression shape check alone does not meet this criterion.

The field is editable, so it belongs in `EDITABLE` (`applybot/application_store.py:16`), in `application_input` (`:44`), in the `create` INSERT and the `update` UPDATE, and in the table with a CHECK constraint in the style of the existing columns.

### Part B: the migration

An existing tracker database must upgrade in place, keeping its rows and its audit history. `__init__` currently accepts `PRAGMA user_version` of 0 or 1 and pins it to 1 (`applybot/application_store.py:127-128`), and `_check_identity` (`applybot/application_store.py:135`) raises a 503 for anything else. Add a version 2. Recreating the database, or dropping and rebuilding the table, fails this assignment.

### Part C: the boundary

Add `GET /api/applications?due_before=YYYY-MM-DD`, filtering the listing to records whose `follow_up_on` is set and not after that date. It composes with `q`, `status` and `page`. The adapter's allowlist of query keys is an exact set at `applybot/dashboard.py:161`; extend it rather than loosening it. Mutation routes must still reject any query string (`applybot/dashboard.py:151`).

### Part D: the client

Add the control to `applybot/web/index.html`, include the field in `editable` so `adopt` (`applybot/web/app.js:212`) populates it and `values()` sends it, and show the date in the library list. A rejected save must leave what the user typed in the date field.

## Acceptance criteria

1. A valid date round-trips through POST, GET, PUT and the list route unchanged.
2. `2025-02-30`, `2025-13-01`, `"2025-01-01T00:00:00Z"` and an integer are each rejected with a 400 and a message naming the field, with no row written.
3. Clearing the field on an existing record persists the cleared state and a later GET agrees with your stated absent-value decision.
4. A stale `PUT` that carries a follow-up date still returns 409 and does not change the stored date.
5. An existing database created by the current code opens after your change, keeps every application row and every audit row, and reports `PRAGMA user_version` of 2.
6. `?due_before=` with an unparseable value returns 400; combined with `q` and `status` it returns only records matching all three.
7. A failed save leaves the typed date in the form.

## What to submit

A branch or patch, plus a `NOTES.md` of at most one page containing: your absent-value decision and why; the migration strategy and what happens if the process dies mid-migration; one example request and response for the new query parameter, including a 400; and anything you chose not to do.

## Tests and commands

Add store cases to `tests/test_store.py` and boundary cases to `tests/test_http.py`, following the existing naming style (`test_stale_update_returns_conflict_and_preserves_current_data`, `test_invalid_input_is_rejected_before_any_insert`). The `draft()` helper at `tests/test_store.py:16` is where the new field's default belongs. For the migration test, build a pre-change schema in a `tempfile.TemporaryDirectory`, insert a row, then open it with the new `ApplicationStore` and assert the row and its audit survived.

Run:

```powershell
./.venv/Scripts/python -m pytest -q
./.venv/Scripts/python -m pytest -q tests/test_store.py tests/test_http.py
```

The environment block in `../astraupskill/README.md` is the setup. Do not run `scripts/verify_browser.py` for this exercise; Chromium is not required.

## Grading rubric

| Criterion | Strong | Weak |
|---|---|---|
| Date validation | Uses `datetime.date.fromisoformat` or equivalent so `2025-02-30` fails; raises `StoreError` with a 400 and a field-named message, in the style of `clean_text` | A regex or `len(value) == 10`; or raises a bare `ValueError` that escapes as a 500 through `applybot/dashboard.py:233` |
| Migration safety | A `user_version` 2 branch inside the existing `BEGIN IMMEDIATE` block, adding the column and bumping the pragma atomically; a test opens a pre-change database and finds every row and audit row | `CREATE TABLE IF NOT EXISTS` untouched so existing databases break at first UPDATE, or the table is dropped and rebuilt |
| Concurrency preserved | The new column sits in the same conditional UPDATE, `rowcount` check unchanged; a test shows a stale PUT carrying a date still returns 409 | The date is written by a second statement, or the version check is bypassed for "just a date" |
| Boundary discipline | The query allowlist stays an exact set; `due_before` is parsed before reaching SQL; mutation routes still reject query strings | The allowlist relaxed to a superset test, or the raw string interpolated into the WHERE clause |
| Null handling | One stated decision, applied in the CHECK constraint, the JSON, the form and the list; a cleared field round-trips | `null` in the database, `""` on the wire, `undefined` in the form, with `??` papering over it |
| Tests | New cases in both test files covering the happy path, each rejection, the stale PUT and the migration; they fail without the change | Only a happy-path test, or body assertions without re-reading persisted state |
| Notes | Names the absent-value decision, the mid-migration failure behaviour, and one thing left out | Restates the brief, or claims behaviour the tests do not show |

## Reviewer's notes

I open the tests first. If the only new test is "post a date, get the date back", the submission is capped at weak however clean the code is; this codebase's style is that failure paths are tested against real persisted state, as in the injected-trigger tests at `tests/test_store.py:81`.

Second, `__init__`. A candidate who leaves `CREATE TABLE IF NOT EXISTS` alone and tests only against a fresh temporary database has shipped a change that destroys every existing tracker at the first edit. This is the most common failure on this assignment.

Third, whether the invalid-date path raises `StoreError` or something else, which is the difference between a 400 the user can act on and a 500 with a request id. Fourth, that the new column is inside the existing conditional UPDATE rather than in a second statement.

I do not care about the CSS. I do care whether `NOTES.md` admits what you skipped: "I did not add the date to the audit payload, so history shows that version 4 happened but not what the date became" reads stronger than a quiet omission.
