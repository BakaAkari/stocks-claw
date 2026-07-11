"""天天基金净值 Provider。

使用天天基金 JSONP 接口获取公募基金最新确认净值和盘中估算净值。
接口: https://fundgz.1234567.com.cn/js/{fund_code}.js
免费、无认证、限单只查询。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class FundNav:
    """基金净值快照"""

    fund_code: str
    fund_name: str
    confirmed_nav: float       # 最新确认净值 (dwjz)
    confirmed_date: str         # 净值日期 (jzrq)
    estimated_nav: Optional[float] = None   # 盘中估算净值 (gsz)
    estimated_change_pct: Optional[float] = None  # 估算涨跌幅 (gszzl)
    source: str = "tiantian"


class FundNavProvider:
    """天天基金净值拉取器。

    单次请求一只基金，带节流（2s 间隔）。
    结果缓存在内存中，同一次 build_context 内不重复请求。
    """

    def __init__(self, min_interval: float = 2.0):
        self._min_interval = max(0.5, min_interval)
        self._last_request_at = 0.0
        self._cache: dict[str, Optional[FundNav]] = {}

    def clear_cache(self) -> None:
        self._cache.clear()

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        wait = self._min_interval - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _fetch_sync(self, fund_code: str) -> Optional[FundNav]:
        """同步获取单只基金净值。"""
        if fund_code in self._cache:
            return self._cache[fund_code]

        self._throttle()
        url = f"https://fundgz.1234567.com.cn/js/{fund_code}.js"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://fund.eastmoney.com/",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                text = resp.read().decode("utf-8", errors="replace")
        except Exception as exc:
            logger.warning(f"FundNav fetch failed for {fund_code}: {exc}")
            self._cache[fund_code] = None
            return None

        match = re.search(r"jsonpgz\((.*)\)", text)
        if not match:
            logger.warning(f"FundNav parse failed for {fund_code}: no jsonpgz wrapper")
            self._cache[fund_code] = None
            return None

        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            logger.warning(f"FundNav JSON parse failed for {fund_code}")
            self._cache[fund_code] = None
            return None

        nav = FundNav(
            fund_code=str(data.get("fundcode", fund_code)),
            fund_name=str(data.get("name", "")),
            confirmed_nav=float(data["dwjz"]),
            confirmed_date=str(data.get("jzrq", "")),
            estimated_nav=float(data["gsz"]) if data.get("gsz") else None,
            estimated_change_pct=float(data["gszzl"]) if data.get("gszzl") else None,
        )
        self._cache[fund_code] = nav
        return nav

    async def fetch(self, fund_code: str) -> Optional[FundNav]:
        return await asyncio.to_thread(self._fetch_sync, fund_code)

    async def fetch_batch(self, fund_codes: list[str]) -> dict[str, Optional[FundNav]]:
        results: dict[str, Optional[FundNav]] = {}
        for code in fund_codes:
            results[code] = await self.fetch(code)
        return results
