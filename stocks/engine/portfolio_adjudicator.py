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

from stocks.engine.execution_rules import resolve_execution
from stocks.engine.quant_action import _TAG_TO_BUCKET
from stocks.engine.valuation_freshness import freshness_is_estimate

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

# settlement_rule tokens safe to surface verbatim as human-facing settlement_timing;
# non-executable tokens (periodic_open, locked, review_required) fall back to the
# presentation layer's placeholder instead of leaking a raw machine enum.
_EXECUTABLE_SETTLEMENT_DISPLAY = frozenset({"T+0", "T+1", "T+2"})


@dataclass
class CashSchedule:
    """Cash schedule classification.

    The legacy four fields (immediate_cash_cny / settling_cash_cny /
    strategic_exit_value_cny / locked_value_cny / safety_buffer_cny) are kept
    verbatim for existing consumers (audit tool, outlook evidence,
    presentation). The five canonical, exactly-named buckets required by
    TASK-001 — available_now / confirmed_settling / planned_release /
    strategic_exit / locked — are the authoritative decomposition emitted by
    to_dict(): every cash-bearing position lands in exactly one of the five.
    available_now/confirmed_settling/strategic_exit alias the legacy fields
    (identical membership); locked_value_cny is the pure aggregate of the new
    locked + planned_release split (a position with a known future release —
    periodic_open tier, or a locked position with a lockup_until/
    maturity_date — is planned_release, not indefinitely locked).

    An approved sale's proceeds land in unresolved_settlement_cny, never in
    settling_cash_cny (confirmed_settling) or immediate_cash_cny
    (available_now), whenever its settlement timing is not one of the
    confirmed executable tokens (T+0/cash/same_day/T+1/T+2) — e.g. a missing
    timing, or a review_required/periodic_open/locked settlement_rule.
    Fabricating a settlement date for money that has no resolved settlement
    would misrepresent it as spendable or on a known clock.
    """
    immediate_cash_cny: float = 0.0
    settling_cash_cny: float = 0.0
    strategic_exit_value_cny: float = 0.0
    locked_value_cny: float = 0.0
    safety_buffer_cny: float = 0.0
    planned_release_cny: float = 0.0
    unresolved_settlement_cny: float = 0.0
    immediate_cash_position_ids: list[str] = field(default_factory=list)
    settling_cash_position_ids: list[str] = field(default_factory=list)
    strategic_exit_position_ids: list[str] = field(default_factory=list)
    locked_position_ids: list[str] = field(default_factory=list)
    planned_release_position_ids: list[str] = field(default_factory=list)
    unresolved_settlement_position_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "immediate_cash_cny": round(self.immediate_cash_cny, 2),
            "settling_cash_cny": round(self.settling_cash_cny, 2),
            "strategic_exit_value_cny": round(self.strategic_exit_value_cny, 2),
            "locked_value_cny": round(self.locked_value_cny, 2),
            "safety_buffer_cny": round(self.safety_buffer_cny, 2),
            "unresolved_settlement_cny": round(self.unresolved_settlement_cny, 2),
            "immediate_cash_position_ids": self.immediate_cash_position_ids,
            "settling_cash_position_ids": self.settling_cash_position_ids,
            "strategic_exit_position_ids": self.strategic_exit_position_ids,
            "locked_position_ids": self.locked_position_ids,
            "planned_release_position_ids": self.planned_release_position_ids,
            "unresolved_settlement_position_ids": self.unresolved_settlement_position_ids,
            "available_now": round(self.immediate_cash_cny, 2),
            "confirmed_settling": round(self.settling_cash_cny, 2),
            "planned_release": round(self.planned_release_cny, 2),
            "strategic_exit": round(self.strategic_exit_value_cny, 2),
            "locked": round(self.locked_value_cny - self.planned_release_cny, 2),
            "unresolved_settlement": round(self.unresolved_settlement_cny, 2),
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
    cancel_condition: str = ""
    next_checkpoint: str = ""
    # Phase 2: platform info for human-readable execution hints
    platform_display: str = ""
    institution_type: str = ""
    account_id: str = ""
    # TASK-001 item 3: final vs. original ratio and decision provenance.
    final_ratio: Optional[float] = None
    original_ratio: Optional[float] = None
    decision_reason: str = ""
    evidence_summary: str = ""
    settlement_rule: Optional[str] = None
    executable_quantity: Optional[float] = None
    execution_status: str = "full"
    # TASK-001 item 8: amount derivation moved out of presentation.py.
    estimated_amount_cny: Optional[float] = None
    amount_is_estimate: bool = True

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
            "cancel_condition": self.cancel_condition or "若触发条件或组合约束不再成立，取消执行",
            "next_checkpoint": self.next_checkpoint or "下一交易窗口复核",
            "platform_display": self.platform_display,
            "institution_type": self.institution_type,
            "account_id": self.account_id,
            "final_ratio": self.final_ratio if self.final_ratio is not None else self.ratio,
            "original_ratio": self.original_ratio if self.original_ratio is not None else self.ratio,
            "decision_reason": self.decision_reason or self.reason,
            "evidence_summary": self.evidence_summary,
            "settlement_rule": self.settlement_rule,
            "executable_quantity": self.executable_quantity,
            "execution_status": self.execution_status,
            "estimated_amount_cny": self.estimated_amount_cny,
            "amount_is_estimate": self.amount_is_estimate,
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
    post_trade_projection: dict = field(default_factory=dict)
    cash_schedule: dict = field(default_factory=dict)
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
            "post_trade_projection": self.post_trade_projection,
            "cash_schedule": self.cash_schedule,
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


