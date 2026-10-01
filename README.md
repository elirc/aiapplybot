# ApplyBot: local application tracker and assisted form filler

Keep applications, notes, and status changes in SQLite. The new dashboard supports create, search, edit, delete, version conflicts, and retained change history. Start the [detailed learning course](astraupskill/README.md) to study the actual code as a CRUD web application.

## Start the tracker

From this project folder with Python 3.11 or later:

```powershell
python -m applybot dashboard
```

Open the private URL printed in the terminal. The dashboard uses the Python standard library: no provider account, API key, or JavaScript build is required. It binds to `127.0.0.1` and saves to `data/applications.sqlite3`. Stop with Ctrl+C. The token stays in browser memory; refreshing requires the private link or token again. Restarting creates a new token without changing records. This is a local personal tool with no user accounts or tenant roles.

Use `--port 8422` to avoid a busy port or `--data-dir ./practice-data` for a separate database. The form filler and `history` still use the default `data` directory; a custom dashboard directory does not redirect those commands.

## Existing history and backups

New automation logs go to SQLite. Existing `data/applications.jsonl` stays untouched. CLI history combines SQLite with unimported legacy records; the dashboard displays SQLite records only.

```powershell
python -m applybot history --limit 200
python -m applybot import-history --dry-run
python -m applybot import-history
python -m applybot backup --output ./tracker-backup-2026-09-09.sqlite3
```

Dry run validates every line without opening a database. Explicit import is atomic and retains receipts: repeating it preserves edits and does not resurrect deleted imported records. Malformed lines produce bounded line-numbered errors instead of being silently skipped. Backup uses SQLite's backup API and refuses an existing destination. Choose a new filename each time. Editor draft export contains one unsaved form, not a database backup.

For a restore rehearsal, copy a backup into a **new** directory as `applications.sqlite3`, then run a dashboard against that directory on a separate port. Do not copy only the main live database file while committed changes may remain in its WAL.

## Assisted form filling

The original browser workflow remains separate:

```powershell
python -m venv .venv
./.venv/Scripts/python -m pip install -r requirements.txt
./.venv/Scripts/python -m playwright install chromium
./.venv/Scripts/python -m applybot init
./.venv/Scripts/python -m applybot apply --help
```

Edit `profile.yaml` yourself. The neutral template uses blank text and unknown (`null`) yes/no facts. Document paths resolve relative to the profile. `--llm local` uses offline rules; other existing adapters depend on a configured CLI or provider. Their live availability and model defaults were not verified in this pass. Review all generated answers and factual claims before use.

After scanning and filling, the CLI waits: `r` rescans; `s` requests confirmation before attempting the submit button; `d` records a user-reported submission; `q` abandons the attempt. The dashboard cannot trigger this automation. The offline planner now skips unknown answers and leaves consent/certification checkboxes for manual review. Real profiles, browser sessions, and applications were not used in verification.

## Verify

```powershell
./.venv/Scripts/python -m pip install -r requirements-dev.txt
./.venv/Scripts/python -m playwright install chromium
./.venv/Scripts/python scripts/verify.py
./.venv/Scripts/python scripts/verify_browser.py
node --check applybot/web/app.js
```

The first script parses Python and runs 43 store, HTTP, import, profile, and CLI tests. The second runs eight real Chromium scenarios with temporary local fixtures, writes JSON evidence, and captures desktop/narrow screenshots. Node provides an optional JavaScript syntax check and is not needed to run the tracker. See [verification scope and limits](astraupskill/VERIFICATION.md).
