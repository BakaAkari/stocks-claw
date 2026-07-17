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


def display_label(display_name: str, instrument_key: str, product_type: str = "", *, fallback: str = "") -> str:
    """Render real name plus public code without leaking the machine fallback id."""
    del fallback
    name = str(display_name or "").strip() or "未命名持仓"
    code = public_instrument_code(instrument_key, product_type)
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
