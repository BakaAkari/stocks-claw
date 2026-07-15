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

from stocks.engine.data_quality_gate import compute_action_eligible
from stocks.engine.exchange_rate import get_usd_cny_rate
from stocks.engine.factor_rules import adjudicate, collect_votes

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
    # Macro → user positions
    "USO": "XLE",           # oil fund → US energy ETF
    "GLD": "NEM",            # gold ETF → gold miners ETF (and gold positions)
    "NEM": "NEM",            # gold miners → direct
    # Equity indices
    # Gold → multiple gold-related positions
    "GOLD": "NEM",
    # Equity
    "QQQ": "alipay_gf_nasdaq",  # NASDAQ ETF → Alipay NASDAQ fund
    "SPY": "SPY",           # S&P 500
    "ITA": "ITA",           # defense ETF
    "NVDA": "NVDA",         # NVIDIA stock
    "XLE": "XLE",           # energy ETF
    # China market
    "KWEB": "alipay_info",  # China internet → active info fund
    "FXI": "a_510300",     # China large cap → 沪深300
    "ASHR": "a_510300",    # China A-shares → 沪深300
    # Gold/precious metals
    "GDX": "NEM",          # gold miners ETF → NEM
    "SLV": "NEM",          # silver → gold miners proxy
    "GC=F": "ccb_gold",    # gold futures → 建行黄金
    "XAU": "ccb_gold",     # gold spot → 建行黄金
    "GC": "ccb_gold",      # gold futures → 建行黄金
    # China gold
    "518880": "518880",    # 黄金ETF
    # Fixed income
    "TLT": "SGOV",         # long treasury → short treasury
    "SHY": "SGOV",         # short treasury → short treasury
    "SGOV": "SGOV",        # short treasury ETF
    # Broad market
    "IWM": "a_512890",     # Russell 2000 → 中证500
    # Bitcoin
    "BTCUSDT": "alipay_info",  # Bitcoin → active fund (tech-heavy)
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
    # ── 全执行：场内证券 ──
    "exchange_traded_fund": {"mode": "full"},
    "stock": {"mode": "full"},
    "short_treasury_etf": {"mode": "full"},
    # ── 高门槛执行：场外基金（T+2，更高止盈/止损阈值，单次操作）──
    "qdii_fund": {"mode": "fund",
                  "context": "场外 QDII，申赎 T+2，以收盘净值为准。止盈阈值 40%，止损阈值 -20%"},
    "feeder_fund": {"mode": "fund",
                    "context": "场外联接基金，申赎 T+2，以收盘净值为准。止盈阈值 40%，止损阈值 -20%"},
    "mixed_fund": {"mode": "fund",
                   "context": "主动管理混合基金，高浮盈后需关注基金经理风格漂移。止盈阈值 40%"},
    "fixed_income_plus_fund": {"mode": "fund",
                               "context": "固收+产品，波动率低。技术信号仅供参考，以资产配置逻辑为准"},
    # ── 价差产品：贵金属（正常阈值，但有买卖价差）──
    "precious_metal_account": {"mode": "precious",
                               "context": "积存金/贵金属账户，买卖有价差，短线操作成本高"},
    # ── 只读：银行理财 ──
    "bank_wealth_management": {"mode": "info_only",
                              "context": "银行理财，有开放期限制，非开放期不可操作"},
    # ── 跳过：现金等价物 ──
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
    technical_evidence: float = 0.0  # 0.0-1.0, 触发条件本身的信号强度


