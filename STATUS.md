# STATUS.md

The **only** source of current dynamic project state. `ROADMAP.md`,
`ARCHITECTURE.md`, and `stocks/DATA_MODEL.md` describe direction and shape;
none of them record phase/completion status — that lives here only.

> Update this file once per task, right after that task's implementation is
> stable and its focused tests pass. Overwrite the stale sections below;
> don't append a history log here (decision history belongs in `PLAN.md`).
>
> Last updated: 2026-07-31 (fourth update) — M2 outlook mainline landed at
> `7c35c7f`; VISION §2.3 score 5/1/1 → 7/0/0 (pipeline); M3 is next.
> Earlier same-day updates: M1 (`382207b`), de-hardcode verification
> (`03ee449`), direction reset (`docs/analysis/adversarial-review-2026-07-31.md`,
> `docs/analysis/direction-2026-07-31.md`).

## Baseline (as of 2026-07-31, fourth verification)

- HEAD (code baseline, verified): `7c35c7f` — "feat(M2): wire advisory
  mainline into production push outlook"
- Branch: `master` == `origin/master`
- Working tree: **clean** (verified this session — `git status --short --branch`)
- Full pytest: **1287 passed, 7 skipped, 0 failed** (verified on `7c35c7f`;
  1285 unit + 2 integration)
- ruff: **clean**; compileall: **clean**; git diff --check: **clean**
- Smoke (M2 acceptance, no-LLM-endpoint case): forced `cn_after_close`
  2026-07-30 run → `structured_outlook.status == "unavailable"` with
  message `研判待复核：目标市场行情数据过旧或缺失` (freshness gate fired
  on stale forced-date quotes — gate works as designed); push report
  renders `走势研判 - 中长期研判：研判待复核：…` and completes through
  the push validators. The configured-endpoint case (real short/medium
  judgments) is covered by fake-client unit/integration tests; a live-LLM
  run has **not** been performed (no endpoint configured locally).
- Smoke (M1 acceptance, earlier): regenerated `cn_after_close` 2026-07-30
  report — six sections in order, 40 non-empty lines (gate ≤55), 697
  Chinese chars (gate ≤1800), `validate_payload_text` clean. Sample:
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
  → Advisory Mainline (primary sessions): snapshot → LLM analyst →
    validation receipt → structured_outlook (研判待复核 on any failure)
  → Deterministic Renderer (6-section concise report)
  → validate_push_truth + validate_payload_text
  → Feishu delivery
