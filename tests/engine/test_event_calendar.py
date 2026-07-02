"""未来事件日历测试。"""

from __future__ import annotations

from datetime import date, datetime, timezone

from stocks.domain.models import Instrument
from stocks.engine.event_calendar import (
    EventCalendar,
    FinnhubEarningsCalendarProvider,
    StaticEventCalendarProvider,
)

_NOW = datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc)


def _static_config() -> dict:
    return {
        "events": [
            {
                "date": "2026-07-02",
                "time_utc": "12:30",
                "name": "美国 6 月非农就业报告",
                "event_type": "macro_release",
                "market": "us",
                "affected_categories": ["tech", "gold"],
                "note": "就业影响利率预期",
            },
            {
                "date": "2026-07-14",
                "name": "美国 6 月 CPI",
                "event_type": "macro_release",
                "market": "us",
                "affected_categories": ["tech"],
            },
            {
                "date": "2026-09-16",
                "name": "FOMC 利率决议",
                "event_type": "central_bank",
                "market": "us",
                "affected_categories": ["tech"],
            },
            {
                "date": "not-a-date",
                "name": "坏数据",
                "event_type": "other",
                "market": "us",
            },
        ]
    }


def _watchlist() -> list[Instrument]:
    return [
        Instrument(code="QQQ", name="纳指ETF", market="us", category="tech"),
        Instrument(code="518880", name="黄金ETF", market="a", exchange="sh", category="gold"),
        Instrument(code="159110", name="债ETF", market="a", exchange="sz", category="bond"),
    ]


class TestStaticEventCalendarProvider:
    async def test_window_filtering_drops_out_of_range_and_invalid(self):
        provider = StaticEventCalendarProvider(_static_config())
        events = await provider.fetch(
            start=date(2026, 7, 2), end=date(2026, 7, 16), watchlist=[]
        )
        names = [e.name for e in events]
        assert "美国 6 月非农就业报告" in names
        assert "美国 6 月 CPI" in names
        assert "FOMC 利率决议" not in names  # 窗口外
        assert "坏数据" not in names  # 无效日期被丢弃

    async def test_empty_config_returns_no_events(self):
        provider = StaticEventCalendarProvider({})
        events = await provider.fetch(
            start=date(2026, 7, 2), end=date(2026, 7, 16), watchlist=[]
        )
        assert events == []


class TestEventCalendar:
    async def test_days_until_and_symbol_matching(self):
        calendar = EventCalendar(
            [StaticEventCalendarProvider(_static_config())],
            lookahead_days=14,
        )
        events, quality = await calendar.fetch(now=_NOW, watchlist=_watchlist())

        assert quality["status"] == "ok"
        assert quality["event_count"] == 2
        assert quality["sources"] == {"static_config": 2}

        nfp = events[0]
        assert nfp.days_until == 0
        assert "us:QQQ" in nfp.affected_symbols  # tech 命中
        assert "a:518880" in nfp.affected_symbols  # gold 命中
        assert "a:159110" not in nfp.affected_symbols  # bond 未在敏感类别中

        cpi = events[1]
        assert cpi.days_until == 12
        assert cpi.affected_symbols == ["us:QQQ"]

    async def test_provider_failure_reports_partial_not_silent(self):
        class FailingProvider:
            @property
            def name(self) -> str:
                return "boom"

            async def fetch(self, *, start, end, watchlist):
                raise RuntimeError("网络断了")

        calendar = EventCalendar(
            [StaticEventCalendarProvider(_static_config()), FailingProvider()],
            lookahead_days=14,
        )
        events, quality = await calendar.fetch(now=_NOW, watchlist=[])
        assert quality["status"] == "partial"
        assert "boom" in quality["errors"]
        assert len(events) == 2

    async def test_all_failed_reports_missing(self):
        class FailingProvider:
            @property
            def name(self) -> str:
                return "boom"

            async def fetch(self, *, start, end, watchlist):
                raise RuntimeError("网络断了")

        calendar = EventCalendar([FailingProvider()], lookahead_days=14)
        events, quality = await calendar.fetch(now=_NOW, watchlist=[])
        assert events == []
        assert quality["status"] == "missing"

    async def test_no_providers_reports_not_configured(self):
        calendar = EventCalendar([], lookahead_days=14)
        events, quality = await calendar.fetch(now=_NOW, watchlist=[])
        assert events == []
        assert quality["status"] == "not_configured"

    async def test_events_serializable(self):
        calendar = EventCalendar(
            [StaticEventCalendarProvider(_static_config())],
            lookahead_days=14,
        )
        events, _ = await calendar.fetch(now=_NOW, watchlist=_watchlist())
        payload = events[0].to_dict()
        assert payload["date"] == "2026-07-02"
        assert payload["event_type"] == "macro_release"
        assert payload["days_until"] == 0


class TestFinnhubEarningsCalendarProvider:
    async def test_maps_rows_to_events(self, monkeypatch):
        provider = FinnhubEarningsCalendarProvider(api_key="test-key")

        def fake_fetch(symbol, start, end):
            assert symbol == "QCOM"
            return [
                {"date": "2026-07-10", "hour": "amc"},
                {"date": "2027-01-01"},  # 窗口外
            ]

        monkeypatch.setattr(provider, "_fetch_sync", fake_fetch)
        events = await provider.fetch(
            start=date(2026, 7, 2),
            end=date(2026, 7, 16),
            watchlist=[
                Instrument(code="QCOM", name="高通", market="us", category="tech"),
                Instrument(code="000001", name="平安银行", market="a"),  # 非美股跳过
            ],
        )
        assert len(events) == 1
        event = events[0]
        assert event.date == "2026-07-10"
        assert event.event_type == "earnings"
        assert event.affected_symbols == ["us:QCOM"]
        assert event.affected_categories == ["tech"]

    async def test_missing_api_key_raises(self):
        provider = FinnhubEarningsCalendarProvider(api_key="")
        provider.api_key = ""
        try:
            await provider.fetch(start=date(2026, 7, 2), end=date(2026, 7, 16), watchlist=[])
        except RuntimeError as exc:
            assert "key" in str(exc).lower() or "未配置" in str(exc)
        else:
            raise AssertionError("expected RuntimeError when api key missing")