@dataclass
class FinalDecision:
    """一次调用确定所有输出 — 无 mutation 管道。

    新增字段（Task 2）：
      raw_signal / raw_ratio / raw_action — 原始技术面信号（异常阻断时保留）
      evidence_status — 证据态: ok / blocked / partial
    """
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
    constraint_conflict: str = "none"
    drivers: list[dict] = field(default_factory=list)
    dissent: Optional[dict] = None
    confidence: str = "medium"
    # ── Task 2: 数据异常守门 ──
    raw_signal: str = ""
    raw_ratio: float = 0.0
    raw_action: str = ""
    evidence_status: str = "ok"

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
            technical_evidence = 0.90
            return self._build(position_id, "stop_loss", "止损清仓", 1.0,
                [f"浮亏 {pnl_pct:.2f}% 已超过硬止损 {c['stop_loss_pct']}%"],
                round(cost * (1 + c["stop_loss_pct"] / 100), 4) if cost else None,
                [], current_weight_pct, price, quantity)

        # 2. 中间减仓
        if isinstance(pnl_pct, (int, float)) and pnl_pct <= c["mid_stop_pct"]:
            technical_evidence = 0.75
            return self._build(position_id, "reduce",
                f"浮亏超出中间阈值 {c['mid_stop_pct']}%，减仓 {int(c['mid_stop_ratio']*100)}%",
                c["mid_stop_ratio"],
                [f"浮亏 {pnl_pct:.2f}% 触发中间减仓阈值 {c['mid_stop_pct']}%"],
                round(cost * (1 + c["mid_stop_pct"] / 100), 4) if cost else None,
                [], current_weight_pct, price, quantity)

        # 3. 止损预警
        if isinstance(pnl_pct, (int, float)) and pnl_pct <= c["warning_loss_pct"]:
            facts.append(f"浮亏 {pnl_pct:.2f}% 触发警示阈值 {c['warning_loss_pct']}%")

        # 4. 趋势跌破 — 阶梯减仓（trend_confirm_days 收紧触发阈值）
        if isinstance(price, (int, float)) and ma20 is not None:
            confirm_days = c.get("trend_confirm_days", 1)
            # 多天确认 → 收紧 cutoff，要求更深偏离才触发（每多1天收紧 0.005）
            adjusted_cutoff = c["trend_ma20_break_cutoff"] - (confirm_days - 1) * 0.005
            adjusted_cutoff = max(adjusted_cutoff, 0.910)  # 不低于 0.91
            trigger_price = ma20 * adjusted_cutoff
            if price < trigger_price and (macd_hist is None or macd_hist < 0):
                ratio = 0.25
                deviation = (ma20 - price) / ma20
                for threshold, ladder_ratio in reversed(c["trend_break_ladder"]):
                    if price / ma20 < threshold:
                        ratio = ladder_ratio
                        break
                reason = (
                    f"现价 {price:.2f} 跌破 MA20 {ma20:.2f}（触发线 {trigger_price:.4f}，"
                    f"偏离 {deviation:.1%}），MACD 柱为负"
                )
                facts_list = [reason]
                if confirm_days > 1:
                    facts_list.append(
                        f"趋势确认模式（{confirm_days}天）：跌破阈值收紧至 {adjusted_cutoff:.3f}"
                        f"（默认 {c['trend_ma20_break_cutoff']:.3f}），要求偏离 ≥{((1-adjusted_cutoff)*100):.1f}% 才触发"
                    )
                # evidence: deeper deviation → stronger signal (0.3 base + deviation depth)
                evidence_from_deviation = min(0.9, 0.3 + deviation * 3.0)
                technical_evidence = round(evidence_from_deviation, 2)
                return self._build(position_id, "reduce",
                    f"趋势走弱（MA20偏离 {deviation:.1%}），减仓 {int(ratio*100)}%",
                    ratio,
                    facts_list,
                    round(float(trigger_price), 4), [], current_weight_pct, price, quantity,
                    technical_evidence=technical_evidence)

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
                    evidence_from_pnl = min(1.0, pnl_pct / 50.0)
                    technical_evidence = round(evidence_from_pnl, 2)
                    return self._build(position_id, "take_profit",
                        f"触发 {max_level}% 止盈，减仓 {int(total_reduce*100)}%",
                        total_reduce, [f"浮盈 {pnl_pct:.2f}% 触发止盈"],
                        stop_price, target_prices, current_weight_pct, price, quantity,
                        technical_evidence=technical_evidence)

        # 6. 单日浮盈回撤
        if (isinstance(one_day_change_pct, (int, float))
                and one_day_change_pct <= c["profit_pullback_pct"]
                and isinstance(pnl_pct, (int, float))
                and pnl_pct >= c["profit_pullback_min_pnl"]):
            technical_evidence = 0.55
            return self._build(position_id, "reduce",
                f"单日回撤 {one_day_change_pct:.2f}%，减仓 25% 锁定浮盈", 0.25,
                [f"单日下跌 {one_day_change_pct:.2f}%，浮盈 {pnl_pct:.2f}%"],
                stop_price, [], current_weight_pct, price, quantity, technical_evidence=technical_evidence)

        # 7. 回踩加仓（MA20 偏离驱动档位 + 仓位上限门）
        if isinstance(price, (int, float)) and ma20 is not None and cost is not None:
            rsi = self.indicators.get("rsi_14")
            near_ma20 = 0.97 <= price / ma20 <= 1.03
            rsi_ok = rsi is None or (c["left_add_min_rsi"] <= rsi <= c["left_add_max_rsi"])
            macd_ok = macd_hist is None or macd_hist >= 0
            # 仓位上限门：已接近 position_limit → 不加仓
            limit = c.get("default_position_limit_pct", 5.0)
            weight_ok = (current_weight_pct or 0) < limit * 0.8
            if near_ma20 and rsi_ok and macd_ok and weight_ok                     and pnl_pct is not None and pnl_pct < 5.0:
                ladder = c.get("add_ladder", [0.02])
                # MA20 偏离驱动档位（与触发条件同源）
                deviation_ma20 = (ma20 - price) / ma20  # >0 = 低于MA20
                tier = 0
                if deviation_ma20 > 0.02 and len(ladder) > 2:
                    tier = 2
                elif deviation_ma20 > 0.01 and len(ladder) > 1:
                    tier = 1
                add_size = ladder[min(tier, len(ladder) - 1)]
                add_pct = round(add_size * 100)
                tier_label = ["首档", "二档", "三档"][tier] if tier < 3 else f"档位{tier+1}"
                facts_extras = [
                    f"价格回踩 MA20（偏离 {deviation_ma20:.1%}），RSI {rsi}，MACD 未走坏",
                ]
                if len(ladder) > 1:
                    remaining = [f"{round(s*100)}%" for s in ladder[tier+1:]]
                    if remaining:
                        facts_extras.append(
                            f"加仓阶梯({tier_label} {add_pct}%)：偏离扩大后→ {', '.join(remaining)}"
                        )
                evidence_from_retrace = min(0.8, 0.35 + deviation_ma20 * 15.0)
                technical_evidence = round(evidence_from_retrace, 2)
                return self._build(position_id, "add",
                    f"回踩 MA20（偏离 {deviation_ma20:.1%}），加仓 {add_pct}%（{tier_label}）", -add_size,
                    facts_extras,
                    stop_price, [], current_weight_pct, price, quantity, technical_evidence=technical_evidence)
            # 仓位已满，记录但不生成 add 信号
            if near_ma20 and rsi_ok and macd_ok and not weight_ok                     and pnl_pct is not None and pnl_pct < 5.0:
                facts.append(
                    f"回踩 MA20 但仓位 {current_weight_pct:.1f}% 接近上限 {limit:.0f}%×0.8，不触发加仓"
                )

        if not facts:
            facts.append("未触发任何规则")
        technical_evidence = 0.0
        return self._build(position_id, signal, action, ratio, facts,
                           stop_price, target_prices, current_weight_pct, price, quantity, technical_evidence=technical_evidence)

    def _build(self, position_id, signal, action, ratio, facts,
               stop_price, target_prices, current_weight_pct, price, quantity,
               technical_evidence: float = 0.0) -> QuantReview:
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
            technical_evidence=technical_evidence,
        )


