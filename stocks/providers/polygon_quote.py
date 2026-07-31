from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from stocks.domain.models import Instrument, Quote
from stocks.engine.config_loader import provider_base_url
from stocks.errors import (
    ProviderAuthError,
    ProviderConfigError,
    ProviderDataError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from stocks.providers.base import QuoteProvider

# Provider 端点：env (STOCKS_PROVIDER_POLYGON_BASE_URL) > engine.yaml > 代码默认
_PROVIDER_BASE_URL = provider_base_url("polygon", "https://api.polygon.io")

ROOT = Path(__file__).resolve().parents[2]
POLYGON_KEY_PATH = ROOT / ".secret" / "polygon-key.md"
ALT_KEY_PATH = Path("/opt/data/.secret/polygon-key.md")


class PolygonQuoteProvider(QuoteProvider):
    """Polygon.io 美股行情 Provider

    使用 Polygon.io REST API:
    - 最新价: GET /v2/aggs/ticker/{symbol}/prev
    - 快照:   GET /v2/snapshot/locale/us/markets/stocks/tickers/{symbol}

    免费档约 5 次/分钟，客户端节流默认 12 秒间隔。
    作为 Finnhub 的备用源，提供美股第二行情通道。
    """

    @property
    def name(self) -> str:
        return "polygon"

    @property
    def supported_markets(self) -> list[str]:
        return ["us"]

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        min_request_interval: float = 12.0,
    ):
        if api_key:
            self.api_key = api_key
        else:
            env_key = os.environ.get("POLYGON_API_KEY", "").strip()
            if env_key:
                self.api_key = env_key
            elif POLYGON_KEY_PATH.exists():
                self.api_key = POLYGON_KEY_PATH.read_text(encoding="utf-8").strip()
            elif ALT_KEY_PATH.exists():
                self.api_key = ALT_KEY_PATH.read_text(encoding="utf-8").strip()
            else:
                self.api_key = ""
        self._min_request_interval = max(1.0, min_request_interval)
        self._rate_lock = threading.Lock()
        self._last_request_at = 0.0

    def _throttle(self) -> None:
        with self._rate_lock:
            elapsed = time.monotonic() - self._last_request_at
            wait_for = self._min_request_interval - elapsed
            if wait_for > 0:
                time.sleep(wait_for)
            self._last_request_at = time.monotonic()

    def _build_symbol(self, instrument: Instrument) -> str:
        return instrument.code.strip().upper()

    def _request_json(self, endpoint: str) -> dict:
        if not self.api_key:
            raise ProviderConfigError(
                "Polygon API key 未配置", source=self.name
            )
        self._throttle()
        endpoint = endpoint.strip("/")
        sep = "&" if "?" in endpoint else "?"
        url = f"{_PROVIDER_BASE_URL}/{endpoint}{sep}apiKey={self.api_key}"
        req = urllib.request.Request(
            url, headers={"User-Agent": "stocks-claw/1.0"}
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            if not text.strip():
                raise ProviderDataError("Polygon 返回空响应", source=self.name)
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ProviderDataError(
                    "Polygon 返回无效 JSON", source=self.name, detail=str(exc)
                ) from exc

            status = data.get("status", "")
            if status == "ERROR":
                msg = data.get("error", "unknown")
                if "API key" in msg or "authentication" in msg.lower():
                    raise ProviderAuthError(msg, source=self.name)
                raise ProviderDataError(msg, source=self.name)

            results_count = data.get("resultsCount", 0)
            if results_count == 0 and "results" not in data:
                raise ProviderDataError(
                    "Polygon 无数据返回（可能为非交易时段）", source=self.name
                )
            return data
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise ProviderAuthError(
                    f"Polygon 鉴权失败: HTTP {exc.code}", source=self.name
                ) from exc
            if exc.code == 429:
                raise ProviderRateLimitError(
                    "Polygon 请求限流: HTTP 429", source=self.name
                ) from exc
            if exc.code in {408, 504}:
                raise ProviderTimeoutError(
                    f"Polygon 请求超时: HTTP {exc.code}", source=self.name
                ) from exc
            raise ProviderNetworkError(
                f"Polygon HTTP 错误: {exc.code}", source=self.name
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ProviderTimeoutError(
                f"Polygon 请求超时: {exc}", source=self.name
            ) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise ProviderTimeoutError(
                    f"Polygon 请求超时: {exc.reason}", source=self.name
                ) from exc
            raise ProviderNetworkError(
                f"Polygon 网络错误: {exc.reason}", source=self.name
            ) from exc

    def _fetch_prev_close(self, symbol: str) -> Optional[dict]:
        return self._request_json(f"v2/aggs/ticker/{symbol}/prev")

    def _data_to_quote(self, data: dict, instrument: Instrument) -> Optional[Quote]:
        results = data.get("results")
        if not results or not isinstance(results, list) or len(results) == 0:
            return None

        bar = results[0]
        price = bar.get("c")
        if price is None:
            return None

        as_of = None
        raw_ts = bar.get("t")
        if raw_ts:
            try:
                ts = float(raw_ts) / 1000.0
                if ts > 0:
                    as_of = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            except (ValueError, TypeError, OverflowError, OSError):
                pass

        volume = bar.get("v")
        return Quote(
            instrument=instrument,
            price=float(price),
            change=None,
            pct_change=None,
            volume_lot=int(volume) if volume is not None else None,
            amount_10k=None,
            open_price=float(bar["o"]) if bar.get("o") is not None else None,
            high=float(bar["h"]) if bar.get("h") is not None else None,
            low=float(bar["l"]) if bar.get("l") is not None else None,
            prev_close=None,
            source=self.name,
            as_of=as_of,
        )

    async def fetch(self, instrument: Instrument) -> Optional[Quote]:
        symbol = self._build_symbol(instrument)
        try:
            data = await asyncio.to_thread(self._fetch_prev_close, symbol)
        except ProviderDataError:
            return None
        if data is None:
            return None
        return self._data_to_quote(data, instrument)

    async def fetch_batch(self, instruments: list[Instrument]) -> list[Quote]:
        if not instruments:
            return []
        quotes: list[Quote] = []
        for instrument in instruments:
            quote = await self.fetch(instrument)
            if quote is not None:
                quotes.append(quote)
        return quotes
