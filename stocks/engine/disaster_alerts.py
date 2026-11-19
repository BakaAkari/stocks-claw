"""Deterministic, read-only portfolio risk & sentinel alerts with replayable evidence."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import floor
from typing import Any

import pandas as pd

from stocks.engine.indicators import TechnicalIndicators

DEFAULT_CONFIG: dict[str, Any] = {
    "warning_day_pct": -5.0,
    "warning_five_day_pct": -10.0,
    "warning_unrealized_pct": -10.0,
    "disaster_day_pct": -12.0,
    "disaster_day_atr_multiple": 3.0,
    "disaster_five_day_pct": -20.0,
    "disaster_five_day_atr_multiple": 4.0,
    "account_warning_day_pct": -2.0,
    "account_disaster_day_pct": -5.0,
    "technical_warnings_enabled": True,
    "rearm_cooldown_minutes": 0,
    "provider_failure_cooldown_minutes": 0,
    "sentinels": {},
}

SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2, "disaster": 3}
REASON_LABELS = {
    "single_day_crash": "单日灾难性下跌",
    "multi_day_crash": "连续下跌并破坏中期趋势",
    "single_day_drop": "单日跌幅异常",
    "multi_day_drop": "五日累计跌幅异常",
    "unrealized_loss": "持仓浮亏达到阈值",
    "below_ma20": "价格跌破 MA20",
    "below_ma50": "价格跌破 MA50",
    "oversold_momentum": "RSI 超卖且 MACD 动能向下",
    "bollinger_break": "价格跌破布林下轨",
    "volume_selloff": "放量下跌",
    "sentinel_hard_stop": "触发硬止损 / 逻辑证伪线",
    "sentinel_stop_1r": "触发 1R 纪律止损线",
    "sentinel_watch_below": "跌破关键观察支撑位",
    "sentinel_entry_zone": "进入分批左侧接盘区间",
    "sentinel_take_profit": "到达目标止盈区间",
    "account_day_loss": "账户估算单日损失达到阈值",
    "delayed_quote": "使用延时行情，非实时报价",
    "missing_quote": "缺少可靠现价或昨收，已停止价格规则判断",
    "provider_failure": "IBKR 数据查询失败或部分不可用",
}


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _pct_change(current: float, previous: float) -> float:
    if previous <= 0:
        raise ValueError("previous price must be positive")
    return (current / previous - 1.0) * 100.0


def _atr_pct(bars: list[dict[str, Any]], period: int = 14) -> float | None:
    if len(bars) < period + 1:
        return None
    true_ranges = []
    for index in range(len(bars) - period, len(bars)):
        current = bars[index]
        previous_close = float(bars[index - 1]["close"])
        high = float(current["high"])
        low = float(current["low"])
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    close = float(bars[-1]["close"])
    if close <= 0:
        return None
    return sum(true_ranges) / period / close * 100.0


def _position_metrics(position: dict[str, Any]) -> dict[str, Any]:
    price = float(position["price"])
    previous_close = float(position["prev_close"])
    bars = position.get("bars") or []
    metrics: dict[str, Any] = {
        "price": price,
        "prev_close": previous_close,
        "day_pct": _pct_change(price, previous_close),
        "data_as_of": position.get("data_as_of"),
    }
    quantity = float(position.get("quantity", 0))
    average_cost = float(position.get("avg_cost", 0))
    if average_cost > 0:
        metrics["unrealized_pct"] = _pct_change(price, average_cost)
        metrics["unrealized_pnl"] = quantity * (price - average_cost)
    metrics["estimated_day_pnl"] = quantity * (price - previous_close)
    metrics["atr_pct"] = _atr_pct(bars)
    if len(bars) >= 6:
        metrics["five_day_pct"] = _pct_change(price, float(bars[-6]["close"]))
    if bars:
        frame = pd.DataFrame({
            "price": [float(bar["close"]) for bar in bars],
            "volume_lot": [float(bar.get("volume", 0)) for bar in bars],
        })
        indicators = TechnicalIndicators.calculate(frame)
        metrics.update({
            "ma_20": indicators.get("ma_20"),
            "ma_50": TechnicalIndicators.ma(frame, 50),
            "rsi_14": indicators.get("rsi_14"),
            "macd_hist": (indicators.get("macd") or {}).get("hist"),
            "bollinger_lower": (indicators.get("bollinger") or {}).get("lower"),
            "volume_ratio": indicators.get("volume_ratio"),
        })
    return metrics


def _max_severity(current: str, candidate: str) -> str:
    return candidate if SEVERITY_RANK[candidate] > SEVERITY_RANK[current] else current


def _evaluate_sentinels(
    symbol: str,
    metrics: dict[str, Any],
    sentinels: dict[str, Any],
) -> tuple[list[str], str | None]:
    spec = sentinels.get(symbol) or sentinels.get(symbol.upper())
    if not isinstance(spec, dict):
        return [], None

    price = float(metrics["price"])
    reasons: list[str] = []
    severity: str | None = None

    hard_stop = spec.get("hard_stop")
    stop_1r = spec.get("stop_1r")
    watch_below = spec.get("watch_below")
    entry_zone = spec.get("entry_zone_max") if spec.get("entry_zone_max") is not None else spec.get("entry_zone")
    take_profit = spec.get("take_profit")

    hard_stop_hit = hard_stop is not None and price <= float(hard_stop)
    if hard_stop_hit:
        reasons.append("sentinel_hard_stop")
        metrics["sentinel_hard_stop"] = float(hard_stop)
        severity = "critical"
    elif stop_1r is not None and price <= float(stop_1r):
        reasons.append("sentinel_stop_1r")
        metrics["sentinel_stop_1r"] = float(stop_1r)
        severity = "warning"
    elif watch_below is not None and price <= float(watch_below):
        reasons.append("sentinel_watch_below")
        metrics["sentinel_watch_below"] = float(watch_below)
        severity = "warning"

    if entry_zone is not None and price <= float(entry_zone) and not hard_stop_hit:
        reasons.append("sentinel_entry_zone")
        metrics["sentinel_entry_zone"] = float(entry_zone)
        severity = _max_severity(severity, "info") if severity else "info"

    if take_profit is not None and price >= float(take_profit):
        reasons.append("sentinel_take_profit")
        metrics["sentinel_take_profit"] = float(take_profit)
        severity = _max_severity(severity, "info") if severity else "info"

    if reasons and spec.get("note"):
        metrics["sentinel_note"] = str(spec["note"])

    return reasons, severity


def _position_alert(position: dict[str, Any], config: dict[str, Any]) -> dict[str, Any] | None:
    symbol = position["symbol"]
    metrics = _position_metrics(position)
    reasons: list[str] = []
    severity: str | None = None
    day_pct = metrics["day_pct"]
    atr_pct = metrics.get("atr_pct")
    five_day_pct = metrics.get("five_day_pct")

    day_disaster_threshold = config["disaster_day_pct"]
    if atr_pct is not None:
        day_disaster_threshold = min(
            day_disaster_threshold,
            -config["disaster_day_atr_multiple"] * atr_pct,
        )
    if day_pct <= day_disaster_threshold:
        reasons.append("single_day_crash")
        severity = "disaster"
    elif day_pct <= config["warning_day_pct"]:
        reasons.append("single_day_drop")
        severity = _max_severity(severity, "warning") if severity else "warning"

    if five_day_pct is not None:
        five_day_disaster_threshold = config["disaster_five_day_pct"]
        if atr_pct is not None:
            five_day_disaster_threshold = min(
                five_day_disaster_threshold,
                -config["disaster_five_day_atr_multiple"] * atr_pct,
            )
        below_ma50 = metrics.get("ma_50") is not None and metrics["price"] < metrics["ma_50"]
        if five_day_pct <= five_day_disaster_threshold and below_ma50:
            reasons.append("multi_day_crash")
            severity = "disaster"
        elif five_day_pct <= config["warning_five_day_pct"]:
            reasons.append("multi_day_drop")
            severity = _max_severity(severity, "warning") if severity else "warning"

    if config.get("technical_warnings_enabled", True):
        if metrics.get("unrealized_pct", 0) <= config["warning_unrealized_pct"]:
            reasons.append("unrealized_loss")
        if metrics.get("ma_20") is not None and metrics["price"] < metrics["ma_20"]:
            reasons.append("below_ma20")
        if metrics.get("ma_50") is not None and metrics["price"] < metrics["ma_50"]:
            reasons.append("below_ma50")
        if (metrics.get("rsi_14") is not None and metrics["rsi_14"] <= 30
                and metrics.get("macd_hist") is not None and metrics["macd_hist"] < 0):
            reasons.append("oversold_momentum")
        if (metrics.get("bollinger_lower") is not None
                and metrics["price"] < metrics["bollinger_lower"]):
            reasons.append("bollinger_break")
        if (metrics.get("volume_ratio") is not None and metrics["volume_ratio"] >= 2
                and day_pct < 0):
            reasons.append("volume_selloff")
        if reasons and severity is None:
            severity = "warning"

    sentinel_reasons, sentinel_severity = _evaluate_sentinels(
        symbol, metrics, config.get("sentinels") or {},
    )
    if sentinel_reasons:
        reasons.extend(sentinel_reasons)
        if sentinel_severity:
            severity = _max_severity(severity, sentinel_severity) if severity else sentinel_severity

    if not reasons or severity is None:
        return None
    if position.get("delayed"):
        reasons.insert(0, "delayed_quote")

    loss_candidates = [abs(day_pct)]
    if five_day_pct is not None and five_day_pct < 0:
        loss_candidates.append(abs(five_day_pct))
    if metrics.get("unrealized_pct") is not None and metrics["unrealized_pct"] < 0:
        loss_candidates.append(abs(metrics["unrealized_pct"]))
    band = max(1, floor(max(loss_candidates) / 5))
    if "sentinel_hard_stop" in reasons:
        band = max(band, 2)

    if severity == "disaster":
        title = f"{symbol} 灾难风险"
    elif "sentinel_hard_stop" in reasons:
        title = f"{symbol} 触发硬止损 / 证伪线"
    elif severity == "info":
        title = f"{symbol} 哨兵价位到达"
    elif any(reason.startswith("sentinel_") for reason in reasons) and not any(
        reason in {"single_day_drop", "multi_day_drop"} for reason in reasons
    ):
        title = f"{symbol} 哨兵价位告警"
    else:
        title = f"{symbol} 持仓风险"

    return {
        "key": f"position:{symbol}",
        "title": title,
        "severity": severity,
        "band": band,
        "reasons": reasons,
        "metrics": {key: _round(value) if isinstance(value, float) else value
                    for key, value in metrics.items()},
    }


def _account_alert(snapshot: dict[str, Any], config: dict[str, Any]) -> dict[str, Any] | None:
    positions = [position for position in snapshot.get("positions", [])
                 if position.get("price") is not None and position.get("prev_close") is not None]
    if not positions or snapshot.get("net_liquidation") is None:
        return None
    estimated_day_pnl = sum(
        float(position["quantity"]) * (float(position["price"]) - float(position["prev_close"]))
        for position in positions
    )
    net_liquidation = float(snapshot["net_liquidation"])
    previous_net_liquidation = net_liquidation - estimated_day_pnl
    if previous_net_liquidation <= 0:
        return None
    day_pct = estimated_day_pnl / previous_net_liquidation * 100.0
    if day_pct > config["account_warning_day_pct"]:
        return None
    disaster = day_pct <= config["account_disaster_day_pct"]
    return {
        "key": "account",
        "title": "账户灾难风险" if disaster else "账户损失警告",
        "severity": "disaster" if disaster else "warning",
        "band": max(1, floor(abs(day_pct))),
        "reasons": ["account_day_loss"] + (["delayed_quote"] if any(position.get("delayed") for position in positions) else []),
        "metrics": {
            "estimated_day_pnl": _round(estimated_day_pnl),
            "estimated_day_pct": _round(day_pct),
            "net_liquidation": _round(net_liquidation),
            "data_as_of": snapshot.get("data_as_of"),
            "calculation": "按当前持仓与昨收估算，不含入金、出金及汇率影响",
        },
    }


def evaluate_snapshot(snapshot: dict[str, Any], config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    effective_config = dict(DEFAULT_CONFIG)
    if config:
        effective_config.update(config)
    alerts = []
    if snapshot.get("system_errors"):
        alerts.append({
            "key": "system:ibkr",
            "title": "IBKR 告警数据源故障",
            "severity": "critical",
            "band": 1,
            "reasons": ["provider_failure"],
            "metrics": {
                "error": "; ".join(str(error.get("message", error)) for error in snapshot["system_errors"]),
                "data_as_of": snapshot.get("data_as_of"),
            },
        })
    for position in snapshot.get("positions", []):
        symbol = position.get("symbol", "UNKNOWN")
        if position.get("price") is None or position.get("prev_close") is None:
            reason = "missing_quote"
            alerts.append({
                "key": f"data:{symbol}",
                "title": f"{symbol} 行情质量异常",
                "severity": "critical",
                "band": 1,
                "reasons": [reason],
                "metrics": {"data_as_of": position.get("data_as_of")},
            })
            continue
        alert = _position_alert(position, effective_config)
        if alert:
            alerts.append(alert)
    account_alert = _account_alert(snapshot, effective_config)
    if account_alert:
        alerts.append(account_alert)
    return alerts


def _within_cooldown(
    alert: dict[str, Any],
    reason_last_sent: dict[str, str],
    now: datetime,
    cooldown_minutes: int,
) -> bool:
    if cooldown_minutes <= 0:
        return False
    core_reasons = [r for r in alert["reasons"] if r != "delayed_quote"] or list(alert["reasons"])
    cutoff = timedelta(minutes=cooldown_minutes)
    for reason in core_reasons:
        sent_raw = reason_last_sent.get(f"{alert['key']}|{reason}")
        if not sent_raw:
            return False
        try:
            sent_at = datetime.fromisoformat(sent_raw)
            if sent_at.tzinfo is None:
                sent_at = sent_at.replace(tzinfo=timezone.utc)
        except ValueError:
            return False
        if now - sent_at >= cutoff:
            return False
    return True


def apply_delivery_state(
    alerts: list[dict[str, Any]],
    state: dict[str, Any],
    now: datetime | None = None,
    *,
    cooldown_minutes: int = 0,
    provider_failure_cooldown_minutes: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    previous_alerts = dict(state.get("alerts", {}))
    reason_last_sent = dict(state.get("reason_last_sent") or {})
    next_state = dict(state)
    next_state["alerts"] = {}
    deliver = []
    for alert in alerts:
        previous = previous_alerts.get(alert["key"])
        severity = SEVERITY_RANK[alert["severity"]]
        escalated = bool(
            previous
            and (
                alert["band"] > previous["band"]
                or severity > SEVERITY_RANK[previous["severity"]]
            )
        )
        should_deliver = previous is None
        if previous:
            should_deliver = escalated or bool(
                severity == SEVERITY_RANK[previous["severity"]]
                and set(alert["reasons"]) - set(previous.get("reasons", []))
            )
        effective_cooldown = (
            provider_failure_cooldown_minutes
            if alert["key"] == "system:ibkr" and provider_failure_cooldown_minutes > 0
            else cooldown_minutes
        )
        if should_deliver and not escalated and severity < SEVERITY_RANK["disaster"]:
            if _within_cooldown(alert, reason_last_sent, now, effective_cooldown):
                should_deliver = False
        if should_deliver:
            deliver.append(alert)
            for reason in alert["reasons"]:
                reason_last_sent[f"{alert['key']}|{reason}"] = now.isoformat()
            last_sent_at = now.isoformat()
        elif previous is not None:
            last_sent_at = previous.get("last_sent_at")
        else:
            candidates = [
                reason_last_sent.get(f"{alert['key']}|{reason}")
                for reason in alert["reasons"]
                if reason_last_sent.get(f"{alert['key']}|{reason}")
            ]
            last_sent_at = max(candidates) if candidates else None
        next_state["alerts"][alert["key"]] = {
            "band": max(alert["band"], previous["band"] if previous else 0),
            "severity": alert["severity"],
            "reasons": sorted(set(alert["reasons"])),
            "last_sent_at": last_sent_at,
        }
    next_state["reason_last_sent"] = reason_last_sent
    return deliver, next_state


def _format_metric(key: str, value: Any) -> str:
    if key.endswith("_pct") and isinstance(value, (int, float)):
        return f"{value:.2f}%"
    if key in {"estimated_day_pnl", "unrealized_pnl", "net_liquidation"} and isinstance(value, (int, float)):
        return f"${value:,.2f}"
    if key.startswith("sentinel_") and key != "sentinel_note" and isinstance(value, (int, float)):
        return f"${value:,.2f}"
    return str(value)


def render_alerts(alerts: list[dict[str, Any]]) -> str:
    if not alerts:
        return ""
    highest = max(SEVERITY_RANK[alert["severity"]] for alert in alerts)
    if highest >= 3:
        heading = "灾难风险告警"
    elif highest == 2:
        data_only = all(
            set(alert["reasons"]) <= {"provider_failure", "missing_quote", "delayed_quote"}
            for alert in alerts
        )
        heading = "行情质量告警" if data_only else "关键风险告警"
    elif highest == 1:
        heading = "持仓风险提醒"
    else:
        heading = "哨兵价位到达提醒"

    lines = [f"**{heading}**", ""]
    metric_labels = {
        "price": "现价", "day_pct": "单日涨跌", "five_day_pct": "五日涨跌",
        "sentinel_stop_1r": "1R 止损线", "sentinel_hard_stop": "硬止损/证伪线",
        "sentinel_watch_below": "观察支撑线", "sentinel_entry_zone": "左侧接盘区上限",
        "sentinel_take_profit": "目标止盈线", "sentinel_note": "策略备注",
        "unrealized_pct": "持仓浮亏", "unrealized_pnl": "持仓浮亏金额",
        "estimated_day_pnl": "估算日损", "estimated_day_pct": "估算日损比例",
        "net_liquidation": "净清算价值", "atr_pct": "ATR14", "rsi_14": "RSI14",
        "data_as_of": "数据时间", "calculation": "口径", "error": "错误",
    }
    for alert in alerts:
        lines.append(f"**{alert['title']}**")
        lines.append("；".join(f"{REASON_LABELS.get(reason, reason)} (`{reason}`)" for reason in alert["reasons"]))
        for key, label in metric_labels.items():
            value = alert.get("metrics", {}).get(key)
            if value is not None:
                lines.append(f"- {label}: {_format_metric(key, value)}")
        lines.append("")
    lines.append("仅作风险提醒；买入、卖出与仓位调整由你决定。")
    return "\n".join(lines).strip()
