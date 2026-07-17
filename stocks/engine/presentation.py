"""Deterministic user-facing presentation helpers for trading reports.

Machine identifiers and internal enum values remain in the audit artifact. This
module creates stable Chinese labels for the user-facing report contract.
"""
from __future__ import annotations

from typing import Any

_SIGNAL_LABELS = {
    "stop_loss": "止损", "take_profit": "止盈", "reduce": "减仓",
    "add": "加仓", "hold": "持有", "wait": "等待",
}
_STATUS_LABELS = {
    "approved": "已有获批动作", "suppressed": "今日无需操作",
    "review_required": "等待人工确认",
}
_RISK_LABELS = {
    "hedge": "防御状态", "reduce": "降低风险", "watch": "观察状态",
    "normal": "常态",
}
_ANOMALY_MESSAGES = {
    "single_bar_jump": ("单根行情跳变异常，可能存在拆分、复权或数据源问题", "暂停依据该段行情执行交易"),
    "mixed_adjustment_regime": ("历史行情混用了不同复权口径", "暂停技术指标交易，等待统一行情口径"),
    "price_ma20_dislocation": ("价格与20日均线偏差异常，可能存在复权或数据源口径问题", "暂停依据该指标执行交易"),
    "prev_close_mismatch": ("前收盘价在不同数据源之间不一致", "暂停执行，等待核对正确收盘价"),
    "source_regime_change": ("行情数据源或计算口径发生变化", "暂停执行，等待同口径数据恢复"),
}
_EVIDENCE_LABELS = {
    "price": "价格", "ma20": "20日均线", "prev_close": "前收盘价",
    "source_prev_close": "数据源前收盘价", "pct_change": "涨跌幅",
}
_ESTIMATE_FRESHNESS = frozenset({"previous_close", "stale", "old", "unknown", "missing", "no_data"})
_ESTIMATE_VALUATION_METHODS = frozenset({"manual_amount", "fund_nav", "insurance_value"})


def public_instrument_code(instrument_key: str, product_type: str = "") -> str:
    """Return a public trading/fund code, never a position id."""
    del product_type
    value = str(instrument_key or "").strip()
    if not value or ":" not in value:
        return ""
    market, code = value.split(":", 1)
    if market.lower() not in {"a", "us", "hk", "fund", "crypto"}:
        return ""
    return code.strip()


def display_label(
    display_name: str,
    instrument_key: str,
    product_type: str = "",
    *,
    public_code: str = "",
    fallback: str = "",
) -> str:
    """Render real name plus public code without leaking the machine fallback id."""
    del fallback
    name = str(display_name or "").strip() or "未命名持仓"
    code = str(public_code or "").strip() or public_instrument_code(instrument_key, product_type)
    return f"{name}（{code}）" if code else name


def signal_label(signal: str) -> str:
    return _SIGNAL_LABELS.get(str(signal or ""), "待确认动作")


def status_label(status: str) -> str:
    return _STATUS_LABELS.get(str(status or ""), "等待人工确认")


def risk_label(level: str) -> str:
    return _RISK_LABELS.get(str(level or ""), "风险状态待确认")


def freshness_is_estimate(evidence: dict, valuation_method: str) -> bool:
    if str(valuation_method or "") in _ESTIMATE_VALUATION_METHODS:
        return True
    freshness = str((evidence or {}).get("price_freshness") or "unknown")
    return freshness in _ESTIMATE_FRESHNESS


def _evidence_summary(evidence: dict[str, Any]) -> str:
    parts = []
    for key in sorted(evidence):
        value = evidence[key]
        if not isinstance(value, (str, int, float, bool)) and value is not None:
            continue
        parts.append(f"{_EVIDENCE_LABELS.get(key, '相关数据')}={value}")
    return "，".join(parts)


def anomaly_display(anomaly: dict) -> dict:
    """Translate an anomaly without exposing its internal code."""
    code = str((anomaly or {}).get("code") or "")
    message, impact = _ANOMALY_MESSAGES.get(
        code, ("数据质量异常，需人工核对", "暂停相关技术动作，等待数据确认")
    )
    return {
        "display_message": message,
        "user_impact": impact,
        "evidence_summary": _evidence_summary((anomaly or {}).get("evidence") or {}),
    }