# ---------------------------------------------------------------------------
# finalize_decision — 确定性最终决策
# ---------------------------------------------------------------------------


def _build_drivers(*, tech, signal, action, votes, intelligence_signals, position):
    """构建多源信号驱动向量 — 每个信号源独立标注方向和理由。

    Task 3: Intelligence driver now uses unified match_intelligence() result
    with provenance fields (generation_method, match_method, source_as_of).
    """
    drivers = []
    tech_dir = _signal_direction(signal)
    tech_reasons = tech.facts[:2] if tech.facts else [f"最终信号: {signal}"]
    drivers.append({"source": "technical", "signal": signal, "direction": tech_dir, "reasons": tech_reasons})

    # Task 3: Use unified matcher
    from stocks.engine.intelligence_analyzer import (
        _intel_consensus_direction_from_matched,
        coerce_intelligence_signals,
        match_intelligence,
    )
    intel_sigs_raw = intelligence_signals or {}
    parsed_signals = coerce_intelligence_signals(intel_sigs_raw)
    matched = match_intelligence(position, parsed_signals)
    if matched:
        intel_dir = _intel_consensus_direction_from_matched(matched)
        intel_reasons = [
            f"{m.matched_symbol}: {m.direction} ({m.rationale[:80]}) [{m.generation_method}]"
            for m in matched[:2]
        ]
        provenance = {
            "match_count": len(matched),
            "generation_methods": sorted({m.generation_method for m in matched}),
            "match_methods": sorted({m.match_method for m in matched}),
        }
        drivers.append({
            "source": "intelligence",
            "signal": intel_dir,
            "direction": intel_dir,
            "reasons": intel_reasons,
            "provenance": provenance,
        })
    else:
        drivers.append({
            "source": "intelligence",
            "signal": "unavailable",
            "direction": "unavailable",
            "reasons": ["未匹配到可用方向情报信号"],
            "provenance": {
                "match_count": 0,
                "padding_count": 0,
                "generation_methods": [],
                "match_methods": [],
            },
        })

    factor_dirs = []
    factor_items = []
    for v in votes:
        d = _vote_direction(v)
        factor_dirs.append(d)
        factor_items.append({"factor": v.factor_name, "modifier": v.ratio_modifier, "direction": d, "facts": v.facts[:2] if v.facts else []})
    fac_dir = _consensus(factor_dirs) if factor_dirs else "neutral"
    drivers.append({"source": "factor", "signal": fac_dir, "direction": fac_dir, "reasons": [f"{f['factor']}: ratio_mod={f['modifier']:.2f} {' '.join(f['facts'][:1])}" for f in factor_items[:3]] if factor_items else ["无活跃因子"], "details": factor_items})
    return drivers


