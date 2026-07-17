# Human-Readable Trade Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put a deterministic 30-second trading instruction card at the top of every trading push and a human-readable private-assistant explanation directly below it.

**Architecture:** Add a focused deterministic presentation module that converts existing audited v5 decision data into `portfolio_decision.user_view`. Keep machine IDs for audit but make renderers and Push Agents consume only the user view for names, actions, amounts, reasons, cash, risk, and research wording.

**Tech Stack:** Python 3.11+, dataclasses/dicts, pytest, Ruff, existing Hermes cron Push Agents.

## Global Constraints

- No new external dependency.
- v5 five-field trust boundary remains unchanged.
- Only approved actions are executable.
- Amounts use structured market value × approved ratio.
- Internal IDs/enums/codes cannot appear in ordinary user output.
- Main windows show no-action cards; watch windows retain delta silence.
- TDD RED → GREEN → real artifact → domain audit per task.

---

## File Structure

- Create `stocks/engine/presentation.py`: deterministic user-facing labels and `user_view` builder.
- Modify `stocks/engine/scheduled_analysis.py`: attach user view, change v5 Agent Task and deterministic Markdown layout.
- Modify `stocks/engine/portfolio_adjudicator.py`: preserve display metadata on PortfolioAction where needed.
- Modify `scripts/validate_push_artifact.py`: require the user-view contract after rollout.
- Modify `AGENT_GUIDE.md`, `stocks/DATA_MODEL.md`: document the display contract.
- Modify tests in `tests/engine/test_portfolio_adjudicator.py`, `tests/engine/test_scheduled_analysis.py`, `tests/test_push_artifact_guard.py`.
- Add `tests/engine/test_presentation.py`: mapping and user-view unit tests.

### Task 1: Deterministic labels and estimates

**Files:**
- Create: `stocks/engine/presentation.py`
- Create: `tests/engine/test_presentation.py`

**Interfaces:**
- Produces: `public_instrument_code`, `display_label`, `signal_label`, `risk_label`, `anomaly_display`, `freshness_is_estimate`.

- [ ] Write RED tests asserting `a:516020 → 516020`, `us:NVDA → NVDA`, names render as `化工ETF（516020）`, unknown IDs never render, all signal/risk/anomaly mappings are Chinese, and stale/manual/fund_nav evidence is estimated.
- [ ] Run `uv run pytest -q tests/engine/test_presentation.py`; expect failures because module/functions do not exist.
- [ ] Implement the pure mapping functions with explicit dictionaries and safe fallbacks.
- [ ] Run the test; expect PASS.
- [ ] Commit `feat: add deterministic trading presentation labels`.

### Task 2: Enrich PortfolioDecision with user view

**Files:**
- Modify: `stocks/engine/presentation.py`
- Modify: `stocks/engine/scheduled_analysis.py:900-1065`
- Test: `tests/engine/test_presentation.py`
- Test: `tests/engine/test_scheduled_analysis.py`

**Interfaces:**
- Consumes: `portfolio_decision.to_dict()`, `position_valuations`, `position_reviews`, `research_candidates`, `risk_state`, `ScheduledSession`.
- Produces: `build_user_view(...) -> dict`, stored at `run["portfolio_decision"]["user_view"]`.

- [ ] Add RED tests with one approved action, one anomaly suppression, one QDII research candidate, and four cash buckets.
- [ ] Assert action label, real display label, estimated amount, no raw ID, Chinese no-action reasons, natural cash labels, and max-three action cap.
- [ ] Implement lookup maps by `position_id`, deterministic amount calculation, reason selection, risk/cash/research views.
- [ ] Integrate after adjudication and before `build_agent_task` serialization.
- [ ] Run presentation + scheduled-analysis tests; expect PASS.
- [ ] Force-run `cn_pre_open`; assert `portfolio_decision.user_view` exists and no display label equals a position ID.
- [ ] Commit `feat: attach human-readable decision user view`.

### Task 3: Dual-layer v5 report contract

**Files:**
- Modify: `stocks/engine/scheduled_analysis.py:1655-1807`
- Test: `tests/engine/test_scheduled_analysis.py`

