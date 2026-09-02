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
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional, Protocol

from stocks.domain.models import Instrument, UpcomingEvent
from stocks.logging_utils import get_logger
from stocks.providers.finnhub_quote import FinnhubQuoteProvider

logger = get_logger("event_calendar")

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


def _parse_time_utc(value: Optional[str]) -> Optional[time]:
    """解析配置中的 UTC 时刻；只接受 HH:MM 或 HH:MM:SS。"""
    if not value:
        return None
    try:
        parsed = time.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


def _parse_scheduled_at(event: UpcomingEvent) -> Optional[datetime]:
    """返回可比较的 UTC 时点；不把 date-only 伪造成午夜。"""
    if event.scheduled_at:
        try:
            value = str(event.scheduled_at).replace("Z", "+00:00")
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    event_date = _parse_date(event.date)
    event_time = _parse_time_utc(event.time_utc)
    if event_date is None or event_time is None:
        return None
    return datetime.combine(event_date, event_time).astimezone(timezone.utc)


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
            event_date = str(item["date"])[:10]
            event_time = _parse_time_utc(item.get("time_utc"))
            scheduled_at = (
                datetime.combine(date.fromisoformat(event_date), event_time).isoformat()
                if event_time is not None
                else None
            )
            self._events.append(
                UpcomingEvent(
                    date=event_date,
                    name=str(item.get("name", "")) or "未命名事件",
                    event_type=event_type if event_type in _EVENT_TYPES else "other",
                    market=str(item.get("market", "global")),
                    time_utc=item.get("time_utc"),
                    scheduled_at=scheduled_at,
                    time_precision="datetime" if scheduled_at else "date",
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

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        cache_dir: Optional[Path] = None,
        cache_ttl: int = 12 * 60 * 60,
        client: Optional[FinnhubQuoteProvider] = None,
    ):
        self._client = client or FinnhubQuoteProvider(api_key=api_key)
        self._cache_dir = Path(cache_dir) if cache_dir else None
        self._cache_ttl = max(0, int(cache_ttl))
        self.last_errors: dict[str, str] = {}
        self.last_cache = {"hits": 0, "misses": 0}

    @property
    def name(self) -> str:
        return "finnhub_earnings"

    def _fetch_sync(self, symbol: str, start: date, end: date) -> list[dict]:
        data = self._client.request_json(
            "calendar/earnings",
            {
                "from": start.isoformat(),
                "to": end.isoformat(),
                "symbol": symbol,
            },
        )
        calendar = data.get("earningsCalendar", [])
        return calendar if isinstance(calendar, list) else []

    def _cache_path(self, symbol: str) -> Optional[Path]:
        if self._cache_dir is None:
            return None
        safe_symbol = re.sub(r"[^A-Z0-9_.-]", "_", symbol.upper())
        return self._cache_dir / f"finnhub_earnings_{safe_symbol}.json"

    def _load_cache(self, symbol: str, start: date, end: date) -> Optional[list[dict]]:
        path = self._cache_path(symbol)
        if path is None or not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            fetched_at = datetime.fromisoformat(payload["fetched_at"])
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
            if (
                age <= self._cache_ttl
                and date.fromisoformat(payload["window"]["start"]) <= start
                and date.fromisoformat(payload["window"]["end"]) >= end
                and isinstance(payload.get("events"), list)
            ):
                return payload["events"]
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        return None

    def _save_cache(
        self, symbol: str, start: date, end: date, rows: list[dict]
    ) -> None:
        path = self._cache_path(symbol)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "source": "finnhub_earnings",
                    "window": {"start": start.isoformat(), "end": end.isoformat()},
                    "events": rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)

    async def fetch(
        self,
        *,
        start: date,
        end: date,
        watchlist: list[Instrument],
    ) -> list[UpcomingEvent]:
        self.last_errors = {}
        self.last_cache = {"hits": 0, "misses": 0}
        us_instruments = [i for i in watchlist if i.market == "us"]
        events: list[UpcomingEvent] = []
        request_start = start - timedelta(days=6)
        for instrument in us_instruments:
            symbol = instrument.code.upper()
            rows = self._load_cache(symbol, request_start, end)
            if rows is not None:
                self.last_cache["hits"] += 1
            else:
                self.last_cache["misses"] += 1
            try:
                if rows is None:
                    rows = await asyncio.to_thread(
                        self._fetch_sync, symbol, request_start, end
                    )
                    self._save_cache(symbol, request_start, end, rows)
            except Exception as exc:
                logger.warning(
                    f"Finnhub earnings calendar failed for {instrument.code}: {exc}"
                )
                self.last_errors[symbol] = f"{type(exc).__name__}: {exc}"
                continue
            for row in rows:
                event_date = _parse_date(row.get("date"))
                if event_date is None or not (request_start <= event_date <= end):
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