def _detect_dissent(drivers: list[dict], final_signal: str) -> Optional[dict]:
    final_dir = _signal_direction(final_signal)
    for d in drivers:
        src_dir = d.get("direction", "neutral")
        if src_dir in ("neutral", "unavailable") or final_dir == "neutral":
            continue
        if (src_dir == "bullish" and final_dir == "bearish") or (src_dir == "bearish" and final_dir == "bullish"):
            reasons = d.get("reasons", [])
            impact = ""
            if d["source"] == "intelligence":
                impact = "若情报方向正确，当前操作可能过早，建议关注信号是否反转"
            elif d["source"] == "factor":
                impact = "因子层与最终方向冲突，建议复核因子权重"
            return {"source": d["source"], "direction": src_dir, "signal": "、".join(reasons[:2]) if reasons else f"{d['source']} 管线方向与最终结论相反", "impact": impact or f"{d['source']} 方向冲突"}
    return None


def _compute_confidence(
    drivers: list[dict], technical_evidence: float, data_freshness: str = "fresh"
) -> str:
    """基于信号强度 × 数据新鲜度计算置信度。

    技术信号强度来自触发条件本身（偏离深度、浮盈幅度、阈值超越程度），
    不受 intelligence 匹配状态影响。intelligence 作为独立方向标注（dissent/agree）。

    置信度阈值：
    - ≥0.70 → high（强信号 × 新鲜数据）
    - ≥0.40 → medium
    - <0.40 → low（弱信号 或 数据陈旧）
    """
    recency_map = {"fresh": 1.0, "stale": 0.75, "delayed": 0.5, "error": 0.3}
    recency_factor = recency_map.get(data_freshness, 0.7)
    score = technical_evidence * recency_factor

    # 无有效技术信号 → 退化为源可用性判断
    if technical_evidence <= 0:
        dirs = [d.get("direction", "neutral") for d in drivers]
        unavailable = dirs.count("unavailable")
        active = [d for d in dirs if d not in ("neutral", "unavailable")]
        if unavailable >= 2 or not active:
            return "low"
        if unavailable == 1 and len(set(active)) == 1:
            return "medium"
        return "high" if len(set(active)) == 1 and unavailable == 0 else "medium"

    if score >= 0.7:
        return "high"
    if score >= 0.4:
        return "medium"
    return "low"


