"""未来事件日历 — 为前瞻分析提供确定性催化剂。

原则（与 PLAN "先诚实,再博学" 一致）：
- 只收录"已官方公布的未来日程"这类事实（FOMC/CPI/非农的官方日历、财报日），
  不做任何预测；
- 数据缺失显式报 `missing` / `not_configured`，不静默装好；
- 事件只标注"哪些资产类别对它敏感"这一路径事实，方向判断留给 Agent。

组成：
- StaticEventCalendarProvider: 读取 `stocks/config/event_calendar.json` 的官方日程
- FinnhubEarningsCalendarProvider: Finnhub 财报日历（watchlist 美股标的）
- EventCalendar: 组合器，过滤 lookahead 窗口、计算 days_until、
  匹配 watchlist 敏感标的并汇总质量信息
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional, Protocol

from stocks.domain.models import Instrument, UpcomingEvent
from stocks.logging_utils import get_logger

logger = get_logger("event_calendar")

ROOT = Path(__file__).resolve().parents[2]
FINNHUB_KEY_PATH = ROOT / ".secret" / "finnhub-key.md"

_EVENT_TYPES = {"macro_release", "central_bank", "earnings", "other"}


class EventProvider(Protocol):
    """事件日历提供者接口。"""

    @property
    def name(self) -> str: ...

    async def fetch(
        self,
        *,
        start: date,
        end: date,
        watchlist: list[Instrument],
    ) -> list[UpcomingEvent]: ...


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


class StaticEventCalendarProvider:
    """从配置文件读取官方已公布的事件日程。"""

    def __init__(self, config: Optional[dict]):
        self._events = []
        for item in (config or {}).get("events", []):
            if not isinstance(item, dict):
                continue
            if _parse_date(item.get("date")) is None:
                continue
            event_type = item.get("event_type", "other")
            self._events.append(
                UpcomingEvent(
                    date=str(item["date"])[:10],
                    name=str(item.get("name", "")) or "未命名事件",
                    event_type=event_type if event_type in _EVENT_TYPES else "other",
                    market=str(item.get("market", "global")),
                    time_utc=item.get("time_utc"),
                    source="static_config",
                    affected_categories=[
                        str(c) for c in item.get("affected_categories", [])
                    ],
                    note=str(item.get("note", "")),
                )
            )

    @property
    def name(self) -> str:
        return "static_config"

    async def fetch(
        self,
        *,
        start: date,
        end: date,
        watchlist: list[Instrument],
    ) -> list[UpcomingEvent]:
        result = []
        for event in self._events:
            event_date = _parse_date(event.date)
            if event_date is None or not (start <= event_date <= end):
                continue
            result.append(event)
        return result


class FinnhubEarningsCalendarProvider:
    """Finnhub 财报日历 — 只查询 watchlist 中的美股标的。

    API: https://finnhub.io/api/v1/calendar/earnings?from=&to=&symbol=
    免费档可用；失败抛出异常由 EventCalendar 记录为质量错误，不静默。
    """

    def __init__(self, api_key: Optional[str] = None, timeout: float = 15.0):
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
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "finnhub_earnings"

    def _fetch_sync(self, symbol: str, start: date, end: date) -> list[dict]:
        params = urllib.parse.urlencode(
            {
                "from": start.isoformat(),
                "to": end.isoformat(),
                "symbol": symbol,
                "token": self.api_key,
            }
        )
        url = f"https://finnhub.io/api/v1/calendar/earnings?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        calendar = data.get("earningsCalendar", [])
        return calendar if isinstance(calendar, list) else []

    async def fetch(
        self,
        *,
        start: date,
        end: date,
        watchlist: list[Instrument],
    ) -> list[UpcomingEvent]:
        if not self.api_key:
            raise RuntimeError("Finnhub API key 未配置")
        us_instruments = [i for i in watchlist if i.market == "us"]
        events: list[UpcomingEvent] = []
        for instrument in us_instruments:
            try:
                rows = await asyncio.to_thread(
                    self._fetch_sync, instrument.code.upper(), start, end
                )
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                # 单标的失败记录日志后继续，整体失败语义由 EventCalendar 判定
                logger.warning(
                    f"Finnhub earnings calendar failed for {instrument.code}: {exc}"
                )
                raise RuntimeError(
                    f"finnhub earnings fetch failed for {instrument.code}: {exc}"
                ) from exc
            for row in rows:
                event_date = _parse_date(row.get("date"))
                if event_date is None or not (start <= event_date <= end):
                    continue
                events.append(
                    UpcomingEvent(
                        date=event_date.isoformat(),
                        name=f"{instrument.name or instrument.code} 财报"
                        + (f"（{row.get('hour')}）" if row.get("hour") else ""),
                        event_type="earnings",
                        market="us",
                        source="finnhub_earnings",
                        affected_categories=(
                            [instrument.category] if instrument.category else []
                        ),
                        affected_symbols=[f"us:{instrument.code}"],
                        note="财报公布日，个股波动与同板块联动风险上升",
                    )
                )
        return events


class EventCalendar:
    """组合事件日历 — 窗口过滤、days_until 计算、watchlist 敏感标的匹配。"""

    def __init__(
        self,
        providers: list[EventProvider],
        *,
        lookahead_days: int = 14,
    ):
        self._providers = providers
        self.lookahead_days = max(1, int(lookahead_days))

    async def fetch(
        self,
        *,
        now: Optional[datetime] = None,
        watchlist: Optional[list[Instrument]] = None,
    ) -> tuple[list[UpcomingEvent], dict]:
        """返回 (窗口内事件按日期排序, 质量摘要)。

        质量摘要字段：
        - status: ok / partial / missing / not_configured
        - lookahead_days, event_count, sources, errors
        """
        watchlist = watchlist or []
        if not self._providers:
            return [], {
                "status": "not_configured",
                "source": "none",
                "lookahead_days": self.lookahead_days,
                "event_count": 0,
                "sources": {},
                "errors": {},
            }

        current = now or datetime.now(timezone.utc)
        today = current.date()
        end = date.fromordinal(today.toordinal() + self.lookahead_days)

        events: list[UpcomingEvent] = []
        errors: dict[str, str] = {}
        for provider in self._providers:
            try:
                events.extend(
                    await provider.fetch(start=today, end=end, watchlist=watchlist)
                )
            except Exception as exc:
                errors[provider.name] = f"{type(exc).__name__}: {exc}"
                logger.warning(f"Event provider {provider.name} failed: {exc}")

        enriched: list[UpcomingEvent] = []
        for event in sorted(events, key=lambda e: (e.date, e.name)):
            event_date = _parse_date(event.date)
            days_until = (
                (event_date.toordinal() - today.toordinal())
                if event_date is not None
                else None
            )
            affected_symbols = list(event.affected_symbols)
            if event.affected_categories:
                wanted = {c.strip().lower() for c in event.affected_categories}
                for instrument in watchlist:
                    category = (instrument.category or "").strip().lower()
                    key = f"{instrument.market}:{instrument.code}"
                    if category in wanted and key not in affected_symbols:
                        affected_symbols.append(key)
            enriched.append(
                UpcomingEvent(
                    date=event.date,
                    name=event.name,
                    event_type=event.event_type,
                    market=event.market,
                    time_utc=event.time_utc,
                    source=event.source,
                    affected_categories=list(event.affected_categories),
                    affected_symbols=affected_symbols,
                    days_until=days_until,
                    note=event.note,
                )
            )

        sources: dict[str, int] = {}
        for event in enriched:
            sources[event.source] = sources.get(event.source, 0) + 1

        if enriched and not errors:
            status = "ok"
        elif enriched and errors:
            status = "partial"
        else:
            status = "missing"

        quality = {
            "status": status,
            "source": "EventCalendar",
            "lookahead_days": self.lookahead_days,
            "window_end": end.isoformat(),
            "event_count": len(enriched),
            "sources": sources,
            "errors": errors,
        }
        return enriched, quality
