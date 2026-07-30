# TASK-001D — user-view cash and risk projection

## Objective

Make `portfolio_decision.user_view` a pure projection of the authoritative adjudication result for cash, financial amounts, final actions, and risk labels. The decision/adjudication layer owns all financial facts; presentation only projects already-computed values.

This is one bounded continuation of TASK-001A/B/C. Do not start TASK-001E or Task 2+.

## Preconditions already implemented — preserve them

- `position_valuations` and `user_view` are built once per run.
- Approved actions carry decision/evidence/execution fields via the adjudicator.
- Settlement and executable quantity are configuration-driven and fail closed.
- Missing/unmapped settlement becomes `review_required`; never invent `T+1`.
- `build_cash_schedule` distinguishes unresolved settlement from confirmed cash.

Do not redesign or reimplement these foundations.

## Scope

1. **Five canonical user-facing cash buckets.** Project exactly these keys into `user_view` from the authoritative adjudicator cash schedule: `available_now`, `confirmed_settling`, `planned_release`, `strategic_exit`, and `locked`. Preserve CNY amounts and position IDs/provenance where supported. Unresolved settlement must not be counted in either `available_now` or `confirmed_settling`; expose it as an explicit review/data boundary without creating a sixth cash bucket.

2. **Presentation is pure projection.** Remove all financial amount derivation from `stocks/engine/presentation.py`. Presentation must not compute an amount from valuation × ratio, nor decide whether an amount is an estimate. The adjudication/decision record owns authoritative amount and freshness/estimate metadata; presentation reads those values verbatim.

3. **Final action fields pass through unchanged.** Project without recomputation or fallback: `final_ratio`, `original_ratio`, `decision_reason`, `evidence_summary`, `settlement_rule`, `executable_quantity`, `execution_status`, and authoritative amount fields. No fallback to raw ratio, action-card amount, default settlement, or display-time quantity.

4. **Exact risk labels.** All risk wording surfaced by `user_view` and `assistant_brief.risk` must come from one enumerated mapping in `presentation.py`, with these exact public labels:
   - hedge → `对冲/高风险`
   - reduce → `降风险`
   - watch → `观察`
   - normal → `常态`

   Unknown/noncanonical inputs must fail closed to an explicit review/unknown state already supported by the contract; they must not create new prose.

5. **Schema/docs sync after tests pass.** Update relevant sections in `stocks/DATA_MODEL.md` and `docs/contracts/README.md`. Update `STATUS.md` once at final handoff with actual verification evidence. Update `docs/tasks/README.md` to identify this task as current/completed.

## Explicit non-goals

- No action lifecycle (`proposed/confirmed/submitted/...`) implementation.
- No new execution-rule, settlement-rule, or quantity resolver design.
- No signal threshold, portfolio strategy, candidate selection, or outlook synthesis changes.
- No Snapshot v2, Advisory v2, validator, shadow trial, renderer switch, or execution-attribution work.
- No production report-mode switch or deployment.
- No historical artifact rewriting.
- Do not modify `.local/`, `.secret/`, `/opt/data/.env`, credentials, cron state, or real financial memory.
- Do not commit, push, merge, tag, release, or deploy.

## Allowed files

Prefer changes only in:

- `stocks/engine/portfolio_adjudicator.py`
- `stocks/engine/presentation.py`
- `stocks/engine/scheduled_analysis.py` only if plumbing is required
- `stocks/engine/valuation_freshness.py` only if metadata plumbing requires it
- `tests/engine/test_portfolio_adjudicator.py`
- `tests/engine/test_presentation.py`
- `tests/engine/test_scheduled_analysis.py`
- `tests/test_push_payload.py`
- `tests/test_run_push_report.py`
- `stocks/DATA_MODEL.md`
- `docs/contracts/README.md`
- `docs/tasks/README.md`
- `STATUS.md`

If another source file is genuinely necessary, stop and report why instead of expanding scope silently.

## Required focused tests

Add regression tests proving at minimum:

1. Each canonical cash bucket is projected from adjudicator output without recomputation.
2. Unresolved settlement is not available or confirmed-settling cash.
3. Presentation projects an intentionally supplied authoritative amount verbatim even when valuation × ratio would produce a different number.
4. All final action fields are unchanged end-to-end.
5. The four exact risk labels are emitted; unknown labels cannot invent prose.
6. Existing push payload/render tests remain compatible.

Run:

```bash
/mnt/user/code-project/stocks-claw/.venv/bin/pytest -q -o 'addopts=' \
  tests/engine/test_portfolio_adjudicator.py \
  tests/engine/test_presentation.py \
  tests/engine/test_scheduled_analysis.py \
  tests/test_push_payload.py \
  tests/test_run_push_report.py
```

Then run focused Ruff on touched Python files, `/mnt/user/code-project/stocks-claw/.venv/bin/python -m compileall -q stocks/engine`, and `git diff --check`.

Do not run the full repository suite repeatedly while implementing. Run it once at final handoff if focused verification is green.

## Real smoke

Use existing artifacts only; do not fetch live market data or write financial memory:

```bash
/mnt/user/code-project/stocks-claw/.venv/bin/python -m stocks.adapters.cli --scheduled-run-latest cn_after_close
/mnt/user/code-project/stocks-claw/.venv/bin/python -m stocks.adapters.cli --scheduled-run-latest us_after_close
```

If current CLI arguments differ, inspect CLI help and use the existing read-only equivalent. Confirm canonical cash/risk vocabulary and no amount/ratio contradiction. Do not edit artifacts to make the smoke pass.

## Stop criteria

Stop and report — do not start another task — when:

- Scope items 1–5 have regression coverage.
- Focused tests, Ruff, compileall, and `git diff --check` pass.
- Full suite has been run once and its exact result recorded.
- Both read-only smokes have been attempted and actual results recorded, including any artifact/environment blocker.
- The diff contains no unrelated cleanup and no secret/runtime-memory changes.

Final report must list files changed, exact test/smoke output, unresolved risks, and adjacent issues intentionally deferred to TASK-001E or later.
