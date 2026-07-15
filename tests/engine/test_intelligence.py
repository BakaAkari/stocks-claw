"""Tests for global intelligence components."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stocks.engine.intelligence_analyzer import (
    AnalysisResult,
    IntelligenceAnalyzer,
    LLMIntelligenceAnalyzer,
)
from stocks.engine.intelligence_harvester import (
    HarvestResult,
    IntelligenceHarvester,
)
from stocks.engine.news_intelligence_store import (
    EventCluster,
    IntelligenceSignal,
    IntelligenceSnapshot,
    NewsIntelligenceStore,
)


@pytest.fixture
def tmp_store(tmp_path: Path) -> NewsIntelligenceStore:
    return NewsIntelligenceStore(tmp_path)


@pytest.fixture
def sample_snapshot() -> IntelligenceSnapshot:
    now = datetime(2026, 7, 9, 10, 0, 0, tzinfo=timezone.utc)
    return IntelligenceSnapshot(
        collected_at=now,
        sources={"gnews": {"status": "ok", "count": 3}},
        articles=[
            {
                "title": "VIX surge as fear grips markets",
                "url": "https://example.com/1",
                "source_name": "GNews",
                "source_type": "gnews",
                "published_at": (now - timedelta(minutes=5)).isoformat(),
                "summary": "Volatility index jumps on recession worry.",
                "language": "en",
                "tags": ["VIX volatility"],
                "scope": "general",
            },
            {
                "title": "Oil prices climb on supply concern",
                "url": "https://example.com/2",
                "source_name": "GNews",
                "source_type": "gnews",
                "published_at": (now - timedelta(minutes=10)).isoformat(),
                "summary": "Crude oil up 2%.",
                "language": "en",
                "tags": ["crude oil price"],
                "scope": "general",
            },
        ],
        macro={
            "vix": 28.0,
            "us_10y_yield": 4.3,
            "usd_cny": 7.2,
            "crude_oil": 85.0,
            "gold": 2300.0,
            "field_sources": {"vix": {"source": "fred", "as_of": "2026-07-09"}},
        },
        quotes={
            "SPY": {"price": 540.0, "pct_change": -1.2},
            "USO": {"price": 78.0, "pct_change": 2.3},
            "GLD": {"price": 220.0, "pct_change": 0.5},
        },
        data_quality={"status": "ok", "errors": []},
    )


class TestNewsIntelligenceStore:
    def test_save_and_load_snapshot(
        self, tmp_store: NewsIntelligenceStore, sample_snapshot: IntelligenceSnapshot
    ) -> None:
        path = tmp_store.save_snapshot(sample_snapshot)
        assert path.exists()
        loaded = tmp_store.latest_snapshot()
        assert loaded is not None
        assert loaded.collected_at == sample_snapshot.collected_at
        assert len(loaded.articles) == 2

    def test_save_and_load_clusters(self, tmp_store: NewsIntelligenceStore) -> None:
        now = datetime.now(timezone.utc)
        cluster = EventCluster(
            cluster_id="geo_0001",
            theme="geopolitics",
            event_type="geopolitics",
            summary="Tensions rising",
            articles=[],
            affected_markets=["equity", "oil"],
            affected_symbols=["XLE", "USO"],
            sentiment="negative",
            urgency="high",
            confidence=0.7,
            formed_at=now,
        )
        path = tmp_store.save_clusters([cluster], formed_at=now)
        assert path.exists()
        loaded = tmp_store.latest_clusters()
        assert loaded is not None
        assert loaded["clusters"][0]["theme"] == "geopolitics"

    def test_save_and_load_signals(self, tmp_store: NewsIntelligenceStore) -> None:
        now = datetime.now(timezone.utc)
        signal = IntelligenceSignal(
            symbol="VIX",
            name="VIX volatility index",
            direction="sell",
            horizon="short_term",
            rationale="Fear elevated",
            falsification="VIX below 20",
            risk_source="VIX can spike further",
            confidence=0.8,
            urgency="high",
            generated_at=now,
        )
        path = tmp_store.save_signals([signal], generated_at=now)
        assert path.exists()
        loaded = tmp_store.latest_signals()
        assert loaded is not None
        assert loaded["signals"][0]["symbol"] == "VIX"

    def test_archive_and_purge(
        self, tmp_store: NewsIntelligenceStore, sample_snapshot: IntelligenceSnapshot
    ) -> None:
        # Create snapshot with mtime in the past
        path = tmp_store.save_snapshot(sample_snapshot)
        past = sample_snapshot.collected_at - timedelta(days=10)
        import os

        os.utime(path, (past.timestamp(), past.timestamp()))
        result = tmp_store.archive_and_purge(now=sample_snapshot.collected_at + timedelta(days=1))
        assert result["archived"] == 1
        assert not path.exists()
        archive_path = tmp_store.archive_dir / path.relative_to(tmp_store.root).with_suffix(
            ".json.gz"
        )
        assert archive_path.exists()


class TestIntelligenceAnalyzer:
    def test_analyze_empty(self) -> None:
        analyzer = IntelligenceAnalyzer()
        result = analyzer.analyze([])
        assert isinstance(result, AnalysisResult)
        assert result.clusters == []
        assert result.signals == []
        assert result.data_quality["status"] == "degraded"

    def test_analyze_clusters(self, sample_snapshot: IntelligenceSnapshot) -> None:
        analyzer = IntelligenceAnalyzer()
        result = analyzer.analyze(
            [sample_snapshot],
            analyzed_at=sample_snapshot.collected_at + timedelta(minutes=1),
        )
        assert len(result.clusters) > 0
        themes = {c.theme for c in result.clusters}
        assert "geopolitics" in themes or "macro_data" in themes or "energy" in themes
        assert result.market_impact["equity"]["direction"] in {"negative", "neutral", "positive"}

    def test_vix_signal(self, sample_snapshot: IntelligenceSnapshot) -> None:
        analyzer = IntelligenceAnalyzer()
        result = analyzer.analyze(
            [sample_snapshot],
            analyzed_at=sample_snapshot.collected_at + timedelta(minutes=1),
        )
        vix_signals = [s for s in result.signals if s.symbol == "VIX"]
        assert len(vix_signals) > 0
        assert vix_signals[0].direction == "sell"

    def test_urgency_and_sentiment(self, sample_snapshot: IntelligenceSnapshot) -> None:
        analyzer = IntelligenceAnalyzer()
        result = analyzer.analyze(
            [sample_snapshot],
            analyzed_at=sample_snapshot.collected_at + timedelta(minutes=1),
        )
        for cluster in result.clusters:
            assert cluster.urgency in {"low", "medium", "high", "critical"}
            assert cluster.sentiment in {"positive", "negative", "neutral"}

    def test_category_padding_covers_every_configured_holding(self) -> None:
        analyzer = LLMIntelligenceAnalyzer()
        direct_signal = IntelligenceSignal(
            symbol="NVDA",
            name="NVIDIA",
            direction="buy",
            horizon="short_term",
            rationale="AI demand remains strong",
            falsification="Demand weakens",
            risk_source="llm_analysis",
            confidence=0.8,
            urgency="medium",
            generated_at=datetime.now(timezone.utc),
        )
        padded = analyzer._pad_category_signals([direct_signal], [])
        by_symbol = {signal.symbol: signal for signal in padded}
        expected_positions = {
            "us:NEM", "a:518880", "ccb_gold", "us:NVDA", "us:QQQ",
            "us:XLE", "us:ITA", "a:510300", "a:512890", "a:511880",
            "a:588000", "a:512480", "a:561560", "alipay_gf_nasdaq",
            "alipay_dc_nasdaq", "alipay_info", "us:SGOV", "a:159110",
        }
        covered_positions = set(by_symbol) | {"us:NVDA"}
        assert expected_positions <= covered_positions
        assert by_symbol["NVDA"] is direct_signal
        assert all(
            signal.direction == "hold"
            for symbol, signal in by_symbol.items()
            if symbol != "NVDA"
        )


class TestIntelligenceHarvester:
    @pytest.mark.asyncio
    async def test_harvest_without_keys(self, tmp_path: Path) -> None:
        # No API keys -> should gracefully degrade to RSS and return empty dicts, no exceptions.
        harvester = IntelligenceHarvester(max_items_per_source=2, fred_cache_dir=tmp_path)
        result = await harvester.harvest()
        assert isinstance(result, HarvestResult)
        assert result.collected_at.tzinfo is not None
        assert result.data_quality["status"] in {"ok", "degraded"}
        assert result.source_status is not None

    def test_data_quality_flags_errors(self) -> None:
        harvester = IntelligenceHarvester(max_items_per_source=2)
        dq = harvester._build_data_quality(
            source_status={"gnews": {"status": "error", "count": 0}},
            macro={},
            quotes={},
            article_count=0,
        )
        assert dq["status"] == "degraded"
        assert len(dq["errors"]) > 0


class TestIntegration:
    def test_store_analyzer_round_trip(
        self, tmp_store: NewsIntelligenceStore, sample_snapshot: IntelligenceSnapshot
    ) -> None:
        tmp_store.save_snapshot(sample_snapshot)
        snapshots = tmp_store.load_snapshots(tmp_store.list_snapshots())
        analyzer = IntelligenceAnalyzer()
        result = analyzer.analyze(
            snapshots,
            analyzed_at=sample_snapshot.collected_at + timedelta(minutes=1),
        )
        tmp_store.save_clusters(result.clusters, formed_at=sample_snapshot.collected_at)
        tmp_store.save_signals(result.signals, generated_at=sample_snapshot.collected_at)
        loaded_clusters = tmp_store.latest_clusters()
        loaded_signals = tmp_store.latest_signals()
        assert loaded_clusters is not None and loaded_clusters["clusters"]
        assert loaded_signals is not None and loaded_signals["signals"]