```

E1 truth gate and E2 concise renderer are landed and verified. Details on
what each covers are in the archived task files under
`docs/archive/tasks-completed-2026-07/` for the record.

## Known gaps against `stocks/VISION.md` §2.3 (re-scored after M2, 2026-07-31)

M2 landed at `7c35c7f`: the advisory mainline now produces the 走势研判
content from the LLM Investment Analyst (short-term 3-7天 / medium-term
1-3个月 judgments with 验证/证伪 lines, scenarios, source_refs), gated by
quote freshness, snapshot age, client configuration, and the validation
receipt. Verified with fake-client tests and the no-endpoint smoke; a
live-LLM run is still pending (no endpoint configured locally).

Coverage of VISION §2.3's seven required questions:

| # | VISION requirement | Current coverage |
|---|---|---|
| 1 | Market state, drivers, conflicts | ✅ pipeline landed (advisory outlook summary + drivers; honest 研判待复核 fallback when gated) |
| 2 | Position actions, magnitude, condition, reason | ✅ covered (manual-review conflicts now carry 参考: ratio / 参考数量 / 参考金额 audit lines) |
| 3 | Post-trade portfolio/cash/risk delta | ✅ covered (post_trade_projection renders as 执行后估算 line when executable actions exist) |
| 4 | Short/medium-term scenarios with validation/falsification | ✅ pipeline landed (near/medium-term + 验证/证伪 lines + base/bull/risk scenarios; falsification is a hard validation error) |
| 5 | Watch / setup candidates | ✅ covered (提前布局 first-class section, top 2-3 by score + overflow tail) |
| 6 | Data unreliability & suspend condition | ✅ covered (capital-gap data_notes reach push as 待决事项 lines) |
| 7 | Next check condition | ✅ covered |

**Score after M2: 7 full / 0 partial / 0 missing** (was 5 / 1 / 1) — at
pipeline level. Content-level verification (live LLM judgments in real
reports) remains gated on an ad-hoc endpoint and the shadow/user-value
gates below.

## Report mode

Production push runs the **M2 advisory mainline by default** for primary
sessions: `build_unified_snapshot` → `synthesize_advisory` (LLM Investment
Analyst) → `validate_advisory` → projection into `structured_outlook`,
orchestrated by `stocks/engine/advisory_mainline.py`. Every failure path
(stale/missing quotes, snapshot older than 90 minutes, unconfigured LLM
endpoint, `hold_default` fallback, receipt errors) degrades to an honest
`研判待复核` unavailable outlook — never a fabricated judgment.

Toggle: `llm.advisory_mainline.enabled` (default `true` in
`DEFAULT_ENGINE_CONFIG`). Setting it `false` restores the legacy
constrained `OutlookSynthesizer` path (evidence + hash metadata included).
Advisory contracts (`UnifiedAnalysisSnapshot`, `InvestmentAdvisory`,
`AdvisoryValidationReceipt`) are PRODUCTION as of M2; `AdvisoryShadowRun`
stays SHADOW. Rule engine adjudicator and instruction_card actions are
untouched: advisory informs judgment only, never action selection.

## Roadmap now: M1 ✅ → M2 ✅ → M3 (+ M4 candidate)

Full description in `ROADMAP.md`. Rationale in
`docs/analysis/direction-2026-07-31.md`.

- **M1 — Report structure upgrade. ✅ landed 2026-07-31 (`382207b`).**
  Six-section report is live: 走势研判 (sanitized fallback until M2),
  提前布局 first-class, manual-review choice space with 参考: audit lines,
  cash-bucket collapse, data-notes capital gaps, post-trade projection
  line. VISION §2.3 score moved 2/2/3 → 5/1/1.
- **M2 — Outlook mainline. ✅ landed 2026-07-31 (`7c35c7f`).**
  `advisory_mainline.py` wires the LLM Investment Analyst into primary
  sessions behind `llm.advisory_mainline.enabled` (default true): freshness
  gate → snapshot → synthesis → receipt validation → projection into the
  whitelisted `structured_outlook` shape (delta/forecast pipelines
  unchanged). Short-term (3–7 day) + medium-term (1–3 month) judgments
  carry direction/confidence/rationale/validation/falsification and typed
  source_refs; falsification absence is a hard validation error. All
  failure paths render 研判待复核 — never fabricate. VISION §2.3 score
  moved 5/1/1 → 7/0/0 (pipeline level).
- **M3 — Feedback loop.** User marks each recommendation (accepted /
  partial / rejected / deferred). Feedback becomes evidence for future
  outlook runs, not an auto-tuner. **This is the next task.**
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

Both retained as verification concepts for M2; **status after M2 landing:**

- **Shadow gate — PENDING.** The 5-consecutive-trading-day replay of live
  main-window runs cannot be satisfied by a local one-off run; it requires
  real trading days with a configured LLM endpoint. Recorded as pending,
  not waived.
- **User-value gate — PENDING.** User confirms the new capability reduces
  decision cost before M2 fully replaces the legacy outlook (the legacy
  path remains available via `llm.advisory_mainline.enabled: false`).

## Advisory pipeline — status after M2

- `advisory_mainline.py` (new, M2) orchestrates the production advisory
  path for primary sessions; `UnifiedAnalysisSnapshot` /
  `InvestmentAdvisory` / `AdvisoryValidationReceipt` are PRODUCTION
  contracts (see `docs/contracts/README.md`).
- Shadow tooling (`advisory_shadow_store.py`, `run_shadow_advisory.py`,
  `compare_advisory_paths.py`, artifacts under `.local/advisory_shadow/`)
  remains available for the shadow-gate replay; `AdvisoryShadowRun` stays
  SHADOW.
- Asset-intake (`asset_intake_parser.py`, `llm_asset_intake.py`,
  `asset_intake_writer.py`) remains library-only (SHADOW), unchanged by M2.

## Next concrete task

M2 is done. Next is **M3 — Feedback loop** (task file to be written before
starting; direction: CLI feedback channel `--advice-feedback <id>
<accepted|partial|rejected|deferred>` + AdviceRecord ledger + weekly rollup
flowing back into `recent_advice` in `AnalysisContext`; scope lives in
`ROADMAP.md` §M3). `TASK-M4-constraint-model-upgrade.md` stays backlog
behind M3.
