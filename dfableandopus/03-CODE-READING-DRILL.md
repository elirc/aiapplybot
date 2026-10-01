# Screen-share drills

Twelve minutes each, source closed. Say your reasoning out loud; a silent correct answer scores worse than a narrated one.

## Drill 1: explain this function

`applybot/application_store.py:217-243`, copied verbatim:

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
            changed = db.execute(
                """UPDATE applications SET url=?,company=?,title=?,status=?,notes=?,
                                    version=version+1,updated_at=? WHERE id=? AND version=?""",
                (
                    data["url"], data["company"], data["title"], data["status"],
                    data["notes"], utc_now(), identity, expected,
                ),
            )
            if changed.rowcount != 1:
                raise StoreError("Application changed while saving.", 409)
            self._audit(db, identity, "UPDATE", expected + 1)
            return self._get(db, identity)
```

Questions: why is line 218 outside the `with`? Why does the `UPDATE` repeat the version test that line 221 already performed? Why does the method return from inside the context manager?

<details>
<summary>Answer</summary>

Line 218 parses and normalises before `transaction()` runs `BEGIN IMMEDIATE`, which takes SQLite's database-wide write lock. Validating inside would hold the lock during parsing and, on invalid input, would have taken it for nothing.

The repeat is deliberate. The read-and-compare exists for the error message and to separate 404 (`_get` raises it) from 409. The `WHERE id=? AND version=?` with `rowcount != 1` is the version that is atomic in one statement and therefore survives a move to a weaker lock model. A candidate who calls one of them redundant without naming which guarantee it provides has missed the question.

Returning inside the `with` still runs the generator's resume, so `db.execute("COMMIT")` in `transaction()` (`applybot/application_store.py:166`) executes before the value reaches the caller. If the commit raises, the caller gets the exception, not a success value. `_get` is called again so the returned dict is the committed row including the new `version` and `updated_at`, not a locally reconstructed guess.
</details>

## Drill 2: explain this function

`applybot/web/app.js:299-318`, copied verbatim:

```javascript
form.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!editor) return;
  const base = editor,
    application = values();
  void run(async () => {
    const result = await request(
      "/api/applications" + (base.id ? "/" + base.id : ""),
      {
        method: base.id ? "PUT" : "POST",
        body: JSON.stringify(
          base.id ? { version: base.version, application } : application,
        ),
      },
    );
    adopt(result);
    message("notice", "Application saved at version " + result.version + ".");
    await loadList();
  });
});
```

Questions: why capture `base` into a local instead of reading `editor` inside the async callback? Why does `message(...)` come before `await loadList()`?

<details>
<summary>Answer</summary>

`editor` is module state that `adopt` reassigns (`applybot/web/app.js:212`). Capturing `base` at submit time freezes the baseline the user actually edited, so the request carries the version that was on screen. Reading `editor.version` inside the callback would risk sending a version the user never saw, which is exactly the "fetch the latest version and attach it to an old draft" mistake that defeats the whole scheme.

The ordering makes the two outcomes independent. The save is already committed server-side when `adopt` returns; announcing it first means a failure inside `loadList()` throws into `run`'s catch and produces a separate error message, while the "saved at version N" notice remains true. Reversing the order would let a list-refresh failure present itself as a failed save.
</details>

## Drill 3: spot the bug

This is a mutated copy of `ApplicationStore.delete`. One change. Find it and say what it costs.

```python
    def delete(self, identity: str, version: object) -> dict:
        expected = version_input(version)
        with self.transaction() as db:
            current = self._get(db, identity)
            if current["version"] != expected:
                raise StoreError(
                    "Application changed. Review the current record before deleting.",
                    409,
                )
            removed = db.execute(
                "DELETE FROM applications WHERE id=?",
                (identity,),
            )
            if removed.rowcount != 1:
                raise StoreError("Application changed while deleting.", 409)
            self._audit(db, identity, "DELETE", expected + 1)
            return {"id": identity}
```

<details>
<summary>Answer</summary>

The mutation drops `AND version=?` from the `DELETE` and the matching parameter. Real code at `applybot/application_store.py:255` is `"DELETE FROM applications WHERE id=? AND version=?", (identity, expected)`.

Cost: the `rowcount != 1` guard becomes decorative, because the row exists so exactly one row always deletes. The only remaining protection is the read-and-compare a few lines earlier, which is a check-then-act pair. On SQLite under `BEGIN IMMEDIATE` you would probably never see it fail; port the same code to a database with row-level locking and a shorter transaction and a concurrent update can land between the compare and the delete, destroying an edit the deleter never saw. The regression that should catch it is `test_stale_delete_does_not_remove_updated_record` (`tests/test_store.py:73`) but note that it would still pass, because the compare catches the stale case it exercises. That gap is worth saying out loud.
</details>

## Drill 4: spot the bug

A mutated copy of the search-clause construction in `list`. One change.

```python
        if query:
            escaped = query
            clauses.append(
                "(company LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\' OR notes LIKE ? ESCAPE '\\')"
            )
            parameters.extend(["%" + escaped + "%"] * 3)
```

<details>
<summary>Answer</summary>

The escaping step is gone. Real code at `applybot/application_store.py:263` onwards is `escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")`.

