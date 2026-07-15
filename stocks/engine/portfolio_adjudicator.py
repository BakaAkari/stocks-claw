"""Decision Trust T1 — Portfolio Adjudicator.

Task 4 deliverables:
- CashSchedule: 4-category cash classification by liquidity tier/product_type/settlement
- Action card immutability: cards must never be mutated by allocation logic
- Suppression record: below-\uffe5800 adds produce records, not card mutations

Task 5 deliverables:
- PortfolioDecision with status (approved/suppressed/review_required)
- Stable decision_id via SHA256(run_id + position_id + raw_signal + raw_ratio + rule_version)
- Replacement chains (sale leg + buy leg + settlement timing + post_trade_ratio)
- Six RED fixture classes verified
"""
from __future__ import annotations

import copy
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Optional

from stocks.engine.quant_action import _TAG_TO_BUCKET

logger = logging.getLogger(__name__)

# Product types that are NOT immediately accessible cash
_NON_IMMEDIATE_PRODUCT_TYPES = frozenset({
    "stock", "etf", "exchange_traded_fund", "short_treasury_etf",
    "qdii_fund", "feeder_fund", "mixed_fund", "fixed_income_plus_fund",
    "precious_metal_account", "precious_metal", "bank_wealth_management",
    "insurance_policy",
})

# Liquidity tiers that represent immediately accessible cash
_IMMEDIATE_LIQUIDITY_TIERS = frozenset({"cash", "t0"})

# Locked / non-tradable liquidity tiers
_LOCKED_LIQUIDITY_TIERS = frozenset({"locked", "periodic_open"})

# Default rule version for stable decision_id
_DEFAULT_RULE_VERSION = "decision-trust-t1-v1"

# Signals that are reduce/stop directions
_REDUCE_SIGNALS = frozenset({"reduce", "stop_loss", "take_profit"})

# Signals that are add/increase directions
_ADD_SIGNALS = frozenset({"add"})


@dataclass
class CashSchedule:
    """Four-category cash schedule classification."""
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


# Task 5: Portfolio Decision Types


@dataclass
class PortfolioAction:
    """A single approved or suppressed portfolio action."""
    position_id: str
    signal: str
    action_description: str
    ratio: float
    decision_id: str
    reason: str
    settlement_timing: Optional[str] = None
    post_trade_ratio: Optional[float] = None
    alternative_position_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "position_id": self.position_id,
            "signal": self.signal,
            "action_description": self.action_description,
            "ratio": self.ratio,
            "decision_id": self.decision_id,
            "reason": self.reason,
            "settlement_timing": self.settlement_timing,
            "post_trade_ratio": self.post_trade_ratio,
            "alternative_position_id": self.alternative_position_id,
        }


@dataclass
class ReplacementChain:
    """A sale-and-buy pair that resolves a constraint conflict."""
    sale_leg: PortfolioAction
    buy_leg: PortfolioAction
    settlement_timing: str
    post_trade_ratio: float
    reason: str

    def to_dict(self) -> dict:
        return {
            "sale_leg": self.sale_leg.to_dict(),
            "buy_leg": self.buy_leg.to_dict(),
            "settlement_timing": self.settlement_timing,
            "post_trade_ratio": self.post_trade_ratio,
            "reason": self.reason,
        }


@dataclass
class PortfolioDecision:
    """The result of adjudicating a portfolio.

    Status is one of: approved, suppressed, review_required.
    approved/suppressed/review_required are mutually exclusive.
    When unresolved_conflicts is non-empty, status must NOT be approved.
    """
    status: str
    decision_id: str
    approved_actions: list[PortfolioAction] = field(default_factory=list)
    suppressed_actions: list[PortfolioAction] = field(default_factory=list)
    replacement_chains: list[ReplacementChain] = field(default_factory=list)
    unresolved_conflicts: list[dict] = field(default_factory=list)
    rule_version: str = _DEFAULT_RULE_VERSION

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "decision_id": self.decision_id,
            "rule_version": self.rule_version,
            "approved_actions": [a.to_dict() for a in self.approved_actions],
            "suppressed_actions": [a.to_dict() for a in self.suppressed_actions],
            "replacement_chains": [c.to_dict() for c in self.replacement_chains],
            "unresolved_conflicts": self.unresolved_conflicts,
        }


