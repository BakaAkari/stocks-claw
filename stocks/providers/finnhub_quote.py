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
from stocks.errors import (
    ProviderAuthError,
    ProviderConfigError,
    ProviderDataError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from stocks.providers.base import QuoteProvider

ROOT = Path(__file__).resolve().parents[2]
FINNHUB_KEY_PATH = ROOT / ".secret" / "finnhub-key.md"


class FinnhubQuoteProvider(QuoteProvider):
    """Finnhub API 行情 Provider

    使用 Finnhub API https://finnhub.io/api/v1/quote
    需要 API key（从环境变量 FINNHUB_API_KEY 或 .secret/finnhub-key.md 读取）。
    支持美股和加密货币，返回 Quote 对象。
    免费档按每分钟约 60 次请求做客户端节流，错误按统一 Provider 异常分类抛出。

    加密货币 symbol 格式：
    - 完整格式：EXCHANGE:SYMBOL（如 BINANCE:BTCUSDT）
    - 简写格式：SYMBOL（如 BTCUSDT），自动添加 BINANCE: 前缀
    """

    @property
    def name(self) -> str:
        return "finnhub"

    @property
    def supported_markets(self) -> list[str]:
        return ["us", "crypto"]

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        min_request_interval: float = 1.05,
    ):
        if api_key:
            self.api_key = api_key
        else:
            env_key = os.environ.get("FINNHUB_API_KEY", "").strip()
            if env_key:
                self.api_key = env_key
            elif FINNHUB_KEY_PATH.exists():
                self.api_key = FINNHUB_KEY_PATH.read_text(encoding="utf-8").strip()
            else:
                self.api_key = ""
        self._min_request_interval = max(0.0, min_request_interval)
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
        """根据市场类型构建 Finnhub symbol。"""
        code = instrument.code.strip().upper()
        market = (instrument.market or "us").lower()

        if market == "crypto":
            # 如果已包含交易所前缀（如 BINANCE:BTCUSDT），直接使用
            if ":" in code:
                return code
            # 否则默认使用 BINANCE 前缀
            return f"BINANCE:{code}"

        # 美股默认直接使用 code
        return code

    def _fetch_sync(self, symbol: str) -> Optional[dict]:
        """同步请求 Finnhub quote 接口，返回 JSON 字典。"""
        if not self.api_key:
            raise ProviderConfigError("Finnhub API key 未配置", source=self.name)
        self._throttle()
        params = urllib.parse.urlencode({"symbol": symbol, "token": self.api_key})
        url = f"https://finnhub.io/api/v1/quote?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            if not text.strip():
                raise ProviderDataError("Finnhub 返回空响应", source=self.name)
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ProviderDataError(
                    "Finnhub 返回无效 JSON",
                    source=self.name,
                    detail=str(exc),
                ) from exc
            if "error" in data:
                message = str(data["error"])
                lowered = message.lower()
                if "api key" in lowered or "forbidden" in lowered:
                    raise ProviderAuthError(message, source=self.name)
                if "limit" in lowered:
                    raise ProviderRateLimitError(message, source=self.name)
                raise ProviderDataError(message, source=self.name)
            return data
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise ProviderAuthError(
                    f"Finnhub 鉴权失败: HTTP {exc.code}",
                    source=self.name,
                ) from exc
            if exc.code == 429:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                raise ProviderRateLimitError(
                    "Finnhub 请求限流: HTTP 429",
                    source=self.name,
                    retry_after=int(retry_after) if retry_after and retry_after.isdigit() else None,
                ) from exc
            if exc.code in {408, 504}:
                raise ProviderTimeoutError(
                    f"Finnhub 请求超时: HTTP {exc.code}",
                    source=self.name,
                ) from exc
            raise ProviderNetworkError(
                f"Finnhub HTTP 错误: {exc.code}",
                source=self.name,
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ProviderTimeoutError(
                f"Finnhub 请求超时: {exc}",
                source=self.name,
            ) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise ProviderTimeoutError(
                    f"Finnhub 请求超时: {exc.reason}",
                    source=self.name,
                ) from exc
            raise ProviderNetworkError(
                f"Finnhub 网络错误: {exc.reason}",
                source=self.name,
            ) from exc

    def _data_to_quote(self, data: dict, instrument: Instrument) -> Optional[Quote]:
        """将 Finnhub 返回数据转换为 Quote。"""
        price = data.get("c")
        if price is None:
            return None

        as_of = None
        raw_timestamp = data.get("t")
        if raw_timestamp not in (None, "", "-", 0, "0"):
            try:
                timestamp = float(raw_timestamp)
                if timestamp > 0:
                    as_of = datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
            except (ValueError, TypeError, OverflowError, OSError):
                pass
        return Quote(
            instrument=instrument,
            price=float(price) if price is not None else None,
            change=float(data["d"]) if data.get("d") is not None else None,
            pct_change=float(data["dp"]) if data.get("dp") is not None else None,
            volume_lot=None,
            amount_10k=None,
            open_price=float(data["o"]) if data.get("o") is not None else None,
            high=float(data["h"]) if data.get("h") is not None else None,
            low=float(data["l"]) if data.get("l") is not None else None,
            prev_close=float(data["pc"]) if data.get("pc") is not None else None,
            source=self.name,
            as_of=as_of,
        )

    async def fetch(self, instrument: Instrument) -> Optional[Quote]:
        """获取单只标的行情。"""
        symbol = self._build_symbol(instrument)
        data = await asyncio.to_thread(self._fetch_sync, symbol)
        if data is None:
            return None
        return self._data_to_quote(data, instrument)

    async def fetch_batch(self, instruments: list[Instrument]) -> list[Quote]:
        """批量获取行情（逐个 fetch，避免触发 API 限制）。"""
        if not instruments:
            return []
        quotes: list[Quote] = []
        for instrument in instruments:
            quote = await self.fetch(instrument)
            if quote is not None:
                quotes.append(quote)
        return quotes
