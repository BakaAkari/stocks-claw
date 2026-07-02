"""Finnhub Provider typed error 与降级记录测试。"""

import socket
import urllib.error
from email.message import Message
from unittest.mock import patch

import pytest

from stocks.domain.models import Instrument
from stocks.engine.fetchers import DataFetcher
from stocks.errors import (
    ProviderAuthError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from stocks.providers.finnhub_quote import FinnhubQuoteProvider
from stocks.providers.registry import ProviderRegistry


def _provider() -> FinnhubQuoteProvider:
    return FinnhubQuoteProvider("test-key", min_request_interval=0)


def _http_error(code: int, retry_after: str | None = None):
    headers = Message()
    if retry_after:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://finnhub.test", code, "error", headers, None)


@pytest.mark.parametrize(
    ("side_effect", "error_type"),
    [
        (_http_error(401), ProviderAuthError),
        (_http_error(403), ProviderAuthError),
        (_http_error(429, "10"), ProviderRateLimitError),
        (urllib.error.URLError("dns"), ProviderNetworkError),
        (urllib.error.URLError(socket.timeout("slow")), ProviderTimeoutError),
    ],
)
def test_fetch_sync_classifies_failures(side_effect, error_type):
    provider = _provider()
    with patch("urllib.request.urlopen", side_effect=side_effect):
        with pytest.raises(error_type):
            provider._fetch_sync("AAPL")


async def test_degradation_record_preserves_rate_limit_type():
    provider = _provider()
    registry = ProviderRegistry()
    registry.register(provider)
    fetcher = DataFetcher(registry, max_retries=0)
    instrument = Instrument("AAPL", "Apple", "us")

    with patch("urllib.request.urlopen", side_effect=_http_error(429, "10")):
        result = await fetcher.fetch_quotes([instrument])

    assert result["us"] == []
    record = fetcher.get_degradation_log()[0]
    assert record.error_type == "ProviderRateLimitError"
    assert record.error_retryable is True
