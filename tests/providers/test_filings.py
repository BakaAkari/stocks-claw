"""SEC EDGAR 与巨潮一手公告 Provider 测试。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, Mock, patch

from stocks.domain.models import Instrument, NewsItem
from stocks.engine.news_sources import NewsAggregator
from stocks.providers.filings import CninfoFilingsProvider, SecEdgarFilingsProvider

SEC_RESPONSE = {
    "filings": {
        "recent": {
            "form": ["8-K", "10-Q", "4"],
            "filingDate": ["2026-07-02", "2026-06-20", "2026-07-02"],
            "accessionNumber": [
                "0000320193-26-000001",
                "0000320193-26-000002",
                "0000320193-26-000003",
            ],
            "primaryDocument": ["aapl-8k.htm", "aapl-10q.htm", "xslF345X05/doc.xml"],
            "primaryDocDescription": ["Current report", "Quarterly report", "Form 4"],
        }
    }
}

CNINFO_RESPONSE = {
    "totalAnnouncement": 1,
    "announcements": [
        {
            "secCode": "159110",
            "secName": "科创债ETF",
            "announcementTitle": "<em>科创债ETF</em>上市交易公告书",
            "announcementTime": 1782950400000,
            "adjunctUrl": "finalpage/2026-07-02/1224000000.PDF",
            "announcementId": "1224000000",
        }
    ],
}


def _response(payload):
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    return response


async def test_sec_parses_recent_supported_forms_and_builds_archive_url():
    instrument = Instrument("AAPL", "Apple", "us")
    provider = SecEdgarFilingsProvider(
        lambda: [instrument],
        {"AAPL": "320193"},
        user_agent="stocks-claw test@example.com",
        min_request_interval=0.1,
    )

    with patch("urllib.request.urlopen", return_value=_response(SEC_RESPONSE)) as urlopen:
        items = await provider.fetch(max_items=10)

    assert [item.raw_metadata["form"] for item in items] == ["8-K", "10-Q"]
    assert items[0].source_type == "filing"
    assert items[0].url.endswith("/320193/000032019326000001/aapl-8k.htm")
    request = urlopen.call_args.args[0]
    assert request.full_url.endswith("CIK0000320193.json")
    assert "test@example.com" in request.headers["User-agent"]


async def test_sec_skips_unmapped_watchlist_symbols():
    provider = SecEdgarFilingsProvider(
        lambda: [Instrument("QQQ", "QQQ", "us")],
        {"AAPL": "320193"},
        user_agent="stocks-claw test@example.com",
    )
    with patch("urllib.request.urlopen") as urlopen:
        assert await provider.fetch() == []
    urlopen.assert_not_called()


async def test_sec_symbol_failure_does_not_drop_other_symbols():
    instruments = [
        Instrument("AAPL", "Apple", "us"),
        Instrument("QCOM", "Qualcomm", "us"),
    ]
    provider = SecEdgarFilingsProvider(
        lambda: instruments,
        {"AAPL": "320193", "QCOM": "804328"},
        user_agent="stocks-claw test@example.com",
        min_request_interval=0.1,
    )

    with patch(
        "urllib.request.urlopen",
        side_effect=[OSError("temporary failure"), _response(SEC_RESPONSE)],
    ):
        items = await provider.fetch(max_items=10)

    assert len(items) == 2
    assert provider.last_errors == {"AAPL": "OSError: temporary failure"}


async def test_cninfo_parses_fixture_and_posts_watchlist_code():
    instrument = Instrument("159110", "科创债ETF", "a", exchange="sz")
    provider = CninfoFilingsProvider(
        lambda: [instrument], {"159110": "jjjl0000050"}
    )

    with patch("urllib.request.urlopen", return_value=_response(CNINFO_RESPONSE)) as urlopen:
        items = await provider.fetch(max_items=10)

    assert len(items) == 1
    assert items[0].title == "科创债ETF上市交易公告书"
    assert items[0].source_name == "巨潮资讯"
    assert items[0].url == "https://static.cninfo.com.cn/finalpage/2026-07-02/1224000000.PDF"
    request = urlopen.call_args.args[0]
    assert b"stock=159110%2Cjjjl0000050" in request.data
    assert b"column=szse" in request.data


async def test_cninfo_symbol_failure_does_not_drop_other_symbols():
    instruments = [
        Instrument("159110", "科创债ETF", "a", exchange="sz"),
        Instrument("588000", "科创50ETF", "a", exchange="sh"),
    ]
    provider = CninfoFilingsProvider(lambda: instruments)
    second_response = {
        **CNINFO_RESPONSE,
        "announcements": [
            {**CNINFO_RESPONSE["announcements"][0], "secCode": "588000"}
        ],
    }

    with patch(
        "urllib.request.urlopen",
        side_effect=[OSError("gateway timeout"), _response(second_response)],
    ):
        items = await provider.fetch(max_items=10)

    assert len(items) == 1
    assert provider.last_errors == {"159110": "OSError: gateway timeout"}


async def test_filing_and_rss_coexist_in_aggregator():
    filing = NewsItem(
        title="8-K",
        url="https://sec.test/1",
        source_name="SEC EDGAR",
        source_type="filing",
        published_at=None,
        summary=None,
    )
    rss = NewsItem(
        title="News",
        url="https://news.test/1",
        source_name="RSS",
        source_type="rss",
        published_at=None,
        summary=None,
    )
    class Provider:
        def __init__(self, item):
            self.item = item

        async def fetch(self, max_items=10):
            return [self.item]

    items = await NewsAggregator([Provider(filing), Provider(rss)]).fetch()
    assert {item.source_type for item in items} == {"filing", "rss"}
