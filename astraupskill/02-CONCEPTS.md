# Identity, transactions, and honest state

Identity answers “which application?” Version answers “which accepted state?” A record receives an opaque UUID when created. Changing a title preserves that identity. Every accepted update advances the version, and a client sends the version it actually loaded. Two editors can therefore detect competing intent rather than silently replacing each other's notes.

```text
Tab A reads X at version 1.
Tab B reads X at version 1.
Tab A saves with expected version 1 -> accepted version 2.
Tab B saves with expected version 1 -> conflict, draft retained.
```

Fetching the latest version immediately before attaching it to an old draft would defeat this protection. The version belongs to the draft's baseline. The browser retains that baseline in `editor` while the form controls contain current input. The store compares the expected version and includes it in the SQL update predicate. Delete also requires a version because deleting a record another editor just changed can lose work.

A transaction groups the application write and audit insert into one outcome. `BEGIN IMMEDIATE` reserves the SQLite writer before the read-and-update sequence. If the audit insert fails, the application change rolls back. Writing the row and then logging on another connection would not provide that guarantee. Tests install a failing audit trigger and inspect real persisted state. The retained audit contains identity, action, version, and time; it is a minimal history, not a full recovery log of every old value.

Validation has several purposes. HTML `required` improves feedback but cannot constrain arbitrary HTTP callers. The adapter bounds requests and checks wrapper shape. `application_input` rejects unknown fields, invalid statuses, and unsupported URLs. Database CHECK constraints defend key lengths, statuses, and positive versions. Parameterized SQL protects query structure, while escaping percent, underscore, and backslash gives LIKE search literal-substring semantics. Parameterization alone would still permit wildcard behavior.

The legacy importer illustrates idempotency. A receipt identifies a normalized old event and its occurrence number. Reimporting the same event skips it. Retaining receipts after deletion prevents later imports from resurrecting deleted records. New HTTP creates do not yet have durable idempotency keys, so a timeout remains ambiguous: refresh and inspect before retrying. The practice course asks you to design a stronger contract.

Finally, model unknown information explicitly. `authorized_to_work: null` means no answer is available; `false` is a definite negative claim. The offline planner now skips unknown values and leaves consent or certification checkboxes to manual review. This is the same discipline used for nullable database columns and optional forms: missing information must not silently become a factual answer. Explain the states in ordinary language before choosing their serialized representation.

## The two-tab sequence, with the numbers

| step | tab | shown version | submitted version | DB version after | result |
|---|---|---|---|---|---|
| 1 | A | 1 | - | 1 | both tabs load the record at version 1 |
| 2 | B | 1 | - | 1 | |
| 3 | A | 1 | 1 | 2 | `rowcount == 1`: accepted, A adopts version 2 |
| 4 | B | 1 | 1 | 2 | `WHERE id = ? AND version = 1` matches nothing, `rowcount == 0`: 409, B keeps its draft |
| 5 | B | 2 (after reload) | 2 | 3 | accepted |

The row that decides is step 4: no read-then-compare, just a conditional write whose row count is the verdict.

## Why validation runs outside the transaction

`update` in `application_store.py` computes `expected, data = version_input(version), application_input(value)` *before* `with self.transaction()`. SQLite has one writer at a time: a write transaction holds the database-wide lock until commit, so every millisecond spent inside it (parsing, validating, raising) is a millisecond every other writer waits, and a validation error raised inside would have taken the lock for nothing. Validate first, hold the lock only for the read-check-write. The same discipline is right on PostgreSQL for a different reason (a held connection), which is why chapter 08 keeps the rule.
