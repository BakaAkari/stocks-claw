"""东方财富 A 股实时行情时间戳测试。"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from stocks.domain.models import Instrument
from stocks.providers.eastmoney_a import EastmoneyAQuoteProvider

# 2026-07-02 真实响应；该端点的 Unix 行情更新时间字段是 f124。
REAL_PAYLOAD = {
    "rc": 0,
    "data": {
        "total": 2,
        "diff": [
            {
                "f2": 4812.3,
                "f3": -2.96,
                "f4": -146.68,
                "f5": 343243758,
                "f6": 1027796834232.7,
                "f12": "000300",
                "f14": "沪深300",
                "f15": 4896.99,
                "f16": 4800.48,
                "f17": 4865.17,
                "f18": 4958.98,
                "f124": 1782979895,
            },
            {
                "f2": 8.475,
                "f3": 2.47,
                "f4": 0.204,
                "f5": 5149790,
                "f6": 4346984456.0,
                "f12": "518880",
                "f14": "黄金ETF华安",
                "f15": 8.484,
                "f16": 8.397,
                "f17": 8.425,
                "f18": 8.271,
                "f124": 1782979899,
            },
        ],
    },
}


class FakeResponse:
    def __init__(self, payload: dict):
        self._data = json.dumps(payload, ensure_ascii=False).encode()

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@pytest.fixture
def provider():
    return EastmoneyAQuoteProvider()


async def test_fetch_real_fixture_includes_source_and_as_of(provider):
    instrument = Instrument("000300", "沪深300", "a", "sh_index")
    with patch("urllib.request.urlopen", return_value=FakeResponse(REAL_PAYLOAD)):
        quote = await provider.fetch(instrument)

    assert quote is not None
    assert quote.price == 4812.3
    assert quote.open_price == 4865.17
    assert quote.high == 4896.99
    assert quote.low == 4800.48
    assert quote.prev_close == 4958.98
    assert quote.source == "eastmoney_a"
    assert quote.as_of == "2026-07-02T08:11:35+00:00"


async def test_fetch_batch_real_fixture_maps_each_timestamp(provider):
    instruments = [
        Instrument("000300", "沪深300", "a", "sh_index"),
        Instrument("518880", "黄金ETF", "a", "sh"),
    ]
    with patch("urllib.request.urlopen", return_value=FakeResponse(REAL_PAYLOAD)):
        quotes = await provider.fetch_batch(instruments)

    assert [quote.instrument for quote in quotes] == instruments
    assert quotes[1].as_of == "2026-07-02T08:11:39+00:00"


def test_request_includes_actual_unix_timestamp_field(provider):
    with patch("urllib.request.urlopen", return_value=FakeResponse(REAL_PAYLOAD)) as urlopen:
        provider._fetch_sync(["1.000300"])

    requested_url = urlopen.call_args.args[0].full_url
    assert "f124" in requested_url
    assert "f86" not in requested_url


@pytest.mark.parametrize("missing_value", [None, "", "-", 0, "0", "invalid"])
def test_missing_or_invalid_timestamp_is_not_fabricated(provider, missing_value):
    row = dict(REAL_PAYLOAD["data"]["diff"][0], f124=missing_value)
    instrument = Instrument("000300", "沪深300", "a", "sh_index")

    quote = provider._row_to_quote(row, instrument)

    assert quote.as_of is None
    assert quote.source == "eastmoney_a"
