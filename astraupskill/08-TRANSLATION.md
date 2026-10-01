# 08. Translation: the three portable mechanisms

[Course index](README.md)

| here (Python + SQLite) | Express / Prisma (TypeScript) | ASP.NET Core / EF Core (C#) | React client |
|---|---|---|---|
| the `{ version, application }` request wrapper: the client states which version it edited | an `If-Match` header, or a `version` field in a Zod-validated body (`.strict()`, so the wrapper is the only shape accepted) | `[FromHeader(Name = "If-Match")]` or a `Version` property on the DTO | the form keeps the loaded record's `version` in state and sends it back unchanged |
| `UPDATE applications SET ..., version = version + 1 WHERE id = ? AND version = ?` and `rowcount != 1` is the 409 (`application_store.py`) | `prisma.application.updateMany({ where: { id, version }, data: { ...fields, version: { increment: 1 } } })` and `count === 0` is the 409 | `[ConcurrencyCheck] int Version`; EF issues the same `WHERE ... AND Version = @p` and throws `DbUpdateConcurrencyException`, which you map to 409 | on 409 keep the draft, show the message, offer to reload |
| `app.js` captured baseline and `adopt()`: the form adopts only what the server returned | the same rule in any client of the route | the same | `const [draft, setDraft] = useState(loaded); const [accepted, setAccepted] = useState(loaded)`; on 200, `setAccepted(response)` and rebase the draft; never `setAccepted(draft)` |

## The one difference that matters

SQLite holds a database-wide write lock for the duration of a write transaction, which is why `application_store.py` validates the input *before* opening the transaction (chapter 02). PostgreSQL locks rows, not the database, so a Prisma or EF service can afford to validate inside the transaction; it still should not, because a validation failure inside a transaction is a wasted round trip and a held connection. Keep the rule: parse first, then transact.

## Exercise

Write the `update` method as an Express route with Zod and Prisma (one conditional `updateMany`, a 409 with the same message as the Python store), then port the `adopt()` rule into a React form with two pieces of state. The test for both: two tabs, the second submit is 409, the second tab's draft text is still on screen.
