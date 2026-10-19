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


class TestLLMIntelligenceAnalyzerCredentials:
    """8/11 修复回归: LLM 分析路径的凭证解析。

    此前 _load_api_config 从 /opt/data/.env 读 OPENAI_COMPATIBLE_API_KEY
    (别的服务的 key, 对 deepseek 401) 且 base_url 从不读 env 文件 →
    每次 LLM 调用失败 → 静默回退规则分析 → 信号层常年 0 信号。
    修复后优先级: 传参 > os.environ > .secret/*.md 工作文件。
    """

    def test_loads_credentials_from_secret_files(self, monkeypatch, tmp_path) -> None:
        # 8-12 修正: .secret 工作文件优先于 os.environ — os.environ 的
        # OPENAI_COMPATIBLE_API_KEY 可能是其他服务残留 key(对 deepseek 401)。
        fake_secret = tmp_path / ".secret"
        fake_secret.mkdir()
        (fake_secret / "openai-key.md").write_text("sk-secret-1234567890abcdef", encoding="utf-8")
        (fake_secret / "openai-base-url.md").write_text("https://secret.example.com/v1", encoding="utf-8")
        monkeypatch.setattr("stocks.engine.intelligence_analyzer.__file__",
                            str(tmp_path / "stocks" / "engine" / "intelligence_analyzer.py"))
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        analyzer = LLMIntelligenceAnalyzer(
            holdings=[], fallback_to_rules=False, env_file_path=None,
        )
        api_key, base_url = analyzer._load_api_config()
        assert api_key == "sk-secret-1234567890abcdef"
        assert base_url == "https://secret.example.com/v1"

    def test_credentials_fail_closed_when_unconfigured(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr("stocks.engine.intelligence_analyzer.__file__",
                            str(tmp_path / "stocks" / "engine" / "intelligence_analyzer.py"))
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("STOCKS_LLM__FALLBACK_BASE_URL", raising=False)
        analyzer = LLMIntelligenceAnalyzer(holdings=[], fallback_to_rules=False)
        api_key, base_url = analyzer._load_api_config()
        assert api_key == ""
        assert base_url == ""

    def test_call_llm_rejects_reasoning_without_final_content(self, monkeypatch) -> None:
        """A parseable reasoning trace is not a completed model answer."""
        import json as _json
        import urllib.request

        class _FakeResp:
            def __init__(self, data):
                self._data = data
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def read(self):
                return self._data.encode("utf-8")

        def fake_urlopen(req, timeout):
            payload = _json.loads(req.data.decode("utf-8"))
            assert payload["max_tokens"] >= 24000
            return _FakeResp(_json.dumps({
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": '{"schema_version":1,"clusters":[],"signals":[],"cross_cluster_synthesis_cn":"测试"}',
                    },
                }],
            }, ensure_ascii=False))

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        analyzer = LLMIntelligenceAnalyzer(holdings=[], fallback_to_rules=False)
        with pytest.raises(ValueError, match="final content is empty"):
            analyzer._call_llm([], {}, "sk-test", "https://example.com/v1")

    def test_urgency_and_sentiment(self, sample_snapshot: IntelligenceSnapshot) -> None:
        analyzer = IntelligenceAnalyzer()
        result = analyzer.analyze(
            [sample_snapshot],
            analyzed_at=sample_snapshot.collected_at + timedelta(minutes=1),
        )
        for cluster in result.clusters:
            assert cluster.urgency in {"low", "medium", "high", "critical"}
            assert cluster.sentiment in {"positive", "negative", "neutral"}

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


async def test_harvester_distinguishes_missing_empty_and_failed_sources(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock

    harvester = IntelligenceHarvester(fred_cache_dir=tmp_path, keywords=['test'])
    harvester._gnews_api_key = ''
    harvester._finnhub = None
    monkeypatch.setattr(harvester, '_fetch_gnews_all', AsyncMock(return_value=[]))
    monkeypatch.setattr(harvester, '_fetch_finnhub_market_news', AsyncMock(return_value=[]))
    monkeypatch.setattr(harvester, '_fetch_google_rss_all', AsyncMock(return_value=[]))
    monkeypatch.setattr(harvester, '_fetch_macro', AsyncMock(return_value={}))
    monkeypatch.setattr(harvester, '_fetch_quotes', AsyncMock(return_value={}))
    result = await harvester.harvest()
    assert result.source_status['gnews']['status'] == 'not_configured'
    assert result.source_status['finnhub_market']['status'] == 'not_configured'
    assert result.source_status['google_rss']['status'] == 'empty'
    monkeypatch.setattr(harvester, '_fetch_google_rss_all', AsyncMock(side_effect=RuntimeError('failed')))
    result = await harvester.harvest()
    assert result.source_status['google_rss']['status'] == 'error'


async def test_google_news_errors_survive_rss_empty_fallback(monkeypatch, tmp_path):
    from stocks.providers.rss_news import RSSNewsProvider

    def fail(provider):
        provider.last_errors = {'transport': 'HTTPError:503'}
        return []

    monkeypatch.setattr(RSSNewsProvider, '_fetch_sync', fail)
    harvester = IntelligenceHarvester(fred_cache_dir=tmp_path, keywords=['test'])
    assert await harvester._fetch_google_rss_all() == []
    assert harvester.last_source_errors['google_rss'] == ['RuntimeError']


def test_llm_news_evidence_filters_old_duplicates_and_dates_signals(monkeypatch, sample_snapshot):
    from dataclasses import replace

    old = dict(sample_snapshot.articles[0], url='https://example.com/old', published_at='2020-01-01T00:00:00Z')
    snapshot = replace(sample_snapshot, articles=sample_snapshot.articles + [sample_snapshot.articles[0], old])
    analyzer = LLMIntelligenceAnalyzer(fallback_to_rules=False)
    monkeypatch.setattr(analyzer, '_load_api_config', lambda: ('test', 'https://example.com'))
    def respond(articles, *args):
        assert len(articles) == 2
        return {'clusters': [{'article_ids': [0], 'sentiment': {'gold': 'bullish'}}],
                'signals': [{'symbol': 'GLD', 'source_article_ids': [0], 'direction': 'buy'}]}
    monkeypatch.setattr(analyzer, '_call_llm', respond)
    result = analyzer.analyze([snapshot])
    assert result.metadata['excluded_articles'] == 2
    assert result.clusters[0].affected_markets == ['gold']
    assert result.signals[0].source_as_of == datetime.fromisoformat(sample_snapshot.articles[0]['published_at'])
    with pytest.raises(ValueError, match='source article IDs'):
        analyzer._parse_clusters({'clusters': [{'article_ids': [99]}]}, snapshot.articles)
    with pytest.raises(ValueError, match='source article IDs'):
        analyzer._parse_signals({'signals': [{'source_article_ids': [99]}]}, articles=snapshot.articles)
