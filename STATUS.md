# STATUS.md

The **only** source of current dynamic project state. `ROADMAP.md`,
`ARCHITECTURE.md`, and `stocks/DATA_MODEL.md` describe direction and shape;
none of them record phase/completion status — that lives here only.

> Update this file once per task, right after that task's implementation is
> stable and its focused tests pass. Overwrite the stale sections below;
> don't append a history log here (decision history belongs in `PLAN.md`).
>
> Last updated: 2026-07-31 (third update) — M1 report structure upgrade
> landed at `382207b`; backlog re-evaluated, M2 is next.
> Earlier same-day updates: de-hardcode verification (`03ee449`); direction
> reset (`docs/analysis/adversarial-review-2026-07-31.md`,
> `docs/analysis/direction-2026-07-31.md`).

## Baseline (as of 2026-07-31, third verification)

- HEAD (code baseline, verified): `382207b` — "feat(M1): close six-section
  report gaps against TASK-M1 spec"
- Doc-only commits on top of the previous verified baseline: `742b2d8`,
  `a116340`.
- Branch: `master` == `origin/master`
- Working tree: **clean** (verified this session — `git status --short --branch`)
- Full pytest: **1272 passed, 7 skipped, 0 failed** (verified on `382207b`)
- ruff: **clean**; compileall: **clean**; git diff --check: **clean**
- Smoke (M1 acceptance): regenerated `cn_after_close` 2026-07-30 report —
  six sections in order, 40 non-empty lines (gate ≤55), 697 Chinese chars
  (gate ≤1800), `validate_payload_text` clean. Sample:
  `.local/m1-sample-cn_after_close-20260730.md`.
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

## Known gaps against `stocks/VISION.md` §2.3 (re-scored after M1, real cn_after_close run 2026-07-30)

M1 landed at `382207b` and was verified against a regenerated real report
(sample: `.local/m1-sample-cn_after_close-20260730.md`). Earlier per-field
breakdown archived in `docs/analysis/adversarial-review-2026-07-31.md`.

Coverage of VISION §2.3's seven required questions:

| # | VISION requirement | Current coverage |
|---|---|---|
| 1 | Market state, drivers, conflicts | 🟡 structure landed (本窗口变化 + 走势研判 section); content waits M2 (outlook unavailable → sanitized fallback line) |
| 2 | Position actions, magnitude, condition, reason | ✅ covered (manual-review conflicts now carry 参考: ratio / 参考数量 / 参考金额 audit lines) |
| 3 | Post-trade portfolio/cash/risk delta | ✅ covered (post_trade_projection renders as 执行后估算 line when executable actions exist) |
| 4 | Short/medium-term scenarios with validation/falsification | ❌ missing (waits M2 outlook mainline) |
| 5 | Watch / setup candidates | ✅ covered (提前布局 first-class section, top 2-3 by score + overflow tail) |
| 6 | Data unreliability & suspend condition | ✅ covered (capital-gap data_notes reach push as 待决事项 lines) |
| 7 | Next check condition | ✅ covered |

**Score after M1: 5 full / 1 partial / 1 missing** (was 2 / 2 / 3). The
remaining gap is question 4 — the M2 outlook mainline.

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

## Roadmap now: M1 ✅ → M2 → M3 (+ M4 candidate)

Full description in `ROADMAP.md`. Rationale in
`docs/analysis/direction-2026-07-31.md`.

- **M1 — Report structure upgrade. ✅ landed 2026-07-31 (`382207b`).**
  Six-section report is live: 走势研判 (sanitized fallback until M2),
  提前布局 first-class, manual-review choice space with 参考: audit lines,
  cash-bucket collapse, data-notes capital gaps, post-trade projection
  line. VISION §2.3 score moved 2/2/3 → 5/1/1.
- **M2 — Outlook mainline.** Wire `advisory_synthesizer` into the push
  path. Short-term (3–7 day) + medium-term (1–3 month) judgments backed by
  news/industry/sentiment/macro/technical evidence, with `source_refs` and
  freshness gate. Fallback to "研判待复核" on failure — never fabricate.
  **This is the next task.**
- **M3 — Feedback loop.** User marks each recommendation (accepted /
  partial / rejected / deferred). Feedback becomes evidence for future
  outlook runs, not an auto-tuner.
- **M4 — Constraint model upgrade (backlog candidate, added 2026-07-31).**
  Irreversibility (no-buyback), segregated pools, hard caps. Motivation:
  `docs/analysis/kimi-report-constraint-comparison-2026-07-31.md`; scope:
  `docs/tasks/TASK-M4-constraint-model-upgrade.md`. Sequencing was
  re-evaluated when M1 closed: kept as backlog behind M2/M3 — the
  constraint semantics matter most once M2's richer advice is live, and no
  constraint-driven advice error has been observed in real reports yet.

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

M1 is done. Next is **M2 — Outlook mainline** (task file to be written
before starting; scope lives in `ROADMAP.md` §M2).
`TASK-M4-constraint-model-upgrade.md` stays backlog behind M2/M3.
