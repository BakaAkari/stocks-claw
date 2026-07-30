# STATUS.md

The **only** source of current dynamic project state. `ROADMAP.md`,
`ARCHITECTURE.md`, and `stocks/DATA_MODEL.md` describe direction and shape;
none of them record phase/completion status — that lives here only.

> Update this file once per task, right after that task's implementation is
> stable and its focused tests pass. Overwrite the stale sections below;
> don't append a history log here (decision history belongs in `PLAN.md`).
>
> Last updated: 2026-07-30 for the audit-baseline repair (fixture
> reconstruction + STATUS correction + report-quality re-audit).

## Baseline (as of 2026-07-30, corrected this session)

- HEAD: `ae8abc6` — "feat: complete TASK-001E1 truth gate and TASK-001E2
  concise report renderer". (The hash previously recorded here, `96ed43d`,
  never existed in this repository — the E1/E2 history was rewritten after
  the previous STATUS update; this entry is the correction.)
- Tag: `v2.8-e1e2-complete` → `cc1eaa0`, which is **not** the current HEAD
  (the tag predates the history rewrite; left untouched — re-tagging is a
  git-history decision for the user, not for an agent session).
- Branch: `master` == `origin/master` at `ae8abc6` (the rewritten history
  was force-pushed; the earlier "2 commits ahead, not pushed" note was
  equally stale).
- Working tree: **dirty** with this session's audit-baseline repair
  (modified `tests/engine/test_data_quality_gate.py`; new
  `tests/fixtures/a512480_split_jump_fixture.json`; new
  `docs/analysis/system-consistency-review-2026-07-30.md`) — **not yet
  committed**; commit only on explicit user authorization.

## Audit-baseline repair (2026-07-30)

Triggered by the adversarial consistency review saved at
`docs/analysis/system-consistency-review-2026-07-30.md` (new in the dirty
tree above). That review's §0 found the self-audit baseline itself was
broken; this section records what was repaired and what remains open.

- **Test suite was not reproducible from a clean checkout — fixed.** Six
  tests in `tests/engine/test_data_quality_gate.py` loaded
  `.superpowers/sdd/task-2-a512480-fixture.json`, a file never committed to
  git and absent from `.gitignore`; any clean clone failed those 6 tests
  (6 failed / 1247 passed) despite the "1253 passed, 0 failed" figures
  quoted elsewhere in this file. The fixture (semiconductor ETF a_512480,
  15 bars 2026-06-25→2026-07-15 with the 2.70→1.33 split jump, deliberately
  not settling at the new level so the anomaly stays critical/high) was
  reconstructed as the git-tracked
  `tests/fixtures/a512480_split_jump_fixture.json`, and the test now
  references it. Verified this session: full suite **1253 passed, 7
  skipped, 0 failed** — same counts as previously claimed, now actually
  reproducible.
- **117 P0 / 17 P1 report-truth baseline: re-audit attempted, NOT
  conclusively re-measurable.** Ran
  `scripts/audit_report_quality.py --start 2026-07-01 --end 2026-07-30`:
  result **0 P0 / 1 P1** — but only two dated artifacts survive under
  `.local/scheduled_runs/` (2026-07-06 `cn_pre_close` and
  `us_after_close`), and the artifact population the original 117/17
  baseline was measured on no longer exists locally. This re-run proves the
  two surviving artifacts are clean (the single P1 is
  `advisory_receipt_coverage`: no shadow trial for us_after_close on
  2026-07-06, expected — the shadow pipeline is not scheduled), it does
  **not** prove the 117/17 baseline shrank. That baseline stays open until
  a comparable artifact population is audited (e.g. on the deployment host
  the original run targeted) or enough new production history accumulates.
- Verified in this session (2026-07-30): full repo suite
  `.venv/bin/python -m pytest -q -o 'addopts='` → **1253 passed, 7 skipped,
  0 failed**; `ruff check .` — clean; the audit command above — read-only,
  exit 0.

## Report mode

Production push still runs the **legacy path only**:
`OutlookSynthesizer` → `portfolio_decision.user_view` /
`structured_outlook` → `build_push_payload` → Feishu. There is no
`report_mode` config toggle in the codebase yet (`advisory_shadow` /
`advisory_primary` from the A0–A6 roadmap do not exist as a switch) — the
Advisory pipeline runs only via the standalone
`scripts/run_shadow_advisory.py`, writing to `.local/advisory_shadow/`, never
into production push. See `docs/contracts/README.md` for what's PRODUCTION
vs SHADOW.

