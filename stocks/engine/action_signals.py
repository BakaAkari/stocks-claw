"""引擎级动作信号 — 把技术事实翻译为方向性候选动作。

2026-07-02 用户裁决（见 PLAN 决策日志）：允许引擎输出规则化的方向性
action signal。约束条件：

- 每个信号必须附 `reasons`，逐条引用触发该规则的指标事实，可被人工复核
- 信号是"候选动作"，不是指令；最终采纳权在用户与 Agent
- 数据不足显式输出 `no_data`，绝不在缺数据时给方向
- 不输出任何收益承诺

信号语义：
- accumulate_candidate       趋势与动能配合，可分批布局
- wait_for_pullback          趋势好但短线过热，等回踩不追
- reduce_risk                趋势与动能同步转弱，应降低暴露
- avoid_catching_falling_knife  加速下跌中，别接下跌刀
- rotation_candidate         相对强弱领先，资金可能轮动到这里
- neutral_hold               无明确方向信号，维持现状
- no_data                    历史/指标不足，不给方向

叠加层（与主信号并存）：
- event_watch                T+3 内有已公布催化剂，动作宜等事件落地
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from stocks.engine.indicators import TechnicalIndicators

ACTION_SIGNALS_SCHEMA_VERSION = 1

# 规则阈值（集中定义，便于复核与调参）
_KNIFE_R5 = -3.0          # 5 根 K 线跌幅超过该值视为加速下跌
_KNIFE_RSI = 38.0
_REDUCE_R20 = -5.0        # 20 根 K 线跌幅超过该值视为趋势转弱
_PULLBACK_R20 = 5.0       # 趋势强劲阈值
_PULLBACK_RSI = 65.0
_PULLBACK_POSITION = 85.0  # 布林带位置百分比
_ACCUMULATE_R20 = 2.0      # 排除仅略高于 0 的横盘噪声
_ACCUMULATE_RSI_LOW = 40.0
_ACCUMULATE_RSI_HIGH = 65.0

_SIGNAL_ACTION_HINTS = {
    "accumulate_candidate": "趋势与动能配合，可分批布局；回踩短均线附近优先于追高",
    "wait_for_pullback": "趋势完好但短线过热，等回踩确认再进，不追",
    "reduce_risk": "趋势与动能同步转弱，优先降低该标的暴露",
    "avoid_catching_falling_knife": "下跌未止，任何补仓等收盘重新站回短均线再说",
    "rotation_candidate": "相对强弱领先，若与组合缺口匹配可作为轮入候选",
    "neutral_hold": "无明确方向信号，维持现状，等待新信号",
    "no_data": "历史或指标不足，本轮不给方向",
}


def _signal_for_item(
    *,
    price: Optional[float],
    ma5: Optional[float],
    ma20: Optional[float],
    rsi: Optional[float],
    macd_hist: Optional[float],
    price_position: Optional[float],
    r5: Optional[float],
    r20: Optional[float],
    data_points: int,
) -> tuple[str, list[str]]:
    """按固定顺序匹配规则，返回 (signal, reasons)。首个命中的规则生效。"""
    if data_points < 15 or price is None:
        return "no_data", [f"历史仅 {data_points} bars，不足以判级"]

    reasons: list[str] = []

    # 1. 下跌刀：短线加速下跌 + 价格在短均线下方 + RSI 弱势
    if (
        r5 is not None
        and r5 <= _KNIFE_R5
        and ma5 is not None
        and price < ma5
        and (rsi is None or rsi <= _KNIFE_RSI)
    ):
        reasons.append(f"近5根K线累计 {r5:+.2f}%（≤{_KNIFE_R5}%）")
        reasons.append(f"现价 {price:.2f} 低于 MA5 {ma5:.2f}")
        if rsi is not None:
            reasons.append(f"RSI {rsi:.1f} 处于弱势区")
        return "avoid_catching_falling_knife", reasons

    # 2. 降低暴露：中期趋势与动能同步转弱
    if (
        ma20 is not None
        and price < ma20
        and macd_hist is not None
        and macd_hist < 0
        and r20 is not None
        and r20 <= _REDUCE_R20
    ):
        reasons.append(f"现价 {price:.2f} 低于 MA20 {ma20:.2f}")
        reasons.append(f"MACD 柱 {macd_hist:.3f} 为负")
        reasons.append(f"近20根K线累计 {r20:+.2f}%（≤{_REDUCE_R20}%）")
        return "reduce_risk", reasons

    # 3. 等回踩：趋势强但短线过热
    if (
        ma20 is not None
        and price > ma20
        and r20 is not None
        and r20 >= _PULLBACK_R20
        and (
            (rsi is not None and rsi >= _PULLBACK_RSI)
            or (price_position is not None and price_position >= _PULLBACK_POSITION)
        )
    ):
        reasons.append(f"现价 {price:.2f} 高于 MA20 {ma20:.2f}，近20根 {r20:+.2f}%")
        if rsi is not None and rsi >= _PULLBACK_RSI:
            reasons.append(f"RSI {rsi:.1f} 接近超买")
        if price_position is not None and price_position >= _PULLBACK_POSITION:
            reasons.append(f"布林带位置 {price_position:.0f}% 接近上轨")
        return "wait_for_pullback", reasons

    # 4. 分批布局：趋势向上、动能配合、未过热
    if (
        ma20 is not None
        and price > ma20
        and r20 is not None
        and r20 >= _ACCUMULATE_R20
        and rsi is not None
        and _ACCUMULATE_RSI_LOW <= rsi < _ACCUMULATE_RSI_HIGH
        and (macd_hist is None or macd_hist > 0 or (r5 is not None and r5 > 0))
    ):
        reasons.append(f"现价 {price:.2f} 站上 MA20 {ma20:.2f}")
        reasons.append(
            f"近20根K线累计 {r20:+.2f}%（≥{_ACCUMULATE_R20}%）"
        )
        reasons.append(f"RSI {rsi:.1f} 中性偏强，未过热")
        if macd_hist is not None and macd_hist > 0:
            reasons.append(f"MACD 柱 {macd_hist:.3f} 为正")
        return "accumulate_candidate", reasons

    return "neutral_hold", ["未命中任何方向性规则"]


def compute_action_signals(
    frames: dict[str, pd.DataFrame],
    instruments: dict[str, object],
    rotation: dict,
    upcoming_events: Optional[list] = None,
    scan_keys: Optional[set[str]] = None,
) -> dict:
    """为 watchlist + 扫描池每个标的输出候选动作信号。

    Args:
        frames: "market:code" -> 历史 DataFrame（与轮动共用）
        instruments: "market:code" -> Instrument
        rotation: compute_rotation 的输出（复用 r5/r20/leaders）
        upcoming_events: UpcomingEvent 列表，用于 event_watch 叠加
        scan_keys: 扫描池 key 集合
    """
    scan_keys = scan_keys or set()
    rotation_items = {
        item["symbol"]: item for item in (rotation or {}).get("items", [])
    }
    leaders = set((rotation or {}).get("leaders", []))
    # 轮动候选要求排名有统计意义:至少 4 个标的有 r20 才谈"相对强弱领先"
    ranked_count = sum(
        1 for item in rotation_items.values() if item.get("r20") is not None
    )

    # T+3 内事件 → 受影响标的的 event_watch 叠加
    event_overlay: dict[str, list[str]] = {}
    for event in upcoming_events or []:
        days_until = getattr(event, "days_until", None)
        status = getattr(event, "status", "scheduled")
        if (
            days_until is None
            or days_until < 0
            or days_until > 3
            or status not in {"scheduled", "imminent"}
        ):
            continue
        label = f"{getattr(event, 'date', '?')} {getattr(event, 'name', '?')}"
        for symbol in getattr(event, "affected_symbols", []) or []:
            event_overlay.setdefault(symbol, []).append(label)

    items: list[dict] = []
    counts: dict[str, int] = {}
    for key, instrument in instruments.items():
        frame = frames.get(key)
        indicators: dict = {}
        if frame is not None and not frame.empty and "price" in frame.columns:
            cleaned = frame.copy()
            cleaned["price"] = pd.to_numeric(cleaned["price"], errors="coerce")
            cleaned = cleaned.dropna(subset=["price"])
            if not cleaned.empty:
                indicators = TechnicalIndicators.calculate(cleaned)

        rotation_item = rotation_items.get(key, {})
        price = None
        if indicators:
            frame_prices = frame["price"] if frame is not None else None
            try:
                price = float(pd.to_numeric(frame_prices, errors="coerce").dropna().iloc[-1])
            except (IndexError, TypeError, ValueError):
                price = None

        macd = indicators.get("macd") or {}
        signal, reasons = _signal_for_item(
            price=price,
            ma5=indicators.get("ma_5"),
            ma20=indicators.get("ma_20"),
            rsi=indicators.get("rsi_14"),
            macd_hist=macd.get("hist"),
            price_position=indicators.get("price_position"),
            r5=rotation_item.get("r5"),
            r20=rotation_item.get("r20"),
            data_points=int(indicators.get("data_points") or 0),
        )

        # 轮动领先且尚无方向信号 → rotation_candidate
        # 门槛:排名样本 ≥4、MA20 上方、短线与中线动量均为实质正值
        if (
            signal == "neutral_hold"
            and key in leaders
            and ranked_count >= 4
            and rotation_item.get("above_ma20")
            and (rotation_item.get("r5") or 0) > 0.5
            and (rotation_item.get("r20") or 0) >= 2.0
        ):
            signal = "rotation_candidate"
            reasons = [
                f"轮动排名第 {rotation_item.get('rank')}"
                f"（{ranked_count} 个标的中，20日 {rotation_item.get('r20'):+.2f}%）",
                f"价格在 MA20 上方且近5根 {rotation_item.get('r5'):+.2f}%",
            ]

        entry = {
            "symbol": key,
            "name": getattr(instrument, "name", key) or key,
            "category": getattr(instrument, "category", None) or "unknown",
            "pool": getattr(instrument, "pool", None)
            or ("scan" if key in scan_keys else "core"),
            "universe": "scan" if key in scan_keys else "watchlist",
            "signal": signal,
            "reasons": reasons,
            "action_hint": _SIGNAL_ACTION_HINTS[signal],
            "as_of": rotation_item.get("as_of"),
        }
        if key in event_overlay:
            entry["event_watch"] = event_overlay[key]
            entry["action_hint"] += "；T+3 内有已公布催化剂，动作宜等事件落地"
        items.append(entry)
        counts[signal] = counts.get(signal, 0) + 1

    directional = [
        item for item in items if item["signal"] not in ("no_data",)
    ]
    if not items:
        status = "no_data"
    elif not directional:
        status = "no_data"
    elif counts.get("no_data"):
        status = "partial"
    else:
        status = "ok"

    # 输出按信号优先级分组排序，便于阅读
    order = [
        "avoid_catching_falling_knife",
        "reduce_risk",
        "wait_for_pullback",
        "accumulate_candidate",
        "rotation_candidate",
        "neutral_hold",
        "no_data",
    ]
    items.sort(key=lambda item: (order.index(item["signal"]), item["symbol"]))

    return {
        "schema_version": ACTION_SIGNALS_SCHEMA_VERSION,
        "status": status,
        "items": items,
        "counts": counts,
        "disclaimer": "规则化候选动作，非指令；reasons 为触发事实，最终判断归用户与 Agent",
    }
