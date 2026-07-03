"""未来事件日历测试。"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from stocks.domain.models import Instrument
from stocks.engine.event_calendar import (
    EventCalendar,
    FinnhubEarningsCalendarProvider,
    StaticEventCalendarProvider,
)
from stocks.errors import ProviderRateLimitError
from stocks.providers.finnhub_quote import FinnhubQuoteProvider

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
        assert quality["expired_count"] == 0
        assert quality["sources"] == {"static_config": 2}

        nfp = events[0]
        assert nfp.days_until == 0
        assert nfp.scheduled_at == "2026-07-02T12:30:00+00:00"
        assert nfp.time_precision == "datetime"
        assert nfp.status == "imminent"
        assert "us:QQQ" in nfp.affected_symbols  # tech 命中
        assert "a:518880" in nfp.affected_symbols  # gold 命中
        assert "a:159110" not in nfp.affected_symbols  # bond 未在敏感类别中

        cpi = events[1]
        assert cpi.days_until == 12
        assert cpi.scheduled_at is None
        assert cpi.time_precision == "date"
        assert cpi.status == "scheduled"
        assert cpi.affected_symbols == ["us:QQQ"]

    async def test_exact_event_is_kept_one_minute_before_and_dropped_after(self):
        calendar = EventCalendar(
            [StaticEventCalendarProvider(_static_config())], lookahead_days=14
        )
        before, before_quality = await calendar.fetch(
            now=datetime(2026, 7, 2, 12, 29, tzinfo=timezone.utc), watchlist=[]
        )
        after, after_quality = await calendar.fetch(
            now=datetime(2026, 7, 2, 12, 31, tzinfo=timezone.utc), watchlist=[]
        )

        assert "美国 6 月非农就业报告" in {event.name for event in before}
        assert "美国 6 月非农就业报告" not in {event.name for event in after}
        assert before_quality["expired_count"] == 0
        assert after_quality["expired_count"] == 1

    async def test_local_date_rollover_does_not_expire_future_utc_event(self):
        config = {
            "events": [
                {
                    "date": "2026-07-02",
                    "time_utc": "20:00",
                    "name": "美国盘中事件",
                    "event_type": "other",
                    "market": "us",
                }
            ]
        }
        calendar = EventCalendar(
            [StaticEventCalendarProvider(config)], lookahead_days=2
        )
        shanghai_now = datetime(
            2026, 7, 3, 3, 0, tzinfo=ZoneInfo("Asia/Shanghai")
        )
        events, quality = await calendar.fetch(now=shanghai_now, watchlist=[])

        assert len(events) == 1
        assert events[0].scheduled_at == "2026-07-02T20:00:00+00:00"
        assert events[0].days_until == 0
        assert events[0].status == "imminent"
        assert quality["expired_count"] == 0

    async def test_date_only_event_retains_explicit_precision(self):
        calendar = EventCalendar(
            [StaticEventCalendarProvider(_static_config())], lookahead_days=14
        )
        events, _ = await calendar.fetch(now=_NOW, watchlist=[])
        cpi = next(event for event in events if event.name == "美国 6 月 CPI")
        assert cpi.scheduled_at is None
        assert cpi.time_precision == "date"

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

    async def test_successful_provider_with_no_scheduled_events_is_ok(self):
        calendar = EventCalendar([StaticEventCalendarProvider({})], lookahead_days=14)
        events, quality = await calendar.fetch(now=_NOW, watchlist=[])
        assert events == []
        assert quality["status"] == "ok"

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
        assert payload["scheduled_at"] == "2026-07-02T12:30:00+00:00"
        assert payload["time_precision"] == "datetime"
        assert payload["status"] == "imminent"


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

    async def test_missing_api_key_is_per_symbol_error_not_global_abort(self):
        client = FinnhubQuoteProvider("placeholder", min_request_interval=0)
        client.api_key = ""
        provider = FinnhubEarningsCalendarProvider(client=client)
        events = await provider.fetch(
            start=date(2026, 7, 2),
            end=date(2026, 7, 16),
            watchlist=[Instrument("QCOM", "高通", "us")],
        )

        assert events == []
        assert "QCOM" in provider.last_errors
        assert "ProviderConfigError" in provider.last_errors["QCOM"]

    async def test_single_symbol_failure_keeps_other_symbol_and_reports_partial(
        self, monkeypatch
    ):
        provider = FinnhubEarningsCalendarProvider(api_key="test-key")

        def fake_fetch(symbol, start, end):
            if symbol == "AAPL":
                raise ProviderRateLimitError("limited")
            return [{"date": "2026-07-10", "hour": "bmo"}]

        monkeypatch.setattr(provider, "_fetch_sync", fake_fetch)
        calendar = EventCalendar([provider], lookahead_days=14)
        events, quality = await calendar.fetch(
            now=_NOW,
            watchlist=[
                Instrument("AAPL", "Apple", "us"),
                Instrument("QCOM", "高通", "us"),
            ],
        )

        assert [event.affected_symbols for event in events] == [["us:QCOM"]]
        assert quality["status"] == "partial"
        assert "finnhub_earnings:AAPL" in quality["errors"]

    async def test_cache_hit_and_expiry(self, tmp_path, monkeypatch):
        provider = FinnhubEarningsCalendarProvider(
            api_key="test-key", cache_dir=tmp_path, cache_ttl=3600
        )
        calls = 0

        def fake_fetch(symbol, start, end):
            nonlocal calls
            calls += 1
            return [{"date": "2026-07-10", "hour": "amc"}]

        monkeypatch.setattr(provider, "_fetch_sync", fake_fetch)
        kwargs = {
            "start": date(2026, 7, 2),
            "end": date(2026, 7, 16),
            "watchlist": [Instrument("QCOM", "高通", "us")],
        }
        first = await provider.fetch(**kwargs)
        second = await provider.fetch(**kwargs)

        assert len(first) == len(second) == 1
        assert calls == 1
        assert provider.last_cache == {"hits": 1, "misses": 0}

        cache_path = tmp_path / "finnhub_earnings_QCOM.json"
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        payload["fetched_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).isoformat()
        cache_path.write_text(json.dumps(payload), encoding="utf-8")
        await provider.fetch(**kwargs)
        assert calls == 2
        assert provider.last_cache == {"hits": 0, "misses": 1}
