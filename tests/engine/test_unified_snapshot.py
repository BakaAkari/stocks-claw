"""Tests for UnifiedAnalysisSnapshot v1 contract.

These tests verify the new advisory snapshot contract, not the legacy
AnalysisContext. The builder is allowed to be lossy; it must never invent facts
or leak internal reasoning.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from stocks.domain.advisory_models import (
    FactRef,
    UnifiedAnalysisSnapshot,
)
from stocks.domain.models import (
    AnalysisContext,
    Instrument,
    MarketState,
    PortfolioMapping,
    Quote,
)
from stocks.engine.unified_snapshot import build_unified_snapshot


def _minimal_context() -> AnalysisContext:
    """Return the smallest AnalysisContext that satisfies dataclass defaults."""
    return AnalysisContext(
        generated_at=datetime.now(timezone.utc).isoformat(),
        assets=[],
        asset_count=0,
        portfolio_constraints={},
        portfolio_profile={},
        quotes={},
        news=[],
        news_count=0,
        market_state=MarketState(),
        portfolio_mapping=PortfolioMapping(),
        drift_checks=[],
        recent_snapshots=[],
        raw_prompt_input="test",
    )


class TestAdvisoryModels:
    def test_fact_ref_is_immutable(self) -> None:
        fact = FactRef(
            fact_id="f1",
            metric="price",
            value=100.0,
            unit="cny",
            as_of="2026-07-22T00:00:00+00:00",
            source_ref="src-1",
        )
        with pytest.raises(AttributeError):
            fact.value = 200.0  # type: ignore[misc]

    def test_snapshot_requires_id_and_time(self) -> None:
        snapshot = UnifiedAnalysisSnapshot(
            snapshot_id="s1",
            generated_at="2026-07-22T00:00:00+00:00",
            trigger="test",
            session="unit",
            market_scope="cn",
        )
        assert snapshot.snapshot_id == "s1"
        assert snapshot.trigger == "test"
        assert snapshot.session == "unit"


class TestUnifiedSnapshotBuilder:
    def test_builds_snapshot_id_from_context(self) -> None:
        context = _minimal_context()
        snapshot = build_unified_snapshot(context, trigger="scheduled", session="cn_pre_open")
        assert snapshot.snapshot_id
        assert len(snapshot.snapshot_id) == 24
        assert snapshot.session == "cn_pre_open"
        assert snapshot.market_scope == "cn"

    def test_snapshot_includes_generated_at(self) -> None:
        context = _minimal_context()
        snapshot = build_unified_snapshot(context)
        assert snapshot.generated_at
        parsed = datetime.fromisoformat(snapshot.generated_at)
        assert parsed.tzinfo is not None

    def test_empty_context_produces_empty_facts(self) -> None:
        context = _minimal_context()
        snapshot = build_unified_snapshot(context)
        assert snapshot.portfolio == ()
        assert snapshot.quotes == ()
        assert snapshot.macro == ()
        assert snapshot.data_quality == ()
        assert snapshot.source_registry == ()

    def test_position_valuations_become_facts(self) -> None:
        context = _minimal_context()
        context = context.__class__(  # type: ignore[call-arg]
            generated_at=context.generated_at,
            assets=[],
            asset_count=0,
            portfolio_constraints={},
            portfolio_profile={},
            quotes={},
            news=[],
            news_count=0,
            market_state=MarketState(),
            portfolio_mapping=PortfolioMapping(),
            drift_checks=[],
            recent_snapshots=[],
            raw_prompt_input="test",
            position_valuations=[
                {
                    "position_id": "p1",
                    "instrument_key": "a:510300",
                    "market_value_cny": 2956.8,
                    "unrealized_pnl_cny": -120.0,
                    "pnl_pct": -0.04,
                    "quantity": 600.0,
                    "as_of": "2026-07-22T08:35:00+00:00",
                }
            ],
        )
        snapshot = build_unified_snapshot(context)
        facts = snapshot.portfolio
        assert len(facts) >= 3
        metrics = {f.metric for f in facts}
        assert any("market_value_cny" in m for m in metrics)
        assert all(isinstance(f, FactRef) for f in facts)
        assert all(f.source_ref for f in facts)

    def test_quotes_become_facts(self) -> None:
        inst = Instrument(code="510300", name="HS300 ETF", market="a", exchange="SSE", category="ETF")
        quote = Quote(
            instrument=inst,
            price=4.928,
            pct_change=0.015,
            volume_lot=100_000.0,
            as_of="2026-07-22T08:35:00+00:00",
            source="eastmoney",
        )
        context = _minimal_context()
        context = context.__class__(  # type: ignore[call-arg]
            generated_at=context.generated_at,
            assets=[],
            asset_count=0,
            portfolio_constraints={},
            portfolio_profile={},
            quotes={"a": [quote]},
            news=[],
            news_count=0,
            market_state=MarketState(),
            portfolio_mapping=PortfolioMapping(),
            drift_checks=[],
            recent_snapshots=[],
            raw_prompt_input="test",
        )
        snapshot = build_unified_snapshot(context)
        facts = snapshot.quotes
        assert any(f.metric == "quote:a:510300:price" for f in facts)
        price_fact = next(f for f in facts if f.metric == "quote:a:510300:price")
        assert price_fact.value == 4.928
        assert price_fact.unit == "cny"

    def test_data_quality_becomes_explicit_facts(self) -> None:
        context = _minimal_context()
        context = context.__class__(  # type: ignore[call-arg]
            generated_at=context.generated_at,
            assets=[],
            asset_count=0,
            portfolio_constraints={},
            portfolio_profile={},
            quotes={},
            news=[],
            news_count=0,
            market_state=MarketState(),
            portfolio_mapping=PortfolioMapping(),
            drift_checks=[],
            recent_snapshots=[],
            raw_prompt_input="test",
            data_quality={"quotes": {"status": "ok"}, "news": {"status": "stale"}},
        )
        snapshot = build_unified_snapshot(context)
        facts = {f.metric: f.value for f in snapshot.data_quality}
        assert facts == {"data_quality:quotes": "ok", "data_quality:news": "stale"}

    def test_snapshot_excludes_api_keys_and_internal_reasoning(self) -> None:
        context = _minimal_context()
        snapshot = build_unified_snapshot(context)
        dump = repr(snapshot)
        assert "API_KEY" not in dump
        assert "secret" not in dump.lower()
        assert "advisory_reasoning" not in dump.lower()

    def test_missing_values_are_omitted_not_faked(self) -> None:
        context = _minimal_context()
        snapshot = build_unified_snapshot(context)
        # No portfolio facts exist because no valuations were provided.
        assert snapshot.portfolio == ()
        for f in snapshot.all_facts():
            assert f.value is not None
            assert f.value != ""

    def test_source_registry_records_provider_status(self) -> None:
        context = _minimal_context()
        context = context.__class__(  # type: ignore[call-arg]
            generated_at=context.generated_at,
            assets=[],
            asset_count=0,
            portfolio_constraints={},
            portfolio_profile={},
            quotes={},
            news=[],
            news_count=0,
            market_state=MarketState(),
            portfolio_mapping=PortfolioMapping(),
            drift_checks=[],
            recent_snapshots=[],
            raw_prompt_input="test",
            data_quality={
                "news": {"status": "ok", "sources": {"finnhub": {"status": "ok", "as_of": "2026-07-22T08:00:00+00:00"}}},
            },
        )
        snapshot = build_unified_snapshot(context)
        src = snapshot.source_by_id("finnhub:news")
        assert src is not None
        assert src.provider == "finnhub"
        assert src.endpoint_type == "news"
