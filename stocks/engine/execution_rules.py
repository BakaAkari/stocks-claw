"""Configuration-driven settlement and executable-quantity resolution.

No market/product defaults live in Python.  A caller must supply explicit
rules; missing, ambiguous, or incomplete facts fail closed as review_required.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Any

_ALLOWED_SETTLEMENT = {"T+0", "T+1", "T+2", "periodic_open", "locked", "review_required"}
_EXECUTABLE_SETTLEMENT = {"T+0", "T+1", "T+2"}


@dataclass(frozen=True)
class ExecutionResolution:
    settlement_rule: str
    quantity_step: float | None
    executable_quantity: float | None
    final_ratio: float
    execution_status: str
    reason: str
    rule_id: str | None


def _facts(evidence: dict, card: dict, side: str) -> dict[str, Any]:
    instrument_key = str(evidence.get("instrument_key") or card.get("instrument_key") or "")
    market = instrument_key.split(":", 1)[0] if ":" in instrument_key else ""
    classification = evidence.get("classification") or {}
    liquidity = evidence.get("liquidity") or {}
    holding = evidence.get("holding") or {}
    return {
        "market": market,
        "institution_type": str(card.get("institution_type") or ""),
        "product_type": str(classification.get("product_type") or card.get("product_type") or ""),
        "liquidity_tier": str(liquidity.get("tier") or ""),
        "holding_unit": str(holding.get("unit") or ""),
        "side": side,
        "tradable": liquidity.get("tradable"),
        "rebalance_eligible": liquidity.get("rebalance_eligible"),
        "redemption_rule": liquidity.get("redemption_rule"),
        "quantity": holding.get("quantity"),
    }


def _matches(rule: dict, facts: dict) -> bool:
    match = rule.get("match") or {}
    return all(facts.get(key) == value for key, value in match.items())


def _first_rule(rules: list[dict], facts: dict) -> dict | None:
    for rule in rules:
        if isinstance(rule, dict) and _matches(rule, facts):
            return rule
    return None


def _review(reason: str, ratio: float, *, rule_id: str | None = None,
            settlement_rule: str = "review_required") -> ExecutionResolution:
    return ExecutionResolution(
        settlement_rule=settlement_rule,
        quantity_step=None,
        executable_quantity=None,
        final_ratio=ratio,
        execution_status="review_required",
        reason=reason,
        rule_id=rule_id,
    )


def _round_down(value: float, step: float) -> float:
    dec_value = Decimal(str(value))
    dec_step = Decimal(str(step))
    units = (dec_value / dec_step).to_integral_value(rounding=ROUND_DOWN)
    return float(units * dec_step)


def resolve_execution(
    *, evidence: dict, card: dict, side: str, ratio: float,
    ratio_basis: str = "position", config: dict | None,
) -> ExecutionResolution:
    """Resolve settlement and quantity from explicit facts/config only."""
    facts = _facts(evidence, card, side)

    if facts["tradable"] is False or facts["rebalance_eligible"] is False:
        token = "locked" if facts["liquidity_tier"] == "locked" else "review_required"
        return _review("position is not executable", ratio, settlement_rule=token)
    if facts["liquidity_tier"] == "locked":
        return _review("position is locked", ratio, settlement_rule="locked")
    if not isinstance(config, dict):
        return _review("execution_rules configuration is missing", ratio)

    redemption = facts.get("redemption_rule")
    if redemption:
        mapped = (config.get("redemption_rule_map") or {}).get(str(redemption))
        if mapped not in _ALLOWED_SETTLEMENT:
            return _review("unmapped position redemption_rule", ratio)
        settlement_rule = mapped
        settlement_rule_id = "position:redemption_rule"
    else:
        settlement = _first_rule(config.get("settlement_rules") or [], facts)
        if not settlement:
            return _review("no settlement rule matched", ratio)
        settlement_rule = str(settlement.get("settlement_rule") or "")
        settlement_rule_id = str(settlement.get("id") or "") or None
        if settlement_rule not in _ALLOWED_SETTLEMENT:
            return _review("matched settlement rule is invalid", ratio, rule_id=settlement_rule_id)

    if settlement_rule not in _EXECUTABLE_SETTLEMENT:
        return _review(
            f"settlement requires manual timing: {settlement_rule}", ratio,
            rule_id=settlement_rule_id, settlement_rule=settlement_rule,
        )

    if ratio_basis != "position":
        return _review(
            f"quantity basis {ratio_basis} is not modeled", ratio,
            rule_id=settlement_rule_id, settlement_rule=settlement_rule,
        )

    quantity = facts.get("quantity")
    if not isinstance(quantity, (int, float)) or quantity <= 0:
        return _review(
            "authoritative holding quantity is missing", ratio,
            rule_id=settlement_rule_id, settlement_rule=settlement_rule,
        )

    quantity_rule = _first_rule(config.get("quantity_rules") or [], facts)
    if not quantity_rule:
        return _review(
            "no quantity-step rule matched", ratio,
            rule_id=settlement_rule_id, settlement_rule=settlement_rule,
        )
    step = quantity_rule.get("quantity_step")
    if not isinstance(step, (int, float)) or step <= 0:
        return _review(
            "matched quantity-step rule is invalid", ratio,
            rule_id=str(quantity_rule.get("id") or "") or settlement_rule_id,
            settlement_rule=settlement_rule,
        )

    raw_quantity = float(quantity) * abs(float(ratio))
    executable = _round_down(raw_quantity, float(step))
    quantity_rule_id = str(quantity_rule.get("id") or "") or None
    rule_id = "+".join(x for x in (settlement_rule_id, quantity_rule_id) if x) or None
    if executable <= 0:
        return ExecutionResolution(
            settlement_rule, float(step), 0.0, 0.0, "deferred_min_unit",
            "computed quantity is below configured minimum step", rule_id,
        )
    final_ratio = executable / float(quantity)
    status = "full" if abs(executable - raw_quantity) < 1e-9 else "adjusted_to_step"
    return ExecutionResolution(
        settlement_rule=settlement_rule,
        quantity_step=float(step),
        executable_quantity=executable,
        final_ratio=final_ratio,
        execution_status=status,
        reason="matched explicit execution rules",
        rule_id=rule_id,
    )
