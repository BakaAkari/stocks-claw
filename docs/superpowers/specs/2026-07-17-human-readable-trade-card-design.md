# Human-Readable Trade Card and Private Assistant Design

## Target

Every CN/US trading-window push starts with a deterministic **交易指令卡** that can be understood within 30 seconds, followed immediately by a **私人投资助理说明**. The feature improves presentation only: it does not auto-trade, add new action authority, or allow the Agent to derive new actions from internal fields.

## Approved Product Decisions

- Top block: trading instruction card.
- Second block: private investment assistant explanation.
- When a main-plan window has no `approved_actions`, show `今日无需操作` plus the 1–2 most important deterministic reasons.
- Watch windows remain silent when `window_delta.material=false` and no approved action exists.
- Instrument label: real name plus public trading/fund code, e.g. `化工ETF（516020）`.
- Action card shows ratio plus estimated CNY amount. Non-current data is labelled `估算`.

## Unconditional Invariants

1. Only `portfolio_decision.approved_actions` may become executable trade instructions.
2. `suppressed_actions`, `unresolved_conflicts`, and `research_candidates` never become instructions.
3. Machine IDs (`position_id`, `decision_id`), English enums, anomaly codes, and liquidity tier values do not appear in the ordinary user body.
4. Machine fields remain in the artifact for audit and linkage.
5. Display names, codes, labels, estimated amounts, and translations are generated deterministically in Python, not improvised by the Agent.
6. Estimated action amount equals `market_value_cny * ratio`; no amount may be extracted from rationale or free text.
7. Manual valuation, fund NAV, previous-close, stale, old, or unknown price evidence marks the amount as `估算`.
8. The private-assistant section may explain approved/suppressed/research information but may not create new actions.
9. The v5 five-field trust boundary remains unchanged: `window_delta`, `portfolio_decision`, `risk_state`, `data_boundaries`, `research_candidates`.
10. All Feishu output uses only bold, inline code, bullets, links, and blank lines.

## Architecture

`position_valuations/action_cards → adjudicate_portfolio → enrich_portfolio_decision_for_display → portfolio_decision.user_view → agent_task/Markdown → Push Agent`

### Deterministic presentation layer

Add a focused module `stocks/engine/presentation.py` with:

- `public_instrument_code(instrument_key: str, product_type: str = "") -> str`
- `display_label(display_name: str, instrument_key: str, product_type: str = "") -> str`
- `signal_label(signal: str) -> str`
- `status_label(status: str) -> str`
- `risk_label(level: str) -> str`
- `freshness_is_estimate(evidence: dict, valuation_method: str) -> bool`
- `anomaly_display(anomaly: dict) -> dict`
- `build_user_view(portfolio_decision: dict, position_valuations: list[dict], position_reviews: list[dict], research_candidates: list[dict], risk_state: dict, session: ScheduledSession) -> dict`

`build_user_view` returns:

- `instruction_card`
  - `status`: `action_required | no_action | manual_review`
  - `status_label`
  - `actions[]` (max 3): `display_label`, `action_label`, `ratio`, `estimated_amount_cny`, `amount_is_estimate`, `reason_summary`, `cancel_condition`, `settlement_display`, `next_checkpoint`
  - `no_action_reasons[]` (max 2)
  - `next_checkpoint`
- `assistant_brief`
  - `why[]`
  - `do_not_do[]`
  - `cash`: user-readable immediate/settling/strategic/locked values
  - `risk`: Chinese state, transition, suspension and release condition
  - `research[]`: user-readable labels and action hints

The structure lives inside `portfolio_decision.user_view`, preserving the five-field v5 boundary.

## Naming Rules

- `a:516020` → `516020`
- `us:NVDA` → `NVDA`
- `fund:012345` or a fund-code field → `012345`
- No usable public code → name only; never fall back to `position_id` in user output.
- The display label is `名称（代码）` when code exists, otherwise `名称`.

## Language Mapping

- `stop_loss` → `止损`
- `take_profit` → `止盈`
- `reduce` → `减仓`
- `add` → `加仓`
- `hold` → `持有`
- `review_required` → `等待人工确认`
- `hedge` → `防御状态`
- `reduce` risk → `降低风险`
- `watch` → `观察状态`
- `normal` → `常态`
- anomaly codes map to stable Chinese `display_message` + `user_impact`; raw code remains audit-only.

## Report Layout

### 交易指令卡

- A status line: `需要操作` / `今日无需操作` / `等待人工确认`.
- Up to 3 approved actions.
- Each action: real name + code, Chinese action, ratio, estimated amount, timing, cancellation condition.
- If no action: 1–2 deterministic reasons and next checkpoint.

### 私人投资助理

- Why the instruction card reached this result.
- What must not be done now.
- Four cash buckets in natural Chinese.
- Current risk state and release condition.
- Research candidates as observation only.

## Window Policy

- Main plan/digest: always emit a concise card when artifact guard passes, including no-action cards.
- Open/mid-session watch: silent only when no material delta, no approved action, and no new manual-review condition.
- Pre-close: silent when no approved action and no new conflict; otherwise emit.
- Intelligence patrol keeps its separate non-trading format.

## Failure Handling

- Missing display name: use public code if available; otherwise `未命名持仓`, never expose `position_id`.
- Missing market value: amount omitted and marked `金额待确认`.
- Missing/unknown valuation freshness: estimated flag true.
- Missing user-view fields: artifact validation fails closed for trading push after rollout.

## Acceptance Criteria

1. No user report body contains patterns such as `a_516020`, `ccb_wmp`, a 16-hex decision ID, or raw anomaly codes.
2. Every approved action contains `display_label`, Chinese action, ratio, and amount/amount-status.
3. No-action main window contains `今日无需操作` and 1–2 reasons.
4. QDII research-only signals appear only in the private-assistant research area with real fund name/code.
5. CN/US force-runs preserve all v5 trust fields and generate `portfolio_decision.user_view`.
6. Deterministic Markdown and actual Push Agent both render the trade card before the assistant section.
7. Full tests, Ruff, compileall, 11-session force-run, 8-prompt validation, counterexample scan, and independent code/trader review pass.