# Decision ID


def make_decision_id(
    run_id: str,
    position_id: str,
    raw_signal: str,
    raw_ratio: float,
    rule_version: str = _DEFAULT_RULE_VERSION,
) -> str:
    """Stable decision_id = sha256(run_id + position_id + raw_signal + raw_ratio + rule_version)[:16]."""
    raw = f"{run_id}{position_id}{raw_signal}{raw_ratio}{rule_version}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# Evidence helpers


def _get_exposure_buckets(ev: dict) -> set[str]:
    classification = ev.get("classification", {})
    tags = classification.get("exposure_tags", [])
    buckets: set[str] = set()
    for tag in tags:
        b = _TAG_TO_BUCKET.get(tag)
        if b:
            buckets.add(b)
    return buckets


def _is_locked(ev: dict) -> bool:
    liquidity = ev.get("liquidity", {})
    tier = liquidity.get("tier", "")
    return tier in _LOCKED_LIQUIDITY_TIERS or liquidity.get("tradable") is False


def _has_data_anomaly(card: dict, ev: dict) -> bool:
    if card.get("evidence_status") == "blocked":
        return True
    evidence = ev.get("evidence", {})
    anomalies = evidence.get("data_anomalies", [])
    if anomalies:
        return True
    return False


# Public API


def build_cash_schedule(
    position_valuations: list[dict],
    approved_sales: list[dict],
    total_value: float,
) -> dict:
    """Classify positions and approved sales into the CashSchedule model."""
    total_value = total_value or 0.0
    schedule = CashSchedule()
    sales_by_position: dict[str, list[dict]] = {}
    for sale in approved_sales:
        sales_by_position.setdefault(sale.get("position_id", ""), []).append(sale)

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
    """Non-mutating capital allocation with suppression records."""
    from stocks.engine.scheduled_analysis import _build_capital_allocation

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
                "message": f"\u5206\u914d\u91d1\u989d \uffe5{alloc_amount:.0f} \u4f4e\u4e8e \uffe5800 \u6709\u6548\u4e0b\u9650\uff0c\u4ec5\u4f5c\u89c2\u5bdf\u4e0d\u6267\u884c",
                "original_signal": card["signal"],
                "original_ratio": card["ratio"],
                "alloc_amount_cny": round(alloc_amount, 2),
            })

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


# Task 5: Adjudicate Portfolio


def _build_evidence_from_cards(
    raw_cards: list[dict],
    evidences: dict[str, dict],
) -> list[dict]:
    merged = []
    for card in raw_cards:
        pid = card["position_id"]
        ev = evidences.get(pid, {})
        merged.append({
            "position_id": pid,
            "card": card,
            "evidence": ev,
            "buckets": _get_exposure_buckets(ev),
            "locked": _is_locked(ev),
            "anomaly": _has_data_anomaly(card, ev),
        })
    return merged


def _build_bucket_ratios_from_evidences(evidences: dict[str, dict]) -> dict[str, float]:
    bucket_values: dict[str, float] = {}
    total = 0.0
    for pid, ev in evidences.items():
        mv = ev.get("market_value_cny", 0.0) or 0.0
        total += mv
        buckets = _get_exposure_buckets(ev)
        for b in buckets:
            bucket_values[b] = bucket_values.get(b, 0.0) + mv
    if total <= 0:
        return {}
    return {b: v / total for b, v in bucket_values.items()}


