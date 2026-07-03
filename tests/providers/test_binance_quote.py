"""Binance crypto 实时 Provider 测试。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, Mock, patch

import pytest

from stocks.domain.models import Instrument
from stocks.engine.fetchers import DataFetcher
from stocks.errors import ProviderDataError, ProviderRateLimitError
from stocks.providers.binance_quote import BinanceQuoteProvider
from stocks.providers.registry import ProviderRegistry

BINANCE_TICKER = {
    "symbol": "BTCUSDT",
    "priceChange": "1575.56",
    "priceChangePercent": "2.627",
    "prevClosePrice": "59976.45",
    "lastPrice": "61552.01",
    "openPrice": "59976.45",
    "highPrice": "62200.00",
    "lowPrice": "59957.72",
    "volume": "21193.33119",
    "closeTime": 1783042009009,
}


def _response(payload):
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    return response


async def test_fetch_maps_ohlcv_timestamp_and_source():
    provider = BinanceQuoteProvider()
    instrument = Instrument("BTCUSDT", "Bitcoin", "crypto")
    with patch("urllib.request.urlopen", return_value=_response(BINANCE_TICKER)):
        quote = await provider.fetch(instrument)

    assert quote is not None
    assert quote.price == 61552.01
    assert quote.open_price == 59976.45
    assert quote.high == 62200.0
    assert quote.low == 59957.72
    assert quote.prev_close == 59976.45
    assert quote.source == "binance"
    assert quote.as_of == "2026-07-03T01:26:49.009000+00:00"


def test_symbol_accepts_exchange_prefix_and_slash():
    provider = BinanceQuoteProvider()
    assert provider._build_symbol(Instrument("BINANCE:BTCUSDT", "", "crypto")) == "BTCUSDT"
    assert provider._build_symbol(Instrument("BTC/USDT", "", "crypto")) == "BTCUSDT"


@pytest.mark.parametrize(
    "payload",
    [{"code": -1121, "msg": "Invalid symbol."}, {"lastPrice": "1"}],
)
async def test_invalid_payload_raises_typed_data_error(payload):
    provider = BinanceQuoteProvider()
    instrument = Instrument("BAD", "Bad", "crypto")
    with patch("urllib.request.urlopen", return_value=_response(payload)):
        with pytest.raises(ProviderDataError):
            await provider.fetch(instrument)


async def test_finnhub_failure_falls_back_to_binance():
    primary = Mock()
    primary.name = "finnhub"
    primary.supported_markets = ["crypto"]
    primary.fetch_batch = Mock(side_effect=ProviderRateLimitError("limited"))
    fallback = BinanceQuoteProvider()
    registry = ProviderRegistry()
    registry.register(primary)
    registry.register(fallback)
    registry._defaults = {"crypto": primary}
    fetcher = DataFetcher(
        registry,
        max_retries=0,
        fallback_order={"crypto": ["binance"]},
    )
    instrument = Instrument("BTCUSDT", "Bitcoin", "crypto")

    with patch("urllib.request.urlopen", return_value=_response(BINANCE_TICKER)):
        result = await fetcher.fetch_quotes([instrument])

    assert result["crypto"][0].source == "binance"
    record = fetcher.get_degradation_log()[0]
    assert record.result == "fallback_success"
    assert record.primary_provider == "finnhub"
    assert record.fallback_provider == "binance"
