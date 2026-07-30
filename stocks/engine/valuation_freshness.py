"""Neutral valuation-freshness semantics shared by decision and presentation layers.

The decision layer (portfolio_adjudicator.py) must never import the
presentation layer, and presentation.py must not become an implicit dependency
of decision logic. Both import this module instead of one importing the
other.
"""
from __future__ import annotations

_ESTIMATE_FRESHNESS = frozenset({"previous_close", "stale", "old", "unknown", "missing", "no_data"})
_ESTIMATE_VALUATION_METHODS = frozenset({"manual_amount", "fund_nav", "insurance_value"})


def freshness_is_estimate(evidence: dict, valuation_method: str) -> bool:
    if str(valuation_method or "") in _ESTIMATE_VALUATION_METHODS:
        return True
    freshness = str((evidence or {}).get("price_freshness") or "unknown")
    return freshness in _ESTIMATE_FRESHNESS
