from __future__ import annotations

import asyncio
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from stocks.domain.models import Instrument, Quote
from stocks.providers.base import QuoteProvider


ROOT = Path(__file__).resolve().parents[2]
FINNHUB_KEY_PATH = ROOT / ".secret" / "finnhub-key.md"


class FinnhubQuoteProvider(QuoteProvider):
    """Finnhub API 行情 Provider

    使用 Finnhub API https://finnhub.io/api/v1/quote
    需要 API key（从环境变量 FINNHUB_API_KEY 或 .secret/finnhub-key.md 读取）。
    支持美股和加密货币，返回 Quote 对象。
    网络异常、API 限制或解析失败时返回 None / 空列表。

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

    def __init__(self, api_key: Optional[str] = None):
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
            return None
        params = urllib.parse.urlencode({"symbol": symbol, "token": self.api_key})
        url = f"https://finnhub.io/api/v1/quote?{params}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            if not text.strip():
                return None
            data = json.loads(text)
            # Finnhub 错误响应可能包含 error 字段
            if "error" in data:
                return None
            return data
        except Exception:
            return None

    def _data_to_quote(self, data: dict, instrument: Instrument) -> Optional[Quote]:
        """将 Finnhub 返回数据转换为 Quote。"""
        price = data.get("c")
        if price is None:
            return None
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
