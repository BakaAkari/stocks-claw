from datetime import datetime, timezone

from stocks.domain.models import AnalysisContext, MarketEvent, NewsItem


def test_news_and_events_roundtrip_preserve_source_timestamp_without_mutating_input():
    now = datetime(2026, 10, 19, 8, tzinfo=timezone.utc)
    context = AnalysisContext.from_dict({})
    from dataclasses import replace
    context = replace(context,
        news=[NewsItem('news', 'https://example.com', 'test', 'rss', now, None)],
        market_events=[MarketEvent('news', 'https://example.com', 'test', 'rss', now, None, 'macro')])
    saved = context.to_dict()
    replayed = AnalysisContext.from_dict(saved)
    assert replayed.news[0].published_at == now
    assert replayed.market_events[0].published_at == now
    assert replayed.to_dict() == saved
    assert isinstance(saved['news'][0]['published_at'], str)