def adjudicate_portfolio(
    raw_cards: list[dict],
    evidences: dict[str, dict],
    constraints: dict,
    risk_state: dict,
    liquidity: dict,
    *,
    run_id: str = "unknown",
    rule_version: str = _DEFAULT_RULE_VERSION,
) -> PortfolioDecision:
    """Adjudicate portfolio action cards. No LLM calls. Deterministic rules only."""
    gold_max = None
    equity_min = None
    if constraints:
        gold_rule = constraints.get("\u9ec4\u91d1", {})
        gold_max = gold_rule.get("max")
        equity_rule = constraints.get("\u6743\u76ca", {})
        equity_min = equity_rule.get("min")

    suspend = risk_state.get("suspend_accumulation", False)
    risk_level = risk_state.get("level", "normal")

    merged = _build_evidence_from_cards(raw_cards, evidences)
    bucket_ratios = _build_bucket_ratios_from_evidences(evidences)

    gold_pct = bucket_ratios.get("\u9ec4\u91d1", 0.0)
    equity_pct = bucket_ratios.get("\u6743\u76ca", 0.0)

    approved: list[PortfolioAction] = []
    suppressed: list[PortfolioAction] = []
    chains: list[ReplacementChain] = []
    conflicts: list[dict] = []
    suppressed_pids: set[str] = set()

    # Priority 1: Data anomaly -> suppress
    for item in merged:
        pid = item["position_id"]
        if not item["anomaly"]:
            continue
        card = item["card"]
        ev = item["evidence"]
        anomalies = ev.get("evidence", {}).get("data_anomalies", [])
        codes = ", ".join(a.get("code", "unknown") for a in anomalies) if anomalies else "blocked"
        did = make_decision_id(run_id, pid, card.get("raw_signal", ""),
                               card.get("raw_ratio", 0.0), rule_version)
        suppressed.append(PortfolioAction(
            position_id=pid, signal=card.get("signal", "hold"),
            action_description="\u6570\u636e\u5f02\u5e38\uff0c\u6682\u505c\u6280\u672f\u52a8\u4f5c",
            ratio=card.get("ratio", 0.0), decision_id=did,
            reason=f"\u6570\u636e\u5f02\u5e38\u963b\u65ad: {codes}",
        ))
        suppressed_pids.add(pid)

    # Priority 2: Locked/periodic_open -> suppress
    for item in merged:
        pid = item["position_id"]
        if not item["locked"] or pid in suppressed_pids:
            continue
        card = item["card"]
        liq = item["evidence"].get("liquidity", {})
        tier = liq.get("tier", "unknown")
        did = make_decision_id(run_id, pid, card.get("raw_signal", ""),
                               card.get("raw_ratio", 0.0), rule_version)
        suppressed.append(PortfolioAction(
            position_id=pid, signal=card.get("signal", "hold"),
            action_description=f"\u9501\u5b9a\u8d44\u4ea7\uff08{tier}\uff09\uff0c\u4e0d\u53ef\u64cd\u4f5c",
            ratio=card.get("ratio", 0.0), decision_id=did,
            reason=f"\u8d44\u4ea7\u5904\u4e8e {tier} \u6d41\u52a8\u6027\u7b49\u7ea7\uff0c\u4e0d\u53ef\u4ea4\u6613",
        ))
        suppressed_pids.add(pid)

    # Priority 3: Risk suspend accumulation -> suppress add signals
    if suspend:
        for item in merged:
            pid = item["position_id"]
            if pid in suppressed_pids:
                continue
            card = item["card"]
            sig = card.get("signal", "hold")
            if sig not in _ADD_SIGNALS:
                continue
            did = make_decision_id(run_id, pid, card.get("raw_signal", ""),
                                   card.get("raw_ratio", 0.0), rule_version)
            suppressed.append(PortfolioAction(
                position_id=pid, signal=sig,
                action_description=card.get("action", ""),
                ratio=card.get("ratio", 0.0), decision_id=did,
                reason=f"\u98ce\u9669\u72b6\u6001 {risk_level}\uff1a\u6682\u505c\u52a0\u4ed3",
            ))
            suppressed_pids.add(pid)

    # Priority 4: Constraint conflicts
    equity_alternatives: list[str] = []
    equity_reduce_positions: list[str] = []

    for item in merged:
        pid = item["position_id"]
        if pid in suppressed_pids:
            continue
        card = item["card"]
        sig = card.get("signal", "hold")
        buckets = item["buckets"]

        if gold_max is not None and gold_pct > gold_max and "\u9ec4\u91d1" in buckets and sig in _ADD_SIGNALS:
            did = make_decision_id(run_id, pid, card.get("raw_signal", ""),
                                   card.get("raw_ratio", 0.0), rule_version)
            suppressed.append(PortfolioAction(
                position_id=pid, signal=sig,
                action_description=card.get("action", ""),
                ratio=card.get("ratio", 0.0), decision_id=did,
                reason=f"\u9ec4\u91d1\u5360\u6bd4 {gold_pct*100:.1f}% \u8d85\u4e0a\u9650 {gold_max*100:.0f}%\uff0c\u4e0d\u6279\u51c6\u52a0\u4ed3",
            ))
            suppressed_pids.add(pid)

        if "\u6743\u76ca" in buckets:
            if sig in _REDUCE_SIGNALS:
                equity_reduce_positions.append(pid)
            elif sig in _ADD_SIGNALS:
                equity_alternatives.append(pid)

    # Priority 5: Equity under-weight + reduce -> chain or review_required
    if equity_min is not None and equity_pct < equity_min and equity_reduce_positions:
        for reduce_pid in equity_reduce_positions:
            if reduce_pid in suppressed_pids:
                continue
            card = next((item["card"] for item in merged if item["position_id"] == reduce_pid), None)
            if not card:
                continue

            alt = None
            for alt_pid in equity_alternatives:
                if alt_pid != reduce_pid and alt_pid not in suppressed_pids:
                    alt = alt_pid
                    break

            if alt:
                reduce_did = make_decision_id(run_id, reduce_pid, card.get("raw_signal", ""),
                                              card.get("raw_ratio", 0.0), rule_version)
                reduce_evidence = evidences.get(reduce_pid, {})
                liq_tier = reduce_evidence.get("liquidity", {}).get("tier", "t1")
                settlement = (
                    "T+0" if liq_tier in ("cash", "t0")
                    else ("T+1" if liq_tier == "t1" else "T+2")
                )

                sale_leg = PortfolioAction(
                    position_id=reduce_pid, signal=card["signal"],
                    action_description=card.get("action", ""),
                    ratio=card.get("ratio", 0.0), decision_id=reduce_did,
                    reason=f"\u66ff\u6362\u94fe\u5356\u51fa\uff1a{reduce_pid} \u2192 {alt}",
                    settlement_timing=settlement,
                )

                reduce_ratio = abs(card.get("ratio", 0.0))
                sale_proceeds = (
                    reduce_evidence.get("market_value_cny", 0.0) or 0.0
                ) * reduce_ratio
                total_after = sum(
                    ev.get("market_value_cny", 0.0) or 0.0
                    for ev in evidences.values()
                )
                buy_ratio = sale_proceeds / total_after if total_after > 0 else 0.0
                alt_did = make_decision_id(
                    run_id,
                    alt,
                    f"replacement_add:{reduce_pid}",
                    buy_ratio,
                    rule_version,
                )
                total_after = sum(
                    ev.get("market_value_cny", 0.0) or 0.0
                    for ev in evidences.values()
                )
                equity_value_before = sum(
                    (ev.get("market_value_cny", 0.0) or 0.0)
                    for ev in evidences.values()
                    if "权益" in _get_exposure_buckets(ev)
                )
                # The replacement chain reinvests the sale proceeds into another
                # equity position, so total equity exposure is preserved.
                post_trade_ratio_val = (
                    equity_value_before / total_after if total_after > 0 else 0.0
                )

                buy_leg = PortfolioAction(
                    position_id=alt, signal="add",
                    action_description=f"\u66ff\u4ee3 {reduce_pid}",
                    ratio=round(buy_ratio, 6), decision_id=alt_did,
                    reason=(
                        f"卖出 {reduce_pid} 的 {settlement} 资金到账后转买 {alt}，"
                        "维持权益敞口"
                    ),
                    settlement_timing=f"after_{settlement}_proceeds",
                    post_trade_ratio=post_trade_ratio_val,
                    alternative_position_id=reduce_pid,
                )

                chain = ReplacementChain(
                    sale_leg=sale_leg, buy_leg=buy_leg,
                    settlement_timing=settlement,
                    post_trade_ratio=round(post_trade_ratio_val, 4),
                    reason=f"\u6743\u76ca {equity_pct*100:.1f}% < \u4e0b\u9650 {equity_min*100:.0f}%\uff0c"
                           f"{reduce_pid} {card['signal']}\uff0c\u6362\u4ed3\u2192{alt}",
                )
                chains.append(chain)
                approved.append(sale_leg)
                approved.append(buy_leg)
                suppressed_pids.add(reduce_pid)
            else:
                did = make_decision_id(run_id, reduce_pid, card.get("raw_signal", ""),
                                       card.get("raw_ratio", 0.0), rule_version)
                conflicts.append({
                    "position_id": reduce_pid,
                    "signal": card.get("signal", ""),
                    "bucket": "\u6743\u76ca",
                    "message": (
                        f"\u6743\u76ca\u5360\u6bd4 {equity_pct*100:.1f}% \u4f4e\u4e8e\u4e0b\u9650 {equity_min*100:.0f}%\uff0c"
                        f"\u4f46 {reduce_pid} \u89e6\u53d1 {card.get('signal', '')}\u3002\u672a\u627e\u5230\u66ff\u4ee3\uff0c\u9700\u4eba\u5de5\u5ba1\u6838\u3002"
                    ),
                    "decision_id": did,
                })
                if card.get("signal") == "stop_loss":
                    approved.append(PortfolioAction(
                        position_id=reduce_pid, signal=card["signal"],
                        action_description=card.get("action", ""),
                        ratio=card.get("ratio", 0.0), decision_id=did,
                        reason="\u786c\u6b62\u635f\u4e0d\u53d7\u7ea6\u675f\u9650\u5236\uff0c\u4f46\u6743\u76ca\u4f4e\u914d\u9700\u590d\u6838",
                        settlement_timing="T+0",
                    ))
                else:
                    suppressed_pids.add(reduce_pid)
                    suppressed.append(PortfolioAction(
                        position_id=reduce_pid, signal=card["signal"],
                        action_description=card.get("action", ""),
                        ratio=card.get("ratio", 0.0), decision_id=did,
                        reason="\u6743\u76ca\u4f4e\u914d+\u51cf\u4ed3\u51b2\u7a81\uff0c\u65e0\u66ff\u4ee3\uff0c\u9700\u5ba1\u6838",
                    ))

    # Remaining non-suppressed cards -> approve
    for item in merged:
        pid = item["position_id"]
        if pid in suppressed_pids:
            continue
        card = item["card"]
        sig = card.get("signal", "hold")
        if sig in ("hold", "wait"):
            continue
        did = make_decision_id(run_id, pid, card.get("raw_signal", ""),
                               card.get("raw_ratio", 0.0), rule_version)
        liq_tier = item["evidence"].get("liquidity", {}).get("tier", "t1")
        timing = "T+0" if liq_tier in ("cash", "t0") else ("T+1" if liq_tier == "t1" else "T+2")
        approved.append(PortfolioAction(
            position_id=pid, signal=sig,
            action_description=card.get("action", ""),
            ratio=card.get("ratio", 0.0), decision_id=did,
            reason=f"\u901a\u8fc7\u88c1\u51b3\uff1a{sig} {card.get('ratio', 0.0)}",
            settlement_timing=timing,
        ))

    # Determine overall status
    if conflicts:
        status = "review_required"
    elif not approved and suppressed:
        status = "suppressed"
    else:
        status = "approved"

    if conflicts and status == "approved":
        status = "review_required"

    portfolio_did = make_decision_id(run_id, "portfolio", status, len(approved), rule_version)

    return PortfolioDecision(
        status=status, decision_id=portfolio_did,
        approved_actions=approved, suppressed_actions=suppressed,
        replacement_chains=chains, unresolved_conflicts=conflicts,
        rule_version=rule_version,
    )