## External strengthening plan (Task 0–8) — current approved direction

This is the current approved direction, separate from and layered on top of
the A0–A6 Advisory migration roadmap (`ROADMAP.md`). It targets correctness
of the *existing* production report pipeline rather than the Advisory
migration itself.

### Task 0 — report-quality audit infrastructure: locally verified; report-truth defects are NOT fixed

**Do not call the overall report-quality task "done."** What is verified is
narrower: the audit tool exists and runs, and two specific root-cause bugs it
helped surface are fixed. The **117 P0 / 17 P1 report-truth defects** the
audit found in historical artifacts are the baseline this task exists to
reduce — they are not resolved by Task 0 itself. Closing them is the job of
`docs/tasks/TASK-001-production-truth-model.md` and later tasks, not
something already landed.

Evidence gathered in this session (2026-07-29):

- `scripts/audit_report_quality.py` (new, 512 lines, read-only) audits
  historical `scheduled_runs` artifacts for 8 concrete defect classes:
  ratio/text drift between decision layer and rendered instruction card,
  unsettled sale proceeds shown as available cash, non-canonical
  settlement/risk vocabulary in the user-facing brief, outlook claims with no
  `source_refs`, stale cross-market quotes feeding actions, an instrument
  appearing as both an executable action and a research candidate, and
  advisory-shadow receipt coverage. This tool is forensic-only — running it
  does not fix anything it finds.
- User-reported result of running this audit against historical artifacts:
  **117 P0 / 17 P1 findings**, and a manual smoke pass of the daily
  intelligence push path. Not independently re-run in this session — treat
  as reported, not re-verified, until re-run. These 117 P0 findings describe
  real defects in what production currently pushes to the user; they remain
  outstanding until a task closes them (see `docs/tasks/`).
- Fixes landed for two of the found *root causes* (mechanisms that produce
  some of the 117 P0s), both in the dirty working tree above: a
  field-boundary bug in `outlook_validation._check_numeric_authority`
  (cross-field context bleed let a hostile number in one narrative field be
  exempted by a safe pattern in an unrelated adjacent field), and a
  session-boundary/dedup bug in `scheduled_analysis.MarketSessionCalendar` /
  `RunArtifactStore` for high-frequency (`run_every_minutes`) sessions. The
  daily-intel cron script was repointed from `daily_intel` to
  `global_intelligence_watch`. Fixing a root cause does not by itself clear
  the historical P0 count — that requires re-running the audit (not done in
  this session) and, for the structural defect classes, the work scoped in
  `docs/tasks/TASK-001-production-truth-model.md`.
- Verified in this session:
  `.venv/bin/pytest -q -o 'addopts=' tests/test_audit_report_quality.py
  tests/test_push_payload.py tests/test_run_push_report.py
  tests/engine/test_outlook_validation.py tests/engine/test_scheduled_analysis.py
  tests/engine/test_context_builder.py` → **230 passed, 6 skipped**.
  (The user's previously reported focused-test figure was 161 passed on a
  narrower file set; the 230 above is this session's broader re-check of the
  same area and does not contradict it — both are "focused," not full-suite.)
  Passing these tests confirms the audit tool and the two root-cause fixes
  work as written; it does not confirm the 117 P0 baseline has shrunk.

### Task 1 (production truth model) — sub-tasks 001C and 001D done this session

Task 1 is scoped in `docs/tasks/TASK-001-production-truth-model.md` and split
into bounded sub-tasks under `docs/tasks/`. Status of the ones that exist:

