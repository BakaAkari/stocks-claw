# TASK-001C — configuration-driven execution rule resolver

## Objective

Replace hardcoded settlement/minimum-unit guesses with one configuration-driven resolver. Missing or ambiguous rules fail closed as `review_required`; they never become a fabricated T+N or executable quantity.

## Facts established before implementation

- All 25 current positions have empty `liquidity.redemption_rule`.
- No execution-rule configuration exists.
- Existing code conflicts: fund-platform timing appears as both T+1 and T+2.
- A replacement-chain buy ratio is portfolio-based, while ordinary action ratios are position-based. Until a quantity basis is explicit, replacement buys must not claim an executable quantity.

## Allowed files

- `stocks/config/engine.yaml`
- `stocks/engine/config_loader.py`
- new `stocks/engine/execution_rules.py`
- `stocks/engine/portfolio_adjudicator.py`
- `stocks/engine/scheduled_analysis.py`
- `tests/engine/test_execution_rules.py`
- `tests/engine/test_portfolio_adjudicator.py`
- `tests/engine/test_scheduled_analysis.py`
- `STATUS.md` after verification

## Contract

Configuration contains ordered settlement and quantity rules. Match fields may include market, institution_type, product_type, liquidity_tier, holding_unit, and side. First matching rule wins. Code defaults are empty/fail-closed; production YAML contains explicit current rules.

Resolution returns:

- `settlement_rule`: `T+0`, `T+1`, `T+2`, `periodic_open`, `locked`, or `review_required`
- `quantity_step`: positive number or null
- `execution_status`: `full`, `adjusted_to_step`, `deferred_min_unit`, `review_required`
- `reason`: machine-auditable explanation
- `rule_id`: matched config rule

Precedence:

1. Non-tradable/locked facts block execution.
2. A position-level `redemption_rule`, when present, is authoritative but must be mapped explicitly; unparseable free text yields review_required.
3. Explicit config rule.
4. No match → review_required.

Quantity:

- Compute from authoritative position quantity and position-ratio only.
- Config determines quantity step. No market hardcoding in Python.
- Missing quantity/rule → review_required.
- Replacement-chain buys are review_required for executable quantity until their portfolio-value ratio basis is modeled explicitly. Do not use target holding quantity × portfolio ratio.

## Production YAML rules to encode explicitly

Settlement:

- cash/t0 → T+0
- brokerage+t1 → T+1
- fund_platform+t2_plus → T+2
- bank+t0 → T+0
- bank+periodic_open → periodic_open (not immediately executable)
- insurance/locked → locked
- unmatched → review_required

Quantity steps:

- A-market brokerage share add: 100; reduce/stop/take-profit: 1
- US brokerage share: 1 (conservative whole-share policy; config may later change)
- fund_platform share: 0.01
- bank gram: 0.01
- unmatched → review_required

## Tests

- Rule precedence and fail-closed behavior.
- Every production YAML rule resolves as declared.
- Unknown product/account never receives guessed settlement/quantity.
- A-share configured step adjusts/defer behavior.
- US whole-share config.
- Missing quantity remains review_required.
- Replacement-chain buy does not claim executable quantity.
- Existing focused adjudicator/scheduled tests stay green.

## Non-goals

- Do not change user_view projection, cash display, or risk labels.
- Do not populate private asset redemption_rule fields.
- Do not infer broker fractional-share eligibility.
- Do not touch Advisory/Shadow.
- Do not commit/push.

## Verification

```bash
.venv/bin/pytest -q -o 'addopts=' tests/engine/test_execution_rules.py \
  tests/engine/test_portfolio_adjudicator.py tests/engine/test_scheduled_analysis.py
.venv/bin/ruff check stocks/engine/execution_rules.py stocks/engine/portfolio_adjudicator.py \
  stocks/engine/scheduled_analysis.py tests/engine/test_execution_rules.py
.venv/bin/python -m compileall -q stocks/engine/execution_rules.py \
  stocks/engine/portfolio_adjudicator.py stocks/engine/scheduled_analysis.py
```
