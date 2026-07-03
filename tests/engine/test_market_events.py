"""MarketEventExtractor 测试 — 新闻到结构化市场事件。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from stocks.domain.models import FinancialAsset, Instrument, NewsItem
from stocks.engine.market_events import MarketEventExtractor


def test_extract_macro_and_theme_event_with_holding_match():
    extractor = MarketEventExtractor()
    generated_at = datetime.now(timezone.utc).isoformat()
    news = [
        NewsItem(
            title="美联储暗示降息，纳指盘前上涨，AI芯片股走强",
            url="https://example.com/fed-ai",
            source_name="test",
            source_type="rss",
            published_at=datetime.now(timezone.utc) - timedelta(minutes=30),
            summary="英伟达和高通受益，市场风险偏好回升",
            language="zh",
        )
    ]
    assets = [
        FinancialAsset(
            name="QCOM高通",
            platform="IBKR",
            amount=1000,
            asset_type="股票",
            notes="QCOM, 高通",
        )
    ]
    instruments = [Instrument(code="QCOM", name="高通", market="us")]

    events, digest = extractor.extract(
        news,
        assets=assets,
        instruments=instruments,
        generated_at=generated_at,
    )

    assert len(events) == 1
    event = events[0]
    assert event.event_type == "monetary_policy"
    assert "AI" in event.themes
    assert "半导体" in event.themes
    assert "us" in event.affected_markets
    assert "us:QCOM" in event.affected_symbols
    assert event.matched_holdings == ["QCOM高通"]
    assert event.sentiment == "positive"
    assert event.urgency == "immediate"
    assert event.confidence >= 0.7

    assert digest["schema_version"] == 1
    assert digest["event_count"] == 1
    assert digest["matched_holdings"] == ["QCOM高通"]
    assert digest["affected_markets"]["us"] == 1


def test_empty_news_returns_empty_digest():
    extractor = MarketEventExtractor()
    events, digest = extractor.extract(
        [],
        assets=[],
        instruments=[],
        generated_at=datetime.now(timezone.utc).isoformat(),
    )

    assert events == []
    assert digest["event_count"] == 0
    assert digest["top_events"] == []


def test_holding_scope_uses_source_metadata_before_keyword_matching():
    item = NewsItem(
        title="Quarterly update",
        url="https://example.com/qcom",
        source_name="Google News 持仓:QCOM",
        source_type="rss",
        published_at=datetime.now(timezone.utc),
        summary=None,
        scope="holding",
        raw_metadata={"market": "us", "symbol": "QCOM"},
    )
    events, _ = MarketEventExtractor().extract(
        [item],
        assets=[],
        instruments=[Instrument("QCOM", "高通", "us")],
        generated_at=datetime.now(timezone.utc).isoformat(),
    )

    assert events[0].affected_symbols == ["us:QCOM"]
    assert events[0].affected_markets == ["us"]
    assert "scope=holding" in events[0].rationale
    assert events[0].confidence >= 0.47
