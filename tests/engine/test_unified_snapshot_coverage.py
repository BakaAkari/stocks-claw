"""Expanded tests for UnifiedAnalysisSnapshot v1 coverage (A2).
"""
from __future__ import annotations

from stocks.domain.models import (
    AnalysisContext,
    MarketState,
    PortfolioMapping,
    UpcomingEvent,
)
from stocks.engine.unified_snapshot import build_unified_snapshot


def _minimal_context() -> AnalysisContext:
    return AnalysisContext(
        generated_at="2026-07-22T10:00:00+00:00",
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


class TestUnifiedSnapshotCoverage:
    def test_technical_indicators_become_facts(self) -> None:
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
            technical_indicators={"a:510300": {"rsi_14": 28.5, "ma20": 4.9}},
        )
        snapshot = build_unified_snapshot(context)
        metrics = {f.metric for f in snapshot.technical_evidence}
        assert "technical:a:510300:rsi_14" in metrics
        assert "technical:a:510300:ma20" in metrics

    def test_news_digest_becomes_facts(self) -> None:
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
            news_digest={"count": 12, "top_themes": ["macro", "ai"]},
        )
        snapshot = build_unified_snapshot(context)
        metrics = {f.metric for f in snapshot.news_clusters}
        assert "news_digest:count" in metrics

    def test_intelligence_digest_becomes_facts(self) -> None:
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
            intelligence_digest={"alerts": ["fed", "earnings"], "risk_level": "medium"},
        )
        snapshot = build_unified_snapshot(context)
        metrics = {f.metric for f in snapshot.news_clusters}
        assert "intelligence_digest:alerts" in metrics

    def test_rotation_becomes_facts(self) -> None:
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
            rotation={"leading_sector": "tech", "rank": 1.0},
        )
        snapshot = build_unified_snapshot(context)
        metrics = {f.metric for f in snapshot.rotation}
        assert "rotation:leading_sector" in metrics
        assert "rotation:rank" in metrics

    def test_action_signals_become_facts(self) -> None:
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
            action_signals={"a:510300": {"left_bottom": True, "reduce": False}},
        )
        snapshot = build_unified_snapshot(context)
        metrics = {f.metric for f in snapshot.candidate_signals}
        assert "action_signal:a:510300:left_bottom" in metrics

    def test_upcoming_events_become_facts(self) -> None:
        context = _minimal_context()
        event = UpcomingEvent(
            date="2026-07-29",
            name="Fed meeting",
            event_type="macro",
            market="us",
            source="static_config",
        )
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
            upcoming_events=[event],
        )
        snapshot = build_unified_snapshot(context)
        metrics = {f.metric for f in snapshot.upcoming_events}
        assert "upcoming_event:Fed meeting" in metrics

    def test_source_registry_records_macro_provider(self) -> None:
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
                "macro": {"status": "ok", "as_of": "2026-07-22T08:00:00+00:00"},
            },
        )
        snapshot = build_unified_snapshot(context)
        src = snapshot.source_by_id("macro:aggregate")
        assert src is not None
        assert src.status == "ok"

    def test_snapshot_metadata_includes_context_schema(self) -> None:
        context = _minimal_context()
        snapshot = build_unified_snapshot(context)
        assert snapshot.metadata["schema_version"] == 1
        assert snapshot.metadata["context_schema_version"] == 12

    def test_omitted_values_do_not_create_empty_facts(self) -> None:
        context = _minimal_context()
        snapshot = build_unified_snapshot(context)
        for f in snapshot.all_facts():
            assert f.value is not None
            assert f.value != ""