def _signal_direction(signal: str) -> str:
    bearish = {"reduce", "reduce_risk", "stop_loss", "sell", "avoid_catching_falling_knife"}
    bullish = {"add", "accumulate_candidate", "buy", "rotation_candidate"}
    neutral_exit = {"take_profit"}
    if signal in bearish:
        return "bearish"
    if signal in bullish:
        return "bullish"
    if signal in neutral_exit:
        return "neutral"
    return "neutral"


def _intel_consensus_direction(signals: list[dict]) -> str:
    dirs = []
    for s in signals:
        d = (s.get("direction") or "").lower()
        if d in ("buy", "bullish", "positive"):
            dirs.append("bullish")
        elif d in ("sell", "bearish", "negative"):
            dirs.append("bearish")
        else:
            dirs.append("neutral")
    return _consensus(dirs)


def _vote_direction(vote) -> str:
    """判断因子是否表达了方向意见。

    ratio_modifier != 1.0 不一定是方向信号 —— data_freshness
    的降权是可靠性调节，不是方向判断。只有 signal_override
    或明确的 conflict_type 才表示方向意见。
    """
    if vote.signal_override:
        return _signal_direction(vote.signal_override)
    if vote.conflict_type in ("override", "caution"):
        return "bearish" if vote.ratio_modifier < 1.0 else "bullish"
    return "neutral"


def _consensus(dirs: list[str]) -> str:
    b = dirs.count("bullish")
    s = dirs.count("bearish")
    if b > s:
        return "bullish"
    if s > b:
        return "bearish"
    return "neutral"





