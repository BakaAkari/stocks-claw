# docs/tasks/

One bounded coding task per file: `TASK-<milestone>-short-slug.md`. This is
where a coding agent finds *exactly one thing to do* — no re-deriving scope
from `ROADMAP.md`, `ARCHITECTURE.md`, or `stocks/DATA_MODEL.md`.

## Rules

- A task file is self-contained: objective, scope, explicit non-goals,
  likely files, interfaces/behavior, focused tests, a real smoke check, and
  stop criteria. An agent should not need to open `ROADMAP.md` to know what
  to build — only to understand *why*, if curious.
- A task file must not describe work belonging to a later task. If you find
  yourself writing "and then also...", that's a new task file.
- Milestone slugs (`M1`, `M2`, `M3`) prefix current tasks and never overlap.
- When a task is complete, its outcome is recorded in `STATUS.md`, not by
  editing the task file's objective. You may append a short "Outcome" note
  at the bottom of the task file itself, but the task file's plan sections
  stay as originally scoped, for the record.
- Only one task should be the "current" one at a time. `STATUS.md` names it.

## Current task

`TASK-M1-report-structure-upgrade.md` — the report structure upgrade
covering VISION §2.3 questions 1, 2, 3, 5, 6, 7 (question 4 waits for M2).

## Backlog (do not start before the current task closes)

- **M1** — Report structure upgrade *(current)*.
- **M2** — Outlook mainline: `advisory_synthesizer` into production push,
  short-term (3-7d) + medium-term (1-3m) judgment with source_refs and
  freshness gate.
- **M3** — Feedback loop: user marks each recommendation accepted / partial
  / rejected / deferred, feedback flows into next Outlook run as evidence.

## Retired

The following were on the previous backlog and are retired as of
2026-07-31 direction reset (`docs/analysis/direction-2026-07-31.md`):

- TASK-002 (AdviceRecord draft writer) — folded into M3 with a redefined
  role as feedback ledger.
- TASK-003 (execution adapter + mock sink) — user places orders manually;
  no execution surface required.
- TASK-004 (E2E smoke payload→receipt) — downstream of retired TASK-003.
- TASK-005 (A2/A5 migration audit) — migration is M2 itself.

Completed TASK-001 sub-tasks are in `docs/archive/tasks-completed-2026-07/`.