This is not SQL injection: the value is still a bound parameter, so query structure is safe. It is a semantics bug. A user searching for `100%` now matches every record whose company, title or notes contains `100` followed by anything, and a search for `a_b` matches `axb`. The ordering of the three replacements matters too: backslash must be doubled first, or the escape characters introduced by the later replacements would themselves be escaped. `test_search_treats_sql_wildcards_as_literal_text` (`tests/test_store.py:111`) is the failing test, with `test_sql_like_search_is_literal_over_http` (`tests/test_http.py:169`) as the boundary twin.
</details>

## Drill 5: predict the status code

The tracker is running. A record exists at version 3. Give the status code and the reason for each request. Assume a valid bearer token and `Host: 127.0.0.1:8421` unless stated.

1. `POST /api/applications` with `Content-Type: text/plain` and a valid JSON body.
2. `PUT /api/applications/<id>` with body `{"version": 3, "application": {...valid...}, "note": "hi"}`.
3. `PUT /api/applications/<id>` with body `{"version": "3", "application": {...valid...}}`.
4. `GET /api/applications/not-a-uuid`.
5. `GET /api/applications?q=cedar&sort=asc`.
6. `POST /api/applications?draft=1` with a valid body.
7. `GET /` with no `Authorization` header.
8. `GET /api/applications` with `Origin: http://evil.example`.

<details>
<summary>Answer</summary>

1. **415.** `_body` (`applybot/dashboard.py:86`) requires `self.headers.get_content_type() == "application/json"` and raises "Use application/json." before parsing.
2. **400.** The wrapper check at `applybot/dashboard.py:207` is `set(value) != expected`, an exact set comparison, not a superset test. The extra key fails with "Supply the captured version and the editable application for updates."
3. **400.** `version_input` (`applybot/application_store.py:76`) requires an `int` that is not a `bool`; the string never reaches the transaction. Note this happens in the store, not the adapter, and still maps to the default `StoreError` status of 400.
4. **404, not 400.** `uuid.UUID(identity)` raises and the handler converts it to `StoreError("Application not found.", 404)` (`applybot/dashboard.py:188`), so a malformed id is indistinguishable from a missing one.
5. **400.** `set(query) - {"q","status","page"}` is non-empty, "Unknown or repeated query parameter."
6. **400.** `if self.command != "GET" and parsed.query` (`applybot/dashboard.py:151`) rejects any query string on a mutation: "Mutation routes do not accept query parameters."
7. **200.** `/` is in the static allowlist and is served *before* `_authorize` is called (`applybot/dashboard.py:136`). The page loads; every API call from it then fails 401 until the token from the URL fragment is supplied. That ordering is intentional, and noticing it is the point of the question.
8. **403.** `_authorize` rejects a present-but-mismatched Origin with "Cross-origin tracker requests are not allowed."
</details>

## Drill 6: review this diff

A teammate proposes adding a `priority` integer to applications. Find the defects.

```diff
--- a/applybot/application_store.py
+++ b/applybot/application_store.py
@@
-EDITABLE = frozenset({"url", "company", "title", "status", "notes"})
+EDITABLE = frozenset({"url", "company", "title", "status", "notes", "priority"})
@@ def application_input(value: object) -> dict:
     status = value.get("status")
     if not isinstance(status, str) or status not in STATUSES:
         raise StoreError("Choose a supported application status.")
     result["status"] = status
+    result["priority"] = int(value.get("priority", 0))
     return result
@@ def update(self, identity, version, value):
             changed = db.execute(
                 """UPDATE applications SET url=?,company=?,title=?,status=?,notes=?,
-                                    version=version+1,updated_at=? WHERE id=? AND version=?""",
+                                    priority=?,version=version+1,updated_at=? WHERE id=? AND version=?""",
                 (
                     data["url"], data["company"], data["title"], data["status"],
-                    data["notes"], utc_now(), identity, expected,
+                    data["notes"], data["priority"], utc_now(), identity, expected,
                 ),
             )
```

<details>
<summary>Answer</summary>

At least four.

**No migration.** `__init__` uses `CREATE TABLE IF NOT EXISTS`, so an existing database keeps the old six-column table and every `UPDATE` now raises `no such column: priority`. `PRAGMA user_version` is still pinned to 1 (`applybot/application_store.py:127-128`) and `_check_identity` rejects anything but 0 or 1, so the diff needs a version-2 branch with an `ALTER TABLE ... ADD COLUMN priority INTEGER NOT NULL DEFAULT 0` and a bumped `user_version`. `astraupskill/05-PRACTICE.md` exercise 1 makes "do not recreate the database to avoid migration work" an explicit acceptance criterion.

**`int()` is the wrong validator.** `int("7")` succeeds, `int(True)` is 1, and `int("nine")` raises a bare `ValueError` that is not a `StoreError`, so it escapes `_handle`'s `except StoreError` and lands in the generic arm at `applybot/dashboard.py:233` as a 500. Every other field in this function raises `StoreError` with a 400 and a message the user can act on. It also needs a bound; `version_input` shows the house style.

**`create` was not touched.** The `INSERT` at `applybot/application_store.py:197` lists eight columns and does not include `priority`, so a created record silently takes the column default and the field the user typed is dropped. Nothing errors, which makes it the worst defect in the diff.

**No test, and no CHECK constraint.** The table's other columns all carry CHECK clauses; `priority` gets none, so a direct write can store anything. There is no new test asserting the round trip or the rejection of a bad value, and the import path (`import_history`, `applybot/application_store.py:319`) calls `application_input(record.data)` on legacy records that have no `priority` key, which here happens to work via the `0` default but was not considered.
</details>
