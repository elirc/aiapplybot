# ApplyBot: from append-only history to reliable CRUD

This course uses a personal application tracker to teach the path from a browser draft to an accepted database write. The original project filled job application forms and appended history to JSONL. The improved project adds a local SQLite notebook with create, search, edit, delete, audit history, and conflict handling. You can learn the entire tracker with fictional records, without opening job sites or calling model providers.

Start from the project folder with `python -m applybot dashboard --data-dir .\practice-data`. Open the private URL printed in the terminal. Create a fictional application for Cedar Labs, give it a queued status, and write a specific next action. Save it, change its title, and save again. Notice that the identity stays stable while the version advances. Restarting the server preserves the record but generates a new access token.

## Prerequisites and environment

**Prerequisites:** Python with classes and context managers, SQL against SQLite, HTTP methods and status codes, and running everything inside one virtual environment. No browser automation or model provider is needed for the tracker part of the course.

Nothing below works on a clean machine until this block has run:

```powershell
python -m venv .venv
./.venv/Scripts/python -m pip install -r requirements.txt
./.venv/Scripts/python -m pip install -r requirements-dev.txt
./.venv/Scripts/python -m playwright install chromium
./.venv/Scripts/python -m applybot init
./.venv/Scripts/python -m pytest -q
```

Use `./.venv/Scripts/python -m ...` for every command in this course so the interpreter with the dependencies is the one that runs.

| Term | Meaning here | Where in this project |
|---|---|---|
| version | The per-row counter an edit must match; an accepted write raises it by one. | the `version` column and `WHERE id=? AND version=?` in `ApplicationStore.update`, `applybot/application_store.py` |
| conflict | The refusal a stale edit receives, raised before anything is written. | `current["version"] != expected` in `update` and `delete`, `applybot/application_store.py` |
| affected-row count | The second check that the row really changed, not just that no error was raised. | `changed.rowcount != 1` in `ApplicationStore.update` |
| transaction | One `BEGIN IMMEDIATE` covering the row change and its audit row together. | `ApplicationStore.transaction()` in `applybot/application_store.py` |
| audit row | The durable record of an action and the version it produced. | `_audit` writing `application_audit`, `applybot/application_store.py` |
| schema identity | A stored marker that refuses to open a database this code did not create. | `_check_identity` and `PRAGMA user_version` in `applybot/application_store.py` |
| boundary validation | Input is parsed and normalised before the transaction opens. | `application_input` and `version_input` in `applybot/application_store.py` |
| draft vs record | The editor holds your typing; the library holds what the database accepted. | `editor` and `adopt(record)` in `applybot/web/app.js` |
| uncertain save | A request whose response was lost; refresh before retrying a create. | the retry guidance in `run()`, `applybot/web/app.js` |
| Location header | Where a created record can be read, returned with the 201. | `extra={"Location": "/api/applications/" + result["id"]}` in `applybot/dashboard.py` |
| loopback token | A local access token that gates the dashboard; not user ownership. | `_authorize` and the generated `token` in `applybot/dashboard.py` |
| history import | Adopting the older append-only JSONL records into the tracker. | `import_history` in `applybot/application_store.py`, `applybot/legacy_history.py` |

## Learning sequence

1. Read [the codebase map](01-CODEBASE-MAP.md), then follow a POST in browser developer tools. Identify the request body, response body, and Location header.
2. Study [the concepts](02-CONCEPTS.md). Predict what happens when two tabs edit the same version, then test your prediction.
3. Explain [the worked change](03-WORKED-CHANGE.md) without looking at the source. Connect validation, transactions, versions, and draft preservation.
4. Run [the testing exercises](04-TESTING-AND-DEBUGGING.md), including the deliberately failing audit insert. Observe persisted state after the failure.
5. Implement [the practice tasks](05-PRACTICE.md) in order, consulting [solution reasoning](06-SOLUTIONS-AND-REVIEW.md) after your own design attempt.
6. Finish [the trace lab](07-TRACE-LAB.md) and compare your claims with [verification evidence](VERIFICATION.md).

Keep a learning journal with observation, hypothesis, experiment, and result. “Both tabs show version one” is an observation. “The second tab overwrites the first” is a hypothesis. A conflict response should disprove it while preserving the losing draft. Record the accepted database value and exported draft as evidence rather than relying only on a message on screen.

Your junior-to-mid-level target is to explain failure behavior as clearly as the happy path. A saved application and a refreshed library are separate outcomes. An editor export contains one draft; a SQLite backup contains the database. A shared loopback token protects a local personal tool but does not establish user ownership. These distinctions become essential when a CRUD project grows.

Finish with a small reviewed patch, an example request and response, a regression test that would fail without your change, and a compatibility note. You are ready to move on when you can explain why an invalid request changes nothing, why two competing edits have one accepted winner, and what a user should do after an uncertain network timeout.