def _has_known_release(tier: str, liquidity: dict) -> bool:
    """True when a non-immediate position has a deterministic future release.

    periodic_open positions redeem on a known cadence; a locked position with
    a lockup_until or maturity_date has a known release date. Both are
    "planned_release", not indefinitely "locked" (TASK-001 item 4/5).
    """
    if tier == "periodic_open":
        return True
    return bool(liquidity.get("lockup_until")) or bool(liquidity.get("maturity_date"))


def _has_data_anomaly(card: dict, ev: dict) -> bool:
    """Return True only if there is a blocking-level (critical/high) data anomaly.

    Info/warning anomalies are recorded as evidence but should not suppress
    actionable signals, otherwise every stale prev_close or settled spike
    would silence the assistant.
    """
    if card.get("evidence_status") == "blocked":
        return True
    evidence = ev.get("evidence", {})
    anomalies = evidence.get("data_anomalies", [])
    for a in anomalies:
        if a.get("severity") in ("critical", "high"):
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
            timing = (sale.get("settlement") or {}).get("timing")
            if timing in ("T+0", "cash", "same_day"):
                schedule.immediate_cash_cny += sale_value
                schedule.immediate_cash_position_ids.append(pid)
            elif timing in ("T+1", "T+2"):
                schedule.settling_cash_cny += sale_value
                schedule.settling_cash_position_ids.append(pid)
            else:
                # Missing timing, or a non-executable settlement token
                # (review_required/periodic_open/locked): the proceeds are
                # real but their settlement is unresolved, so they must not
                # be counted as confirmed_settling or available cash.
                schedule.unresolved_settlement_cny += sale_value
                schedule.unresolved_settlement_position_ids.append(pid)

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
            if _has_known_release(tier, liq):
                schedule.planned_release_cny += residual_value
                schedule.planned_release_position_ids.append(pid)
            else:
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
    """Bucket ratios with multi-tag positions split evenly across buckets.

    A position carrying several exposure tags previously contributed its
    FULL market value to every mapped bucket, so bucket ratios could sum to
    more than 100% and constraint checks (gold_max / equity_min) were
    evaluated against inflated numbers (adversarial review P1-7). Splitting
    evenly keeps the ratios' sum at 100% and the constraint math honest.
    """
    bucket_values: dict[str, float] = {}
    total = 0.0
    for pid, ev in evidences.items():
        mv = ev.get("market_value_cny", 0.0) or 0.0
        total += mv
        buckets = _get_exposure_buckets(ev)
        if not buckets:
            continue
        share = mv / len(buckets)
        for b in buckets:
            bucket_values[b] = bucket_values.get(b, 0.0) + share
    if total <= 0:
        return {}
    return {b: v / total for b, v in bucket_values.items()}


