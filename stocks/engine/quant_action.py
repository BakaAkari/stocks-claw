"""量化行动规则引擎 — 技术面分析 + 确定性最终决策。

引擎职责分层：
  QuantActionEngine  — 纯技术面信号（价格、MA/RSI/MACD、PnL）
  finalize_decision() — 确定性决策函数：一次接收所有上下文输入，按固定优先级
                        输出最终信号，替代原来的多阶段顺序 mutation 管道。

优先级（高→低）：
  stop_loss → constraint_override → intel_override →
  macro/event overlay → routing_downgrade → data_freshness

所有参数均可通过 engine.yaml 的 quant_action 段覆盖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from stocks.engine.exchange_rate import get_usd_cny_rate
from stocks.engine.factor_rules import collect_votes, adjudicate

# ── 事件主题 → 持仓暴露标签 ──
THEME_TO_EXPOSURE: dict[str, list[str]] = {
    "geopolitics": ["energy", "defense", "gold", "oil_gas", "aerospace", "mining"],
    "energy": ["energy", "oil_gas"],
    "technology": ["tech", "semiconductor", "ai", "star_board", "nasdaq100"],
    "earnings": ["tech", "ai", "semiconductor", "nasdaq100"],
    "monetary_policy": ["gold", "fixed_income", "us_rates", "cash_like",
                         "money_market", "bank_wmp", "credit_plus"],
    "crypto": ["crypto"],
    "healthcare": ["healthcare", "bio"],
    "financials": ["financials"],
    "china_policy": ["a_share", "broad_index", "blue_chip", "dividend_low_vol",
                     "high_dividend", "active_equity", "star_board", "utilities", "power"],
    "general": [],
}

# ── 情报信号 symbol → 持仓关联 ──
_INTEL_SIGNAL_PROXY: dict[str, str] = {
    "USO": "XLE", "GLD": "NEM", "QQQ": "alipay_gf_nasdaq",
    "ITA": "ITA", "SPY": "SPY",
}

# ── 曝光标签 → 约束大类 ──
_TAG_TO_BUCKET: dict[str, str] = {
    "gold": "黄金", "mining": "黄金",
    "a_share": "权益", "us_equity": "权益", "tech": "权益",
    "nasdaq100": "权益", "qdii": "权益", "semiconductor": "权益",
    "star_board": "权益", "blue_chip": "权益", "dividend_low_vol": "权益",
    "high_dividend": "权益", "active_equity": "权益",
    "energy": "权益", "oil_gas": "权益", "defense": "权益",
    "aerospace": "权益", "ai": "权益",
    "fixed_income": "固收", "credit_plus": "固收", "us_rates": "固收",
    "bank_wmp": "固收", "short_treasury": "固收",
    "cash_like": "现金", "money_market": "现金",
}

# ── 产品类型路由规则 ──
_PRODUCT_TYPE_RULES: dict[str, dict] = {
    "exchange_traded_fund": {"mode": "full"},
    "stock": {"mode": "full"},
    "short_treasury_etf": {"mode": "full"},
    "qdii_fund": {"mode": "config_only",
                  "context": "场外 QDII，申赎 T+2，适合长期配置。无明确替代方向时不宜频繁止盈"},
    "feeder_fund": {"mode": "config_only",
                    "context": "场外联接基金，申赎 T+2，适合长期配置"},
    "mixed_fund": {"mode": "config_only",
                   "context": "主动管理混合基金，高浮盈后需关注基金经理风格漂移风险"},
    "fixed_income_plus_fund": {"mode": "config_only",
                               "context": "固收+产品，波动率低，MA20/RSI 技术信号不适用"},
    "precious_metal_account": {"mode": "config_only",
                               "context": "积存金，买卖有价差，短线操作成本高"},
    "bank_wealth_management": {"mode": "info_only",
                              "context": "银行理财，有开放期限制，非开放期不可操作"},
    "money_market_fund": {"mode": "skip"},
    "cash": {"mode": "skip"},
    "cash_equivalent": {"mode": "skip"},
    "insurance_policy": {"mode": "skip"},
}

# ── 默认配置（与 config_loader.py 同步）──
_DEFAULT_QUANT_CONFIG: dict = {
    "stop_loss_pct": -12.0,
    "mid_stop_pct": -10.0,
    "mid_stop_ratio": 0.3,
    "warning_loss_pct": -8.0,
    "take_profit_levels": [(10.0, 0.25), (20.0, 0.25), (30.0, 0.50)],
    "profit_pullback_pct": -2.0,
    "profit_pullback_min_pnl": 3.0,
    "trend_ma20_break_cutoff": 0.995,
    "trend_break_ladder": [(0.995, 0.25), (0.980, 0.50), (0.950, 0.75), (0.850, 1.0)],
    "default_position_limit_pct": 5.0,
    "trend_confirmed_limit_pct": 10.0,
    "left_add_max_rsi": 65.0,
    "left_add_min_rsi": 40.0,
}


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class ActionPlan:
    action: str
    ratio: float
    reason: str
    stop_price: Optional[float] = None
    target_prices: list[float] = field(default_factory=list)
    position_limit_pct: Optional[float] = None
    risk_amount_cny: Optional[float] = None


@dataclass
class QuantReview:
    position_id: str
    signal: str
    action: str
    ratio: float
    facts: list[str]
    stop_price: Optional[float]
    target_prices: list[float]
    position_limit_pct: float
    current_weight_pct: Optional[float]
    risk_to_stop_pct: Optional[float]
    risk_amount_cny: Optional[float]
    intelligence_conflict: str = "none"


@dataclass
class FinalDecision:
    """一次调用确定所有输出 — 无 mutation 管道。"""
    position_id: str
    signal: str
    action: str
    ratio: float
    facts: list[str]
    stop_price: Optional[float]
    target_prices: list[float]
    position_limit_pct: float
    current_weight_pct: Optional[float]
    risk_to_stop_pct: Optional[float]
    risk_amount_cny: Optional[float]
    intelligence_conflict: str
    constraint_conflict: str


# ---------------------------------------------------------------------------
# QuantActionEngine — 纯技术面
# ---------------------------------------------------------------------------

class QuantActionEngine:
    """技术面信号引擎。不涉及情报/约束/路由 — 由 finalize_decision 处理。"""

    def __init__(self, indicators: dict, config: Optional[dict] = None):
        self.indicators = indicators
        cfg = _DEFAULT_QUANT_CONFIG if not config else {**_DEFAULT_QUANT_CONFIG, **config}
        self._c = cfg

    def review_position(
        self, *, position_id: str, price: Optional[float], cost: Optional[float],
        pnl_pct: Optional[float], one_day_change_pct: Optional[float],
        current_weight_pct: Optional[float], quantity: Optional[float],
    ) -> QuantReview:
        """纯技术面 review — 不改 ratio 之外的任何上下文。"""
        facts: list[str] = []
        signal, action, ratio = "hold", "持有", 0.0
        stop_price = None
        target_prices: list[float] = []

        c = self._c
        ma20 = self.indicators.get("ma_20")
        macd_hist = (self.indicators.get("macd") or {}).get("hist")

        # 1. 硬止损
        if isinstance(pnl_pct, (int, float)) and pnl_pct <= c["stop_loss_pct"]:
            return self._build(position_id, "stop_loss", "止损清仓", 1.0,
                [f"浮亏 {pnl_pct:.2f}% 已超过硬止损 {c['stop_loss_pct']}%"],
                round(cost * (1 + c["stop_loss_pct"] / 100), 4) if cost else None,
                [], current_weight_pct, price, quantity)

        # 2. 中间减仓
        if isinstance(pnl_pct, (int, float)) and pnl_pct <= c["mid_stop_pct"]:
            return self._build(position_id, "reduce",
                f"浮亏超出中间阈值 {c['mid_stop_pct']}%，减仓 {int(c['mid_stop_ratio']*100)}%",
                c["mid_stop_ratio"],
                [f"浮亏 {pnl_pct:.2f}% 触发中间减仓阈值 {c['mid_stop_pct']}%"],
                round(cost * (1 + c["mid_stop_pct"] / 100), 4) if cost else None,
                [], current_weight_pct, price, quantity)

        # 3. 止损预警
        if isinstance(pnl_pct, (int, float)) and pnl_pct <= c["warning_loss_pct"]:
            facts.append(f"浮亏 {pnl_pct:.2f}% 触发警示阈值 {c['warning_loss_pct']}%")

        # 4. 趋势跌破 — 阶梯减仓
        if isinstance(price, (int, float)) and ma20 is not None:
            trigger_price = ma20 * c["trend_ma20_break_cutoff"]
            if price < trigger_price and (macd_hist is None or macd_hist < 0):
                ratio = 0.25
                deviation = (ma20 - price) / ma20
                for threshold, ladder_ratio in reversed(c["trend_break_ladder"]):
                    if price / ma20 < threshold:
                        ratio = ladder_ratio
                        break
                return self._build(position_id, "reduce",
                    f"趋势走弱（MA20偏离 {deviation:.1%}），减仓 {int(ratio*100)}%",
                    ratio,
                    [f"现价 {price:.2f} 跌破 MA20 {ma20:.2f}（触发线 {trigger_price:.4f}，"
                     f"偏离 {deviation:.1%}），MACD 柱为负"],
                    round(float(trigger_price), 4), [], current_weight_pct, price, quantity)

        # 5. 止盈阶梯
        if isinstance(pnl_pct, (int, float)) and pnl_pct > 0 and cost is not None:
            for level, _r in c["take_profit_levels"]:
                if pnl_pct >= level:
                    target_prices.append(round(cost * (1 + level / 100), 4))
            if target_prices:
                triggered = [(lv, rv) for lv, rv in c["take_profit_levels"] if pnl_pct >= lv]
                if triggered:
                    total_reduce = min(sum(r for _, r in triggered), 0.75)
                    max_level = triggered[-1][0]
                    return self._build(position_id, "take_profit",
                        f"触发 {max_level}% 止盈，减仓 {int(total_reduce*100)}%",
                        total_reduce, [f"浮盈 {pnl_pct:.2f}% 触发止盈"],
                        stop_price, target_prices, current_weight_pct, price, quantity)

        # 6. 单日浮盈回撤
        if (isinstance(one_day_change_pct, (int, float))
                and one_day_change_pct <= c["profit_pullback_pct"]
                and isinstance(pnl_pct, (int, float))
                and pnl_pct >= c["profit_pullback_min_pnl"]):
            return self._build(position_id, "reduce",
                f"单日回撤 {one_day_change_pct:.2f}%，减仓 25% 锁定浮盈", 0.25,
                [f"单日下跌 {one_day_change_pct:.2f}%，浮盈 {pnl_pct:.2f}%"],
                stop_price, [], current_weight_pct, price, quantity)

        # 7. 左侧加仓
        if isinstance(price, (int, float)) and ma20 is not None and cost is not None:
            rsi = self.indicators.get("rsi_14")
            near_ma20 = 0.97 <= price / ma20 <= 1.03
            rsi_ok = rsi is None or (c["left_add_min_rsi"] <= rsi <= c["left_add_max_rsi"])
            macd_ok = macd_hist is None or macd_hist >= 0
            if near_ma20 and rsi_ok and macd_ok and pnl_pct is not None and pnl_pct < 5.0:
                return self._build(position_id, "add",
                    "回踩 MA20，左侧加仓 2%", -0.02,
                    [f"价格回踩 MA20 {ma20:.2f}，RSI {rsi}，MACD 未走坏"],
                    stop_price, [], current_weight_pct, price, quantity)

        if not facts:
            facts.append("未触发任何规则")
        return self._build(position_id, signal, action, ratio, facts,
                           stop_price, target_prices, current_weight_pct, price, quantity)

    def _build(self, position_id, signal, action, ratio, facts,
               stop_price, target_prices, current_weight_pct, price, quantity) -> QuantReview:
        c = self._c
        limit = c["default_position_limit_pct"]
        ma20 = self.indicators.get("ma_20")
        r20 = self.indicators.get("r20")
        macd_hist = (self.indicators.get("macd") or {}).get("hist")
        trend_confirmed = (
            ma20 is not None and price is not None and price > ma20
            and r20 is not None and r20 > 2.0
            and (macd_hist is None or macd_hist > 0)
        )
        if trend_confirmed:
            limit = c["trend_confirmed_limit_pct"]
            facts.append("趋势确认，单标上限可拓展至 10%")

        risk_amount_cny = None
        risk_to_stop_pct = None
        if (price is not None and stop_price is not None
                and quantity is not None and current_weight_pct is not None):
            risk_amount = max(0.0, (price - stop_price) * quantity)
            fx_rate = get_usd_cny_rate()
            risk_amount_cny = risk_amount * fx_rate.rate
            facts.append(f"止损风险按 USD/CNY {fx_rate.rate:.4f} ({fx_rate.source}) 折算")
            risk_to_stop_pct = current_weight_pct * (price - stop_price) / price if price > 0 else None

        return QuantReview(
            position_id=position_id, signal=signal, action=action,
            ratio=ratio, facts=facts, stop_price=stop_price, target_prices=target_prices,
            position_limit_pct=limit, current_weight_pct=current_weight_pct,
            risk_to_stop_pct=risk_to_stop_pct, risk_amount_cny=risk_amount_cny,
        )


# ---------------------------------------------------------------------------
# finalize_decision — 确定性最终决策
# ---------------------------------------------------------------------------

def finalize_decision(
    *,
    tech: QuantReview,
    position: dict,
    market_state: Optional[dict] = None,
    event_clusters: Optional[list[dict]] = None,
    intelligence_signals: Optional[dict[str, dict]] = None,
    rotation_ranks: Optional[dict[str, int]] = None,
    rotation_symbol: str = "",
    data_freshness: str = "fresh",
    constraints: Optional[dict] = None,
    portfolio_ratios: Optional[dict] = None,
) -> FinalDecision:
    """一次调用完成所有上下文的叠加决策。

    优先级链：stop_loss → constraint_override → intel_override →
              macro/event overlay → routing_downgrade → data_freshness

    返回不可变的 FinalDecision，不修改任何输入。
    """
    facts = list(tech.facts)
    signal = tech.signal
    action = tech.action
    ratio = tech.ratio
    intel_conflict = "none"
    constraint_conflict = "none"

    product_type = (position.get("classification") or {}).get("product_type", "")
    exposure_tags = (position.get("classification") or {}).get("exposure_tags") or []
    rebalance_ok = (position.get("liquidity") or {}).get("rebalance_eligible", True)
    valuation_method = position.get("valuation_method", "")
    one_day_change_pct = position.get("one_day_change_pct")
    mv = position.get("market_value_cny") or 0.0

    clusters = event_clusters or []
    ranks = rotation_ranks or {}
    ms = market_state or {}

    # ── 0. 锁定/已清仓资产 ──
    liq = position.get("liquidity") or {}
    if liq.get("tier") == "locked" or "insurance" in exposure_tags:
        return FinalDecision(
            position_id=tech.position_id, signal="hold",
            action="锁定资产，不可调仓", ratio=0.0, facts=[],
            stop_price=None, target_prices=[], position_limit_pct=0.0,
            current_weight_pct=0.0, risk_to_stop_pct=None, risk_amount_cny=None,
            intelligence_conflict="none", constraint_conflict="none",
        )
    if mv <= 0:
        qty = (position.get("holding") or {}).get("quantity", 0)
        return FinalDecision(
            position_id=tech.position_id, signal="hold",
            action="已清仓，无持仓" if qty == 0 else "持仓为零，无需操作",
            ratio=0.0, facts=[], stop_price=None, target_prices=[],
            position_limit_pct=5.0, current_weight_pct=0.0,
            risk_to_stop_pct=None, risk_amount_cny=None,
            intelligence_conflict="none", constraint_conflict="none",
        )

    # ── 1. 产品路由：skip / info_only 提前退出 ──
    rule = _PRODUCT_TYPE_RULES.get(product_type, {"mode": "full"})
    mode = rule["mode"]
    if mode == "skip":
        return FinalDecision(
            position_id=tech.position_id, signal="hold",
            action="现金/货基/保险类资产，无需操作", ratio=0.0, facts=[],
            stop_price=None, target_prices=[], position_limit_pct=0.0,
            current_weight_pct=position.get("portfolio_weight") or 0.0,
            risk_to_stop_pct=None, risk_amount_cny=None,
            intelligence_conflict="none", constraint_conflict="none",
        )
    if mode == "info_only":
        return FinalDecision(
            position_id=tech.position_id, signal="hold",
            action="持有（" + rule.get("context", "非交易型资产") + "）",
            ratio=0.0, facts=[rule.get("context", "")],
            stop_price=None, target_prices=[], position_limit_pct=0.0,
            current_weight_pct=position.get("portfolio_weight") or 0.0,
            risk_to_stop_pct=None, risk_amount_cny=None,
            intelligence_conflict="none", constraint_conflict="none",
        )

    # ── 2-7. 因子覆盖层（替代原顺序 mutation 管道）──
    # 收集所有因子的投票，按优先级裁决
    votes = collect_votes(
        position,
        current_signal=signal,
        current_ratio=ratio,
        market_state=market_state,
        event_clusters=event_clusters,
        intelligence_signals=intelligence_signals,
        rotation_ranks=rotation_ranks,
        constraints=constraints,
        portfolio_ratios=portfolio_ratios,
        data_freshness=data_freshness,
        rotation_symbol=rotation_symbol,
    )
    result = adjudicate(signal, action, ratio, votes)
    signal = result["signal"]
    action = result["action"]
    ratio = result["ratio"]
    facts.extend(result["facts"])
    # 提取冲突类型
    for c in result["conflicts"]:
        if c.startswith("constraint_check"):
            constraint_conflict = c.split(":", 1)[-1] if ":" in c else "suppression"
        elif c.startswith("intel_conflict"):
            intel_conflict = c.split(":", 1)[-1] if ":" in c else "caution"

    # ── 6. 轮动排名（纯事实追加，不改变决策）──
    ranks = rotation_ranks or {}
    if rotation_symbol and rotation_symbol in ranks:
        rank = ranks[rotation_symbol]
        if rank and rank <= 3 and signal == "add":
            facts.append(f"轮动排名 #{rank}，加仓信号获动量确认")

    # ── 8. config_only 路由降权 ──
    if mode == "config_only":
        ratio = 0.0
        ctx = rule.get("context", "")
        if ctx:
            facts.insert(0, ctx)
        nav_label = ("手工估值（非实时净值），建议确认后手动执行"
                     if valuation_method != "fund_nav"
                     else "净值来源：天天基金（T-1 确认净值）")
        if signal == "stop_loss":
            action = "止损预警（配置型资产，建议登录平台确认）"
            facts.append(nav_label)
        elif signal == "take_profit":
            action = "止盈提醒（配置型资产，无明确替代方向时建议持有）"
            facts.append(nav_label)
        elif signal in ("reduce", "add"):
            action = action + "（配置型资产，建议审慎评估）"
            facts.append(nav_label)
        elif signal not in ("hold", "wait"):
            facts.append(nav_label)
        odc = one_day_change_pct
        if valuation_method == "fund_nav" and isinstance(odc, (int, float)) and abs(odc) > 0.5:
            lag_pct = round(odc * 0.85, 2)
            lag_dir = "上涨" if lag_pct > 0 else "下跌"
            facts.append(f"T-1净值滞后：当日标的ETF {lag_dir} {abs(odc):.2f}%，"
                         f"估算真实净值偏差 {abs(lag_pct):.2f}%，止盈建议以基金公司确认为准")

    # ── 9. 非 rebalance_eligible 降权 ──
    if not rebalance_ok:
        facts.append("可调仓但需谨慎（场外基金/贵金属），仓位上限 2%")

    return FinalDecision(
        position_id=tech.position_id, signal=signal, action=action,
        ratio=ratio, facts=facts,
        stop_price=tech.stop_price, target_prices=tech.target_prices,
        position_limit_pct=tech.position_limit_pct,
        current_weight_pct=tech.current_weight_pct,
        risk_to_stop_pct=tech.risk_to_stop_pct,
        risk_amount_cny=tech.risk_amount_cny,
        intelligence_conflict=intel_conflict,
        constraint_conflict=constraint_conflict,
    )


# ---------------------------------------------------------------------------
# 组合风险计算
# ---------------------------------------------------------------------------

def compute_portfolio_risk(
    reviews: list[QuantReview], total_value_cny: float,
    *, position_valuations: Optional[list[dict]] = None,
) -> dict:
    if not reviews or total_value_cny <= 0:
        return {"total_value_cny": total_value_cny, "top3_concentration_pct": 0.0,
                "stop_loss_risk_pct": 0.0, "stop_loss_risk_cny": 0.0,
                "scenario": {}, "items": []}
    sorted_weights = sorted(
        [r for r in reviews if r.current_weight_pct is not None],
        key=lambda r: r.current_weight_pct or 0, reverse=True)
    top3 = sum(r.current_weight_pct for r in sorted_weights[:3])
    stop_risk_cny = sum(r.risk_amount_cny for r in reviews if r.risk_amount_cny) or 0.0
    stop_risk_pct = stop_risk_cny / total_value_cny * 100
    items = [{"position_id": r.position_id, "signal": r.signal, "action": r.action,
              "weight_pct": r.current_weight_pct, "stop_price": r.stop_price,
              "risk_to_stop_pct": r.risk_to_stop_pct} for r in reviews]
    scenario = _build_scenarios(total_value_cny, position_valuations)
    return {"total_value_cny": round(total_value_cny, 2),
            "top3_concentration_pct": round(top3, 2),
            "stop_loss_risk_pct": round(stop_risk_pct, 4),
            "stop_loss_risk_cny": round(stop_risk_cny, 2),
            "scenario": scenario, "items": items}


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

