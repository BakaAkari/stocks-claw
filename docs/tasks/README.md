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

**TRUST-01 个人约束、金融事实核对与 LLM 建议责任链**
（[任务方案与验收标准](TASK-TRUST-01-financial-reconciliation-advisory-lineage.md)）。
按用户指定先核对持仓、资金、成交，再贯通 LLM 建议来源；采用
[金融真实性契约](../contracts/financial-trust-v1.md)，保留 DECISION-01 与 EVAL 基础。
暂不扩展 E4 胜率指标。当前进度、验证和部署状态只见 [STATUS](../../STATUS.md)。

**硬边界**：旧 `settlements.jsonl` 不作策略真值，旧胜率不作反馈，系统恢复另行处理。
原调查见 `docs/analysis/signal-evaluation-architecture-review-2026-10-15.md`；既往阶段
验收只证明其中列明的局部能力，不能替代真实纵向闭环验收。

## Backlog (do not start before the current task closes)

- **M1** — Report structure upgrade *(done 2026-07-31, `382207b`)*.
- **M2** — Outlook mainline *(done 2026-07-31, `7c35c7f`; live-LLM
  verified same day)*.
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
