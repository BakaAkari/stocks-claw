# docs/tasks/

One bounded coding task per file: `TASK-NNN-short-slug.md`. This is where a
coding agent finds *exactly one thing to do* — no re-deriving scope from
`ROADMAP.md`, `ARCHITECTURE.md`, or `stocks/DATA_MODEL.md`.

## Rules

- A task file is self-contained: objective, scope, explicit non-goals,
  likely files, interfaces/behavior, focused tests, a real smoke check, and
  stop criteria. An agent should not need to open `ROADMAP.md` to know what
  to build — only to understand *why*, if curious.
- A task file must not describe work belonging to a later task. If you find
  yourself writing "and then also...", that's a new `TASK-NNN` file, not a
  section.
- Numbering is sequential (`TASK-001`, `TASK-002`, ...) and never reused.
- When a task is complete, its outcome is recorded in `STATUS.md`, not by
  editing the task file's objective. You may append a short "Outcome" note
  at the bottom of the task file itself, but the task file's plan sections
  stay as originally scoped, for the record.
- Only one task should be the "current" one at a time. `STATUS.md` names it.

## Current task

No active `TASK-001` sub-task remains. `TASK-001E1` and `TASK-001E2` are done
and committed (tag `v2.8-e1e2-complete`). See `STATUS.md` § "Remaining work
(after TASK-001E1/E2)" for the next concrete units of work.

## Backlog

- TASK-002 — user-confirmed advice records (A6 execution feedback entry).
- TASK-003 — execution adapter skeleton and mock execution sink.
- TASK-004 — end-to-end smoke from payload to advice record to mock receipt.
- TASK-005 — A2/A5 production migration feasibility audit.