**Interfaces:**
- Consumes: `portfolio_decision.user_view`, plus `window_delta`, `risk_state`, `data_boundaries`, `research_candidates` only for cross-checks.
- Produces: two-section `agent_task.output_structure`: `交易指令卡`, `私人投资助理`.

- [ ] Add RED tests asserting the first section is `交易指令卡`, the second is `私人投资助理`, Agent instructions ban `position_id`, `decision_id`, raw enums/codes, and main no-action windows must display reasons.
- [ ] Implement revised `must_answer`, `must_not_do`, `data_reference`, and layout while keeping exactly five referenced top-level fields.
- [ ] Add explicit watch-window silence and main-window no-action policies.
- [ ] Run all configured-session contract tests; expect PASS.
- [ ] Commit `feat: define trade-card-first agent contract`.

### Task 4: Deterministic Markdown renderer

**Files:**
- Modify: `stocks/engine/scheduled_analysis.py:1826-2000`
- Test: `tests/engine/test_scheduled_analysis.py`
- Test: `tests/engine/test_report_contract.py`

**Interfaces:**
- Consumes: `portfolio_decision.user_view` only for user-facing action/name/reason/amount rendering.
- Produces: Markdown with `交易指令卡` before `私人投资助理`.

- [ ] Add RED tests using hostile internal values (`a_516020`, `ccb_wmp`, hex decision ID, raw anomaly codes) and assert none occur in Markdown.
- [ ] Assert real labels, ratio + estimated amount, no-action reasons, four natural cash buckets, risk explanation, and research area.
- [ ] Replace old five-section renderer with two top-level user sections and small subsections inside the assistant block.
- [ ] Run report tests; expect PASS.
- [ ] Force-run CN and US and inspect Markdown files.
- [ ] Commit `feat: render human-readable trade cards`.

### Task 5: Push guard and cron prompts

**Files:**
- Modify: `scripts/validate_push_artifact.py`
- Modify: `tests/test_push_artifact_guard.py`
- External config: `/mnt/user/appdata/hermes/cron/jobs.json` through Hermes CLI.

**Interfaces:**
- Guard requires `portfolio_decision.user_view.instruction_card` and `.assistant_brief`.
- Push prompt says render user_view exactly; never expose machine fields.

- [ ] Add RED guard tests for missing/malformed user view.
- [ ] Implement fail-closed validation.
- [ ] Update all 8 Push prompts through `/opt/hermes/.venv/bin/hermes cron edit`.
- [ ] Assert every prompt includes card-first order, real-name/code policy, amount estimate policy, internal-field ban, main/watch silence policy.
- [ ] Manually run one main and one watch Push Agent; inspect outputs.
- [ ] Commit repository changes `fix: require user-readable push contract`.

### Task 6: Documentation and full real acceptance

**Files:**
- Modify: `AGENT_GUIDE.md`
- Modify: `stocks/DATA_MODEL.md`
- Modify: `docs/T1_DECISION_TRUST_ACCEPTANCE_20260715.md`

- [ ] Document `portfolio_decision.user_view`, naming/amount/mapping rules, two-layer layout, and audit boundary.
- [ ] Force-run all 11 trading sessions using market-appropriate `--now` values.
- [ ] Programmatically scan every JSON/Markdown for leaked `position_id`, `decision_id`, raw anomaly code, raw signal/risk/liquidity enum in user-facing fields.
- [ ] Verify QDII research-only remains non-executable and rendered by real fund name/code.
- [ ] Verify no-action main windows show two reasons and watch windows can SILENT.
- [ ] Run `uv run ruff check .`, full pytest, compileall, and `git diff --check`.
- [ ] Dispatch independent code reviewer and trader-perspective reviewer.
- [ ] Fix all P0/P1 findings, rerun gates, commit, push, and verify remote SHA.

## Stop Conditions

- Any `suppressed_action` appears in instruction-card actions.
- Any user body contains a raw position ID or decision ID.
- Any estimated amount is not exactly structured market value × approved ratio.
- Any QDII research-only signal appears as an executable instruction.
- Any v5 data reference expands beyond the five trusted fields.
- Main/watch delivery policy deviates from the approved window rules.
