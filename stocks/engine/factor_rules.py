"""
Factor Rules — 决策因子接口与规则库

把 finalize_decision 中的覆盖规则提取为独立、可测试、可回测的因子。
每个因子实现 evaluate() → FactorVote，finalize_decision 收集所有投票后裁决。

## 因子化边界（当前策略）

**走因子管道的规则（finalize_decision 步骤 2-7，已迁移）：**
- ConstraintCheckRule: 组合超限互查
- MarketStateRule: 市场状态影响
- EventClusterRule: 事件聚类
- IntelConflictRule: 情报面冲突
- DataFreshnessRule: 数据新鲜度

**不走因子管道，留在 finalize_decision 内的规则：**
- 产品类型路由（full/fund/precious/info_only/skip）：静态路由表与产品合规约束
- 非 rebalance 信号降权：临时/止损信号的 ratio 调整属于信号后处理
- fund/precious 路由阈值与操作限制：场外基金和贵金属的特殊处理属于产品合规

**未来候选迁移的规则：**
- 数据新鲜度在 fund/precious 路由中的特殊降权
- ratio 归一化逻辑

新增因子规则应优先添加到此处，而非直接写入 finalize_decision。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


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
        from stocks.engine.quant_action import _TAG_TO_BUCKET
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
                              priority=self.priority, conflict_type="over_limit_suppressed")
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
        from stocks.engine.quant_action import THEME_TO_EXPOSURE
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
    """数据时效性处理（与呈现层 fail-closed 语义统一，adversarial review P1-9）。

    统一后的语义：数据不可用（stale/very_stale/unknown/missing）= 阻断非
    止损信号——与 presentation._market_quote_stale 的完全阻断一致；数据
    可用但非实时（previous_close）= 需要盘中精度的信号降权 50%。旧实现
    对 stale 只降权 50%，与呈现层的全阻断形成双重语义。
    """
    name = "data_freshness"
    priority = 60

    def evaluate(self, position, *, current_signal, current_ratio, data_freshness="fresh", **kwargs):
        if data_freshness in ("stale", "very_stale", "unknown") and current_signal not in ("hold", "wait", "stop_loss"):
            return FactorVote("data_freshness", "hold", 0.0, signal_override="hold",
                              action_text="行情延迟或严重延迟，仅止损信号保留",
                              facts=["行情延迟（非实时或不可用），仅止损信号保留"], priority=self.priority)
        if data_freshness == "missing" and current_signal not in ("hold", "wait", "stop_loss"):
            return FactorVote("data_freshness", "hold", 0.0, signal_override="hold",
                              action_text="行情缺失，仅止损信号保留",
                              facts=["行情缺失，仅止损信号保留"], priority=self.priority)
        if data_freshness == "previous_close" and current_signal in ("reduce", "add", "accumulate"):
            return FactorVote("data_freshness", current_signal, 0.5,
                              facts=["非实时行情（前收盘），需要盘中精度的信号降权 50%"],
                              priority=self.priority)
        return FactorVote("data_freshness", current_signal)


class IntelConflictRule(FactorRule):
    """情报信号与技术信号冲突检查。"""
    name = "intel_conflict"
    priority = 70

    def evaluate(self, position, *, current_signal, current_ratio,
                 intelligence_signals=None, **kwargs):
        from stocks.engine.intelligence_analyzer import (
            _intel_consensus_direction_from_matched,
            coerce_intelligence_signals,
            match_intelligence,
        )

        intel_sigs_raw = intelligence_signals or {}
        parsed_signals = coerce_intelligence_signals(intel_sigs_raw)
        matched = match_intelligence(position, parsed_signals)
        # Filter out category_padding for conflict detection
        significant = [m for m in matched
                       if m.generation_method != "category_padding"]
        if not significant:
            return FactorVote("intel_conflict", current_signal)

        # Check for directional conflict between intelligence and tech signal
        intel_dir = _intel_consensus_direction_from_matched(significant)
        tech_bullish = current_signal in ("add", "accumulate", "take_profit")
        tech_bearish = current_signal in ("reduce", "stop_loss", "reduce_risk")
        is_conflict = (tech_bullish and intel_dir == "bearish") or (tech_bearish and intel_dir == "bullish")
        if not is_conflict:
            return FactorVote("intel_conflict", current_signal)

        # Check urgency for severity
        max_urgency = "low"
        urgency_order = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        for item in significant:
            if urgency_order.get(item.urgency, 0) > urgency_order.get(max_urgency, 0):
                max_urgency = item.urgency

        matched_symbols = [m.matched_symbol for m in significant[:2]]
        sym_str = ", ".join(matched_symbols)

        if current_signal == "stop_loss":
            # 硬止损不可被情报冲突覆盖（P0-2）
            return FactorVote(self.name, current_signal, 1.0,
                              facts=["硬止损信号优先级最高，忽略情报冲突"],
                              priority=self.priority)

        if max_urgency == "critical":
            return FactorVote("intel_conflict", "hold", 0.0, signal_override="hold",
                              action_text=f"暂停：技术面与情报面反向（{sym_str}），等待确认",
                              facts=[f"情报冲突（{sym_str} {intel_dir}），暂停执行"],
                              priority=self.priority, conflict_type="override")
        return FactorVote("intel_conflict", current_signal, 0.5,
                          facts=[f"情报面 {intel_dir} {sym_str} — 非 critical，信号降权"],
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
    # P0-1 fix: quant_action 用负 ratio 表示加仓（add_size），钳制前必须先归一化。
    # 下游 adjudicator 消费 abs(action.ratio)，不依赖负号语义。
    if current_ratio < 0:
        current_ratio = abs(current_ratio)
    signal, action, ratio = current_signal, current_action, current_ratio
    facts, conflicts = [], []
    for v in votes:
        if v.signal_override and signal not in ("hold", "wait", "stop_loss"):
            signal = v.signal_override
            if v.action_text:
                action = v.action_text
        ratio *= v.ratio_modifier
        facts.extend(v.facts)
        if v.conflict_type != "none":
            conflicts.append(f"{v.factor_name}:{v.conflict_type}")
    # ── 后处理：ratio 边界保护 ──
    ratio = max(0.0, min(1.0, ratio))
    if ratio <= 0 and signal in ("add", "accumulate"):
        signal, action = "hold", "加仓信号被因子管道压制（ratio→0），暂不操作"
    return {"signal": signal, "action": action, "ratio": ratio, "facts": facts, "conflicts": conflicts}
