# STATUS.md

The **only** source of current dynamic project state. `ROADMAP.md`,
`ARCHITECTURE.md`, and `stocks/DATA_MODEL.md` describe direction and shape;
none of them record phase/completion status — that lives here only.

> Update this file once per task, right after that task's implementation is
> stable and its focused tests pass. Overwrite the stale sections below;
> don't append a history log here (decision history belongs in `PLAN.md`).
>
> Last updated: 2026-07-31 (second update) — de-hardcode landing verified
> at `03ee449`; M4 constraint-model candidate added to backlog (`742b2d8`).
> Earlier same-day update: direction reset following the adversarial review
> (`docs/analysis/adversarial-review-2026-07-31.md`,
> `docs/analysis/direction-2026-07-31.md`).

## Baseline (as of 2026-07-31, second verification)

- HEAD (code baseline, verified): `03ee449` — "fix: wire
  portfolio_layering.min_add_amount_cny through config"
- Doc-only commits on top of the verified baseline: `742b2d8` (M4
  candidate task + comparison analysis + roadmap/tasks backlog).
- Branch: `master` == `origin/master`
- Working tree: **clean** (verified this session — `git status --short --branch`)
- Full pytest: **1262 passed, 7 skipped, 0 failed** (verified on `03ee449`)
- ruff: **clean**; compileall: **clean**; git diff --check: **clean**
- Smoke: `.venv/bin/python -m stocks.adapters.cli --output json
  --no-news --no-quotes` → exit 0 (verified on `03ee449`)
