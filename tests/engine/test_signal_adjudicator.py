"""Tests for the intelligence signal adjudicator (docs v4.1).

R1 provenance / R2 confidence tiers / R3 temporal TTL / R4 dissent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from stocks.engine.news_intelligence_store import IntelligenceSignal
from stocks.engine.signal_adjudicator import (
    adjudicate_signals,
)


def _sig(
    symbol: str,
    *,
    direction: str = "buy",
    confidence: float = 0.75,
    urgency: str = "medium",
    generated_at: datetime | None = None,
    generation_method: str = "llm",
    source_article_ids: list[int] | None = None,
) -> IntelligenceSignal:
    return IntelligenceSignal(
        symbol=symbol,
        name=symbol,
        direction=direction,
        horizon="short_term",
        rationale=f"rationale {symbol}",
        falsification="falsification",
        risk_source="test",
        confidence=confidence,
        urgency=urgency,
        generated_at=generated_at or datetime.now(timezone.utc),
        generation_method=generation_method,
        source_as_of=datetime.now(timezone.utc),
        source_article_ids=source_article_ids if source_article_ids is not None else [0, 1],
    )


NOW = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)


class TestR1Provenance:
    def test_llm_signal_missing_provenance_rejected(self) -> None:
        sig = _sig("GLD", source_article_ids=[])
        res = adjudicate_signals([sig], now=NOW, articles_input=10)
        assert len(res.passed) == 0
        assert len(res.rejected) == 1
        assert res.reject_reasons["GLD"] == "missing_provenance"

    def test_llm_signal_provenance_out_of_range_rejected(self) -> None:
        sig = _sig("GLD", source_article_ids=[99])
        res = adjudicate_signals([sig], now=NOW, articles_input=10)
        assert res.reject_reasons["GLD"] == "provenance_out_of_range"

    def test_rule_fallback_skips_provenance(self) -> None:
        sig = _sig("GLD", generation_method="rule_fallback", source_article_ids=[])
        res = adjudicate_signals([sig], now=NOW)
        assert len(res.passed) == 1  # provenance not required

    def test_category_padding_skips_provenance(self) -> None:
        sig = _sig("a:518880", generation_method="category_padding", source_article_ids=[])
        res = adjudicate_signals([sig], now=NOW)
        assert len(res.passed) == 1


class TestR2Confidence:
    def test_passed_above_threshold(self) -> None:
        res = adjudicate_signals([_sig("GLD", confidence=0.75)], now=NOW)
        assert len(res.passed) == 1

    def test_weak_between_tiers(self) -> None:
        res = adjudicate_signals([_sig("NVDA", confidence=0.65)], now=NOW)
        assert len(res.weak) == 1
        assert len(res.passed) == 0

    def test_below_weak_still_weak_with_note(self) -> None:
        res = adjudicate_signals([_sig("USO", confidence=0.4)], now=NOW)
        assert len(res.weak) == 1
        assert res.reject_reasons["USO"] == "low_confidence"

    def test_exact_boundary_passed(self) -> None:
        res = adjudicate_signals([_sig("GLD", confidence=0.70)], now=NOW)
        assert len(res.passed) == 1

    def test_exact_boundary_weak(self) -> None:
        res = adjudicate_signals([_sig("GLD", confidence=0.55)], now=NOW)
        assert len(res.weak) == 1


class TestR3Temporal:
    def test_expired_signal_rejected(self) -> None:
        old = NOW - timedelta(hours=30)  # medium TTL = 24h
        sig = _sig("GLD", generated_at=old, urgency="medium")
        res = adjudicate_signals([sig], now=NOW)
        assert res.reject_reasons["GLD"] == "expired"

    def test_fresh_signal_passed(self) -> None:
        fresh = NOW - timedelta(hours=1)
        sig = _sig("GLD", generated_at=fresh, urgency="medium")
        res = adjudicate_signals([sig], now=NOW)
        assert len(res.passed) == 1

    def test_critical_short_ttl(self) -> None:
        # critical TTL 6h; 10h old should expire
        old = NOW - timedelta(hours=10)
        sig = _sig("GLD", generated_at=old, urgency="critical")
        res = adjudicate_signals([sig], now=NOW)
        assert res.reject_reasons["GLD"] == "expired"

    def test_valid_until_overrides_ttl(self) -> None:
        from dataclasses import replace
        old = NOW - timedelta(hours=30)
        sig = _sig("GLD", generated_at=old, urgency="medium")
        sig = replace(sig, valid_until=NOW + timedelta(hours=1))
        res = adjudicate_signals([sig], now=NOW)
        assert len(res.passed) == 1

    def test_batch_stale_rejects_all(self) -> None:
        fresh = NOW - timedelta(minutes=1)
        sig = _sig("GLD", generated_at=fresh)
        res = adjudicate_signals([sig], now=NOW, batch_stale=True)
        assert res.reject_reasons["GLD"] == "batch_stale"


class TestR4Dissent:
    def test_opposing_signals_record_dissent(self) -> None:
        buy = _sig("USO", direction="buy", confidence=0.75, generated_at=NOW - timedelta(minutes=1))
        sell = _sig("USO", direction="sell", confidence=0.80, generated_at=NOW - timedelta(minutes=1))
        res = adjudicate_signals([buy, sell], now=NOW)
        assert len(res.passed) == 2
        # majority sell (0.80 > 0.75)
        sell_adj = next(s for s in res.passed if s.direction == "sell")
        assert sell_adj.dissent is not None
        assert sell_adj.dissent["direction"] == "sell"
        assert sell_adj.dissent["weighted_margin"] == pytest.approx(0.05, abs=0.001)
        assert len(sell_adj.dissent["evidence"]) == 1
        assert sell_adj.dissent["evidence"][0]["direction"] == "buy"

    def test_no_dissent_when_same_direction(self) -> None:
        a = _sig("GLD", direction="buy", confidence=0.7, generated_at=NOW - timedelta(minutes=1))
        b = _sig("GLD", direction="buy", confidence=0.8, generated_at=NOW - timedelta(minutes=1))
        adjudicate_signals([a, b], now=NOW)
        assert a.dissent is None
        assert b.dissent is None


class TestSummary:
    def test_summary_counts(self) -> None:
        sigs = [
            _sig("GLD", confidence=0.75, source_article_ids=[]),  # rejected provenance
            _sig("NVDA", confidence=0.65),  # weak
            _sig("USO", confidence=0.80),  # passed
        ]
        res = adjudicate_signals(sigs, now=NOW, articles_input=10)
        s = res.summary()
        assert s["input"] == 3
        assert s["passed"] == 1
        assert s["weak"] == 1
        assert s["rejected"] == 1
        assert s["by_reason"] == {"missing_provenance": 1}

    def test_by_generation_counts(self) -> None:
        sigs = [
            _sig("GLD", confidence=0.75, generation_method="llm"),
            _sig("a:518880", confidence=0.7, generation_method="category_padding"),
        ]
        res = adjudicate_signals(sigs, now=NOW)
        gen = res.by_generation()
        assert gen.get("llm") == 1
        assert gen.get("category_padding") == 1