def _finalize_approved_action(
    *,
    position_id: str,
    signal: str,
    action_description: str,
    ratio: float,
    decision_id: str,
    reason: str,
    ev: dict,
    card: dict,
    settlement_timing: Optional[str] = None,
    post_trade_ratio: Optional[float] = None,
    alternative_position_id: Optional[str] = None,
    execution_rules: Optional[dict] = None,
    ratio_basis: str = "position",
    portfolio_value_cny: Optional[float] = None,
) -> PortfolioAction:
    """Single producer of an approved PortfolioAction's derived fields.

    Computes settlement_rule (item 5), executable_quantity/execution_status
    (item 7), and estimated_amount_cny/amount_is_estimate (item 8) once, from
    the position's own evidence — never re-derived downstream. Every
    approve-path call site in adjudicate_portfolio routes through here so
    there is exactly one place these fields are computed (item 2/9).

    estimated_amount_cny must use the same basis as the ratio: a
    position-basis ratio multiplies the position's market value; a
    portfolio-basis ratio (replacement-chain buy legs) multiplies the total
    portfolio value. Mixing the two understates the buy leg's amount by
    portfolio_value/position_value (adversarial review P0-1).
    """
    resolution = resolve_execution(
        evidence=ev,
        card=card,
        side="add" if signal in _ADD_SIGNALS else "reduce",
        ratio=ratio,
        ratio_basis=ratio_basis,
        config=execution_rules,
    )
    settlement_rule = resolution.settlement_rule
    # settlement_rule may hold a non-executable machine token (periodic_open,
    # locked, review_required); those must never leak into the human-facing
    # settlement_timing field verbatim, so only executable tokens pass through.
    resolved_settlement_timing = settlement_timing or (
        settlement_rule if settlement_rule in _EXECUTABLE_SETTLEMENT_DISPLAY else None
    )
    executable_quantity = resolution.executable_quantity
    execution_status = resolution.execution_status
    final_ratio = resolution.final_ratio
    decision_reason = reason
    if resolution.reason:
        decision_reason = f"{reason}；{resolution.reason}"

    market_value = ev.get("market_value_cny")
    if ratio_basis == "portfolio":
        amount_base = portfolio_value_cny
    else:
        amount_base = market_value
    estimated_amount_cny = (
        round(float(amount_base) * abs(final_ratio), 2) if amount_base is not None else None
    )
    amount_is_estimate = freshness_is_estimate(
        ev.get("evidence") or {}, ev.get("valuation_method", "")
    )
    classification = ev.get("classification") or {}
    product_type = classification.get("product_type", "unknown")
    liq_tier = (ev.get("liquidity") or {}).get("tier", "unknown")
    evidence_summary = (
        f"signal={signal}, requested_ratio={round(ratio, 4)}, "
        f"product_type={product_type}, liquidity_tier={liq_tier}, "
        f"execution_rule={resolution.rule_id or 'none'}"
    )

    return PortfolioAction(
        position_id=position_id,
        signal=signal,
        action_description=action_description,
        ratio=final_ratio,
        decision_id=decision_id,
        reason=reason,
        settlement_timing=resolved_settlement_timing,
        post_trade_ratio=post_trade_ratio,
        alternative_position_id=alternative_position_id,
        platform_display=card.get("platform_display", ""),
        institution_type=card.get("institution_type", ""),
        account_id=card.get("account_id", ""),
        final_ratio=final_ratio,
        original_ratio=ratio,
        decision_reason=decision_reason,
        evidence_summary=evidence_summary,
        settlement_rule=settlement_rule,
        executable_quantity=executable_quantity,
        execution_status=execution_status,
        estimated_amount_cny=estimated_amount_cny,
        amount_is_estimate=amount_is_estimate,
    )