def _format_action_text(
    signal: str,
    ratio: float,
    mode: str,
    rule: dict,
    *,
    base_action: str = '',
) -> str:
    """从最终 (signal, ratio, mode) 统一生成 action 文本。

    这是 action 文本的单一生成点——所有管道阶段修改 ratio/signal 后，
    不直接拼接字符串，而是调用此函数。保证 action 文本永远与 ratio 一致。
    """
    import re
    pct = round(abs(ratio) * 100)

    # ── 路由后缀 ──
    if mode == 'fund':
        suffix = '场外基金 T+2 到账，以收盘净值为准 — 登录平台操作'
    elif mode == 'precious':
        suffix = '贵金属账户有买卖价差 — 登录平台确认后操作'
    else:
        suffix = ''

    def joined(s):
        return f'{s}。{suffix}' if suffix else s

    if signal == 'stop_loss':
        if mode in ('fund', 'precious'):
            return joined(f'止损触发 — 建议减仓 {pct}%')
        return '止损清仓' if ratio >= 1.0 else f'止损触发 — 减仓 {pct}%'

    if signal == 'take_profit':
        return joined(f'止盈触发 — 建议减仓 {pct}%')

    if signal == 'reduce':
        # 保留 tech engine 的定性描述，替换百分比部分
        desc = re.sub(r'[，,]\s*减仓\s*\d+%?\s*$', '', base_action)
        result = f'{desc}，减仓 {pct}%'
        return f'{result}（{suffix}）' if suffix else result

    if signal == 'add':
        desc = re.sub(r'[，,]\s*加仓\s*\d+%?\s*$', '', base_action)
        result = f'{desc}，加仓 {pct}%'
        return f'{result}（{suffix}）' if suffix else result

    # hold / wait — 保持原文本
    if suffix and signal not in ('hold', 'wait'):
        return f'{base_action}（{suffix}）'
    return base_action

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

    ranks = rotation_ranks or {}

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

    # ── 0.5 数据异常守门（Task 2）──
    # 优先级高于技术面动作，但低于锁定/已清仓
    evidence = position.get("evidence") or {}
    raw_anomalies = evidence.get("data_anomalies", [])
    # 保存原始技术信号以备审计（Task 4 将使用）
    raw_signal = signal
    raw_ratio = ratio
    raw_action = action
    evidence_status = "ok"
    if raw_anomalies:
        eligible, reasons = compute_action_eligible(raw_anomalies)
        if not eligible:
            evidence_status = "blocked"
            block_reason = "；".join(reasons[:2])  # 取前两个原因
            facts.append(f"数据异常阻断: {block_reason}")
            return FinalDecision(
                position_id=tech.position_id, signal="hold",
                action=f"数据异常，暂停技术动作（{block_reason[:60]}）",
                ratio=0.0, facts=facts,
                stop_price=tech.stop_price, target_prices=tech.target_prices,
                position_limit_pct=tech.position_limit_pct,
                current_weight_pct=tech.current_weight_pct,
                risk_to_stop_pct=tech.risk_to_stop_pct,
                risk_amount_cny=tech.risk_amount_cny,
                intelligence_conflict="none", constraint_conflict="none",
                raw_signal=raw_signal, raw_ratio=raw_ratio, raw_action=raw_action,
                evidence_status=evidence_status,
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
    tech_ratio = ratio  # 保存原始技术信号 ratio，用于信号链可见化
    result = adjudicate(signal, action, ratio, votes)
    signal = result["signal"]
    action = result["action"]
    ratio = result["ratio"]
    facts.extend(result["facts"])
    # ── 信号链可见化：因子调整若改变了 ratio，说明原因 ──
    if tech_ratio > 0 and abs(ratio - tech_ratio) > 0.001:
        # 找出哪些因子修改了 ratio
        ratio_votes = [v for v in votes if v.ratio_modifier != 1.0]
        modifiers = [f'{v.factor_name}: ×{v.ratio_modifier:.2f}' for v in ratio_votes]
        chain = ' → '.join(modifiers) if modifiers else '因子调整'
        facts.append(f'信号链: 原始 ratio={tech_ratio} → {chain} → 最终 ratio={ratio:.4f}')
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

    # ── 8. 分层路由：统一从最终 (signal, ratio, mode) 生成 action ──
    ctx = rule.get("context", "")
    if mode in ("fund", "precious", "full"):
        if ctx:
            facts.insert(0, ctx)
        # 重新生成 action 文本 — 使用最终 ratio，保证与 signal 链一致
        action = _format_action_text(
            signal, ratio, mode, rule, base_action=action,
        )
        if mode in ("fund", "precious"):
            nav_label = ("手工估值（非实时净值），建议确认后手动操作"
                         if valuation_method != "fund_nav"
                         else "净值来源：天天基金（T-1 确认净值）")
            facts.append(nav_label)
        odc = one_day_change_pct
        if valuation_method == "fund_nav" and isinstance(odc, (int, float)) and abs(odc) > 0.5:
            lag_pct = round(odc * 0.85, 2)
            lag_dir = "上涨" if lag_pct > 0 else "下跌"
            facts.append(f"T-1净值滞后：当日标的ETF {lag_dir} {abs(odc):.2f}%，"
                         f"估算真实净值偏差 {abs(lag_pct):.2f}%，止盈建议以基金公司确认为准")
    elif mode == "info_only":
        ratio = 0.0
        if ctx:
            facts.insert(0, ctx)
        if signal == "stop_loss":
            action = "止损预警（银行理财，有开放期限制，仅提醒）"
        elif signal == "take_profit":
            action = "止盈提醒（银行理财，有开放期限制，仅提醒）"
        elif signal in ("reduce", "add"):
            action = action + "（银行理财，有开放期限制）"
        elif signal not in ("hold", "wait"):
            pass

    # ── 9. 非 rebalance_eligible 降权 ──
    if not rebalance_ok:
        facts.append("可调仓但需谨慎（场外基金/贵金属），仓位上限 2%")

    # ── 10. 构建多源驱动向量 ──
    drivers = _build_drivers(
        tech=tech, signal=signal, action=action,
        votes=votes, intelligence_signals=intelligence_signals,
        position=position,
    )
    dissent = _detect_dissent(drivers, signal)
    confidence = _compute_confidence(drivers, tech.technical_evidence, data_freshness)

    # ── 低置信度警告 ──
    if confidence == "low":
        unavailable_sources = [d["source"] for d in drivers if d.get("direction") == "unavailable"]
        parts = []
        if unavailable_sources:
            parts.append(f'{"、".join(unavailable_sources)} 缺失')
        tech_ev = getattr(tech, 'technical_evidence', 0)
        if tech_ev < 0.4:
            parts.append(f'信号强度 {tech_ev:.1f}')
        reason = '（' + '；'.join(parts) + '）' if parts else ''
        warn = f'⚠️ 低置信度{reason}'
        facts.insert(0, warn)

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
        drivers=drivers,
        dissent=dissent,
        confidence=confidence,
        # ── Task 2: 数据异常守门 ──
        raw_signal=raw_signal, raw_ratio=raw_ratio, raw_action=raw_action,
        evidence_status=evidence_status,
    )


