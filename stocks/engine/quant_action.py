"""量化行动规则引擎。

根据用户偏好：保守偏激进、左侧买入、中长期持有、趋势证伪果断止损。

规则设计：
- 单笔最大亏损：-12% 硬止损（趋势证伪）
- -10% 中间减仓：降低暴露 30%，避免 -8% 到 -12% 之间裸奔
- -8% 提前预警：建议审视趋势
- 20 日低点：跌破时执行减仓 50%（趋势破坏）
- 止盈：+10% 减仓 25%、+20% 减仓 25%、+30% 减仓 50%
- 单日浮盈回撤 ≥2% 且仍浮盈：触发减仓 25%
- 趋势保护：跌破 MA20 + MACD 柱为负 → 减仓 50%
- 仓位上限：单标的不超过 5%，高确定性趋势确认后可加至 10%
- 左侧买入：价格回踩 MA20、RSI 40-65、MACD 未走坏，分批建仓 2%→5%
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from stocks.engine.exchange_rate import get_usd_cny_rate


@dataclass
class ActionPlan:
    action: str  # hold, reduce, add, stop_loss, take_profit, watch
    ratio: float  # 建议变动比例（0-1）
    reason: str
    stop_price: Optional[float] = None
    target_prices: list[float] = field(default_factory=list)
    position_limit_pct: Optional[float] = None
    risk_amount_cny: Optional[float] = None


@dataclass
class QuantReview:
    position_id: str
    signal: str  # 核心信号：hold, reduce, add, stop_loss, take_profit, watch
    action: str  # 人类可读动作
    ratio: float
    facts: list[str]
    stop_price: Optional[float]
    target_prices: list[float]
    position_limit_pct: float
    current_weight_pct: Optional[float]
    risk_to_stop_pct: Optional[float]
    risk_amount_cny: Optional[float]
    intelligence_conflict: str = "none"  # none, caution, override


class QuantActionEngine:
    """把规则偏好翻译成具体行动。"""

    # 止损
    STOP_LOSS_PCT = -12.0
    MID_STOP_PCT = -10.0
    MID_STOP_RATIO = 0.3
    WARNING_LOSS_PCT = -8.0
    # 止盈减仓
    TAKE_PROFIT_LEVELS = [(10.0, 0.25), (20.0, 0.25), (30.0, 0.50)]
    # 单日回撤止盈
    PROFIT_PULLBACK_PCT = -2.0
    PROFIT_PULLBACK_MIN_PNL = 3.0
    # 趋势保护
    TREND_MA20_BREAK_CUTOFF = 0.995  # 收盘价低于 MA20 * 0.995 视为跌破
    # 仓位
    DEFAULT_POSITION_LIMIT_PCT = 5.0
    TREND_CONFIRMED_LIMIT_PCT = 10.0
    # 左侧买入
    LEFT_ADD_MAX_RSI = 65.0
    LEFT_ADD_MIN_RSI = 40.0

    def __init__(self, indicators: dict):
        self.indicators = indicators

    def review_position(
        self,
        *,
        position_id: str,
        price: Optional[float],
        cost: Optional[float],
        pnl_pct: Optional[float],
        one_day_change_pct: Optional[float],
        current_weight_pct: Optional[float],
        quantity: Optional[float],
    ) -> QuantReview:
        facts: list[str] = []
        signal = "hold"
        action = "持有"
        ratio = 0.0
        stop_price = None
        target_prices: list[float] = []

        # 1. 硬止损
        if isinstance(pnl_pct, (int, float)) and pnl_pct <= self.STOP_LOSS_PCT:
            signal = "stop_loss"
            action = "止损清仓"
            ratio = 1.0
            if cost is not None:
                stop_price = round(cost * (1 + self.STOP_LOSS_PCT / 100), 4)
            facts.append(f"浮亏 {pnl_pct:.2f}% 已超过硬止损 {self.STOP_LOSS_PCT}%")
            return self._build(
                position_id, signal, action, ratio, facts, stop_price, target_prices,
                current_weight_pct, price, quantity,
            )

        # 2. 中间减仓：-10% 触发降低暴露，避免 -8% 到 -12% 之间裸奔
        if isinstance(pnl_pct, (int, float)) and pnl_pct <= self.MID_STOP_PCT:
            signal = "reduce"
            ratio = self.MID_STOP_RATIO
            action = f"浮亏超出中间阈值 {self.MID_STOP_PCT}%，减仓 {int(ratio * 100)}%"
            if cost is not None:
                stop_price = round(cost * (1 + self.MID_STOP_PCT / 100), 4)
            facts.append(f"浮亏 {pnl_pct:.2f}% 触发中间减仓阈值 {self.MID_STOP_PCT}%")
            return self._build(
                position_id, signal, action, ratio, facts, stop_price, target_prices,
                current_weight_pct, price, quantity,
            )

        # 3. 止损预警
        if isinstance(pnl_pct, (int, float)) and pnl_pct <= self.WARNING_LOSS_PCT:
            facts.append(f"浮亏 {pnl_pct:.2f}% 触发警示阈值 {self.WARNING_LOSS_PCT}%")

        # 4. 20 日低点 / 趋势跌破
        ma20 = self.indicators.get("ma_20")
        macd_hist = (self.indicators.get("macd") or {}).get("hist")
        if isinstance(price, (int, float)) and ma20 is not None:
            if price < ma20 * self.TREND_MA20_BREAK_CUTOFF and (macd_hist is None or macd_hist < 0):
                signal = "reduce"
                action = "趋势走弱，减仓 50%"
                ratio = 0.5
                stop_price = round(float(ma20 * self.TREND_MA20_BREAK_CUTOFF), 4)
                facts.append(f"现价 {price:.2f} 跌破 MA20 {ma20:.2f} 且 MACD 柱为负")
                return self._build(
                    position_id, signal, action, ratio, facts, stop_price, target_prices,
                    current_weight_pct, price, quantity,
                )

        # 5. 止盈阶梯
        if isinstance(pnl_pct, (int, float)) and pnl_pct > 0 and cost is not None:
            for level, reduce_ratio in self.TAKE_PROFIT_LEVELS:
                if pnl_pct >= level:
                    target_prices.append(round(cost * (1 + level / 100), 4))
            if target_prices:
                # 取已触发的最高级别减仓
                triggered = [(level, ratio) for level, ratio in self.TAKE_PROFIT_LEVELS if pnl_pct >= level]
                if triggered:
                    max_level, max_ratio = triggered[-1]
                    # 累计减仓比例：触发的各级别加总，但不超过 0.75
                    total_reduce = sum(r for _level, r in triggered)
                    total_reduce = min(total_reduce, 0.75)
                    signal = "take_profit"
                    action = f"触发 {max_level}% 止盈，减仓 {int(total_reduce * 100)}%"
                    ratio = total_reduce
                    facts.append(f"浮盈 {pnl_pct:.2f}% 触发止盈")
                    return self._build(
                        position_id, signal, action, ratio, facts, stop_price, target_prices,
                        current_weight_pct, price, quantity,
                    )

        # 6. 单日浮盈回撤
        if (
            isinstance(one_day_change_pct, (int, float))
            and one_day_change_pct <= self.PROFIT_PULLBACK_PCT
            and isinstance(pnl_pct, (int, float))
            and pnl_pct >= self.PROFIT_PULLBACK_MIN_PNL
        ):
            signal = "reduce"
            action = f"单日回撤 {one_day_change_pct:.2f}%，减仓 25% 锁定浮盈"
            ratio = 0.25
            facts.append(f"单日下跌 {one_day_change_pct:.2f}%，浮盈 {pnl_pct:.2f}%")
            return self._build(
                position_id, signal, action, ratio, facts, stop_price, target_prices,
                current_weight_pct, price, quantity,
            )

        # 7. 左侧加仓
        if isinstance(price, (int, float)) and ma20 is not None and cost is not None:
            rsi = self.indicators.get("rsi_14")
            near_ma20 = 0.97 <= price / ma20 <= 1.03
            rsi_ok = rsi is None or (self.LEFT_ADD_MIN_RSI <= rsi <= self.LEFT_ADD_MAX_RSI)
            macd_ok = macd_hist is None or macd_hist >= 0
            if near_ma20 and rsi_ok and macd_ok and pnl_pct is not None and pnl_pct < 5.0:
                signal = "add"
                action = "回踩 MA20，左侧加仓 2%"
                ratio = -0.02  # 负号表示增加仓位
                facts.append(f"价格回踊 MA20 {ma20:.2f}，RSI {rsi}，MACD 未走坏")
                return self._build(
                    position_id, signal, action, ratio, facts, stop_price, target_prices,
                    current_weight_pct, price, quantity,
                )

        # 默认
        if not facts:
            facts.append("未触发任何规则")
        return self._build(
            position_id, signal, action, ratio, facts, stop_price, target_prices,
            current_weight_pct, price, quantity,
        )

    def resolve_intelligence_conflict(
        self,
        signal: str,
        intelligence_signals: Optional[dict[str, dict]] = None,
        position_symbol: Optional[str] = None,
    ) -> str:
        """对比技术面信号与情报面信号，返回冲突级别。

        Returns:
            "none" — 无冲突或情报缺失
            "caution" — 情报反向但非 critical，建议减半执行
            "override" — critical 情报反向，暂停等待确认
        """
        if not intelligence_signals or not position_symbol:
            return "none"
        intel = intelligence_signals.get(position_symbol)
        if not intel:
            return "none"

        intel_dir = intel.get("direction", "")
        intel_urgency = intel.get("urgency", "medium")

        # 判断技术面方向
        tech_bullish = signal in ("add", "accumulate", "take_profit")
        tech_bearish = signal in ("reduce", "stop_loss", "reduce_risk")
        intel_bullish = intel_dir == "buy"
        intel_bearish = intel_dir == "sell"

        if not (tech_bullish or tech_bearish):
            return "none"
        if not (intel_bullish or intel_bearish):
            return "none"

        # 同向 = 无冲突
        if (tech_bullish and intel_bullish) or (tech_bearish and intel_bearish):
            return "none"

        # 反向冲突
        if intel_urgency == "critical":
            return "override"
        return "caution"

    def _build(
        self,
        position_id: str,
        signal: str,
        action: str,
        ratio: float,
        facts: list[str],
        stop_price: Optional[float],
        target_prices: list[float],
        current_weight_pct: Optional[float],
        price: Optional[float],
        quantity: Optional[float],
    ) -> QuantReview:
        # 仓位上限
        position_limit = self.DEFAULT_POSITION_LIMIT_PCT
        ma20 = self.indicators.get("ma_20")
        r20 = self.indicators.get("r20")  # 20 日收益
        macd_hist = (self.indicators.get("macd") or {}).get("hist")
        trend_confirmed = (
            ma20 is not None
            and price is not None
            and price > ma20
            and r20 is not None
            and r20 > 2.0
            and (macd_hist is None or macd_hist > 0)
        )
        if trend_confirmed:
            position_limit = self.TREND_CONFIRMED_LIMIT_PCT
            facts.append("趋势确认，单标上限可拓展至 10%")

        # 止损风险 — 使用实时 USD/CNY 汇率，而非硬编码值
        risk_amount_cny = None
        risk_to_stop_pct = None
        if price is not None and stop_price is not None and quantity is not None and current_weight_pct is not None:
            risk_amount = max(0.0, (price - stop_price) * quantity)
            fx_rate = get_usd_cny_rate()
            risk_amount_cny = risk_amount * fx_rate.rate
            facts.append(f"止损风险按 USD/CNY {fx_rate.rate:.4f} ({fx_rate.source}) 折算")
            risk_to_stop_pct = current_weight_pct * (price - stop_price) / price if price > 0 else None

        return QuantReview(
            position_id=position_id,
            signal=signal,
            action=action,
            ratio=ratio,
            facts=facts,
            stop_price=stop_price,
            target_prices=target_prices,
            position_limit_pct=position_limit,
            current_weight_pct=current_weight_pct,
            risk_to_stop_pct=risk_to_stop_pct,
            risk_amount_cny=risk_amount_cny,
            intelligence_conflict="none",
        )


def compute_portfolio_risk(
    reviews: list[QuantReview],
    total_value_cny: float,
    *,
    position_valuations: Optional[list[dict]] = None,
) -> dict:
    """组合风险仪表盘。

    Args:
        reviews: QuantReview 列表
        total_value_cny: 组合总市值 (CNY)
        position_valuations: 可选，包含 classification/exposure_tags 的持仓估值列表，
            用于多因子压力测试。未提供时回退到简单 ±5%/±10% 情景。
    """
    if not reviews or total_value_cny <= 0:
        return {
            "total_value_cny": total_value_cny,
            "top3_concentration_pct": 0.0,
            "stop_loss_risk_pct": 0.0,
            "stop_loss_risk_cny": 0.0,
            "scenario": {},
            "items": [],
        }

    sorted_weights = sorted(
        [r for r in reviews if r.current_weight_pct is not None],
        key=lambda r: r.current_weight_pct or 0,
        reverse=True,
    )
    top3 = sum(r.current_weight_pct for r in sorted_weights[:3])

    stop_risk_cny = sum(r.risk_amount_cny for r in reviews if r.risk_amount_cny) or 0.0
    stop_risk_pct = stop_risk_cny / total_value_cny * 100

    items = []
    for r in reviews:
        items.append({
            "position_id": r.position_id,
            "signal": r.signal,
            "action": r.action,
            "weight_pct": r.current_weight_pct,
            "stop_price": r.stop_price,
            "risk_to_stop_pct": r.risk_to_stop_pct,
        })

    scenario = _build_scenarios(total_value_cny, position_valuations)

    return {
        "total_value_cny": round(total_value_cny, 2),
        "top3_concentration_pct": round(top3, 2),
        "stop_loss_risk_pct": round(stop_risk_pct, 4),
        "stop_loss_risk_cny": round(stop_risk_cny, 2),
        "scenario": scenario,
        "items": items,
    }


def _build_scenarios(
    total_value_cny: float,
    position_valuations: Optional[list[dict]] = None,
) -> dict:
    """构建多因子压力测试情景。

    当提供持仓分类数据时，按 exposure_tags 对各资产分配情景冲击系数；
    未提供时回退到简单完美相关假设。
    """
    base_scenarios = {
        "market_down_5_pct": round(total_value_cny * -0.05, 2),
        "market_down_10_pct": round(total_value_cny * -0.10, 2),
        "market_up_5_pct": round(total_value_cny * 0.05, 2),
    }

    if not position_valuations:
        return base_scenarios

    # 多因子冲击系数：按 exposure_tags 分配不同 beta
    # 基于历史相关性的方向性估算，非精确协方差
    factor_shocks = {
        "global_risk_off": {
            "description": "VIX > 30 全球避险",
            "tags": {
                "us_equity": -0.15, "us_tech": -0.18, "a_equity": -0.10,
                "gold": 0.03, "treasury": 0.02, "crypto": -0.30,
                "energy": -0.12, "cash": 0.00, "fixed_income": 0.01,
            },
            "default": -0.10,
        },
        "china_shock": {
            "description": "中国特定政策冲击",
            "tags": {
                "a_equity": -0.20, "a_broad": -0.18, "a_sector": -0.22,
                "hk_proxy": -0.22, "us_equity": -0.05, "us_tech": -0.05,
                "gold": 0.00, "treasury": -0.01, "crypto": -0.05,
                "cash": 0.00, "fixed_income": 0.00,
            },
            "default": -0.08,
        },
        "inflation_commodity": {
            "description": "通胀/大宗商品冲击",
            "tags": {
                "energy": 0.10, "gold": 0.08, "us_equity": -0.08,
                "us_tech": -0.12, "a_equity": -0.07, "treasury": -0.05,
                "a_sector": -0.06, "crypto": -0.10,
                "cash": 0.00, "fixed_income": -0.02,
            },
            "default": -0.05,
        },
    }

    # 为每个持仓匹配最相关的冲击系数（每笔持仓只用一次，避免多标签重复计算）
    scenarios = {}
    for scenario_name, config in factor_shocks.items():
        impact = 0.0
        tags = config["tags"]
        default_shock = config["default"]
        mapped = 0.0
        for pv in position_valuations:
            value = pv.get("market_value_cny")
            if value is None:
                continue
            classification = pv.get("classification") or {}
            exposure_tags = classification.get("exposure_tags") or [classification.get("asset_class", "unknown")]
            # 取第一个匹配该情景的标签，按列表中顺序（越靠前越具体）
            shock = default_shock
            for tag in exposure_tags:
                if tag in tags:
                    shock = tags[tag]
                    break
            impact += value * shock
            mapped += value
        scenarios[scenario_name] = {
            "description": config["description"],
            "impact_cny": round(impact, 2),
            "impact_pct": round(impact / total_value_cny * 100, 4) if total_value_cny > 0 else 0.0,
            "details": {
                "method": "exposure_tag_weighted",
                "total_mapped_cny": round(mapped, 2),
                "unmapped_cny": round(total_value_cny - mapped, 2),
            },
        }

    # 保留简单情景供快速参考
    return {**base_scenarios, **scenarios}


def backtest_action_signals(
    frames: dict[str, pd.DataFrame],
    signals_history: list[dict],
    hold_days: int = 5,
) -> dict:
    """用历史数据回测 action signal 规则胜率。"""
    signal_to_direction = {
        "accumulate_candidate": "buy",
        "rotation_candidate": "buy",
        "wait_for_pullback": "sell",
        "reduce_risk": "sell",
        "avoid_catching_falling_knife": "sell",
    }
    results: list[dict] = []
    for signal in signals_history:
        key = signal.get("symbol")
        raw = signal.get("direction") or signal.get("signal")
        direction = signal_to_direction.get(raw)
        as_of = signal.get("as_of")
        if not key or not as_of or direction not in ("buy", "sell"):
            continue
        frame = frames.get(key)
        if frame is None or frame.empty or "price" not in frame.columns:
            continue
        df = frame.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.sort_values("timestamp").reset_index(drop=True)
        as_of_ts = pd.Timestamp(as_of, tz="UTC")
        entry_positions = [i for i, ts in enumerate(df["timestamp"]) if ts >= as_of_ts]
        if not entry_positions:
            continue
        entry_pos = entry_positions[0]
        if entry_pos + 1 >= len(df):
            continue
        entry_price = float(df.iloc[entry_pos]["price"])
        future_end = min(entry_pos + 1 + hold_days, len(df))
        future = df.iloc[entry_pos + 1 : future_end]
        if future.empty:
            continue
        exit_price = float(future.iloc[-1]["price"])
        pnl = (exit_price / entry_price - 1) * 100
        # 简化: buy 方向赚为正 pnl, sell 方向赚为 -pnl
        score = pnl if direction == "buy" else -pnl
        results.append({
            "symbol": key,
            "signal": raw,
            "direction": direction,
            "as_of": as_of,
            "entry": round(entry_price, 4),
            "exit": round(exit_price, 4),
            "pnl": round(pnl, 4),
            "score": round(score, 4),
        })
    if not results:
        return {"status": "no_data", "win_rate": None, "avg_score": None, "trades": []}
    wins = sum(1 for r in results if r["score"] > 0)
    win_rate = wins / len(results)
    avg_score = sum(r["score"] for r in results) / len(results)
    return {
        "status": "ok",
        "hold_days": hold_days,
        "trades": len(results),
        "win_rate": round(win_rate, 4),
        "avg_score": round(avg_score, 4),
        "details": results,
    }