- **TASK-001C (configuration-driven execution rule resolver): done, verified
  this session (2026-07-29).** `stocks/engine/execution_rules.py` resolves
  `settlement_rule`/`execution_status`/`executable_quantity` from ordered
  match rules in `stocks/config/engine.yaml` (`execution_rules:` block) —
  no market/product defaults live in Python; missing or unmapped facts fail
  closed to `review_required`. `portfolio_adjudicator._finalize_approved_action`
  is the single producer of these fields for every approved action, including
  both legs of a replacement chain (the buy leg's ratio is portfolio-value
  based, so per contract it is always `review_required` for quantity — this
  now correctly makes the *overall* decision `review_required` too whenever a
  chain is present, which is a deliberate, not accidental, status change).
  `scheduled_analysis._evidence_holding` no longer fabricates `unit: "share"`
  for positions with a flat `quantity` and no explicit unit. Verified: focused
  suite (`test_execution_rules.py` + `test_portfolio_adjudicator.py` +
  `test_scheduled_analysis.py`) — 96 passed, 6 skipped; full repo suite —
  **1225 passed, 7 skipped, 0 failed**; `ruff check` clean; `compileall`
  clean; `git diff --check` clean.
  Known structural gap, intentionally **not** fixed here (out of
  TASK-001C's allowed files): `context_builder._value_position()` never
  serializes `Holding.unit` into `position_valuations`, so no caller today
  can see a position's true unit (gram vs share) end-to-end. Worked around by
  keying the bank-gram quantity rule on `product_type: precious_metal_account`
  instead of `holding_unit` (a fact that *is* reliably plumbed). Also found:
  real production data has the one bank precious-metal position
  (`ccb_gold`, account `ccb`) at `liquidity_tier: t1`, but the production
  settlement rules only define `bank+periodic_open` and the institution-
  agnostic `t0`/`cash` tiers for bank-like accounts — there is no
  `bank+t1` rule. This is consistent with fail-closed design (physical gold
  redemption timing is not a simple brokerage T+1), so `ccb_gold` currently
  resolves to `review_required` end-to-end; flagging in case that was meant
  to be executable and needs an explicit rule added later.
- TASK-001A, TASK-001B: task files not read in this session; their tests
  (`test_context_builder.py`, the `test_task001b_*` cases in
  `test_scheduled_analysis.py`/`test_portfolio_adjudicator.py`) are part of
  the full-suite green run above but were not independently re-audited here.
- **TASK-001D (user-view/cash/risk projection): done, corrected and
  re-verified this session (2026-07-29).** A prior pass in this session
  claimed completion while still leaving two real deviations from the task's
  requirements; both are now fixed, independent of green tests on the prior
  (looser) assertions:
  - `assistant_brief.cash` now emits the five canonical key names verbatim —
    `available_now`/`confirmed_settling`/`planned_release`/`strategic_exit`/
    `locked` — sourced from `CashSchedule.to_dict()`'s fields of the same
    names, with no recomputation. It previously kept the legacy dict keys
    `immediate`/`settling` as a compatibility shim; that shim is removed.
    `scripts/build_push_payload.py` (`render_push_payload`'s cash-section key
    tuple) and `stocks/engine/scheduled_analysis.py`
    (`format_run_markdown`'s cash-section key tuple) — the direct production
    consumers of these keys — were updated to read the canonical names, as
    were the fixtures/assertions in `tests/engine/test_presentation.py`,
    `tests/engine/test_portfolio_adjudicator.py`,
    `tests/engine/test_report_contract.py`, and `tests/test_push_payload.py`.
    `unresolved_settlement` is still excluded from every bucket and surfaced
    only as a `data_notes` entry (never a sixth bucket) — unchanged.
  - `presentation.build_user_view`'s per-action loop no longer falls back to
    `ratio` for `final_ratio`/`original_ratio`, to `reason`/default prose for
    `decision_reason`, or to `True` for `amount_is_estimate`. Every one of
    `final_ratio`, `original_ratio`, `decision_reason`, `evidence_summary`,
    `settlement_rule`, `executable_quantity`, `execution_status`,
    `estimated_amount_cny`, `amount_is_estimate` is now `raw.get(field)`
    verbatim from the adjudicator's `PortfolioAction` — a missing source
    field surfaces as `None` in `user_view`, not a synthesized value. (The
    adjudicator's own `PortfolioAction.to_dict()` in
    `stocks/engine/portfolio_adjudicator.py` — not in this task's allowed
    files — still backfills `final_ratio`/`original_ratio`/`decision_reason`
    when its own dataclass fields are `None`/empty; that is a pre-existing,
    separate layer this task did not touch.) `reason_summary` (a legacy
    display-only field, not one of the authoritative final-action fields)
    and the plain `ratio` field keep their existing fallback text, per scope.
    Regression coverage added in `tests/engine/test_presentation.py`
    (`test_build_user_view_never_synthesizes_missing_final_action_fields`)
    supplies an approved action with all nine fields absent and asserts every
    one projects as `None`.
  - Risk labels still come from one fixed mapping with exactly four public
    strings — `hedge`→`对冲/高风险`, `reduce`→`降风险`, `watch`→`观察`,
    `normal`→`常态` — unknown levels fail closed to `风险状态待确认` instead
    of inventing prose; unaffected by this correction.
  Other deviations noted previously and left as-is (unaffected by this
  correction, still out of scope):
  - `ReplacementChain.reason` (the chain-level field, not `sale_leg.reason`/
    `buy_leg.reason`) still embeds raw `position_id`s in
    `portfolio_adjudicator.py`. Left as-is because `build_user_view` never
    reads this field (confirmed by reading `presentation.py`); the two leg-
    level `reason`/`action_description` strings that *are* projected into
    `user_view` were fixed to drop position-id interpolation while preserving
    the `"到账"` substring required by
    `test_portfolio_adjudicator.py::TestReplacementChainSemantics::test_buy_leg_waits_for_sale_proceeds`.
  Verified (this correction pass, 2026-07-29): focused suite
  (`test_portfolio_adjudicator.py` + `test_presentation.py` +
  `test_scheduled_analysis.py` + `test_push_payload.py` +
  `test_run_push_report.py` + `test_report_contract.py`) — **158 passed, 7
  skipped, 0 failed**; `ruff check` on the 8 touched files — clean; `python
  -m compileall -q stocks/engine` — clean (exit 0); `git diff --check` —
  clean (exit 0); full repo suite — **1232 passed, 7 skipped, 0 failed**.
  No exceptions were taken to either of the two required fixes (canonical
  cash-bucket keys; no fallback synthesis for the nine final-action fields)
  — both are unconditional in the code as verified above, not gated by a
  flag or partially applied.

- **TASK-001E1 (production report truth gate): done, verified this session
  (2026-07-30).** The gate functions were already present in the dirty
  working tree from prior work; this session completed the missing regression
  coverage, fixed one test-fixture inconsistency, and re-verified the whole
  E1-relevant surface. Scope coverage:
  1. Action sentences are generated from finalized fields in
     `presentation._action_sentence` and `presentation.build_user_view`;
  2. Executable vs deferred/review split is enforced by
     `presentation._is_executable` and re-checked by
     `scripts/build_push_payload.validate_push_truth`;
  3. Per-market quote freshness fail-closed for cross-market actions is
     implemented in `presentation._market_quote_stale` / `_is_executable`,
     with regression tests for both directions (US action in CN session,
     A action in US session);
  4. Research deduplication against finalized action instrument identities is
     implemented in `build_user_view` and re-checked by `validate_push_truth`;
  5. Outlook source-ref enforcement is implemented in
     `outlook_validation._check_source_refs_presence` and re-checked by
     `validate_push_truth`;
  6. Risk reasons are derived from `risk_state.triggers`; empty reasons for
     `hedge`/`reduce` fail closed to a review message;
  7. Deterministic push truth gate `validate_push_truth` is wired into the
     cron entrypoint `scripts/run_push_report.py`, rejecting contradictory,
     non-executable, stale-duplicated, or unsupported output before delivery.
  Verified (this session, 2026-07-30): focused E1 suite
  (`test_push_payload.py` + `test_presentation.py` + `test_outlook_validation.py`
  + `test_audit_report_quality.py` + `test_portfolio_adjudicator.py` +
  `test_scheduled_analysis.py` + `test_run_push_report.py`) — **286 passed,
  6 skipped, 0 failed**; full repo suite — **1248 passed, 7 skipped, 0 failed**;
  `ruff check .` — clean; `python -m compileall -q stocks scripts` — clean;
  `git diff --check` — clean.
- TASK-001E2: not started.

- **TASK-001E2 (concise report renderer): done, verified this session
  (2026-07-30).** The `render_push_payload` function in
  `scripts/build_push_payload.py` was rewritten to emit a five-section concise
  trading report: **本窗口变化 / 可执行动作 / 禁止与延后 / 组合影响 / 下一检查点**.
  It replaces the previous verbose multi-section layout and is enforced by
  `validate_payload_text`. Legacy headings (`交易指令卡`, `私人投资助理`,
  `为什么这样安排`, `待人工确认的信号分类`, `仅供观察`, `中长期研判`,
  `资产类别`, `行业观察`, `基准情景`, `乐观情景`, `风险情景`) are now rejected.
  Conflict-count lines are rejected. Actions are capped at 3, each rendered
  with signal/instrument, final percentage, executable quantity, estimated
  amount, platform/settlement, and cancel condition. Research candidates are
  compressed to a single line in the blocked section. `run_push_report.py` keeps
  the E1 truth gate and now runs the E2 text validator after rendering.
  Verified (this session, 2026-07-30): focused E2 surface
  (`test_push_payload.py` + `test_run_push_report.py`) — **32 passed, 0
  skipped, 0 failed**; full repo suite — **1253 passed, 7 skipped, 0 failed**;
  `ruff check .` — clean; `python -m compileall -q stocks scripts` — clean;
  `git diff --check` — clean.

## Live/user-value gates — pending, not locally completable

The A0–A6 roadmap's five-day shadow-parity gate (`ROADMAP.md` A3) and
ten-trading-day user-value gate (`ROADMAP.md` A5) require live trading days
and explicit user sign-off. Nothing in this repository can satisfy them
locally; do not mark them done from a local run.



## Remaining work (after TASK-001E1/E2)

The following are the next concrete tasks to reach a production-ready A5/A6
execution-closed loop. They are not inferred from ROADMAP.md; they derive from
what is actually missing after E1/E2 landed:

1. **TASK-002 — user-confirmed advice records (A6 execution feedback entry).**
   Write an `AdviceRecord` datamodel and atomic writer under `stocks/advice/`
   (or `stocks/memory/`), with a deterministic generator that takes the E1/E2
   push payload and produces a user-confirmable draft: `advice_id`,
   `action_id`, `instrument_identity`, `final_ratio`, `executable_quantity`,
   `estimated_amount_cny`, `platform`, `settlement`, `cancel_condition`,
   `expires_at`. The record is **draft only**; never auto-execute. Smoke check:
   run the writer against a sample payload and read it back.

2. **TASK-003 — execution adapter skeleton and mock execution sink.** Add a
   `stocks/execution/` module with a `MockExecutionAdapter` (writes a receipt
   to a temp directory) and a `BrokerExecutionAdapter` stub that raises
   `NotImplementedError` with a clear "real broker integration not configured"
   message. Wire the adapter selection to config; default is mock. No real
   orders are ever placed by this project. Smoke check: execute a confirmed
   mock record and verify the receipt round-trips.

3. **TASK-004 — end-to-end smoke from payload to advice record to mock receipt.**
   A single CLI script `scripts/smoke_advisory_to_receipt.py` that reads a
   `.local/push_payloads/latest/*.json`, runs `validate_push_truth`, renders
   the E2 report, mints an `AdviceRecord`, prompts for mock confirmation,
   writes the receipt, and prints the receipt path. This proves the loop is
   closed locally without touching real money or Feishu production.

4. **TASK-005 — A2/A5 production migration feasibility audit.** Before
   switching to the Advisory shadow pipeline for production push, run the
   existing shadow scripts for 5 consecutive trading days and compare the
   shadow output against the current rule-driven output (A3 shadow gate). Then
   run 10 consecutive trading days of side-by-side comparison before retiring
   the legacy rule-driven report (A5 user-value gate). These gates cannot be
   satisfied locally; this task is the audit harness and checklist, not the
   live days themselves.

## Advisory shadow pipeline (A0–A4) — implemented, shadow-only

Confirmed by reading code and git history in this session: `advisory_models.py`,
`unified_snapshot.py`, `advisory_contract.py`, `advisory_synthesizer.py`,
`advisory_shadow_store.py`, `asset_intake_parser.py`, `llm_asset_intake.py`,
`asset_intake_writer.py`, `scripts/run_shadow_advisory.py`, and
`scripts/compare_advisory_paths.py` all exist, are committed, and produce
real artifacts under `.local/advisory_shadow/`. None of them is wired into
any production adapter or the push path (verified by grep — no caller
outside the shadow scripts and tests). See `docs/contracts/README.md` for
per-contract lifecycle labels.