# ---------------------------------------------------------------------------
# 组合风险计算
# ---------------------------------------------------------------------------

def compute_portfolio_risk(
    cards: list[dict], total_value_cny: float,
    *, position_valuations: Optional[list[dict]] = None,
) -> dict:
    """组合风险仪表盘。cards 为 _build_action_cards 产出的最终决策卡，
    与 action_cards 完全一致——portfolio_risk.items 不再独立计算。"""
    if not cards or total_value_cny <= 0:
        return {"total_value_cny": total_value_cny, "top3_concentration_pct": 0.0,
                "stop_loss_risk_pct": 0.0, "stop_loss_risk_cny": 0.0,
                "scenario": {}, "items": []}
    sorted_weights = sorted(
        [c for c in cards if c.get("current_weight_pct") is not None],
        key=lambda c: c.get("current_weight_pct") or 0, reverse=True)
    top3 = sum(c.get("current_weight_pct") or 0 for c in sorted_weights[:3])
    stop_risk_cny = sum(c.get("risk_amount_cny") or 0 for c in cards)
    stop_risk_pct = stop_risk_cny / total_value_cny * 100 if total_value_cny > 0 else 0.0
    items = [{"position_id": c["position_id"], "signal": c.get("signal", "hold"),
              "action": c.get("action", "持有"), "weight_pct": c.get("current_weight_pct"),
              "stop_price": c.get("stop_price"),
              "risk_to_stop_pct": c.get("risk_to_stop_pct")} for c in cards]
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