class AkShareEarningsProvider:
    """A股财报披露日历 — 通过 AKShare (巨潮 cninfo) 查询 A股标的财报披露公告。

    API: ak.stock_zh_a_disclosure_report_cninfo(symbol, market, start_date, end_date)
    免费、无需 API key、活跃维护。对 watchlist 中 market == "a" 的标的查询近期披露，
    只保留标题命中财报类的公告(年报/中报/季报/业绩快报/业绩预告)，转成 earnings 事件。
    失败抛出异常由 EventCalendar 记录为质量错误，不静默。akshare 为惰性导入，
    未安装时该 provider 在 fetch 时显式报 not_configured，不阻断其它 provider。
    """

    # 财报类公告标题关键词(精确白名单, 报表期+报告组合, 避免宽泛"报告"误报)
    _EARNINGS_KEYWORDS = (
        "年度报告", "半年度财务报告", "半年度报告", "半年度业绩",
        "季度报告", "年度业绩快报", "业绩快报", "业绩预告",
    )
    # 明确非财报类公告(黑名单): 即使触发宽泛匹配也排除
    _NON_EARNINGS_KEYWORDS = (
        "董事会", "监事会", "股东大会", "独立董事", "审计委员会",
        "权益分派", "分红", "回购", "减持", "增持", "质押", "担保",
        "更正", "修订说明", "差错更正", "股票交易异常", "停牌", "复牌",
        "经营情况", "投资者关系", "调研", "章程", "制度", "细则",
        "募集资金", "募集说明书", "可转债", "新增对外投资", "收购", "重组",
        "法律意见", "审计报告意见", "内部控制", "解除限售", "业绩说明会",
    )

    def __init__(
        self,
        *,
        cache_dir: Optional[Path] = None,
        cache_ttl: int = 12 * 60 * 60,
        market: str = "沪深京",
        source_name: str = "akshare_earnings",
    ):
        self._cache_dir = Path(cache_dir) if cache_dir else None
        self._cache_ttl = max(0, int(cache_ttl))
        self._market = market
        self._source_name = source_name
        self.last_errors: dict[str, str] = {}
        self.last_cache = {"hits": 0, "misses": 0}

    @property
    def name(self) -> str:
        return self._source_name

    @staticmethod
    def _akshare_available() -> bool:
        try:
            import akshare  # noqa: F401
            return True
        except Exception:
            return False

    def _fetch_sync(self, symbol: str, start: date, end: date) -> list[dict]:
        """拉取某标的在窗口内的披露公告, 过滤财报类, 返回归一化 dict 列表。"""
        import akshare as ak

        raw = ak.stock_zh_a_disclosure_report_cninfo(
            symbol=symbol,
            market=self._market,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        if raw is None or getattr(raw, "empty", True):
            return []
        rows: list[dict] = []
        for _, r in raw.iterrows():
            title = str(r.get("公告标题") or "")
            date_s = str(r.get("公告时间") or "")
            date_v = _parse_date(date_s)
            if date_v is None or not (start <= date_v <= end):
                continue
            if not any(kw in title for kw in self._EARNINGS_KEYWORDS):
                # 未命中精确财报白名单 -> 非财报类, 排除
                continue
            if any(bk in title for bk in self._NON_EARNINGS_KEYWORDS):
                # 命中黑名单(如"董事会"+"年度报告"混合标题但实属其他公告) -> 排除
                continue
            rows.append(
                {
                    "date": date_v.isoformat(),
                    "name": f"{r.get('简称') or symbol} 财报披露",
                    "title": title,
                    "symbol": symbol,
                }
            )
        return rows

    def _cache_path(self, symbol: str) -> Optional[Path]:
        if self._cache_dir is None:
            return None
        safe = re.sub(r"[^A-Z0-9_.-]", "_", str(symbol).upper())
        return self._cache_dir / f"akshare_earnings_{safe}.json"

    def _load_cache(self, symbol: str, start: date, end: date) -> Optional[list[dict]]:
        path = self._cache_path(symbol)
        if path is None or not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            fetched_at = datetime.fromisoformat(payload["fetched_at"])
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
            if (
                age <= self._cache_ttl
                and date.fromisoformat(payload["window"]["start"]) <= start
                and date.fromisoformat(payload["window"]["end"]) >= end
                and isinstance(payload.get("events"), list)
            ):
                return payload["events"]
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        return None

    def _save_cache(
        self, symbol: str, start: date, end: date, rows: list[dict]
    ) -> None:
        path = self._cache_path(symbol)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    {
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "source": self._source_name,
                        "window": {"start": start.isoformat(), "end": end.isoformat()},
                        "events": rows,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError:
            pass

    async def fetch(
        self,
        *,
        start: date,
        end: date,
        watchlist: list[Instrument],
    ) -> list[UpcomingEvent]:
        self.last_errors = {}
        self.last_cache = {"hits": 0, "misses": 0}
        if not self._akshare_available():
            self.last_errors["akshare"] = "akshare 未安装，A股财报日历不可用"
            return []
        a_instruments = [i for i in watchlist if i.market == "a"]
        if not a_instruments:
            return []
        events: list[UpcomingEvent] = []
        # 取窗口内逐标的查询; 窗口略扩避免时区偏差
        request_start = start - timedelta(days=1)
        for instrument in a_instruments:
            symbol = str(instrument.code).upper()
            symbol6 = re.sub(r"\D", "", symbol)
            # cninfo 只接受 6 位数字 A股代码
            if len(symbol6) != 6:
                continue
            rows = self._load_cache(symbol6, request_start, end)
            if rows is not None:
                self.last_cache["hits"] += 1
            else:
                self.last_cache["misses"] += 1
            try:
                if rows is None:
                    rows = await asyncio.to_thread(
                        self._fetch_sync, symbol6, request_start, end
                    )
                    self._save_cache(symbol6, request_start, end, rows)
            except KeyError:
                # cninfo 查无此代码(如 ETF/基金/非上市标的) → 视为无财报披露,
                # 静默跳过, 不污染 last_errors(与真实网络错误区分)。
                continue
            except Exception as exc:
                logger.warning(
                    f"AkShare earnings calendar failed for {instrument.code}: {exc}"
                )
                self.last_errors[symbol6] = f"{type(exc).__name__}: {exc}"
                continue
            for row in rows:
                event_date = _parse_date(row.get("date"))
                if event_date is None or not (request_start <= event_date <= end):
                    continue
                events.append(
                    UpcomingEvent(
                        date=event_date.isoformat(),
                        name=row.get("name") or f"{instrument.name or instrument.code} 财报披露",
                        event_type="earnings",
                        market="a",
                        source=self._source_name,
                        affected_categories=(
                            [instrument.category] if instrument.category else []
                        ),
                        affected_symbols=[f"a:{instrument.code}"],
                        note=f"财报披露（{row.get('title') or '财报'[:40]}）",
                    )
                )
        return events


class EventCalendar:
    """组合事件日历 — 窗口过滤、days_until 计算、watchlist 敏感标的匹配。

    分窗 lookahead: 央行决议/宏观数据发布是长预期管理事件(FOMC 点阵图
    会议市场提前数周定价), 需要长窗; 财报只影响个股短期波动, 短窗即可。
    窗口长度来自 engine.yaml calendar.*_lookahead_days, 不硬编码业务值。
    """

    def __init__(
        self,
        providers: list[EventProvider],
        *,
        lookahead_days: int = 14,
        macro_lookahead_days: Optional[int] = None,
        earnings_lookahead_days: Optional[int] = None,
    ):
        self._providers = providers
        self.lookahead_days = max(1, int(lookahead_days))
        self.macro_lookahead_days = (
            max(1, int(macro_lookahead_days)) if macro_lookahead_days else self.lookahead_days
        )
        self.earnings_lookahead_days = (
            max(1, int(earnings_lookahead_days)) if earnings_lookahead_days else self.lookahead_days
        )

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
                "expired_count": 0,
                "cache": {"hits": 0, "misses": 0},
                "sources": {},
                "errors": {},
            }

        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current_utc = current.astimezone(timezone.utc)
        today = current.date()
        utc_today = current_utc.date()
        # 查询窗口取分窗最大值(向 UTC 日期前扩一天，避免本地已跨日但未来
        # UTC 时点仍落在前一日); 过滤在下方按事件类型分别执行。
        start = min(today, utc_today) - timedelta(days=1)
        end = max(today, utc_today) + timedelta(
            days=max(self.macro_lookahead_days, self.earnings_lookahead_days)
        )

        events: list[UpcomingEvent] = []
        errors: dict[str, str] = {}
        cache_quality = {"hits": 0, "misses": 0}
        successful_providers = 0
        for provider in self._providers:
            try:
                events.extend(
                    await provider.fetch(start=start, end=end, watchlist=watchlist)
                )
                successful_providers += 1
            except Exception as exc:
                errors[provider.name] = f"{type(exc).__name__}: {exc}"
                logger.warning(f"Event provider {provider.name} failed: {exc}")
            provider_errors = getattr(provider, "last_errors", {})
            for symbol, error in provider_errors.items():
                errors[f"{provider.name}:{symbol}"] = error
            provider_cache = getattr(provider, "last_cache", {})
            cache_quality["hits"] += int(provider_cache.get("hits", 0))
            cache_quality["misses"] += int(provider_cache.get("misses", 0))

        enriched: list[UpcomingEvent] = []
        expired_count = 0
        for event in events:
            event_date = _parse_date(event.date)
            # 分窗过滤: 财报用短窗, 央行/宏观发布用长窗
            if event_date is not None:
                per_type_window = (
                    self.earnings_lookahead_days
                    if event.event_type == "earnings"
                    else self.macro_lookahead_days
                )
                if (event_date - today).days > per_type_window:
                    continue
            scheduled_at = _parse_scheduled_at(event)
            if scheduled_at is not None:
                if scheduled_at <= current_utc:
                    expired_count += 1
                    continue
                local_event_date = scheduled_at.astimezone(current.tzinfo).date()
                days_until = (local_event_date - today).days
                status = (
                    "imminent"
                    if scheduled_at - current_utc <= timedelta(hours=24)
                    else "scheduled"
                )
                precision = "datetime"
                scheduled_iso = scheduled_at.isoformat()
            else:
                # date-only 没有足够信息判断日内先后，只在调用方本地日期过后失效。
                if event_date is None or event_date < today:
                    expired_count += 1
                    continue
                days_until = (event_date - today).days
                status = "imminent" if days_until <= 1 else "scheduled"
                precision = "date"
                scheduled_iso = None
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
                    scheduled_at=scheduled_iso,
                    time_precision=precision,
                    status=status,
                    source=event.source,
                    affected_categories=list(event.affected_categories),
                    affected_symbols=affected_symbols,
                    days_until=days_until,
                    note=event.note,
                )
            )

        enriched.sort(
            key=lambda e: (e.scheduled_at or f"{e.date}T23:59:59+00:00", e.name)
        )

        sources: dict[str, int] = {}
        for event in enriched:
            sources[event.source] = sources.get(event.source, 0) + 1

        if successful_providers and not errors:
            status = "ok"
        elif successful_providers and errors:
            status = "partial"
        else:
            status = "missing"

        quality = {
            "status": status,
            "source": "EventCalendar",
            "lookahead_days": self.lookahead_days,
            "window_end": end.isoformat(),
            "event_count": len(enriched),
            "expired_count": expired_count,
            "cache": cache_quality,
            "sources": sources,
            "errors": errors,
        }
        return enriched, quality
