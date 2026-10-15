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

**EVAL-01 信号评价基础设施重构（IN PROGRESS：E0、E1、E2 已验收，E3 未开始）**（`docs/tasks/TASK-EVAL-01-signal-evaluation-foundation.md`）。
E0 完成停机/封存/盘点；E1 完成生成侧标的身份与价格 lineage；E2 完成 SQLite 唯一评价任务、租约、单次结果和进程恢复。当前全量测试 1486 passed / 7 skipped；旧结算脚本尚未迁移。下一步 E3 为目标时点观测与交易日历，系统继续暂停。

**当前硬边界**：冻结现有胜率的策略反馈用途；不把旧 `settlements.jsonl` 当作策略真值；先建立唯一评价契约、幂等账本、目标时间与价格 lineage，再进入代码实施。
详见 `docs/analysis/signal-evaluation-architecture-review-2026-10-15.md`。

## Backlog (do not start before the current task closes)

- **M1** — Report structure upgrade *(done 2026-07-31, `382207b`)*.
- **M2** — Outlook mainline *(done 2026-07-31, `7c35c7f`; live-LLM
  verified same day)*.
- **A1** — Asset intake entry *(done 2026-08-01, `c313d22`)*:
  `TASK-A1-asset-intake-entry.md`.
- **M3** — Feedback loop *(done 2026-08-01, `83e94ec`)*:
  `TASK-M3-feedback-loop.md`.
- **W1** — Watchlist productization (M5): user-designated instruments
  persisted, scanned daily, surfaced in push. *(next; folds in D1 US
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
