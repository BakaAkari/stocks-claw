"""引擎级动作信号 — 把技术事实翻译为方向性候选动作。

2026-07-02 用户裁决（见 PLAN 决策日志）：允许引擎输出规则化的方向性
action signal。约束条件：

- 每个信号必须附 `reasons`，逐条引用触发该规则的指标事实，可被人工复核
- 信号是"候选动作"，不是指令；最终采纳权在用户与 Agent
- 数据不足显式输出 `no_data`，绝不在缺数据时给方向
- 不输出任何收益承诺

信号语义：
- accumulate_candidate       趋势与动能配合，可分批布局
- left_bottom_candidate      深跌超卖且跌势放缓，左侧轻仓试仓
- wait_for_pullback          趋势好但短线过热，等回踩不追
- reduce_risk                趋势与动能同步转弱，应降低暴露
- avoid_catching_falling_knife  加速下跌中，别接下跌刀
- rotation_candidate         相对强弱领先，资金可能轮动到这里
- neutral_hold               无明确方向信号，维持现状
- no_data                    历史/指标不足，不给方向

叠加层（与主信号并存）：
- event_watch                T+3 内有已公布催化剂，动作宜等事件落地

横截面排序（2026-07-09 新增）：
- accumulate_candidate 和 rotation_candidate 信号内部按综合得分排名
- 让 Agent 在面对多个同类型候选时能区分优先级
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from stocks.engine.indicators import TechnicalIndicators

ACTION_SIGNALS_SCHEMA_VERSION = 1

# 规则阈值（集中定义，便于复核与调参）。
#
# These defaults are frozen so behaviour is stable, but the canonical place to
# tune signal thresholds is engine.yaml under `quant_action.thresholds` /
# `quant_action.rank_weights`.  When loaded via load_engine_config(), the
# values below are overridden at runtime (see compute_action_signals).
#
# To add a new market regime or asset class, change YAML — not this block.
_QUANT_ACTION_THRESHOLDS = {
    "knife_r5": -3.0,                    # 5 根 K 线跌幅超过该值视为加速下跌
    "knife_rsi": 38.0,
    "reduce_r20": -5.0,                  # 20 根 K 线跌幅超过该值视为趋势转弱
    "pullback_r20": 5.0,                 # 趋势强劲阈值
    "pullback_rsi": 65.0,
    "pullback_position": 85.0,           # 布林带位置百分比
    "accumulate_r20": 2.0,               # 排除仅略高于 0 的横盘噪声
    "accumulate_rsi_low": 40.0,
    "accumulate_rsi_high": 65.0,
    "accumulate_r20_max": 15.0,          # 短期涨幅过高，即使 RSI 未超买也应等回调
    "left_bottom_rsi_max": 40.0,         # 左侧抄底：超卖或接近超卖
    "left_bottom_price_position_max": 25.0,  # 价格位置底部区
    "left_bottom_r20_max": -10.0,        # 近20根跌幅超过10%
    "left_bottom_r5_floor": -5.0,        # 近5根跌幅不再加速
    "left_bottom_pullback_cooldown": 0.02,   # 价格低于MA20不超过2%
}

# 横截面排序权重
_RANK_WEIGHTS = {
    "r20": 0.40,         # 中期趋势强度
    "rsi_zone": 0.30,    # RSI 区间得分
    "price_pos": 0.20,   # 布林带位置惩罚
    "volume": 0.10,      # 量比加成
}

# Backwards-compatible module-level names (deprecated; prefer the dicts above
# or the runtime config from engine.yaml).
_KNIFE_R5 = _QUANT_ACTION_THRESHOLDS["knife_r5"]
_KNIFE_RSI = _QUANT_ACTION_THRESHOLDS["knife_rsi"]
_REDUCE_R20 = _QUANT_ACTION_THRESHOLDS["reduce_r20"]
_PULLBACK_R20 = _QUANT_ACTION_THRESHOLDS["pullback_r20"]
_PULLBACK_RSI = _QUANT_ACTION_THRESHOLDS["pullback_rsi"]
_PULLBACK_POSITION = _QUANT_ACTION_THRESHOLDS["pullback_position"]
_ACCUMULATE_R20 = _QUANT_ACTION_THRESHOLDS["accumulate_r20"]
_ACCUMULATE_RSI_LOW = _QUANT_ACTION_THRESHOLDS["accumulate_rsi_low"]
_ACCUMULATE_RSI_HIGH = _QUANT_ACTION_THRESHOLDS["accumulate_rsi_high"]
_ACCUMULATE_R20_MAX = _QUANT_ACTION_THRESHOLDS["accumulate_r20_max"]
_LEFT_BOTTOM_RSI_MAX = _QUANT_ACTION_THRESHOLDS["left_bottom_rsi_max"]
_LEFT_BOTTOM_PRICE_POSITION_MAX = _QUANT_ACTION_THRESHOLDS["left_bottom_price_position_max"]
_LEFT_BOTTOM_R20_MAX = _QUANT_ACTION_THRESHOLDS["left_bottom_r20_max"]
_LEFT_BOTTOM_R5_FLOOR = _QUANT_ACTION_THRESHOLDS["left_bottom_r5_floor"]
_LEFT_BOTTOM_PULLBACK_COOLDOWN = _QUANT_ACTION_THRESHOLDS["left_bottom_pullback_cooldown"]

_RANK_WEIGHT_R20 = _RANK_WEIGHTS["r20"]
_RANK_WEIGHT_RSI_ZONE = _RANK_WEIGHTS["rsi_zone"]
_RANK_WEIGHT_PRICE_POS = _RANK_WEIGHTS["price_pos"]
_RANK_WEIGHT_VOLUME = _RANK_WEIGHTS["volume"]

_SIGNAL_ACTION_HINTS = {
    "accumulate_candidate": "趋势完好且回踩确认，可建仓/加仓；分批布局优先于一次性重仓",
    "left_bottom_candidate": "深跌超卖且跌势放缓，左侧轻仓试仓，跌破前低或 RSI 持续恶化则止损",
    "wait_for_pullback": "趋势完好但短线过热，等回踩确认再进，不追",
    "reduce_risk": "趋势与动能同步转弱，优先降低该标的暴露",
    "avoid_catching_falling_knife": "下跌未止，任何补仓等收盘重新站回短均线再说",
    "rotation_candidate": "相对强弱领先，若与组合缺口匹配可作为轮入候选",
    "neutral_hold": "无明确方向信号，维持现状，等待新信号",
    "no_data": "历史或指标不足，本轮不给方向",
}

# 占位：candidate-level risk sizing and stop-loss rules are emitted by the
# research pipeline; research candidates are never executable actions, so these
# are displayed as sizing/stop conditions for human reference only.
_RESEARCH_SIZING_HINTS = {
    "left_bottom_candidate": "轻仓试仓（建议占组合不超过1-3%）；止损：跌破触发前低或20日低点继续下移",
    "accumulate_candidate": "趋势回踩建仓机会（首仓建议占组合2-4%，确认企稳后可加至5-8%）；止损：跌破MA20且MACD柱转负，或跌破建仓日低点",
    "rotation_candidate": "仅在组合有对应缺口时轮入（建议占组合不超过1-3%）；止损：轮动排名掉出前3或趋势破MA20",
    "wait_for_pullback": "等回踩MA5/MA10附近观察，不追高；未触发不建仓",
    "reduce_risk": "优先降低暴露，减仓后观察是否止跌",
    "avoid_catching_falling_knife": "禁止抄底，等待站回短均线或RSI脱离超卖区",
    "neutral_hold": "维持现有仓位，不主动调仓",
    "no_data": "无数据支持，不形成任何观点",
}


_RESEARCH_CONFLICT_HINTS = {
    "suspend_accumulation": "当前风险状态暂停加仓，风险解除后再评估",
    "risk_high": "当前风险状态为{level}，需先观察风险触发条件是否缓解",
}


def _research_sizing_hint(signal: str, risk_level: str, suspend: bool) -> str:
    """Produce the research-candidate sizing/stop guidance line.

    Non-suspend: return the signal's concrete sizing + stop-loss guidance as-is
    (optionally annotated with the risk level).

    Suspend (suspend_accumulation=True, i.e. hedge/reduce global risk state):
    - Pausing accumulation forbids *building* a position, so accumulation
      phrasing (布局/试仓/轮入/建仓/分批) must not coexist with the pause text.
    - The stop-loss line is risk PROTECTION for an existing position and is NOT
      contradicted by pausing accumulation, so it is preserved verbatim.
    - Non-accumulation signals (wait_for_pullback, reduce_risk, ...) carry no
      build phrasing and keep their full observation guidance plus a pause note.
    """
    base = _RESEARCH_SIZING_HINTS.get(str(signal or ""), "仅供参考，不形成交易动作")
    if suspend:
        # 激进方案(2026-08-13): 危机时左侧超跌(left_bottom)不暂停,降为"危机
        # 试仓 1%"。三重门: 超跌+趋势未破已由 left_bottom 条件保证(含 ma60
        # 门槛),产业逻辑在渲染层带出情报关联。左侧交易者危机超跌恰是机会,
        # 但必须少量试仓+严格止损,不是无脑抄底。
        if str(signal or "") == "left_bottom_candidate":
            stop = ""
            if "止损：" in base:
                _, _, stop = base.partition("止损：")
                stop = "止损：" + stop
            head = "危机左侧试仓（轻仓 1%，风险自担，严格止损）"
            parts = [p for p in (head, stop) if p]
            return "；".join(parts)
        stop = ""
        head = base
        if "止损：" in base:
            head, _, stop = base.partition("止损：")
            stop = "止损：" + stop
        head = head.strip("；，。 ")
        # Positive accumulation phrase only (布局/试仓/轮入/分批/轻仓).
        # wait_for_pullback/reduce_risk/... carry no build directive ("不建仓",
        # "不追高" are negations of an observe stance, not accumulation), so
        # they keep their full guidance instead of being flattened to the pause
        # text.
        _POSITIVE_BUILD = ("布局", "试仓", "轮入", "分批", "轻仓")
        if head and any(k in head for k in _POSITIVE_BUILD):
            build = "暂停加仓，风险解除后再评估"
        else:
            build = head
        parts = [p for p in (build, stop) if p]
        return "；".join(parts) if parts else "暂停加仓，风险解除后再评估"
    if risk_level in ("hedge", "reduce"):
        base += f"；当前风险状态为{risk_level}，优先观察风险触发条件"
    return base


RESEARCH_CONFLICT_HINTS = {
    "suspend_accumulation": "当前风险状态暂停加仓，风险解除后再评估",
    "risk_high": "当前风险状态为{level}，需先观察风险触发条件是否缓解",
}


def _signal_for_item(
    *,
    price: Optional[float],
    ma5: Optional[float],
    ma20: Optional[float],
    ma60: Optional[float],
    rsi: Optional[float],
    macd_hist: Optional[float],
    price_position: Optional[float],
    r5: Optional[float],
    r20: Optional[float],
    data_points: int,
    is_leader: bool = False,
    thresholds: Optional[dict[str, float]] = None,
) -> tuple[str, list[str]]:
    """按固定顺序匹配规则，返回 (signal, reasons)。首个命中的规则生效。

    Thresholds default to the module-level constants but can be overridden via
    engine.yaml quant_action.thresholds for per-market or per-regime tuning.
    """
    t = {**_QUANT_ACTION_THRESHOLDS, **(thresholds or {})}
    if data_points < 15 or price is None:
        return "no_data", [f"历史仅 {data_points} bars，不足以判级"]

    reasons: list[str] = []

    # 1. 左侧抄底：深跌超卖且跌势放缓，轻仓试仓。
    # 三重门之一"趋势未破": MA20 仍在 MA60 上方 = 多头排列 = 长期趋势未破坏。
    # 深跌(price<MA20)但 MA20>MA60 是"回调";MA20<MA60 是"反转"(不接)。
    if (
        ma20 is not None
        and price < ma20 * (1 + t['left_bottom_pullback_cooldown'])
        and ma60 is not None
        and ma20 > ma60
        and ((rsi is not None and rsi <= t['left_bottom_rsi_max']) or
               (price_position is not None and price_position <= t['left_bottom_price_position_max']))
        and r20 is not None
        and r20 <= t['left_bottom_r20_max']
        and r5 is not None
        and r5 > r20  # 5-day total跌幅 not worse than 20-day total (stabilizing)
    ):
        reasons.append(f"现价 {price:.2f} 低于 MA20 {ma20:.2f}，不超过{t['left_bottom_pullback_cooldown']*100:.0f}%")
        reasons.append(f"MA20 {ma20:.2f} 仍在 MA60 {ma60:.2f} 上方，趋势结构未破（回调非反转）")
        if rsi is not None and rsi <= t['left_bottom_rsi_max']:
            reasons.append(f"RSI {rsi:.1f} 处于超卖或接近超卖区（≤{t['left_bottom_rsi_max']}）")
        if price_position is not None and price_position <= t['left_bottom_price_position_max']:
            reasons.append(f"价格位于布林带下轨附近（{price_position:.0f}%≤{t['left_bottom_price_position_max']}%)")
        reasons.append(f"近20根K线累计 {r20:+.2f}%，跌幅较深")
        reasons.append(f"近5根K线累计 {r5:+.2f}% 不再加速，跌势放缓")
        return "left_bottom_candidate", reasons

    # 1b. 轮动领先者回调布局：轮动排名靠前 + 回踩 MA20 附近 + 中长期趋势未破
    if (
        is_leader
        and ma20 is not None
        and ma60 is not None
        and ma20 > ma60
        and 0.97 <= price / ma20 <= 1.03
        and (r5 is None or r5 > t['knife_r5'])
        and (rsi is None or rsi < t['pullback_rsi'])
    ):
        reasons.append(f"轮动排名前段且回踩 MA20 {ma20:.2f} 附近，趋势未破")
        reasons.append("MA20 仍在 MA60 上方（回调非反转）")
        if rsi is not None:
            reasons.append(f"RSI {rsi:.1f} 未超买")
        return "accumulate_candidate", reasons

    # 1. 下跌刀：短线加速下跌 + 价格在短均线下方 + RSI 弱势
    if (
        r5 is not None
        and r5 <= t['knife_r5']
        and ma5 is not None
        and price < ma5
        and (rsi is None or rsi <= t['knife_rsi'])
    ):
        reasons.append(f"近5根K线累计 {r5:+.2f}%（≤{t['knife_r5']}%）")
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
        and r20 <= t['reduce_r20']
    ):
        reasons.append(f"现价 {price:.2f} 低于 MA20 {ma20:.2f}")
        reasons.append(f"MACD 柱 {macd_hist:.3f} 为负")
        reasons.append(f"近20根K线累计 {r20:+.2f}%（≤{t['reduce_r20']}%）")
        return "reduce_risk", reasons

    # 3. 等回踩：趋势强但短线过热
    if (
        ma20 is not None
        and price > ma20
        and r20 is not None
        and r20 >= t['pullback_r20']
        and (
            (rsi is not None and rsi >= t['pullback_rsi'])
            or (price_position is not None and price_position >= t['pullback_position'])
        )
    ):
        reasons.append(f"现价 {price:.2f} 高于 MA20 {ma20:.2f}，近20根 {r20:+.2f}%")
        if rsi is not None and rsi >= t['pullback_rsi']:
            reasons.append(f"RSI {rsi:.1f} 接近超买")
        if price_position is not None and price_position >= t['pullback_position']:
            reasons.append(f"布林带位置 {price_position:.0f}% 接近上轨")
        return "wait_for_pullback", reasons

    # 3b. 短期涨速过高：即使 RSI 未超买，短期已大幅上涨也应等回调
    if (
        ma20 is not None
        and price > ma20
        and r20 is not None
        and r20 >= t['accumulate_r20_max']
        and (rsi is None or rsi < t['pullback_rsi'])
    ):
        reasons.append(f"现价 {price:.2f} 高于 MA20 {ma20:.2f}，近20根 {r20:+.2f}%")
        reasons.append(f"短期涨幅过高（≥{t['accumulate_r20_max']}%），即使RSI未超买也应等回调确认")
        return "wait_for_pullback", reasons

    # 4. 分批布局（左侧越跌越布局）：回踩 MA20 附近 + 中长期趋势未破 + 动能未恶化
    if (
        ma20 is not None
        and ma60 is not None
        and ma20 > ma60
        and 0.97 <= price / ma20 <= 1.03
        and rsi is not None
        and t['accumulate_rsi_low'] <= rsi < t['accumulate_rsi_high']
        and (r5 is None or r5 > t['knife_r5'])
    ):
        reasons.append(f"现价 {price:.2f} 回踩 MA20 {ma20:.2f} 附近")
        reasons.append("MA20 在 MA60 上方，趋势未破")
        reasons.append(f"RSI {rsi:.1f} 中性，动能未恶化")
        if macd_hist is not None and macd_hist > 0:
            reasons.append(f"MACD 柱 {macd_hist:.3f} 为正")
        return "accumulate_candidate", reasons

    return "neutral_hold", ["未命中任何方向性规则"]


def _rank_signals(items: list[dict]) -> list[dict]:
    """对 accumulate_candidate 和 rotation_candidate 信号做横截面排序。

    综合得分 = r20 强度 × 0.40 + RSI 区间得分 × 0.30
             + 布林带位置惩罚 × 0.20 + 量比加成 × 0.10

    RSI 区间得分：40-65 为满分 1.0，越接近 50 越优
    位置惩罚：布林带位置越低越好（越接近下轨加仓成本越低）
    量比加成：量比 > 1 有正面加成

    Args:
        items: compute_action_signals 输出的 items 列表（含 indicators_raw）

    Returns:
        带 rank 和 score 的 items，同信号组内按得分降序排列
    """
    ranked_items = []
    for item in items:
        signal = item.get("signal")
        # 只对方向性买入信号做排名
        if signal not in ("accumulate_candidate", "rotation_candidate"):
            ranked_items.append(item)
            continue

        raw = item.get("_indicators_raw") or {}
        rotation_item = item.get("_rotation_item") or {}

        r20 = rotation_item.get("r20") or 0.0
        rsi = raw.get("rsi_14")
        price_pos = raw.get("price_position")
        vol_ratio = raw.get("volume_ratio")

        # RSI 区间得分：40-65 为最优区间，50 最高，偏离则扣分
        rsi_score = 1.0
        if rsi is not None:
            if 40 <= rsi <= 65:
                # 以 50 为中心，越接近 50 越高
                rsi_score = 1.0 - abs(rsi - 50) / 15
            elif rsi < 40:
                rsi_score = max(0.0, rsi / 40 * 0.7)
            else:
                rsi_score = max(0.0, (80 - rsi) / 15 * 0.6)

        # 布林带位置惩罚：越低越好（更接近下轨）
        pos_penalty = 0.0
        if price_pos is not None:
            if price_pos > 80:
                pos_penalty = (price_pos - 80) / 20 * 0.5  # 0~0.5 惩罚
            elif price_pos < 20:
                pos_penalty = -(20 - price_pos) / 20 * 0.3  # 负值=加分

        # 量比加成
        vol_bonus = 0.0
        if vol_ratio is not None and vol_ratio > 1.0:
            vol_bonus = min((vol_ratio - 1.0) / 5.0, 0.3)

        # r20 归一化：假设 ±20% 为极端值范围
        r20_norm = max(-1.0, min(r20 / 20.0, 1.0))

        score = (
            r20_norm * _RANK_WEIGHT_R20
            + rsi_score * _RANK_WEIGHT_RSI_ZONE
            + pos_penalty * _RANK_WEIGHT_PRICE_POS
            + vol_bonus * _RANK_WEIGHT_VOLUME
        )

        item["_score"] = round(score, 4)
        ranked_items.append(item)

    # 排序：非 ranked 信号保持原有顺序，ranked 信号按得分降序
    order_priority = [
        "avoid_catching_falling_knife",
        "reduce_risk",
        "wait_for_pullback",
        "accumulate_candidate",
        "rotation_candidate",
        "neutral_hold",
        "no_data",
    ]
    ranked_items.sort(
        key=lambda item: (
            order_priority.index(item["signal"])
            if item["signal"] in order_priority
            else 99,
            -(item.get("_score") or 0),
            item["symbol"],
        )
    )
    # 为有序信号分配 rank
    rank_counter: dict[str, int] = {}
    for item in ranked_items:
        if item["signal"] in ("accumulate_candidate", "rotation_candidate"):
            rank_counter.setdefault(item["signal"], 0)
            rank_counter[item["signal"]] += 1
            item["rank"] = rank_counter[item["signal"]]
            # Do not append the numeric score to user-facing text; it will
            # leak decimal numbers into the rendered report and can trigger
            # outlook-style numeric forecast validation (M1 hardening).
            item["action_hint"] = item.get("action_hint", "")
        else:
            item["rank"] = None

    return ranked_items


def compute_action_signals(
    frames: dict[str, pd.DataFrame],
    instruments: dict[str, object],
    rotation: dict,
    upcoming_events: Optional[list] = None,
    scan_keys: Optional[set[str]] = None,
    *,
    thresholds: Optional[dict[str, float]] = None,
    rank_weights: Optional[dict[str, float]] = None,
) -> dict:
    """为 watchlist + 扫描池每个标的输出候选动作信号。

    Args:
        frames: "market:code" -> 历史 DataFrame（与轮动共用）
        instruments: "market:code" -> Instrument
        rotation: compute_rotation 的输出（复用 r5/r20/leaders）
        upcoming_events: UpcomingEvent 列表，用于 event_watch 叠加
        scan_keys: 扫描池 key 集合
        thresholds: 可选规则阈值覆盖（engine.yaml quant_action.thresholds）；
            未提供时使用模块级默认值，行为与历史一致。
        rank_weights: 可选横截面排序权重覆盖（engine.yaml quant_action.rank_weights）。
    """
    scan_keys = scan_keys or set()
    thresholds = thresholds or {}
    rank_weights = rank_weights or {}
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
        t = {**_QUANT_ACTION_THRESHOLDS, **thresholds}
        signal, reasons = _signal_for_item(
            price=price,
            ma5=indicators.get("ma_5"),
            ma20=indicators.get("ma_20"),
            ma60=indicators.get("ma_60"),
            rsi=indicators.get("rsi_14"),
            macd_hist=macd.get("hist"),
            price_position=indicators.get("price_position"),
            r5=rotation_item.get("r5"),
            r20=rotation_item.get("r20"),
            data_points=int(indicators.get("data_points") or 0),
            is_leader=(key in leaders),
            thresholds=t,
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
            # P0-1: 保留现价供 signal_tracker 记录 generation_price(反馈闭环)。
            # 优先 rotation_item 的现价;rotation items 通常不带绝对价格,
            # 退而用 ma_5(5日均线≈现价,与技术指标同源),不引入新数据源。
            "price": rotation_item.get("price")
            or (indicators.get("ma_5") if isinstance(indicators, dict) else None),
            # P0(左侧): 保留三个"位置"指标供渲染层呈现左侧位置卡。
            # price_position=布林位置(0下轨超卖~100上轨), rsi_14=RSI, volume_ratio=量比。
            # 这些是技术指标已算好的客观数据,零新数据源。round 到固定精度,
            # 确保与渲染数字一致(数字门禁: 存储值与渲染值 round4 后须相等)。
            "price_position": (round(float(indicators["price_position"]), 0) if isinstance(indicators, dict) and indicators.get("price_position") is not None else None),
            "rsi_14": (round(float(indicators["rsi_14"]), 0) if isinstance(indicators, dict) and indicators.get("rsi_14") is not None else None),
            "volume_ratio": (round(float(indicators["volume_ratio"]), 1) if isinstance(indicators, dict) and indicators.get("volume_ratio") is not None else None),
            # P1(左侧): 保留技术位供渲染层生成分批档位表(一档MA20/二档布林下轨/三档MA60)。
            # round(2) 存储与渲染 {:.2f} 对齐(数字门禁)。
            "ma_20": (round(float(indicators["ma_20"]), 2) if isinstance(indicators, dict) and indicators.get("ma_20") is not None else None),
            "ma_60": (round(float(indicators["ma_60"]), 2) if isinstance(indicators, dict) and indicators.get("ma_60") is not None else None),
            "bollinger_lower": (round(float(indicators["bollinger"]["lower"]), 2) if isinstance(indicators, dict) and isinstance(indicators.get("bollinger"), dict) and indicators["bollinger"].get("lower") is not None else None),
            "_indicators_raw": indicators,
            "_rotation_item": rotation_item,
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

    # 横截面排序
    items = _rank_signals(items)

    # 清理内部字段，不暴露给下游
    for item in items:
        item.pop("_indicators_raw", None)
        item.pop("_rotation_item", None)
    # 仅保留有 rank 的信号
    has_ranks = any(item.get("rank") is not None for item in items)

    return {
        "schema_version": ACTION_SIGNALS_SCHEMA_VERSION,
        "status": status,
        "items": items,
        "counts": counts,
        "ranked": has_ranks,
        "disclaimer": "规则化候选动作，非指令；reasons 为触发事实，同信号按综合得分排序，最终判断归用户与 Agent",
    }
