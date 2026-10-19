# HARDEN-03: confirmed purchase fee budgets

Implement a strict user-confirmed per-order upper-bound contract, readonly
preview/list, confirmed replace/revoke via CLI/MCP, snapshot provenance and
purchase amount plus fee checks. Unknown/expired/tampered budgets fail closed.
No broker-specific rates are invented. Synthetic A/US tests and isolated smoke.

Bounded scope: existing-position add only, with one purchase per account/currency
per proposal batch. New-position buy remains blocked; allocation constraints
remain blocked until the separate whole-portfolio projection task. No sale
proceeds or cross-account/currency cash netting. No budget consumption or orders.

No real user facts, deployment or confirmation are performed. HARDEN-02 commit
and push were explicitly authorized; HARDEN-03 commit/push are not requested.

Acceptance: confirmed budget scope, validity, quantity/notional limits, exact
Decimal arithmetic, all-in cash sufficiency, unknown/bad data blocked, immutable
snapshot evidence, unchanged proposals, no writes on preview or unconfirmed APIs.
Replace with an empty list revokes active budgets. Final README checks and real
isolated `scripts/smoke_fee_budget.py --output-dir <new external directory>`.


## Outcome

Implemented confirmed replace/revoke and readonly preview/state, strict bounded
all-in reserve checks, frozen provenance and user-facing costs, delivery validity
checks, and conservative multiple-purchase/new-buy guards. Full suite: 1907 passed,
7 skipped. Changed-file Ruff, compileall, diff check and standalone scheduled
fee-budget smoke pass; global Ruff retains 39 existing diagnostics. Actual fee
confirmation and L2-L5 acceptance remain pending; no real financial writes.