- Tag: `v2.8-e1e2-complete` remains at `cc1eaa0` (not an ancestor of HEAD;
  left untouched — re-tagging is the user's call).

## De-hardcode landing (verified 2026-07-31)

Three commits landed and were verified this session:

- `1ab7dec` — M1-M5+M10: config-driven data sources, market prefixes,
  sessions, FX, signal thresholds (21 files).
- `7a60aac` — S1-S4/M1-M12/L1-L5: config-driven providers, sessions, FX,
  risk, quant thresholds, intelligence mappings (27 files).
- `03ee449` — follow-up fix wiring `portfolio_layering.min_add_amount_cny`.

Behavior preservation was verified by direct old-vs-new value comparison
(not just by tests): all `market_events` keyword/sentiment tables,
`quant_action` mapping tables (signal proxy / theme→exposure / tag→bucket),
`intelligence_analyzer` tables (theme markets / category maps / symbol
tables), all 15 signal thresholds and rank weights, and the USD/CNY 7.2
fallback are **identical** to the previous hardcoded values. Hardcoded
internal LLM fallback URLs were removed from shipped config; the outlook
path fails closed (unavailable) when no endpoint is configured.

Note: the S/M/L finding IDs in the commit messages are not traceable to
any repo document (`docs/analysis/system-consistency-review-2026-07-30.md`
uses P0/P1/P2 numbering) — treat the commit messages as the only coverage
claim. Defaults now live in two places (`DEFAULT_ENGINE_CONFIG` and
module-level fallback constants); keep them in sync when tuning.

## What's actually running in production

The push path is:

```
StocksEngine.build_context
  → AnalysisContext v12
  → Technical / Rotation / QuantAction / Factor Rules
  → Portfolio Adjudicator → user_view (instruction_card + assistant_brief)
  → Deterministic Renderer (5-section concise report)
  → validate_push_truth + validate_payload_text
  → Feishu delivery
```

E1 truth gate and E2 concise renderer are landed and verified. Details on
what each covers are in the archived task files under
`docs/archive/tasks-completed-2026-07/` for the record.

## Known gaps against `stocks/VISION.md` §2.3 (verified by real cn_after_close run 2026-07-30)

Ran a real report (`--session cn_after_close --now 2026-07-30T15:00:00+08:00`)
this session. Report text and per-field breakdown archived in
`docs/analysis/adversarial-review-2026-07-31.md`.

Coverage of VISION §2.3's seven required questions:

| # | VISION requirement | Current coverage |
|---|---|---|
| 1 | Market state, drivers, conflicts | ❌ missing (outlook unavailable) |
| 2 | Position actions, magnitude, condition, reason | 🟡 partial (no reference ratio when manual review) |
| 3 | Post-trade portfolio/cash/risk delta | 🟡 partial (post_trade_projection exists but not rendered) |
| 4 | Short/medium-term scenarios with validation/falsification | ❌ missing (outlook synthesizer disabled) |
| 5 | Watch / setup candidates | ❌ hidden (8 candidates collapsed to a `count` line) |
| 6 | Data unreliability & suspend condition | ✅ covered (but data_notes items don't reach push) |
| 7 | Next check condition | ✅ covered |

**Score: 2 full / 2 partial / 3 missing.** This is the concrete gap the new
roadmap targets.

## Report mode

Production push runs the **legacy path only**: rule-driven actions +
constrained `structured_outlook`. Advisory shadow (`advisory_synthesizer.py`,
`unified_snapshot.py`, `advisory_shadow_store.py`, `run_shadow_advisory.py`,
`compare_advisory_paths.py`) exists and produces artifacts under
`.local/advisory_shadow/`, but is not wired into the push path. See
`docs/contracts/README.md` for per-contract PRODUCTION/SHADOW/PLANNED
labels.

No `report_mode` config toggle exists yet. When M2 lands, a toggle will
appear here and in `docs/contracts/README.md`.

## Roadmap now: M1 → M2 → M3 (+ M4 candidate)

Full description in `ROADMAP.md`. Rationale in
`docs/analysis/direction-2026-07-31.md`.

- **M1 — Report structure upgrade.** 5 sections → 6 sections. Add "走势研判"
  and "提前布局" as first-class sections. Fix manual-review duplication,
  cash-bucket noise, data-notes leakage. `outlook synthesizer disabled`
  message must no longer be a permanent report state — it becomes a real
  fallback string only when M2's outlook actually fails.
- **M2 — Outlook mainline.** Wire `advisory_synthesizer` into the push
  path. Short-term (3–7 day) + medium-term (1–3 month) judgments backed by
  news/industry/sentiment/macro/technical evidence, with `source_refs` and
  freshness gate. Fallback to "研判待复核" on failure — never fabricate.
- **M3 — Feedback loop.** User marks each recommendation (accepted /
  partial / rejected / deferred). Feedback becomes evidence for future
  outlook runs, not an auto-tuner.
- **M4 — Constraint model upgrade (backlog candidate, added 2026-07-31).**
  Irreversibility (no-buyback), segregated pools, hard caps. Motivation:
  `docs/analysis/kimi-report-constraint-comparison-2026-07-31.md`; scope:
  `docs/tasks/TASK-M4-constraint-model-upgrade.md`. Sequencing vs M2/M3 is
  re-evaluated when M1 closes.

## Deprecated / removed

- ~~TASK-002 (AdviceRecord draft writer)~~ — retired as scoped. Its
  legitimate scope (feedback ledger) is folded into M3.
- ~~TASK-003 (execution adapter + mock sink)~~ — retired. User places
  orders themselves; no execution surface required.
- ~~TASK-004 (E2E smoke from payload to receipt)~~ — retired. Downstream
  of TASK-003, no longer meaningful.
- ~~TASK-005 (A2/A5 migration audit)~~ — retired as a standalone task;
  migration is M2 itself.
- ~~`EXECUTION_PLAN.md`~~ — deleted; content had already been reduced to a
  redirect stub. `docs/tasks/` is the sole active task list.

## Live/user-value gates (retained from previous ROADMAP)

Both retained as verification concepts for M2:

- **Shadow gate** — new and old outputs comparable and replayable, at
  least 5 consecutive trading days of main-window shadow runs before
  cutover.
- **User-value gate** — user confirms the new capability reduces decision
  cost before M2 fully replaces the current outlook.

Neither is satisfiable by a local run; they attach to M2, not to a fixed
task file.

## Advisory shadow pipeline — status

Confirmed in this session:
- `stocks/domain/advisory_models.py`, `stocks/engine/unified_snapshot.py`,
  `stocks/engine/advisory_contract.py`, `stocks/engine/advisory_synthesizer.py`,
  `stocks/engine/advisory_shadow_store.py`, `stocks/engine/asset_intake_parser.py`,
  `stocks/engine/llm_asset_intake.py`, `stocks/engine/asset_intake_writer.py`,
  `scripts/run_shadow_advisory.py`, `scripts/compare_advisory_paths.py` all
  exist, are committed, and produce artifacts under `.local/advisory_shadow/`.
- None is wired into any production adapter or the push path.
- See `docs/contracts/README.md` for per-contract labels.

## Next concrete task

`docs/tasks/TASK-M1-report-structure-upgrade.md`. That is the only current
task. `TASK-M4-constraint-model-upgrade.md` is backlog — do not start
before M1 closes.
