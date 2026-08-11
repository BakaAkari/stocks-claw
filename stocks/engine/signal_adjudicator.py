"""Intelligence signal adjudicator — deterministic quality gate.

Design: docs/tasks/intelligence-signal-adjudicator-design-2026-08-12.md (v4.1)
Implemented: 2026-08-12.

Principle: LLM produces candidates, the rule layer decides what enters the
data pipeline. The adjudicator is a pure function: it takes raw signals plus
a `now` timestamp and returns an AdjudicationResult. No network, no storage.

Four rules:
  R1 provenance — only generation_method=="llm" signals must carry
      source_article_ids (non-empty, in range [0, articles_input)).
      rule_fallback / category_padding skip provenance (their provenance
      IS the rule).
  R2 confidence — three tiers, no hard reject (LLM output is unstable):
      >= conf_passed  -> passed (drives action driver)
      >= conf_weak    -> weak (LLM-facing display only, weak=True)
      <  conf_weak    -> weak + note(low_confidence), still preserved
  R3 temporal — per-urgency TTL, evaluated at consumption time:
      critical 6h / high 12h / medium 24h / low 72h (configurable).
      expired -> rejected(reason=expired). Half-life confidence decay.
  R4 dissent — same symbol with opposing passed signals -> confidence
      weighted consensus + structured dissent evidence. No forced
      disambiguation.

Batch-level (G2/G3): `batch_stale` is decided upstream (risk_eligible),
the adjudicator tags each signal with the batch status so both consumption
paths (deterministic + LLM-facing) stay consistent.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Optional

from stocks.engine.news_intelligence_store import IntelligenceSignal

# TTL in hours per urgency (R3). Config overridable via config_loader:
# llm.intel_signal_ttl.
DEFAULT_TTL_HOURS: dict[str, float] = {
    "critical": 6.0,
    "high": 12.0,
    "medium": 24.0,
    "low": 72.0,
}

# Confidence tiers (R2). Config overridable via config_loader:
# llm.intel_signal_confidence = {"passed": 0.70, "weak": 0.55}
DEFAULT_CONFIDENCE: dict[str, float] = {
    "passed": 0.70,
    "weak": 0.55,
}

# Signal sources that bypass provenance (R1).
PROVENANCE_OPTIONAL_METHODS = frozenset({"rule_fallback", "category_padding"})


@dataclass
class AdjudicationResult:
    """Outcome of adjudicating one batch of signals."""

    passed: list[IntelligenceSignal] = field(default_factory=list)
    weak: list[IntelligenceSignal] = field(default_factory=list)
    rejected: list[IntelligenceSignal] = field(default_factory=list)
    # rejected reasons keyed by signal symbol for data_quality_notes
    reject_reasons: dict[str, str] = field(default_factory=dict)
    # batch-level staleness propagated from risk_eligible
    batch_stale: bool = False

    def summary(self) -> dict:
        """Compact adjudication summary for AnalysisResult.metadata."""
        reasons: dict[str, int] = {}
        for r in self.reject_reasons.values():
            reasons[r] = reasons.get(r, 0) + 1
        return {
            "input": len(self.passed) + len(self.weak) + len(self.rejected),
            "passed": len(self.passed),
            "weak": len(self.weak),
            "rejected": len(self.rejected),
            "by_reason": reasons,
            "batch_stale": self.batch_stale,
        }

    def by_generation(self) -> dict[str, int]:
        """Per-source counts (F1: no padding fake baseline)."""
        counts: dict[str, int] = {}
        for sig in [*self.passed, *self.weak, *self.rejected]:
            m = sig.generation_method or "unknown"
            counts[m] = counts.get(m, 0) + 1
        return counts


def _ttl_for_urgency(urgency: str, ttl_hours: dict[str, float]) -> float:
    return ttl_hours.get(urgency, ttl_hours.get("medium", 24.0))


def adjudicate_signals(
    signals: list[IntelligenceSignal],
    *,
    now: datetime,
    articles_input: int = 0,
    confidence: Optional[dict[str, float]] = None,
    ttl_hours: Optional[dict[str, float]] = None,
    batch_stale: bool = False,
) -> AdjudicationResult:
    """Adjudicate raw signals at consumption time.

    Pure function — no I/O. `now` injected for testability.
    `articles_input` is the number of articles the LLM saw; provenance
    ids must fall in [0, articles_input).
    """
    conf = dict(DEFAULT_CONFIDENCE)
    if confidence:
        conf.update(confidence)
    ttl = dict(DEFAULT_TTL_HOURS)
    if ttl_hours:
        ttl.update(ttl_hours)

    result = AdjudicationResult(batch_stale=batch_stale)
    now = now.astimezone(timezone.utc)

    # R1 provenance — skip optional sources
    prov_ok: dict[str, str] = {}
    for sig in signals:
        method = sig.generation_method or "unknown"
        if method in PROVENANCE_OPTIONAL_METHODS:
            prov_ok[sig.symbol] = ""
            continue
        ids = sig.source_article_ids or []
        if not ids:
            prov_ok[sig.symbol] = "missing_provenance"
            continue
        if articles_input and any(i < 0 or i >= articles_input for i in ids):
            prov_ok[sig.symbol] = "provenance_out_of_range"
            continue
        prov_ok[sig.symbol] = ""

    # R3 temporal + R2 tiers — evaluated at consumption time.
    # IntelligenceSignal is frozen: emit replaced copies carrying the
    # adjudication verdict instead of mutating inputs.
    emitted: list[tuple[str, IntelligenceSignal]] = []  # (stage, sig)
    for sig in signals:
        method = sig.generation_method or "unknown"
        if prov_ok.get(sig.symbol):
            _reject(result, sig, prov_ok[sig.symbol])
            continue
        if batch_stale:
            _reject(result, sig, "batch_stale")
            continue
        ttl_h = _ttl_for_urgency(sig.urgency, ttl)
        generated = sig.generated_at
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        valid_until = sig.valid_until or (
            generated + timedelta(hours=ttl_h)
        )
        if valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=timezone.utc)
        if valid_until < now:
            _reject(result, sig, "expired")
            continue

        # R2 confidence tiers — no hard reject
        c = sig.confidence or 0.0
        if c >= conf["passed"]:
            emitted.append(("passed", replace(sig, adjudication="passed")))
        elif c >= conf["weak"]:
            emitted.append(("weak", replace(sig, adjudication="weak")))
        else:
            emitted.append(("weak", replace(sig, adjudication="weak", reject_reason="low_confidence")))
            result.reject_reasons.setdefault(sig.symbol, "low_confidence")

    passed_sigs = [s for stage, s in emitted if stage == "passed"]
    weak_sigs = [s for stage, s in emitted if stage == "weak"]

    # R4 dissent aggregation — only over passed signals
    passed_with_dissent = _aggregate_dissent(passed_sigs)

    result.passed = passed_with_dissent
    result.weak = weak_sigs
    return result


def _reject(result: AdjudicationResult, sig: IntelligenceSignal, reason: str) -> None:
    rejected = replace(sig, adjudication="rejected", reject_reason=reason)
    result.rejected.append(rejected)
    result.reject_reasons.setdefault(sig.symbol, reason)


def _aggregate_dissent(passed: list[IntelligenceSignal]) -> list[IntelligenceSignal]:
    """R4 — group passed signals by symbol, record opposing evidence.

    Returns new signal list with dissent attached (frozen dataclass:
    dissent via replace). Does not force disambiguation: consensus by
    confidence-weighted vote, dissent evidence attached. Consumers (e.g.
    _build_drivers) may surface the dissent in reasons.
    """
    by_symbol: dict[str, list[IntelligenceSignal]] = {}
    for sig in passed:
        by_symbol.setdefault(sig.symbol, []).append(sig)

    out: list[IntelligenceSignal] = []
    for symbol, group in by_symbol.items():
        if len(group) < 2:
            out.extend(group)
            continue
        bullish = {"buy", "bullish", "positive"}
        bearish = {"sell", "bearish", "negative", "reduce"}
        bull_weight = sum(
            s.confidence for s in group if s.direction.lower() in bullish
        )
        bear_weight = sum(
            s.confidence for s in group if s.direction.lower() in bearish
        )
        if not (bull_weight and bear_weight):
            out.extend(group)
            continue
        majority_dir = "buy" if bull_weight >= bear_weight else "sell"
        margin = abs(bull_weight - bear_weight)
        minority = [
            {
                "direction": s.direction,
                "confidence": s.confidence,
                "rationale": (s.rationale or "")[:120],
            }
            for s in group
            if (s.direction.lower() in bullish and majority_dir == "sell")
            or (s.direction.lower() in bearish and majority_dir == "buy")
        ]
        for sig in group:
            sig_is_majority = (
                (sig.direction.lower() in bullish and majority_dir == "buy")
                or (sig.direction.lower() in bearish and majority_dir == "sell")
            )
            if sig_is_majority:
                out.append(replace(sig, dissent={
                    "direction": majority_dir,
                    "evidence": minority,
                    "weighted_margin": round(margin, 3),
                }))
            else:
                out.append(sig)
    return out
