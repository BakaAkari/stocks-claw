# TASK-M2 — outlook mainline (advisory into production push)

## Objective

Wire the LLM Investment Analyst path (`advisory_synthesizer`, today
shadow-only) into the production push for primary sessions, replacing the
constrained `structured_outlook` as the source of forward-looking judgment
in the report's 走势研判 section. Failure downgrades to 研判待复核 —
never fabricate.

## Scope

- Extend `InvestmentAdvisory` with typed short-term (3-7 day) and
  medium-term (1-3 month) outlook objects: direction, confidence,
  rationale, drivers, validation condition, falsification condition,
  source_refs. Direction vocabulary: `supportive | neutral | adverse |
  uncertain | mixed` (matches the renderer's `_DIRECTION_LABELS`).
- New `stocks/engine/advisory_mainline.py`: orchestrate snapshot →
  synthesis → validation → projection into the outlook display shape that
  `presentation._OUTLOOK_ALLOWED_TOP` already whitelists (`status`,
  `summary`, `near_term`, `medium_term`, `asset_views`, `sector_views`,
  `scenarios`, `source_refs`, `data_limitations`, `message`,
  `generated_at`).
- Freshness gate before synthesis: primary-market quotes stale/missing or
  snapshot older than 90 minutes → unavailable, no LLM call.
- No LLM client (key/endpoint unconfigured) → unavailable
  (`研判待复核：LLM 分析端未配置`), no fallback fabrication.
- `validate_advisory` receipt with `errors` → unavailable
  (`研判待复核：本期研判未通过校验`).
- `scheduled_analysis` primary sessions consume `build_advisory_outlook`
  when `llm.advisory_mainline.enabled` (default true, config-driven);
  `run["structured_outlook"]` keeps the same top-level shape so
  observation-window `compute_outlook_delta` and
  `build_forecast_candidates` keep working unchanged.
- Rule engine adjudicator and instruction_card actions are untouched:
  advisory informs judgment only, never action selection.
- Contracts: flip `UnifiedAnalysisSnapshot` / `InvestmentAdvisory` /
  `AdvisoryValidationReceipt` to PRODUCTION in `docs/contracts/README.md`
  (`AdvisoryShadowRun` stays SHADOW).
- Renderer fallback line updates from "等待 M2 上线" to 研判待复核 wording.

## Non-goals (must not do in this task)

- Replacing rule-driven action selection or the adjudicator.
- Feeding advisory `actions`/`watchlist_candidates` into 提前布局 or
  instruction_card (research candidates remain rule-driven).
- Feedback loop (M3), constraint model (M4).
- Shadow-parity 5-day replay — the shadow gate requires 5 consecutive
  trading days of live runs; record it as pending in STATUS.md.
- Any execution surface.

## Acceptance

Tests must prove:

1. With a fake llm_client returning a valid advisory JSON, a primary
   session's `run["structured_outlook"]` has `status == "ok"` and carries
   near_term/medium_term with direction/confidence/rationale plus
   scenarios and source_refs; user_view outlook projects the same fields.
2. No LLM client → outlook `unavailable` with a sanitized Chinese
   `研判待复核` message; no exception escapes; report renders the fallback.
3. Stale primary-market quotes → `unavailable` and the LLM client is
   never called.
4. Receipt with errors (e.g. scenario missing required fields) →
   `unavailable` with the 未通过校验 message.
5. Observation-window delta still computes from two advisory-based
   primary outlooks (shape compatibility).
6. `llm.advisory_mainline.enabled: false` restores the legacy
   OutlookSynthesizer path.
7. Rendered six-section report passes `validate_payload_text` with an
   advisory-based outlook (numbers in 走势研判 remain upstream-validated,
   other sections unchanged).
8. Focused tests, full suite, ruff, compileall, diff-check.

## Files likely to touch

- `stocks/domain/advisory_models.py` — AdvisoryOutlook + advisory fields.
- `stocks/engine/advisory_synthesizer.py` — prompt + parsing.
- `stocks/engine/advisory_contract.py` — outlook validation.
- `stocks/engine/advisory_mainline.py` — new orchestration module.
- `stocks/engine/scheduled_analysis.py` — primary-session wiring.
- `stocks/engine/config_loader.py` — `llm.advisory_mainline` defaults.
- `scripts/build_push_payload.py` — fallback wording.
- `docs/contracts/README.md` — contract labels.
- `tests/engine/test_advisory_mainline.py` — new; plus renderer /
  scheduled-analysis test updates.

## Smoke check

```bash
.venv/bin/python -m stocks.adapters.cli \
  --scheduled-run-session cn_after_close \
  --now "2026-07-30T15:00:00+08:00" --force --output json

.venv/bin/python scripts/run_push_report.py \
  --session cn_after_close \
  --now "2026-07-30T15:05:00+08:00" \
  --payload-root /tmp/stocks-claw-m2-check
```

With no LLM endpoint configured the report must show 研判待复核 in
走势研判 and still pass the push validators; with a configured endpoint
(ad-hoc, not committed) the section shows real short/medium judgments.
Record which case was verified in STATUS.md.
