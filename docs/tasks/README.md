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

**C1 报告决策支持层补全 done 2026-08-06**（`docs/tasks/TASK-C1-report-decision-support.md`）：
冲突解读（`_conflict_tilt` tilt/tilt_reason）、研判边界降级（
`_apply_freshness_downgrade`）、确定性明日计划（`_tomorrow_plan` +
push "明日计划"节）。全量 1380 passed / 7 skipped / 1 deselected；
ruff clean。下一步按需求分析 §7：双引擎信息面专项 / 用户中立化清理 / W1。

**2026-08-11 官方统计 freshness 发布周期语义修复**（P4-1 家族根因，
`context_builder.py`）：官方统计（CPI/失业率/利率）新鲜度改走发布周期
语义（`_official_freshness` + `next_official_release`），不再被交易日语义
误判 old；顶层 macro.freshness 以官方层为主。全量 1392 passed / 4 预存
失败 / 7 skipped；真实 `us_post_open` artifact 验证置信度 low → medium。
详见 `STATUS.md` §2026-08-11。

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
