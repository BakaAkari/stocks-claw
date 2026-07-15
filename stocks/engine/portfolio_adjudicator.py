"""Decision Trust T1 — Portfolio Adjudicator.

Task 4 deliverables:
- CashSchedule: 4-category cash classification by liquidity tier/product_type/settlement
- Action card immutability: cards must never be mutated by allocation logic
- Suppression record: below-¥800 adds produce records, not card mutations
"""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Product types that are NOT immediately accessible cash ────────────
_NON_IMMEDIATE_PRODUCT_TYPES = frozenset({
    "stock",
    "etf",
    "exchange_traded_fund",
    "short_treasury_etf",
    "qdii_fund",
    "feeder_fund",
    "mixed_fund",
    "fixed_income_plus_fund",
    "precious_metal_account",
    "precious_metal",
    "bank_wealth_management",
    "insurance_policy",
})

# ── Liquidity tiers that represent immediately accessible cash ────────
_IMMEDIATE_LIQUIDITY_TIERS = frozenset({"cash", "t0"})

# ── Locked / non-tradable liquidity tiers ────────────────────────────
_LOCKED_LIQUIDITY_TIERS = frozenset({"locked", "periodic_open"})


@dataclass
class CashSchedule:
    """Four-category cash schedule classification.

    immediate_cash:    Cash/t0 items minus safety buffer, plus T+0 sale proceeds.
    settling_cash:     Approved sale proceeds that need T+1/T+2 settlement.
    strategic_exit:    Unsold position values (stock, ETF, fund, gold) minus locked.
    locked:            Truly inaccessible assets (insurance, periodic-open WMP).
    """

    immediate_cash_cny: float = 0.0
    settling_cash_cny: float = 0.0
    strategic_exit_value_cny: float = 0.0
    locked_value_cny: float = 0.0
    safety_buffer_cny: float = 0.0
    immediate_cash_position_ids: list[str] = field(default_factory=list)
    settling_cash_position_ids: list[str] = field(default_factory=list)
    strategic_exit_position_ids: list[str] = field(default_factory=list)
    locked_position_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize to the dict format expected by downstream consumers."""
        return {
            "immediate_cash_cny": round(self.immediate_cash_cny, 2),
            "settling_cash_cny": round(self.settling_cash_cny, 2),
            "strategic_exit_value_cny": round(self.strategic_exit_value_cny, 2),
            "locked_value_cny": round(self.locked_value_cny, 2),
            "safety_buffer_cny": round(self.safety_buffer_cny, 2),
            "immediate_cash_position_ids": self.immediate_cash_position_ids,
            "settling_cash_position_ids": self.settling_cash_position_ids,
            "strategic_exit_position_ids": self.strategic_exit_position_ids,
            "locked_position_ids": self.locked_position_ids,
        }


# ── Public API ────────────────────────────────────────────────────────


def build_cash_schedule(
    position_valuations: list[dict],
    approved_sales: list[dict],
    total_value: float,
) -> dict:
    """Classify positions and approved sales into the CashSchedule model.

    Rules:
      1. Locked/periodic_open items → locked_value_cny
      2. Approved sales with T+0 settlement → immediate_cash
      3. Approved sales with T+1/T+2 settlement → settling_cash
      4. Unsold positions with liquidity=cash/t0 AND product not in
         non-immediate set → immediate_cash (before safety buffer)
      5. All other unsold positions → strategic_exit_value_cny
      6. Safety buffer (5% of total_value) deducted from immediate_cash only
    """
    total_value = total_value or 0.0
    schedule = CashSchedule()

    # --- Index approved sale ratios by position ---
    sales_by_position: dict[str, list[dict]] = {}
    for sale in approved_sales:
        sales_by_position.setdefault(sale.get("position_id", ""), []).append(sale)

    # --- Classify positions and move approved fractions to settlement buckets ---
    for item in position_valuations:
        pid = item.get("position_id", "")
        value = item.get("market_value_cny") or 0.0
        liq = item.get("liquidity") or {}
        tier = liq.get("tier", "unknown")
        classification = item.get("classification") or {}
        product_type = classification.get("product_type", "")
        sales = sales_by_position.get(pid, [])
        sold_ratio = min(1.0, sum(abs(sale.get("ratio", 0) or 0) for sale in sales))
        residual_value = value * (1.0 - sold_ratio)

        remaining_sale_ratio = 1.0
        for sale in sales:
            requested_ratio = min(abs(sale.get("ratio", 0) or 0), 1.0)
            ratio = min(requested_ratio, remaining_sale_ratio)
            remaining_sale_ratio -= ratio
            sale_value = value * ratio
            timing = (sale.get("settlement") or {}).get("timing", "T+1")
            if timing in ("T+0", "cash", "same_day"):
                schedule.immediate_cash_cny += sale_value
                schedule.immediate_cash_position_ids.append(pid)
            else:
                schedule.settling_cash_cny += sale_value
                schedule.settling_cash_position_ids.append(pid)

        if residual_value <= 0:
            continue
        if (
            tier in _LOCKED_LIQUIDITY_TIERS
            or liq.get("tradable") is False
            or (
                product_type == "insurance_policy"
                and liq.get("rebalance_eligible") is False
            )
        ):
            schedule.locked_value_cny += residual_value
            schedule.locked_position_ids.append(pid)
        elif product_type in _NON_IMMEDIATE_PRODUCT_TYPES:
            schedule.strategic_exit_value_cny += residual_value
            schedule.strategic_exit_position_ids.append(pid)
        elif tier in _IMMEDIATE_LIQUIDITY_TIERS:
            schedule.immediate_cash_cny += residual_value
            schedule.immediate_cash_position_ids.append(pid)
        else:
            schedule.strategic_exit_value_cny += residual_value
            schedule.strategic_exit_position_ids.append(pid)

    # --- Safety buffer (5% of total, from immediate_cash only) ---
    safety_target = total_value * 0.05
    applied_safety = min(schedule.immediate_cash_cny, safety_target)
    schedule.safety_buffer_cny = applied_safety
    schedule.immediate_cash_cny -= applied_safety

    return schedule.to_dict()


def build_capital_allocation_with_suppression(
    action_cards: list[dict],
    position_valuations: list[dict],
    portfolio_mapping: dict,
    liquidity_summary: dict,
    *,
    constraints: Optional[dict] = None,
    rotation_ranks: Optional[dict[str, int]] = None,
    rotation_leaders: Optional[list[dict]] = None,
) -> dict:
    """Non-mutating capital allocation with suppression records.

    Uses the fixed ``_build_capital_allocation`` (no longer mutates cards).
    Detects below-¥800 add cards that were excluded from add_candidates
    and produces suppression records instead.
    """
    from stocks.engine.scheduled_analysis import _build_capital_allocation

    # Identify which add cards would be suppressed (below ¥800 threshold)
    # before calling the legacy function, so we can compare after
    suppressed = []
    for card in action_cards:
        if card.get("signal") != "add":
            continue
        mv = 0.0
        for pv in position_valuations:
            if pv.get("position_id") == card["position_id"]:
                mv = pv.get("market_value_cny") or 0.0
                break
        alloc_amount = mv * abs(card.get("ratio", 0))
        if alloc_amount < 800:
            suppressed.append({
                "position_id": card["position_id"],
                "reason": "below_minimum_amount",
                "message": (
                    f"分配金额 ¥{alloc_amount:.0f} 低于 ¥800 有效下限，仅作观察不执行"
                ),
                "original_signal": card["signal"],
                "original_ratio": card["ratio"],
                "alloc_amount_cny": round(alloc_amount, 2),
            })

    # Deep copy so the legacy function absolutely cannot mutate originals
    result = _build_capital_allocation(
        copy.deepcopy(action_cards),
        copy.deepcopy(position_valuations),
        copy.deepcopy(portfolio_mapping),
        copy.deepcopy(liquidity_summary),
        constraints=constraints,
        rotation_ranks=rotation_ranks,
        rotation_leaders=rotation_leaders,
    )

    result["suppressed_adds"] = suppressed
    return result