def adjudicate_portfolio(
    raw_cards: list[dict],
    evidences: dict[str, dict],
    constraints: dict,
    risk_state: dict,
    liquidity: dict,
    *,
    run_id: str = "unknown",
    rule_version: str = _DEFAULT_RULE_VERSION,
    execution_rules: Optional[dict] = None,
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

    # Priority 0: Research-only routes can never become executable actions.
    for item in merged:
        pid = item["position_id"]
        card = item["card"]
        if card.get("evidence_status") != "research_only":
            continue
        did = make_decision_id(
            run_id, pid, card.get("raw_signal", ""),
            card.get("raw_ratio", 0.0), rule_version,
        )
        suppressed.append(PortfolioAction(
            position_id=pid,
            signal=card.get("signal", "hold"),
            action_description=card.get("action", ""),
            ratio=card.get("ratio", 0.0),
            decision_id=did,
            reason="research_only：长期配置仓信号仅供研究，不可执行",
        ))
        suppressed_pids.add(pid)

    # Priority 1: Data anomaly -> suppress
    for item in merged:
        pid = item["position_id"]
        if pid in suppressed_pids or not item["anomaly"]:
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
                settlement = None

                sale_leg = _finalize_approved_action(
                    position_id=reduce_pid, signal=card["signal"],
                    action_description=card.get("action", ""),
                    ratio=card.get("ratio", 0.0), decision_id=reduce_did,
                    reason="\u66ff\u6362\u94fe\u5356\u51fa\uff1a\u8d44\u91d1\u5230\u8d26\u540e\u8f6c\u5165\u66ff\u4ee3\u6807\u7684",
                    ev=reduce_evidence, card=card,
                    settlement_timing=settlement,
                    execution_rules=execution_rules,
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

                alt_card = next(
                    (item["card"] for item in merged if item["position_id"] == alt), {}
                )
                buy_leg = _finalize_approved_action(
                    position_id=alt, signal="add",
                    action_description="替代链买入",
                    ratio=round(buy_ratio, 6), decision_id=alt_did,
                    reason="卖出资金到账后转买替代标的，维持权益敞口",
                    ev=evidences.get(alt, {}), card=alt_card,
                    settlement_timing="after_sale_proceeds",
                    execution_rules=execution_rules,
                    ratio_basis="portfolio",
                    portfolio_value_cny=total_after,
                    post_trade_ratio=post_trade_ratio_val,
                    alternative_position_id=reduce_pid,
                )

                chain = ReplacementChain(
                    sale_leg=sale_leg, buy_leg=buy_leg,
                    settlement_timing=sale_leg.settlement_rule or "review_required",
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
                equity_value = sum(
                    (ev.get("market_value_cny", 0.0) or 0.0)
                    for ev in evidences.values()
                    if "\u6743\u76ca" in _get_exposure_buckets(ev)
                )
                portfolio_value = sum(
                    ev.get("market_value_cny", 0.0) or 0.0
                    for ev in evidences.values()
                )
                conflicts.append({
                    "position_id": reduce_pid,
                    "signal": card.get("signal", ""),
                    "bucket": "\u6743\u76ca",
                    "bucket_ratio": round(equity_pct, 6),
                    "bucket_value_cny": round(equity_value, 2),
                    "portfolio_value_cny": round(portfolio_value, 2),
                    "calculation": "position-deduplicated exposure_tags -> bucket",
                    "message": (
                        f"\u6743\u76ca\u5360\u6bd4 {equity_pct*100:.1f}% \u4f4e\u4e8e\u4e0b\u9650 {equity_min*100:.0f}%\uff0c"
                        f"\u4f46 {reduce_pid} \u89e6\u53d1 {card.get('signal', '')}\u3002\u672a\u627e\u5230\u66ff\u4ee3\uff0c\u9700\u4eba\u5de5\u5ba1\u6838\u3002"
                    ),
                    "decision_id": did,
                })
                if card.get("signal") == "stop_loss":
                    approved.append(_finalize_approved_action(
                        position_id=reduce_pid, signal=card["signal"],
                        action_description=card.get("action", ""),
                        ratio=card.get("ratio", 0.0), decision_id=did,
                        reason="硬止损不受约束限制，但权益低配需复核",
                        ev=evidences.get(reduce_pid, {}), card=card,
                        execution_rules=execution_rules,
                    ))
                    suppressed_pids.add(reduce_pid)
                else:
                    # Adversarial review P1-1: a directional conflict
                    # (equity under-weight vs reduce signal, no replacement)
                    # must be handed to the user unresolved. The previous
                    # "execute 50% by default" rule fabricated an arbitrary
                    # number in exactly the situation where VISION §3.3
                    # forbids default-rule answers. No approved action is
                    # produced; the conflict above is the only output.
                    suppressed_pids.add(reduce_pid)

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
        # reason text carries no raw pre-adjudication ratio: execution_rules
        # may still revise card["ratio"] down to final_ratio (TASK-001E1 defect 1).
        approved.append(_finalize_approved_action(
            position_id=pid, signal=sig,
            action_description=card.get("action", ""),
            ratio=card.get("ratio", 0.0), decision_id=did,
            reason=f"\u901a\u8fc7\u88c1\u51b3\uff1a{sig}",
            ev=item["evidence"], card=card,
            execution_rules=execution_rules,
        ))

    # Determine overall status
    if conflicts or any(a.execution_status == "review_required" for a in approved):
        status = "review_required"
    elif not approved and suppressed:
        status = "suppressed"
    else:
        status = "approved"

    if conflicts and status == "approved":
        status = "review_required"

    portfolio_did = make_decision_id(
        run_id, "portfolio", status, len(approved), rule_version
    )

    before_ratios = _build_bucket_ratios_from_evidences(evidences)
    projected_values: dict[str, float] = {}
    total_value = sum(
        ev.get("market_value_cny", 0.0) or 0.0
        for ev in evidences.values()
    )
    for ev in evidences.values():
        value = ev.get("market_value_cny", 0.0) or 0.0
        buckets = _get_exposure_buckets(ev)
        if not buckets:
            continue
        # Even split across buckets, same basis as before_ratios (P1-7).
        share = value / len(buckets)
        for bucket in buckets:
            projected_values[bucket] = projected_values.get(bucket, 0.0) + share

    approved_sales: list[dict] = []
    for action in approved:
        ev = evidences.get(action.position_id, {})
        buckets = _get_exposure_buckets(ev)
        n_buckets = len(buckets) or 1
        if action.signal in _REDUCE_SIGNALS:
            proceeds = (ev.get("market_value_cny", 0.0) or 0.0) * abs(action.ratio)
            for bucket in buckets:
                projected_values[bucket] = max(
                    0.0, projected_values.get(bucket, 0.0) - proceeds / n_buckets
                )
            approved_sales.append({
                "position_id": action.position_id,
                "ratio": abs(action.ratio),
                # action.settlement_timing is None whenever the resolver
                # could not confirm an executable settlement token; that
                # must reach build_cash_schedule as unresolved, not be
                # fabricated into a specific settlement date here.
                "settlement": {"timing": action.settlement_timing},
            })
        elif action.signal in _ADD_SIGNALS:
            added_value = total_value * abs(action.ratio)
            for bucket in buckets:
                projected_values[bucket] = projected_values.get(bucket, 0.0) + added_value / n_buckets

    after_ratios = {
        bucket: value / total_value
        for bucket, value in projected_values.items()
        if total_value > 0
    }
    post_trade_projection = {
        "before_ratios": before_ratios,
        "after_ratios": after_ratios,
        "cash_schedule_before": copy.deepcopy(liquidity),
        "total_value_cny": round(total_value, 2),
    }
    valuations = [
        {"position_id": pid, **ev}
        for pid, ev in evidences.items()
    ]
    decision_cash_schedule = build_cash_schedule(
        valuations, approved_sales, total_value
    )
    post_trade_projection["cash_schedule_after"] = copy.deepcopy(
        decision_cash_schedule
    )

    return PortfolioDecision(
        status=status, decision_id=portfolio_did,
        approved_actions=approved, suppressed_actions=suppressed,
        replacement_chains=chains, unresolved_conflicts=conflicts,
        post_trade_projection=post_trade_projection,
        cash_schedule=decision_cash_schedule,
        rule_version=rule_version,
    )
