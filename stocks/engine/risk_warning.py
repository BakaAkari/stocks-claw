"""Three-tier risk warning framework.

Evaluates multi-factor risk signals and produces a risk level (watch/reduce/hedge)
with associated triggers and recommended actions.

All thresholds are configurable via ``risk_warning`` section in engine.yaml.
When ``config`` is None, the function uses the same defaults as before (no
behavioural change for existing callers).
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


# ---------------------------------------------------------------------------
# Hard-coded defaults — keep in sync with DEFAULT_ENGINE_CONFIG["risk_warning"]
# in config_loader.py so that behaviour is identical when no config is passed.
# ---------------------------------------------------------------------------
_DEFAULT_RISK_CONFIG: dict = {
    "vix_hedge": 35,
    "vix_reduce": 25,
    "vix_watch": 20,
    "critical_cluster_trigger": 1,
    "negative_cluster_trigger": 3,
    "geopolitical_action": "hedge",
    "drawdown_hedge_pct": 12,
    "drawdown_reduce_pct": 8,
    "cash_target_hedge": 0.15,
    "cash_target_reduce": 0.10,
    "hedge_actions": ["暂停全部加仓", "评估对冲工具", "检查止损线"],
    "reduce_actions": ["暂停权益加仓", "关注防御板块", "检查高beta暴露"],
    "watch_actions": ["关注风险指标", "不新增高beta仓位"],
}


def assess_risk(
    *,
    vix: Optional[float] = None,
    cluster_urgencies: Optional[list[str]] = None,
    negative_cluster_count: int = 0,
    geopolitical_crisis: bool = False,
    position_drawdown_pct: Optional[float] = None,
    config: Optional[dict] = None,
) -> RiskAssessment:
    """Evaluate multi-factor risk and return an assessment.

    Args:
        config: Optional overrides for risk thresholds, matching the
            ``risk_warning`` section in engine.yaml.  When ``None`` (the
            default) all thresholds fall back to ``_DEFAULT_RISK_CONFIG``
            (backward-compatible behaviour).
    """
    cfg = _DEFAULT_RISK_CONFIG if config is None else {**_DEFAULT_RISK_CONFIG, **config}

    triggers: list = []
    scores: dict[str, int] = {"watch": 0, "reduce": 0, "hedge": 0}

    # --- VIX ---
    if vix is not None:
        if vix > cfg["vix_hedge"]:
            triggers.append(RiskTrigger(
                f"VIX > {cfg['vix_hedge']}", f"VIX={vix:.1f}", "hedge",
            ))
            scores["hedge"] += 1
        elif vix > cfg["vix_reduce"]:
            triggers.append(RiskTrigger(
                f"VIX {cfg['vix_reduce']}-{cfg['vix_hedge']}",
                f"VIX={vix:.1f}", "reduce",
            ))
            scores["reduce"] += 1
        elif vix > cfg["vix_watch"]:
            triggers.append(RiskTrigger(
                f"VIX {cfg['vix_watch']}-{cfg['vix_reduce']} elevated",
                f"VIX={vix:.1f}", "watch",
            ))
            scores["watch"] += 1

    # --- Cluster urgency ---
    if cluster_urgencies:
        critical = sum(1 for u in cluster_urgencies if u == "critical")
        if critical >= cfg["critical_cluster_trigger"]:
            triggers.append(RiskTrigger(
                "Critical cluster", f"{critical} critical", "reduce",
            ))
            scores["reduce"] += 1

    if negative_cluster_count >= cfg["negative_cluster_trigger"]:
        triggers.append(RiskTrigger(
            "Broad negative", f"{negative_cluster_count} clusters", "reduce",
        ))
        scores["reduce"] += 1

    # --- Geopolitical ---
    if geopolitical_crisis:
        geo_action = cfg.get("geopolitical_action", "hedge")
        triggers.append(RiskTrigger(
            "Geopolitical crisis", "geopolitics critical", geo_action,
        ))
        scores[geo_action] += 1

    # --- Drawdown ---
    if position_drawdown_pct is not None:
        if position_drawdown_pct > cfg["drawdown_hedge_pct"]:
            triggers.append(RiskTrigger(
                "Severe drawdown", f"-{position_drawdown_pct:.1f}%", "hedge",
            ))
            scores["hedge"] += 1
        elif position_drawdown_pct > cfg["drawdown_reduce_pct"]:
            triggers.append(RiskTrigger(
                "Drawdown warning", f"-{position_drawdown_pct:.1f}%", "reduce",
            ))
            scores["reduce"] += 1

    # --- Resolve level ---
    if scores["hedge"] > 0:
        level = "hedge"
        actions = list(cfg["hedge_actions"])
        suspend = True
        cash_target = cfg["cash_target_hedge"]
    elif scores["reduce"] > 0:
        level = "reduce"
        actions = list(cfg["reduce_actions"])
        suspend = True
        cash_target = cfg["cash_target_reduce"]
    elif scores["watch"] > 0:
        level = "watch"
        actions = list(cfg["watch_actions"])
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


def assessment_to_observation(
    assessment: RiskAssessment,
    *,
    observed_at,
    evidence_keys: list[str] | tuple[str, ...],
    ttl_minutes: int = 360,
):
    """Convert a stateless assessment into a persistent risk observation."""
    from datetime import timedelta

    from stocks.engine.risk_state import RiskObservation

    return RiskObservation(
        candidate_level=assessment.level,
        evidence_keys=tuple(sorted(set(evidence_keys))),
        observed_at=observed_at,
        expires_at=observed_at + timedelta(minutes=ttl_minutes),
    )
