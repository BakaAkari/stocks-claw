"""Watchlist 范围的一手公司公告 Provider。"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable

from stocks.domain.models import Instrument, NewsItem


class SecEdgarFilingsProvider:
    """SEC submissions API；仅查询配置了 CIK 的 watchlist 美股。"""

    name = "sec_edgar"
    _FORMS = {"8-K", "10-Q", "10-K"}

    def __init__(
        self,
        instruments_getter: Callable[[], list[Instrument]],
        cik_by_symbol: dict[str, str],
        *,
        user_agent: str = "",
        min_request_interval: float = 0.12,
    ):
        self._config_error = (
            None if "@" in user_agent else "SEC_USER_AGENT 未配置可联系邮箱"
        )
        self._instruments_getter = instruments_getter
        self._cik_by_symbol = {
            symbol.upper(): str(cik).zfill(10)
            for symbol, cik in cik_by_symbol.items()
        }
        self._headers = {
            "User-Agent": user_agent,
        }
        self._min_request_interval = max(0.1, min_request_interval)
        self._lock = threading.Lock()
        self._last_request_at = 0.0
        self.last_errors: dict[str, str] = {}

    def _throttle(self) -> None:
        with self._lock:
            wait_for = self._min_request_interval - (
                time.monotonic() - self._last_request_at
            )
            if wait_for > 0:
                time.sleep(wait_for)
            self._last_request_at = time.monotonic()

    def _fetch_symbol_sync(
        self, instrument: Instrument, cik: str
    ) -> list[NewsItem]:
        if self._config_error:
            raise RuntimeError(self._config_error)
        self._throttle()
        request = urllib.request.Request(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers=self._headers,
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        recent = payload.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        filed_dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        documents = recent.get("primaryDocument", [])
        descriptions = recent.get("primaryDocDescription", [])
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=30)
        items: list[NewsItem] = []
        for index, form in enumerate(forms):
            if form not in self._FORMS:
                continue
            try:
                filed_date = datetime.strptime(
                    filed_dates[index], "%Y-%m-%d"
                ).replace(tzinfo=timezone.utc)
                accession = accessions[index]
                document = documents[index]
            except (IndexError, TypeError, ValueError):
                continue
            if filed_date.date() < cutoff:
                continue
            accession_path = accession.replace("-", "")
            cik_path = str(int(cik))
            url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik_path}/"
                f"{accession_path}/{document}"
            )
            description = descriptions[index] if index < len(descriptions) else ""
            items.append(
                NewsItem(
                    title=f"{instrument.name or instrument.code} {form} 公告",
                    url=url,
                    source_name="SEC EDGAR",
                    source_type="filing",
                    published_at=filed_date,
                    summary=description or f"{form} filing",
                    language="en",
                    tags=[instrument.code, form],
                    scope="holding",
                    raw_metadata={
                        "market": "us",
                        "symbol": instrument.code,
                        "form": form,
                        "accession_number": accession,
                    },
                )
            )
        return items

    async def fetch(self, max_items: int = 10) -> list[NewsItem]:
        self.last_errors = {}
        instruments = [
            instrument
            for instrument in self._instruments_getter()
            if instrument.market == "us"
            and instrument.code.upper() in self._cik_by_symbol
        ]
        items: list[NewsItem] = []
        for instrument in instruments:
            cik = self._cik_by_symbol[instrument.code.upper()]
            try:
                items.extend(
                    await asyncio.to_thread(
                        self._fetch_symbol_sync, instrument, cik
                    )
                )
            except Exception as exc:
                self.last_errors[instrument.code] = (
                    f"{type(exc).__name__}: {exc}"
                )
        items.sort(
            key=lambda item: item.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return items[:max_items]


class CninfoFilingsProvider:
    """巨潮资讯公告查询；仅查询 watchlist A 股/ETF 代码。"""

    name = "cninfo"

    def __init__(
        self,
        instruments_getter: Callable[[], list[Instrument]],
        org_id_by_symbol: dict[str, str] | None = None,
    ):
        self._instruments_getter = instruments_getter
        self._org_id_by_symbol = {
            str(symbol): str(org_id)
            for symbol, org_id in (org_id_by_symbol or {}).items()
        }
        self.last_errors: dict[str, str] = {}

    @staticmethod
    def _market_params(instrument: Instrument) -> tuple[str, str]:
        exchange = (instrument.exchange or "").lower()
        is_shanghai = exchange.startswith("sh") or instrument.code.startswith(
            ("5", "6", "9")
        )
        return ("sse", "sh") if is_shanghai else ("szse", "sz")

    def _fetch_symbol_sync(self, instrument: Instrument) -> list[NewsItem]:
        column, plate = self._market_params(instrument)
        org_id = self._org_id_by_symbol.get(instrument.code)
        stock = f"{instrument.code},{org_id}" if org_id else instrument.code
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=30)
        body = urllib.parse.urlencode(
            {
                "pageNum": 1,
                "pageSize": 30,
                "column": column,
                "tabName": "fulltext",
                "plate": plate,
                "stock": stock,
                "searchkey": "",
                "secid": "",
                "category": "",
                "trade": "",
                "seDate": f"{start.isoformat()}~{end.isoformat()}",
                "sortName": "",
                "sortType": "",
                "isHLtitle": "true",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            "https://www.cninfo.com.cn/new/hisAnnouncement/query",
            data=body,
            headers={
                "User-Agent": "Mozilla/5.0 (stocks-claw/1.0)",
                "Referer": "https://www.cninfo.com.cn/",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            },
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        items: list[NewsItem] = []
        for row in payload.get("announcements") or []:
            row_code = str(row.get("secCode") or "").strip()
            if row_code and row_code != instrument.code:
                continue
            raw_time = row.get("announcementTime")
            try:
                published_at = datetime.fromtimestamp(
                    float(raw_time) / 1000, tz=timezone.utc
                )
            except (TypeError, ValueError, OverflowError, OSError):
                published_at = None
            adjunct_url = str(row.get("adjunctUrl") or "").lstrip("/")
            if not adjunct_url:
                continue
            title = re.sub(r"<[^>]+>", "", str(row.get("announcementTitle") or ""))
            items.append(
                NewsItem(
                    title=title or f"{instrument.name or instrument.code} 公告",
                    url=f"https://static.cninfo.com.cn/{adjunct_url}",
                    source_name="巨潮资讯",
                    source_type="filing",
                    published_at=published_at,
                    summary=f"{row.get('secName') or instrument.name} 官方公告",
                    language="zh",
                    tags=[instrument.code, "announcement"],
                    scope="holding",
                    raw_metadata={
                        "market": "a",
                        "symbol": instrument.code,
                        "announcement_id": row.get("announcementId"),
                    },
                )
            )
        return items

    async def fetch(self, max_items: int = 10) -> list[NewsItem]:
        self.last_errors = {}
        instruments = [
            instrument
            for instrument in self._instruments_getter()
            if instrument.market == "a"
        ]
        items: list[NewsItem] = []
        for instrument in instruments:
            try:
                items.extend(
                    await asyncio.to_thread(self._fetch_symbol_sync, instrument)
                )
            except Exception as exc:
                self.last_errors[instrument.code] = (
                    f"{type(exc).__name__}: {exc}"
                )
        items.sort(
            key=lambda item: item.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return items[:max_items]
