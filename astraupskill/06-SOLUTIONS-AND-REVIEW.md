# Solution reasoning and review questions

Begin by naming an invariant. For the date exercise: “Every stored follow-up value is absent or a valid ISO calendar date, and edits obey the existing application version contract.” This drives validation, migration, serialization, controls, and tests. Adding only an input would leave the feature incomplete.

Parse a date with a calendar-aware function and require canonical ISO output to match the input. A schema migration can add a nullable column and advance `user_version` transactionally. Existing records should receive `NULL`, not today's date, because today would invent a decision. Extend the editable field set, normalizer, SQL statements, and form together. Test an older fixture database and confirm that its identities, notes, versions, and audit remain intact. Rehearse backups separately from migration tests.

For summaries, use `SELECT status, count(*) ... GROUP BY status` and bind search values exactly as the list does. If counts and rows describe one snapshot, share their connection and read transaction. A reviewer should ask whether counts are global or filtered and whether the visible wording matches the query. Missing groups should become zero counts rather than disappear unpredictably.

For idempotent creation, use a dedicated receipt table with a unique request key and normalized payload hash. Insert the application and receipt together. A competing request must observe the accepted result or a defined conflict, not create a second row after a preflight check races. Decide what the receipt returns after later edits or deletion. It can preserve the original accepted identity while a separate GET reports the current state. Document key retention: deleting receipts casually can enable duplicates again.

For owner isolation, include authorization in each data access path. Updates should match identity, expected version, and owner. Avoid fetching globally and relying on the UI to hide unauthorized values. Retained audits need ownership even after their application is deleted, so an audit row may need its own owner field. Imports require an explicit destination owner. The current shared token cannot provide that identity.

For reminders, separate acceptance of an intent from delivery. A worker claims bounded pending work, calls a fake adapter, and records the result. A crash between delivery and recording completion still requires downstream idempotency or an explicitly accepted duplicate risk. Explain that remaining uncertainty rather than claiming that an outbox makes all external effects exactly once.

Review questions: Can invalid input change anything? Can two valid requests lose data? Can a timeout duplicate a create? Can the UI call an accepted save failed? Can older data still be read? Do deleted imported records reappear? Which checks use real persistence and which use fakes? Answer using source references and observed outcomes. A broad label such as “production ready” communicates less than a precise account of the guarantee and its limits.

For a final exercise, review your own patch without running it first. Write the failure you most expect, then design a test to disprove that hypothesis. This habit turns testing into investigation instead of merely confirming the implementation you just wrote.

## The version contract your new field must obey

Every exercise here extends a record that is already guarded twice: a read comparison inside the transaction and a conditional `UPDATE ... WHERE id=? AND version=?`. A new column joins this statement; it does not get its own path. From `applybot/application_store.py:217-241`:

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
                    data["url"],
                    data["company"],
                    data["title"],
                    data["status"],
                    data["notes"],
                    utc_now(),
                    identity,
                    expected,
                ),
            )
            if changed.rowcount != 1:
                raise StoreError("Application changed while saving.", 409)
```
