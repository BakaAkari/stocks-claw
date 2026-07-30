# TASK-001A — single-build user_view and one valuation source

> Current task. This is the first bounded slice of the larger production-truth-model work. It does not implement cash buckets, settlement rules, trade units, or new action provenance fields.

## Objective

Remove duplicate producer paths before adding new financial semantics:

1. `StocksEngine.build_context()` builds `position_valuations` exactly once.
2. A scheduled run builds `portfolio_decision.user_view` exactly once, after `structured_outlook` / `outlook_delta` are known.
3. Nothing mutates `user_view`, `instruction_card`, or `assistant_brief` after `build_user_view()` returns.
4. Existing direct callers of `build_scheduled_run()` keep a deterministic compatibility path without creating a second production build in `ScheduledAnalysisRunner`.

## Allowed files

- `stocks/engine/context_builder.py`
- `stocks/engine/scheduled_analysis.py`
- `tests/engine/test_context_builder.py`
- `tests/engine/test_scheduled_analysis.py`
- `tests/engine/test_presentation.py` only if an existing signature assertion needs adjustment
- `STATUS.md` once, after verification

No other files may change in this task. Preserve existing Task-0 changes already present in `scheduled_analysis.py`.

## Required implementation

### One valuation build

Today `context_builder.py` calls `_build_position_valuations()` before feature/signal construction and again after `action_signals` exist. Replace this with one authoritative final call. Any earlier consumers that need portfolio assets before action signals must use position facts or a deliberately named preliminary non-valuation projection; they must not create a second authoritative valuation list.

The final `AnalysisContext.position_valuations`, `exposure_summary`, `liquidity_summary`, `asset_data_boundaries`, `advice_granularity`, raw prompt, serialized context, and scheduled analysis must all reference the same final list object/content.

### One user_view build

`ScheduledAnalysisRunner.run_session()` must call `build_scheduled_run(..., attach_user_view=False)`, synthesize outlook/delta, then call `build_user_view()` once. Remove all post-build field patching.

`build_scheduled_run()` may retain `attach_user_view=True` only as a compatibility entry for direct/unit-test callers. Add tests proving the runner production path invokes `build_user_view()` once and direct compatibility invocation also invokes it once, never twice.

## Tests

Add explicit tests that fail without this task:

1. Monkeypatch/count `_build_position_valuations()` during a real `build_context()` call: exactly one call.
2. Assert the final valuation list is the one used by downstream summaries/context fields.
3. Monkeypatch/count `scheduled_analysis.build_user_view` in `run_session()`: exactly one call.
4. Verify primary outlook is present in the returned `user_view` from that single call.
5. Verify observation delta, when emitted, is present from that single call.
6. Search/assert no assignment or mutation path writes into `assistant_brief.outlook` / `outlook_delta` after construction.

Run:

```bash
.venv/bin/pytest -q -o 'addopts=' \
  tests/engine/test_context_builder.py \
  tests/engine/test_scheduled_analysis.py \
  tests/engine/test_presentation.py
.venv/bin/ruff check stocks/engine/context_builder.py stocks/engine/scheduled_analysis.py \
  tests/engine/test_context_builder.py tests/engine/test_scheduled_analysis.py
.venv/bin/python -m compileall -q stocks/engine/context_builder.py stocks/engine/scheduled_analysis.py
```

## Non-goals

- Do not edit `portfolio_adjudicator.py` or `presentation.py` financial behavior.
- Do not add/rename action fields or cash buckets.
- Do not implement settlement or minimum trade units.
- Do not alter risk labels.
- Do not change Task-0 audit/outlook/dedup logic.
- Do not generate reports yet; real report verification belongs after the financial semantics slices.
- Do not commit/push.

## Stop criteria

- Exactly one valuation build in `build_context()` proven by a new test.
- Exactly one user-view build in runner production path proven by a new test.
- Outlook/delta enters at construction, never via mutation.
- Focused tests, ruff, and compile pass.
- Only allowed files changed for this slice.
- Do not start TASK-001B.

## Outcome

Completed locally on 2026-07-29. Verification:

- `position_valuations` authoritative build count: exactly 1 (new test).
- Scheduled runner `build_user_view` count: exactly 1 (new test).
- Direct compatibility `build_scheduled_run` count: exactly 1 (new test).
- Primary Outlook is supplied during the single construction call.
- Focused tests: `90 passed, 6 skipped`.
- Ruff, compileall, and `git diff --check`: pass.
