# Interview preparation from the ApplyBot tracker

ApplyBot is a Python job-application tracker: a SQLite store (`applybot/application_store.py`), a standard-library loopback HTTP adapter (`applybot/dashboard.py`) and a no-build browser client (`applybot/web/app.js`). The `astraupskill/` course next door teaches how it works. This folder turns the same code into material for a CRUD web-developer interview.

## The pitch

I replaced an append-only JSONL history with a real CRUD aggregate: stable UUID identity, a per-row `version` counter, optimistic concurrency enforced by a conditional `UPDATE ... WHERE id=? AND version=?` whose `rowcount` is the verdict, and an audit row written inside the same `BEGIN IMMEDIATE` transaction as the change. The browser keeps the loaded record as a baseline and adopts only what the server returns, so a rejected save keeps the user's unsaved notes on screen. Forty-three tests plus eight Chromium scenarios cover the conflict, the rolled-back audit and the save-succeeded-but-refresh-failed case.

## Stack framing

This project is Python and SQLite, and the roles you are targeting are Node/Express or ASP.NET Core. Every technical answer here is written twice where it matters: what the Python code does, then the equivalent in Express with Prisma or in ASP.NET Core with EF Core. `astraupskill/08-TRANSLATION.md` is the source of those mappings (`If-Match` or a Zod `.strict()` body, `updateMany` with `count === 0`, `[ConcurrencyCheck]` and `DbUpdateConcurrencyException`). Do not claim Node experience you do not have; claim the mechanism and show you can place it in Node.

## Two-week use

Days 1 to 3: read [01-INTERVIEW-QUESTIONS.md](01-INTERVIEW-QUESTIONS.md) with the source open, and answer out loud before reading the model answer. Days 4 and 5: draft your own version of [02-STORIES-AND-RESUME.md](02-STORIES-AND-RESUME.md), replacing my numbers with numbers you can defend. Days 6 to 8: time yourself on [03-CODE-READING-DRILL.md](03-CODE-READING-DRILL.md), twelve minutes per drill, no source open. Days 9 to 11: do [04-TAKE-HOME.md](04-TAKE-HOME.md) end to end and grade yourself against its rubric. Days 12 and 13: [05-SYSTEM-DESIGN-FOLLOWUPS.md](05-SYSTEM-DESIGN-FOLLOWUPS.md), which is where a mid-level offer is usually decided. Every day: ten minutes of [06-FLASHCARDS.md](06-FLASHCARDS.md).

The verification evidence you are allowed to quote is in `../astraupskill/VERIFICATION.md`, including its limits. The limits are part of the strong answer.
