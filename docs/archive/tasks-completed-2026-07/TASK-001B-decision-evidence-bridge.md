# TASK-001B — decision evidence bridge and layer direction

> Current task. Depends on completed TASK-001A. This slice fixes the data bridge and dependency direction only; it does not change user-visible cash/risk/action rendering.

## Objective

1. `portfolio_adjudicator.py` must not import from `presentation.py`.
2. The adjudicator must receive the complete authoritative position facts it already needs: `instrument_key`, `holding`, `valuation_method`, evidence/freshness, classification, liquidity, and CNY valuation.
3. Every approved action path must use one shared finalizer fed by that complete evidence.
4. Add tests proving the bridge carries real quantity/instrument/valuation facts and the current minimum-unit/amount code no longer silently receives empty inputs.

## Allowed files

- `stocks/engine/portfolio_adjudicator.py`
- `stocks/engine/scheduled_analysis.py`
- one new neutral helper module under `stocks/engine/` only if needed to host valuation freshness semantics
- `stocks/engine/presentation.py` only to change an import to the neutral helper; no behavior/output changes
- `tests/engine/test_portfolio_adjudicator.py`
- `tests/engine/test_scheduled_analysis.py`
- `tests/engine/test_presentation.py` only for neutral-helper import compatibility
- `STATUS.md` once after verification

Preserve Task-0 and TASK-001A changes.

## Required implementation

### Dependency direction

Move `freshness_is_estimate` out of presentation into a neutral domain/engine helper. Both adjudicator and presentation may import that helper. The decision layer must never import the presentation layer. No duplicated implementations.

### Complete evidence bridge

When `scheduled_analysis.py` creates adjudicator `evidences`, copy the authoritative fields from the single `position_valuations` record without recomputation:

- `position_id`
- `instrument_key`
- `holding` including `quantity` and unit when present
- `valuation_method`
- `market_value_cny`
- `classification`
- `liquidity` including `tier`, `redemption_rule`, `lockup_until`, `maturity_date`, `tradable`, `rebalance_eligible`
- `evidence` including price freshness/data anomalies

Do not invent defaults that turn missing facts into valid execution facts. Missing quantity remains missing and must be visible to later logic.

### Approved-action producer

All approved action construction paths must route through `_finalize_approved_action`. Suppressed actions are out of scope. Do not yet redesign settlement/minimum-unit formulas; this task only makes their required inputs real and testable.

## Required tests

1. Scheduled-run bridge test with a realistic A-share valuation asserts adjudicator receives `instrument_key`, `holding.quantity`, `valuation_method`, liquidity/redemption, and evidence freshness unchanged.
2. Adjudicator test proves `_finalize_approved_action` sees real A-share quantity and returns a non-`None` executable quantity (do not assert final production rounding policy yet).
3. Adjudicator amount test proves `estimated_amount_cny` uses the supplied authoritative valuation and action ratio.
4. Architecture test/import inspection proves `portfolio_adjudicator.py` no longer imports `presentation`.
5. Existing focused tests remain green.

Run:

```bash
.venv/bin/pytest -q -o 'addopts=' \
  tests/engine/test_portfolio_adjudicator.py \
  tests/engine/test_scheduled_analysis.py \
  tests/engine/test_presentation.py
.venv/bin/ruff check stocks/engine/portfolio_adjudicator.py stocks/engine/scheduled_analysis.py \
  stocks/engine/presentation.py tests/engine/test_portfolio_adjudicator.py \
  tests/engine/test_scheduled_analysis.py
.venv/bin/python -m compileall -q stocks/engine/portfolio_adjudicator.py \
  stocks/engine/scheduled_analysis.py stocks/engine/presentation.py
```

## Non-goals

- Do not choose or change production minimum-trade-unit policy.
- Do not finalize product settlement/redemption policy.
- Do not change cash buckets, risk labels, or user-view fields.
- Do not generate real reports yet.
- Do not touch Snapshot/Advisory/Shadow.
- Do not commit/push.

## Stop criteria

- No decision-to-presentation import.
- Complete evidence fields reach adjudicator unchanged, proven by new tests.
- Existing approved action paths share one finalizer.
- Focused tests, ruff, compile, and diff check pass.
- Do not start TASK-001C.

## Outcome

Completed locally on 2026-07-29. The neutral valuation-freshness helper is active; the complete evidence bridge is called by the production path; missing quantity remains missing; approved actions use the shared finalizer. Verification: `103 passed, 6 skipped`; ruff, compileall, and diff check pass.
