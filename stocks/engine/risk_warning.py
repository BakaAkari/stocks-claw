"""Three-tier risk warning framework.

Evaluates multi-factor risk signals and produces a risk level (watch/reduce/hedge)
with associated triggers and recommended actions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


class RiskLevel:
    WATCH = "watch"
    REDUCE = "reduce"
    HEDGE = "hedge"


@dataclass
class RiskTrigger:
    condition: str
    value: str
    severity: str


@dataclass
class RiskAssessment:
    level: str
    triggers: list = field(default_factory=list)
    recommended_actions: list = field(default_factory=list)
    suspend_accumulation: bool = False
    cash_target_pct: Optional[float] = None


def assess_risk(
    *,
    vix: Optional[float] = None,
    cluster_urgencies: Optional[list[str]] = None,
    negative_cluster_count: int = 0,
    geopolitical_crisis: bool = False,
    position_drawdown_pct: Optional[float] = None,
) -> RiskAssessment:
    """Evaluate multi-factor risk and return an assessment."""
    triggers: list = []
    scores: dict[str, int] = {"watch": 0, "reduce": 0, "hedge": 0}

    if vix is not None:
        if vix > 35:
            triggers.append(RiskTrigger("VIX > 35", f"VIX={vix:.1f}", "hedge"))
            scores["hedge"] += 1
        elif vix > 25:
            triggers.append(RiskTrigger("VIX 25-35", f"VIX={vix:.1f}", "reduce"))
            scores["reduce"] += 1
        elif vix > 20:
            triggers.append(RiskTrigger("VIX 20-25 elevated", f"VIX={vix:.1f}", "watch"))
            scores["watch"] += 1

    if cluster_urgencies:
        critical = sum(1 for u in cluster_urgencies if u == "critical")
        if critical >= 1:
            triggers.append(RiskTrigger("Critical cluster", f"{critical} critical", "reduce"))
            scores["reduce"] += 1

    if negative_cluster_count >= 3:
        triggers.append(RiskTrigger("Broad negative", f"{negative_cluster_count} clusters", "reduce"))
        scores["reduce"] += 1

    if geopolitical_crisis:
        triggers.append(RiskTrigger("Geopolitical crisis", "geopolitics critical", "hedge"))
        scores["hedge"] += 1

    if position_drawdown_pct is not None:
        if position_drawdown_pct > 12:
            triggers.append(RiskTrigger("Severe drawdown", f"-{position_drawdown_pct:.1f}%", "hedge"))
            scores["hedge"] += 1
        elif position_drawdown_pct > 8:
            triggers.append(RiskTrigger("Drawdown warning", f"-{position_drawdown_pct:.1f}%", "reduce"))
            scores["reduce"] += 1

    if scores["hedge"] > 0:
        level = "hedge"
        actions = ["暂停全部加仓", "评估对冲工具", "检查止损线"]
        suspend = True
        cash_target = 0.15
    elif scores["reduce"] > 0:
        level = "reduce"
        actions = ["暂停权益加仓", "关注防御板块", "检查高beta暴露"]
        suspend = True
        cash_target = 0.10
    elif scores["watch"] > 0:
        level = "watch"
        actions = ["关注风险指标", "不新增高beta仓位"]
        suspend = False
        cash_target = None
    else:
        return RiskAssessment(level="normal")

    return RiskAssessment(
        level=level, triggers=triggers,
        recommended_actions=actions,
        suspend_accumulation=suspend,
        cash_target_pct=cash_target,
    )
