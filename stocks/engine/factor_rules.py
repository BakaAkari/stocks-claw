"""
Factor Rules — 决策因子接口与规则库

把 finalize_decision 中的覆盖规则提取为独立、可测试、可回测的因子。
每个因子实现 evaluate() → FactorVote，finalize_decision 收集所有投票后裁决。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from stocks.engine.quant_action import (
    _INTEL_SIGNAL_PROXY,
    _TAG_TO_BUCKET,
    THEME_TO_EXPOSURE,
)


@dataclass
class FactorVote:
    """单个因子的投票结果。"""
    factor_name: str
    direction: str
    ratio_modifier: float = 1.0
    signal_override: str = ""
    action_text: str = ""
    facts: list = field(default_factory=list)
    priority: int = 50
    conflict_type: str = "none"


class FactorRule(ABC):
    """因子规则基类。"""
    name: str = ""
    description: str = ""
    priority: int = 50

    @abstractmethod
    def evaluate(self, position, *, current_signal, current_ratio, **kwargs) -> FactorVote:
        ...


class ConstraintCheckRule(FactorRule):
    """约束互查：组合是否超出大类上限。"""
    name = "constraint_check"
    priority = 95

    def evaluate(self, position, *, current_signal, current_ratio, constraints=None,
                 portfolio_ratios=None, **kwargs):
        if not constraints or not portfolio_ratios:
            return FactorVote("constraint_check", current_signal)
        exposure_tags = (position.get("classification") or {}).get("exposure_tags") or []
        over_limit = set()
        for bucket_name, rule in constraints.items():
            if not isinstance(rule, dict):
                continue
            max_pct = rule.get("max")
            actual = portfolio_ratios.get(bucket_name)
            if max_pct is not None and actual is not None and actual > max_pct:
                over_limit.add(bucket_name)
        matched = {_TAG_TO_BUCKET.get(t, "") for t in exposure_tags} - {""}
        if current_signal == "add" and (matched & over_limit):
            ob = (matched & over_limit).pop()
            return FactorVote("constraint_check", "hold", 0.0,
                              signal_override="hold",
                              action_text=f"暂停加仓（{ob} 大类超限）",
                              facts=[f"约束互查：{ob} 大类已超限，加仓信号暂停"],
                              priority=self.priority, conflict_type="suppression")
        return FactorVote("constraint_check", current_signal)


class MarketStateRule(FactorRule):
    """市场风险状态覆盖。"""
    name = "market_state"
    priority = 80

    def evaluate(self, position, *, current_signal, current_ratio, market_state=None, **kwargs):
        ms = market_state or {}
        risk = ms.get("risk_appetite", "unknown")
        if risk == "panic" and current_signal == "add":
            return FactorVote("market_state", "hold", 0.0, signal_override="hold",
                              action_text="市场恐慌，暂停加仓",
                              facts=["市场恐慌，暂停加仓"], priority=self.priority)
        if risk in ("risk_off", "panic") and current_signal == "add":
            return FactorVote("market_state", current_signal, 0.5,
                              facts=[f"市场风险状态={risk}，加仓信号降权 50%"],
                              priority=self.priority)
        return FactorVote("market_state", current_signal)


class EventClusterRule(FactorRule):
    """事件聚类覆盖。"""
    name = "event_cluster"
    priority = 75

    def evaluate(self, position, *, current_signal, current_ratio, event_clusters=None, **kwargs):
        clusters = event_clusters or []
        exposure_tags = set((position.get("classification") or {}).get("exposure_tags") or [])
        facts = []
        for c in clusters:
            c_theme = c.get("theme", "")
            c_urgency = c.get("urgency", "medium")
            c_sentiment = c.get("sentiment", "neutral")
            theme_tags = set(THEME_TO_EXPOSURE.get(c_theme, []))
            if not (exposure_tags & theme_tags) and c_theme != "general":
                continue
            if c_urgency == "critical" and c_sentiment == "negative":
                if current_signal == "add":
                    facts.append(f"情报 CRITICAL [{c_theme}] → 暂停加仓")
                    return FactorVote("event_cluster", "hold", 0.0, signal_override="hold",
                                      action_text=f"情报 CRITICAL [{c_theme}] → 暂停加仓",
                                      facts=facts, priority=self.priority)
                elif current_signal in ("reduce", "reduce_risk", "take_profit"):
                    facts.append(f"情报 CRITICAL [{c_theme}] → 确认减仓方向")
            if c_sentiment == "positive":
                if current_signal in ("reduce", "reduce_risk"):
                    facts.append(f"情报利好 [{c_theme}] → 与减仓信号矛盾")
        return FactorVote("event_cluster", current_signal, facts=facts)


class DataFreshnessRule(FactorRule):
    """数据时效性降权。"""
    name = "data_freshness"
    priority = 60

    def evaluate(self, position, *, current_signal, current_ratio, data_freshness="fresh", **kwargs):
        if data_freshness == "stale" and current_signal not in ("hold", "wait"):
            return FactorVote("data_freshness", current_signal, 0.5,
                              facts=["行情延迟（非实时），信号降权 50%"], priority=self.priority)
        if data_freshness in ("very_stale", "unknown") and current_signal not in ("hold", "wait", "stop_loss"):
            return FactorVote("data_freshness", "hold", 0.0, signal_override="hold",
                              action_text="行情严重延迟，仅止损信号保留",
                              facts=["行情严重延迟，仅止损信号保留"], priority=self.priority)
        return FactorVote("data_freshness", current_signal)


class IntelConflictRule(FactorRule):
    """情报信号与技术信号冲突检查。"""
    name = "intel_conflict"
    priority = 70

    def evaluate(self, position, *, current_signal, current_ratio,
                 intelligence_signals=None, **kwargs):
        intel_sigs = intelligence_signals or {}
        inst_key = position.get("instrument_key") or ""
        raw = inst_key.split(":")[-1] if ":" in inst_key else ""
        intel_sym = raw if raw and raw in intel_sigs else None
        if not intel_sym:
            for proxy, target in _INTEL_SIGNAL_PROXY.items():
                if target == raw and proxy in intel_sigs:
                    intel_sym = proxy
                    break
        if not intel_sym:
            return FactorVote("intel_conflict", current_signal)
        intel = intel_sigs.get(intel_sym, {})
        intel_dir = intel.get("direction", "")
        intel_urgency = intel.get("urgency", "medium")
        tech_bullish = current_signal in ("add", "accumulate", "take_profit")
        tech_bearish = current_signal in ("reduce", "stop_loss", "reduce_risk")
        if not ((tech_bullish and intel_dir == "sell") or (tech_bearish and intel_dir == "buy")):
            return FactorVote("intel_conflict", current_signal)
        if intel_urgency == "critical":
            return FactorVote("intel_conflict", "hold", 0.0, signal_override="hold",
                              action_text=f"暂停：技术面与情报面反向（{intel_sym}），等待确认",
                              facts=[f"情报冲突（{intel_sym} {intel_dir}），暂停执行"],
                              priority=self.priority, conflict_type="override")
        return FactorVote("intel_conflict", current_signal, 0.5,
                          facts=[f"情报面 {intel_dir} {intel_sym} — 非 critical，信号降权"],
                          priority=self.priority, conflict_type="caution")


# 因子注册表
ALL_FACTORS: list[FactorRule] = [
    ConstraintCheckRule(),
    MarketStateRule(),
    EventClusterRule(),
    IntelConflictRule(),
    DataFreshnessRule(),
]

FACTOR_BY_NAME = {f.name: f for f in ALL_FACTORS}


def collect_votes(position, *, current_signal, current_ratio, **ctx) -> list[FactorVote]:
    """收集所有因子投票，按优先级排序。"""
    votes = []
    for factor in ALL_FACTORS:
        v = factor.evaluate(position, current_signal=current_signal, current_ratio=current_ratio, **ctx)
        if v.ratio_modifier != 1.0 or v.facts:
            votes.append(v)
    votes.sort(key=lambda v: v.priority, reverse=True)
    return votes


def adjudicate(current_signal, current_action, current_ratio, votes):
    """按优先级裁决。"""
    signal, action, ratio = current_signal, current_action, current_ratio
    facts, conflicts = [], []
    for v in votes:
        if v.signal_override and signal not in ("hold", "wait"):
            signal = v.signal_override
            if v.action_text:
                action = v.action_text
        ratio *= v.ratio_modifier
        facts.extend(v.facts)
        if v.conflict_type != "none":
            conflicts.append(f"{v.factor_name}:{v.conflict_type}")
    return {"signal": signal, "action": action, "ratio": ratio, "facts": facts, "conflicts": conflicts}
