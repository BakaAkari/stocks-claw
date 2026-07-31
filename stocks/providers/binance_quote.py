"""Binance 加密货币实时行情 Provider。"""

from __future__ import annotations

import asyncio
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from stocks.domain.models import Instrument, Quote
from stocks.engine.config_loader import provider_base_url
from stocks.errors import (
    ProviderDataError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from stocks.providers.base import QuoteProvider

# Provider 端点：env (STOCKS_PROVIDER_BINANCE_BASE_URL) > engine.yaml > 代码默认
_PROVIDER_BASE_URL = provider_base_url("binance", "https://api.binance.com/api/v3")


class BinanceQuoteProvider(QuoteProvider):
    """免 key 的 Binance 24 小时 ticker，作为 crypto 实时备用源。"""

    @property
    def name(self) -> str:
        return "binance"

    @property
    def supported_markets(self) -> list[str]:
        return ["crypto"]

    @staticmethod
    def _build_symbol(instrument: Instrument) -> str:
        return instrument.code.split(":", 1)[-1].replace("/", "").upper()

    def _fetch_sync(self, symbol: str) -> dict:
        query = urllib.parse.urlencode({"symbol": symbol})
        request = urllib.request.Request(
            f"{_PROVIDER_BASE_URL}/ticker/24hr?{query}",
            headers={"User-Agent": "stocks-claw/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in {418, 429}:
                raise ProviderRateLimitError(
                    f"Binance 请求限流: HTTP {exc.code}", source=self.name
                ) from exc
            if exc.code in {408, 504}:
                raise ProviderTimeoutError(
                    f"Binance 请求超时: HTTP {exc.code}", source=self.name
                ) from exc
            raise ProviderNetworkError(
                f"Binance HTTP 错误: {exc.code}", source=self.name
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ProviderTimeoutError(
                f"Binance 请求超时: {exc}", source=self.name
            ) from exc
        except urllib.error.URLError as exc:
            raise ProviderNetworkError(
                f"Binance 网络错误: {exc.reason}", source=self.name
            ) from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProviderDataError(
                "Binance 返回无效 JSON", source=self.name, detail=str(exc)
            ) from exc
        if not isinstance(payload, dict) or payload.get("code") is not None:
            raise ProviderDataError(
                f"Binance 返回错误: {payload}", source=self.name
            )
        return payload

    def _data_to_quote(self, data: dict, instrument: Instrument) -> Quote:
        try:
            as_of = datetime.fromtimestamp(
                float(data["closeTime"]) / 1000, tz=timezone.utc
            ).isoformat()
            return Quote(
                instrument=instrument,
                price=float(data["lastPrice"]),
                change=float(data["priceChange"]),
                pct_change=float(data["priceChangePercent"]),
                open_price=float(data["openPrice"]),
                high=float(data["highPrice"]),
                low=float(data["lowPrice"]),
                prev_close=float(data["prevClosePrice"]),
                volume_lot=float(data["volume"]),
                source=self.name,
                as_of=as_of,
            )
        except (KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
            raise ProviderDataError(
                "Binance ticker 字段缺失或非法", source=self.name, detail=str(exc)
            ) from exc

    async def fetch(self, instrument: Instrument) -> Optional[Quote]:
        data = await asyncio.to_thread(
            self._fetch_sync, self._build_symbol(instrument)
        )
        return self._data_to_quote(data, instrument)

    async def fetch_batch(self, instruments: list[Instrument]) -> list[Quote]:
        quotes: list[Quote] = []
        for instrument in instruments:
            quote = await self.fetch(instrument)
            if quote is not None:
                quotes.append(quote)
        return quotes