_SUPPRESSION_REASON_TEXT = {
    "research_only": "属于长期配置或研究信号，当前不形成交易动作",
    "periodic_open": "当前不在开放操作期，暂时不能交易",
    "locked": "资产当前处于锁定状态，暂时不能交易",
    "prev_close_mismatch": "前收盘价存在数据源差异，需核对后再决定",
    "source_regime_change": "行情来源或计算口径发生变化，需等待同口径数据",
    "single_bar_jump": "行情出现异常跳变，需排查拆分、复权或数据源问题",
    "mixed_adjustment_regime": "历史行情复权口径不一致，技术动作已暂停",
    "price_ma20_dislocation": "价格与20日均线关系异常，技术动作已暂停",
    "t2_plus": "资金或份额需等待结算完成后才能操作",
    "manual_fallback": "当前使用人工估值，需更新可靠行情后再决定",
}


def _safe_reason_text(reason: str) -> str:
    value = str(reason or "")
    for key, text in _SUPPRESSION_REASON_TEXT.items():
        if key in value:
            return text
    if "数据异常" in value:
        return "相关数据存在异常，需核对后再决定"
    if "流动性" in value or "不可交易" in value or "不可操作" in value:
        return "当前交易条件不满足，暂时不能操作"
    return "组合约束或风险条件尚未满足，等待下一检查点"


def suppression_reason_display(reason: str) -> str:
    """Translate suppression reason codes without leaking machine tokens."""
    return _safe_reason_text(reason)


