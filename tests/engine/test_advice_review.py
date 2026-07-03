"""建议表现回看测试。"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from stocks.domain.models import Instrument
from stocks.engine.advice_review import attach_advice_performance
from stocks.engine.history_cache import HistoryCache


def _advice() -> dict:
    return {
        "created_at": "2026-07-02T09:00:00+00:00",
        "instruments": [{"market": "a", "code": "000001", "name": "平安银行"}],
        "direction": {"a:000001": "watch"},
        "rationale_summary": "继续观察。",
        "based_on": ["quotes"],
        "boundary": [{"type": "inference", "text": "继续观察"}],
    }


def _history_frame(instrument: Instrument) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "timestamp": datetime(2026, 7, 1, 20, 0, tzinfo=timezone.utc),
            "code": instrument.code,
            "name": instrument.name,
            "market": instrument.market,
            "price": 10.0,
            "open_price": 10.0,
            "high": 10.2,
            "low": 9.8,
            "prev_close": 9.9,
            "volume_lot": 1,
        },
        {
            "timestamp": datetime(2026, 7, 2, 20, 0, tzinfo=timezone.utc),
            "code": instrument.code,
            "name": instrument.name,
            "market": instrument.market,
            "price": 11.0,
            "open_price": 10.8,
            "high": 11.2,
            "low": 10.7,
            "prev_close": 10.0,
            "volume_lot": 1,
        },
        {
            "timestamp": datetime(2026, 7, 3, 20, 0, tzinfo=timezone.utc),
            "code": instrument.code,
            "name": instrument.name,
            "market": instrument.market,
            "price": 12.0,
            "open_price": 11.8,
            "high": 12.2,
            "low": 11.7,
            "prev_close": 11.0,
            "volume_lot": 1,
        },
    ])


async def test_attach_advice_performance_calculates_since_advice_date(tmp_path):
    instrument = Instrument(code="000001", name="平安银行", market="a")
    cache = HistoryCache(base_dir=str(tmp_path), ttl=86400)
    await cache.warm(instrument, _history_frame(instrument))

    records = await attach_advice_performance(
        [_advice()],
        watchlist=[instrument],
        history_cache=cache,
    )

    performance = records[0]["performance"][0]
    assert performance["status"] == "ok"
    assert performance["direction"] == "watch"
    assert performance["start_price"] == 11.0
    assert performance["latest_price"] == 12.0
    assert performance["pct_change"] == 9.0909


async def test_attach_advice_performance_missing_history_is_no_data(tmp_path):
    instrument = Instrument(code="000001", name="平安银行", market="a")
    cache = HistoryCache(base_dir=str(tmp_path), ttl=86400)

    records = await attach_advice_performance(
        [_advice()],
        watchlist=[instrument],
        history_cache=cache,
    )

    performance = records[0]["performance"][0]
    assert performance["status"] == "no_data"
    assert performance["reason"] == "missing_history"


async def test_attach_advice_performance_skips_non_watchlist_symbols(tmp_path):
    cache = HistoryCache(base_dir=str(tmp_path), ttl=86400)

    records = await attach_advice_performance(
        [_advice()],
        watchlist=[],
        history_cache=cache,
    )

    assert records[0]["performance"] == []
