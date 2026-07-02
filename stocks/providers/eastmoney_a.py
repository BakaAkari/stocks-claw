from __future__ import annotations

import asyncio
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from stocks.domain.models import Instrument, Quote
from stocks.providers.base import QuoteProvider


class EastmoneyAQuoteProvider(QuoteProvider):
    """东方财富接口 A 股行情 Provider

    使用东方财富接口 https://push2.eastmoney.com/api/qt/ulist.np/get
    支持 A 股，返回 Quote 对象。
    网络异常或解析失败时返回 None / 空列表。
    """

    @property
    def name(self) -> str:
        return "eastmoney_a"

    @property
    def supported_markets(self) -> list[str]:
        return ["a"]

    def _secid(self, instrument: Instrument) -> str:
        """生成东方财富 secid。"""
        exchange = (instrument.exchange or "").lower()
        code = instrument.code
        if exchange in ("sh", "sh_stock", "sh_a", "sh_index"):
            return f"1.{code}"
        if exchange in ("sz", "sz_stock", "sz_a", "sz_index"):
            return f"0.{code}"
        if code.startswith(("5", "6", "9")):
            return f"1.{code}"
        return f"0.{code}"

    def _fetch_sync(self, secids: list[str]) -> Optional[dict]:
        """同步请求东方财富接口，返回 JSON 字典。"""
        params = {
            "fltt": "2",
            "invt": "2",
            "fields": "f12,f14,f2,f3,f4,f5,f6,f15,f16,f17,f18,f124",
            "secids": ",".join(secids),
        }
        url = "https://push2.eastmoney.com/api/qt/ulist.np/get?" + urllib.parse.urlencode(params)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            if not text.strip():
                return None
            return json.loads(text)
        except Exception:
            return None

    def _row_to_quote(self, row: dict, instrument: Instrument) -> Quote:
        """将东方财富返回行转换为 Quote。"""

        def _float(val):
            if val in (None, "", "-"):
                return None
            try:
                return float(val)
            except (ValueError, TypeError):
                return None

        def _as_of(val) -> Optional[str]:
            if val in (None, "", "-", 0, "0"):
                return None
            try:
                timestamp = float(val)
                if timestamp <= 0:
                    return None
                return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
            except (ValueError, TypeError, OverflowError, OSError):
                return None

        return Quote(
            instrument=instrument,
            price=_float(row.get("f2")),
            change=_float(row.get("f4")),
            pct_change=_float(row.get("f3")),
            volume_lot=_float(row.get("f5")),
            amount_10k=_float(row.get("f6")),
            high=_float(row.get("f15")),
            low=_float(row.get("f16")),
            open_price=_float(row.get("f17")),
            prev_close=_float(row.get("f18")),
            source=self.name,
            as_of=_as_of(row.get("f124")),
        )

    async def fetch(self, instrument: Instrument) -> Optional[Quote]:
        """获取单只标的行情。"""
        payload = await asyncio.to_thread(self._fetch_sync, [self._secid(instrument)])
        if payload is None:
            return None
        rows = payload.get("data", {}).get("diff", [])
        if not rows:
            return None
        return self._row_to_quote(rows[0], instrument)

    async def fetch_batch(self, instruments: list[Instrument]) -> list[Quote]:
        """批量获取行情。"""
        if not instruments:
            return []
        instrument_map = {item.code: item for item in instruments}
        payload = await asyncio.to_thread(
            self._fetch_sync, [self._secid(item) for item in instruments]
        )
        if payload is None:
            return []
        rows = payload.get("data", {}).get("diff", [])
        quotes: list[Quote] = []
        for row in rows:
            code = str(row.get("f12", ""))
            instrument = instrument_map.get(code)
            if instrument is None:
                continue
            quotes.append(self._row_to_quote(row, instrument))
        return quotes