def _position_maps(position_valuations: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    by_id = {}
    by_key = {}
    for item in position_valuations or []:
        pid = str(item.get("position_id") or "")
        key = str(item.get("instrument_key") or "")
        if pid:
            by_id[pid] = item
        if key:
            by_key[key] = item
    return by_id, by_key


def _display_for_position(item: dict) -> str:
    classification = item.get("classification") or {}
    return display_label(
        item.get("display_name", ""), item.get("instrument_key", ""),
        classification.get("product_type", ""), public_code=item.get("public_code", ""),
    )


def _cash_view(schedule: dict) -> dict:
    values = schedule or {}
    return {
        "immediate": {"label": "现在能用", "amount_cny": round(values.get("immediate_cash_cny", 0.0) or 0.0, 2)},
        "settling": {"label": "到账途中", "amount_cny": round(values.get("settling_cash_cny", 0.0) or 0.0, 2)},
        "strategic_exit": {"label": "卖出后才能用", "amount_cny": round(values.get("strategic_exit_value_cny", 0.0) or 0.0, 2)},
        "locked": {"label": "不能动", "amount_cny": round(values.get("locked_value_cny", 0.0) or 0.0, 2)},
    }


def _conflict_reason(conflict: dict, by_id: dict[str, dict]) -> str:
    item = by_id.get(str(conflict.get("position_id") or ""), {})
    label = _display_for_position(item)
    bucket = str(conflict.get("bucket") or "组合")
    ratio = conflict.get("bucket_ratio")
    ratio_text = f"{float(ratio) * 100:.1f}%" if isinstance(ratio, (int, float)) else "待确认"
    action = signal_label(conflict.get("signal", ""))
    return f"{label}：{bucket}当前占组合{ratio_text}，低于目标下限，但技术信号要求{action}；方向冲突，需人工确认"


def build_user_view(
    portfolio_decision: dict,
    position_valuations: list[dict],
    position_reviews: list[dict],
    research_candidates: list[dict],
    risk_state: dict,
    *,
    session_id: str,
    session_intent: str,
) -> dict:
    """Build the deterministic trade-card and assistant presentation contract."""
    del position_reviews
    decision = portfolio_decision or {}
    by_id, by_key = _position_maps(position_valuations)
    actions = []
    for raw in (decision.get("approved_actions") or [])[:3]:
        item = by_id.get(str(raw.get("position_id") or ""), {})
        market_value = item.get("market_value_cny")
        ratio = float(raw.get("ratio") or 0.0)
        amount = round(float(market_value) * ratio, 2) if market_value is not None else None
        actions.append({
            "display_label": _display_for_position(item),
            "action_label": signal_label(raw.get("signal", "")),
            "ratio": ratio,
            "estimated_amount_cny": amount,
            "amount_is_estimate": freshness_is_estimate(
                item.get("evidence") or {}, item.get("valuation_method", "")
            ) if item else True,
            "reason_summary": str(raw.get("action_description") or raw.get("reason") or "按获批条件执行"),
            "cancel_condition": str(raw.get("cancel_condition") or "触发条件不再成立时取消"),
            "settlement_display": str(raw.get("settlement_timing") or "到账时间待确认"),
            "next_checkpoint": str(raw.get("next_checkpoint") or "下一交易窗口复核"),
        })

    no_action_reasons = []
    for conflict in decision.get("unresolved_conflicts") or []:
        reason = _conflict_reason(conflict, by_id)
        if reason not in no_action_reasons:
            no_action_reasons.append(reason)
        if len(no_action_reasons) == 2:
            break
    for raw in decision.get("suppressed_actions") or []:
        if len(no_action_reasons) == 2:
            break
        item = by_id.get(str(raw.get("position_id") or ""), {})
        label = _display_for_position(item)
        reason = f"{label}：{_safe_reason_text(raw.get('reason', ''))}"
        if reason not in no_action_reasons:
            no_action_reasons.append(reason)
    if not no_action_reasons and not actions:
        no_action_reasons.append("当前没有满足执行条件的获批动作")

    raw_status = str(decision.get("status") or "")
    if actions:
        card_status, card_label = "action_required", "需要操作"
    elif raw_status == "review_required" and decision.get("unresolved_conflicts"):
        card_status, card_label = "manual_review", "等待人工确认"
    else:
        card_status, card_label = "no_action", "今日无需操作"

    research = []
    for candidate in (research_candidates or [])[:8]:
        symbol = str(candidate.get("symbol") or "")
        item = by_key.get(symbol, {})
        name = candidate.get("name") or item.get("display_name") or "未命名标的"
        reassess_after = str(candidate.get("reassess_after") or "下一交易窗口复核")
        if "当前状态:" in reassess_after:
            reassess_after = "风险解除后再评估"
        research.append({
            "display_label": display_label(name, symbol),
            "action_hint": str(candidate.get("action_hint") or "仅供观察，不形成交易动作"),
            "reassess_after": reassess_after,
        })

    level = str((risk_state or {}).get("level") or "normal")
    transition = str((risk_state or {}).get("transition") or "stable")
    transition_text = {
        "escalated": "风险升级", "deescalated": "风险缓和", "stable": "状态未变",
    }.get(transition, "状态待确认")
    assistant = {
        "why": [a["reason_summary"] for a in actions] or no_action_reasons[:2],
        "do_not_do": [
            f"{_display_for_position(by_id.get(str(x.get('position_id') or ''), {}))}：{_safe_reason_text(x.get('reason', ''))}"
            for x in (decision.get("suppressed_actions") or [])[:5]
        ],
        "cash": _cash_view(decision.get("cash_schedule") or {}),
        "risk": {
            "label": risk_label(level),
            "transition": transition_text,
            "suspend_accumulation": bool((risk_state or {}).get("suspend_accumulation")),
            "release_condition": str((risk_state or {}).get("release_condition") or "等待风险状态满足解除条件"),
        },
        "research": research,
    }
    return {
        "instruction_card": {
            "status": card_status,
            "status_label": card_label,
            "actions": actions,
            "no_action_reasons": no_action_reasons[:2] if not actions else [],
            "next_checkpoint": actions[0]["next_checkpoint"] if actions else _session_checkpoint(session_id, session_intent),
        },
        "assistant_brief": assistant,
    }


def _session_checkpoint(session_id: str, session_intent: str) -> str:
    checkpoints = {
        "cn_pre_open": "A股开盘观察窗口复核",
        "cn_open_watch": "A股收盘前窗口复核",
        "cn_pre_close": "A股盘后复盘",
        "cn_after_close": "下一交易日盘前复核",
        "us_pre_open": "美股开盘观察窗口复核",
        "us_open_watch": "美股收盘前窗口复核",
        "us_pre_close": "美股盘后复盘",
        "us_after_close": "下一交易日盘前复核",
    }
    return checkpoints.get(session_id, "下一交易窗口复核")
