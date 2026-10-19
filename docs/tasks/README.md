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

**HARDEN-04 在线数据与公开市场研判验收**
（[任务与验收标准](TASK-HARDEN-04-online-data-market-acceptance.md)）。
本轮结果、当前基线和待验收内容统一见 `../../STATUS.md`。
全系统审查和后续实施顺序见
[系统健壮性审查](../analysis/system-hardening-2026-10-19.md)。

DOC-02、TRUST-01、DECISION-01 与 EVAL 作为已有基础保留；旧结算记录不作策略真值，
旧胜率不作生产反馈。后续功能须单独定义任务和验收，不把本轮边界加固当作 L2-L5 通过。

## Backlog (do not start before the current task closes)

- **M1** — Report structure upgrade *(done 2026-07-31, `382207b`)*.
- **M2** — Outlook mainline *(historical implementation milestone; its old live-LLM
  claim is not current deployment evidence)*.
- **A1** — Asset intake entry *(done 2026-08-01, `c313d22`)*:
  `TASK-A1-asset-intake-entry.md`.
- **M3** — Feedback loop *(done 2026-08-01, `83e94ec`)*:
  `TASK-M3-feedback-loop.md`.
- **W1** — Watchlist productization (M5): user-designated instruments
  persisted, scanned daily, surfaced in push. *(historical backlog; folds in D1 US
  quotes freshness verification)*
- **M4** — Constraint model upgrade (candidate): irreversibility
  (no-buyback), segregated pools, hard caps. See
  `TASK-M4-constraint-model-upgrade.md`; kept behind W1.

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
