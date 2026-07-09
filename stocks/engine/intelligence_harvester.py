"""Intelligence harvester: collect news, macro data and quotes for global_intelligence_watch.

Sources used:
- GNews API (primary)
- Google News RSS (fallback)
- Finnhub market news (general / commodity / crypto)
- Finnhub quote for US ETFs and equities
- Binance for BTCUSDT
- FRED for treasury yields (cached with 4h TTL)

All network calls are rate-limited and wrapped with structured error handling.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol

from stocks.domain.models import Instrument
from stocks.engine.macro_data import FredMacroProvider
from stocks.engine.news_sources import RSSNewsProvider
from stocks.logging_utils import get_logger
from stocks.providers.finnhub_quote import FinnhubQuoteProvider

logger = get_logger("intelligence_harvester")

GNEWS_KEY_PATHS = (
    Path(__file__).resolve().parents[2] / ".secret" / "gnews-key.md",
    Path(__file__).resolve().parents[2] / ".secret" / "gnews_api_key.md",
)

DEFAULT_KEYWORDS = [
    "VIX volatility",
    "crude oil price",
    "US Treasury yield",
    "gold price",
    "Bitcoin BTC",
    "US Dollar Index",
    "China stock market",
    "Federal Reserve",
]

_ETF_SYMBOLS = [
    ("SPY", "SPDR S&P 500 ETF", "us"),
    ("QQQ", "Invesco QQQ ETF", "us"),
    ("IWM", "iShares Russell 2000 ETF", "us"),
    ("VIXY", "ProShares VIX Short-Term Futures ETF", "us"),
    ("GLD", "SPDR Gold Shares", "us"),
    ("USO", "United States Oil Fund", "us"),
    ("UUP", "Invesco DB US Dollar Index Bullish Fund", "us"),
]

_WATCHLIST_DEFAULT = [
    ("XLE", "Energy Select Sector SPDR", "us"),
    ("NVDA", "NVIDIA", "us"),
    ("ITA", "iShares U.S. Aerospace & Defense ETF", "us"),
    ("NEM", "Newmont", "us"),
    ("SGOV", "iShares 0-3 Month Treasury Bond ETF", "us"),
]


class NewsSource(Protocol):
    async def fetch(self, max_items: int = 10) -> list[dict]: ...


@dataclass(frozen=True)
class HarvestResult:
    """Structured result of one intelligence harvest run."""

    collected_at: datetime
    articles: list[dict]
    macro: dict
    quotes: dict
    source_status: dict
    data_quality: dict
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "collected_at": self.collected_at.isoformat(),
            "articles": self.articles,
            "macro": self.macro,
            "quotes": self.quotes,
            "source_status": self.source_status,
            "data_quality": self.data_quality,
            "metadata": self.metadata,
        }


class IntelligenceHarvester:
    """Harvest global market intelligence from multiple sources.

    Args:
        gnews_api_key: Optional GNews API key. If not provided, reads from
            .secret/gnews-key.md or GNEWS_API_KEY env.
        finnhub_client: Optional FinnhubQuoteProvider instance.
        keywords: List of search keywords. Defaults to global macro topics.
        max_items_per_source: Max articles per source per keyword.
        fred_cache_dir: Directory for FRED cache.
    """

    def __init__(
        self,
        *,
        gnews_api_key: Optional[str] = None,
        finnhub_client: Optional[FinnhubQuoteProvider] = None,
        keywords: Optional[list[str]] = None,
        max_items_per_source: int = 10,
        fred_cache_dir: Optional[Path] = None,
        watchlist: Optional[list[tuple[str, str, str]]] = None,
    ):
        self._gnews_api_key = gnews_api_key or _load_gnews_key()
        self._finnhub = finnhub_client
        self._keywords = keywords or DEFAULT_KEYWORDS
        self._max_items_per_source = max(1, max_items_per_source)
        self._fred = FredMacroProvider(cache_dir=fred_cache_dir, cache_ttl=14400)
        self._watchlist = watchlist or _WATCHLIST_DEFAULT

    async def harvest(self) -> HarvestResult:
        collected_at = datetime.now(timezone.utc)
        articles: list[dict] = []
        source_status: dict = {}

        # News sources in parallel
        news_tasks = {
            "gnews": self._fetch_gnews_all(),
            "google_rss": self._fetch_google_rss_all(),
            "finnhub_market": self._fetch_finnhub_market_news(),
        }
        for name, awaitable in news_tasks.items():
            try:
                items = await awaitable
                source_status[name] = {"status": "ok", "count": len(items)}
                articles.extend(items)
            except Exception as exc:
                logger.warning(f"Intelligence source {name} failed: {exc}")
                source_status[name] = {"status": "error", "error": str(exc), "count": 0}

        # Deduplicate by URL
        seen_urls: set[str] = set()
        unique_articles: list[dict] = []
        for article in articles:
            url = article.get("url") or ""
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            unique_articles.append(article)
        unique_articles.sort(
            key=lambda a: _parse_iso(a.get("published_at", "")).timestamp(),
            reverse=True,
        )

        # Macro and quotes in parallel
        macro_task = self._fetch_macro()
        quotes_task = self._fetch_quotes()
        macro, quotes = await asyncio.gather(macro_task, quotes_task, return_exceptions=True)
        if isinstance(macro, Exception):
            logger.warning(f"Macro fetch failed: {macro}")
            macro = {"error": str(macro)}
        if isinstance(quotes, Exception):
            logger.warning(f"Quotes fetch failed: {quotes}")
            quotes = {"error": str(quotes)}

        data_quality = self._build_data_quality(source_status, macro, quotes, len(unique_articles))
        return HarvestResult(
            collected_at=collected_at,
            articles=unique_articles[:120],
            macro=macro,
            quotes=quotes,
            source_status=source_status,
            data_quality=data_quality,
            metadata={
                "keywords": self._keywords,
                "watchlist": [s[0] for s in self._watchlist],
            },
        )

    # ------------------------------------------------------------------
    # News sources
    # ------------------------------------------------------------------
    async def _fetch_gnews_all(self) -> list[dict]:
        if not self._gnews_api_key:
            return []
        per_keyword = max(1, min(self._max_items_per_source, 15))
        tasks = [self._fetch_gnews_keyword(keyword, per_keyword) for keyword in self._keywords]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        items: list[dict] = []
        for result in results:
            if isinstance(result, list):
                items.extend(result)
            elif isinstance(result, Exception):
                logger.warning(f"GNews keyword failed: {result}")
        return items

    def _fetch_gnews_keyword_sync(self, keyword: str, max_items: int) -> list[dict]:
        query = urllib.parse.quote(keyword)
        url = (
            f"https://gnews.io/api/v4/search?"
            f"q={query}&lang=en&max={max_items}&apikey={self._gnews_api_key}"
        )
        request = urllib.request.Request(url, headers={"User-Agent": "stocks-claw/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise RuntimeError("GNews rate limit exceeded") from exc
            raise RuntimeError(f"GNews HTTP {exc.code}: {exc.reason}") from exc
        except Exception as exc:
            raise RuntimeError(f"GNews request failed: {exc}") from exc

        articles = payload.get("articles") or []
        return [
            {
                "title": str(a.get("title", "")),
                "url": str(a.get("url", "")),
                "source_name": str(a.get("source", {}).get("name", "GNews")),
                "source_type": "gnews",
                "published_at": _rfc3339_to_iso(a.get("publishedAt", "")),
                "summary": str(a.get("description", "")),
                "language": "en",
                "tags": [keyword],
                "scope": "general",
            }
            for a in articles
        ]

    async def _fetch_gnews_keyword(self, keyword: str, max_items: int) -> list[dict]:
        return await asyncio.to_thread(self._fetch_gnews_keyword_sync, keyword, max_items)

    async def _fetch_google_rss_all(self) -> list[dict]:
        per_keyword = max(1, min(self._max_items_per_source, 10))
        tasks = [self._fetch_google_rss_keyword(keyword, per_keyword) for keyword in self._keywords]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        items: list[dict] = []
        for result in results:
            if isinstance(result, list):
                items.extend(result)
            elif isinstance(result, Exception):
                logger.warning(f"Google RSS keyword failed: {result}")
        return items

    def _fetch_google_rss_keyword_sync(self, keyword: str, max_items: int) -> list[dict]:
        query = urllib.parse.quote(keyword)
        url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
        provider = RSSNewsProvider(url, source_name=f"Google News:{keyword}", language="en")
        items = provider._fetch_sync()
        return [
            {
                "title": item.title,
                "url": item.url,
                "source_name": item.source_name,
                "source_type": "rss",
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "summary": item.summary,
                "language": item.language,
                "tags": [keyword],
                "scope": item.scope,
            }
            for item in items[:max_items]
        ]

    async def _fetch_google_rss_keyword(self, keyword: str, max_items: int) -> list[dict]:
        return await asyncio.to_thread(self._fetch_google_rss_keyword_sync, keyword, max_items)

    async def _fetch_finnhub_market_news(self) -> list[dict]:
        if self._finnhub is None:
            return []
        items: list[dict] = []
        for category in ("general", "commodity", "crypto"):
            try:
                data = await asyncio.to_thread(
                    self._finnhub.request_json, "news", {"category": category}
                )
                if not isinstance(data, list):
                    continue
                for item in data[: self._max_items_per_source]:
                    items.append(
                        {
                            "title": str(item.get("headline", "")),
                            "url": str(item.get("url", "")),
                            "source_name": str(item.get("source", "Finnhub")),
                            "source_type": "finnhub_market",
                            "published_at": _unix_to_iso(item.get("datetime", 0)),
                            "summary": str(item.get("summary", "")),
                            "language": "en",
                            "tags": [category],
                            "scope": "general",
                        }
                    )
            except Exception as exc:
                logger.warning(f"Finnhub {category} news failed: {exc}")
        return items

    # ------------------------------------------------------------------
    # Macro / quotes
    # ------------------------------------------------------------------
    async def _fetch_macro(self) -> dict:
        from stocks.engine.macro_data import YahooFinanceMacroProvider
        yahoo = YahooFinanceMacroProvider()
        snapshot = await self._fred.fetch()
        yahoo_snapshot = await yahoo.fetch()
        return {
            "vix": snapshot.vix,
            "us_10y_yield": snapshot.us_10y_yield,
            "us_2y_yield": None,  # FRED free CSV does not include 2y; design accepts None
            "dxy": snapshot.dxy,
            "usd_cny": snapshot.usd_cny,
            "crude_oil": snapshot.crude_oil,
            "gold": yahoo_snapshot.gold,
            "timestamp": snapshot.timestamp,
            "source": snapshot.source,
            "errors": snapshot.errors,
            "field_sources": {**snapshot.field_sources, **yahoo_snapshot.field_sources},
            "official_stats": snapshot.official_stats,
        }

    async def _fetch_quotes(self) -> dict:
        quotes: dict = {}
        if self._finnhub is not None:
            for code, name, market in _ETF_SYMBOLS + self._watchlist:
                try:
                    instrument = Instrument(code=code, name=name, market=market)
                    quote = await self._finnhub.fetch(instrument)
                    if quote is not None:
                        quotes[code] = quote.to_dict()
                except Exception as exc:
                    logger.warning(f"Quote fetch failed for {code}: {exc}")
                    quotes[code] = {"error": str(exc)}
        # Bitcoin from Binance
        try:
            btc = await self._fetch_binance_btc()
            if btc is not None:
                quotes["BTCUSDT"] = btc
        except Exception as exc:
            logger.warning(f"Binance BTC fetch failed: {exc}")
            quotes["BTCUSDT"] = {"error": str(exc)}
        return quotes

    async def _fetch_binance_btc(self) -> Optional[dict]:
        query = urllib.parse.urlencode({"symbol": "BTCUSDT"})
        request = urllib.request.Request(
            f"https://api.binance.com/api/v3/ticker/24hr?{query}",
            headers={"User-Agent": "stocks-claw/1.0"},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {
            "instrument": {"code": "BTCUSDT", "name": "Bitcoin", "market": "crypto"},
            "price": float(payload.get("lastPrice", 0)) or None,
            "pct_change": float(payload.get("priceChangePercent", 0)) or None,
            "source": "binance",
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # Data quality
    # ------------------------------------------------------------------
    def _build_data_quality(
        self,
        source_status: dict,
        macro: dict,
        quotes: dict,
        article_count: int,
    ) -> dict:
        ok_sources = sum(1 for s in source_status.values() if s.get("status") == "ok")
        errors = []
        if ok_sources == 0:
            errors.append("All news sources failed")
        if not macro.get("field_sources"):
            errors.append("Macro data unavailable")
        if not quotes:
            errors.append("Quotes unavailable")
        if article_count < 5:
            errors.append("Very few articles collected")
        return {
            "status": "ok" if not errors else "degraded",
            "errors": errors,
            "source_ok_count": ok_sources,
            "article_count": article_count,
            "macro_fields": list(macro.get("field_sources", {}).keys()),
            "quote_count": len(quotes),
        }


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _load_gnews_key() -> Optional[str]:
    env = os.environ.get("GNEWS_API_KEY", "").strip()
    if env:
        return env
    for path in GNEWS_KEY_PATHS:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    return None


def _rfc3339_to_iso(value: str) -> Optional[str]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def _unix_to_iso(value: int) -> Optional[str]:
    try:
        ts = int(value)
        if ts <= 0:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (ValueError, TypeError):
        return None


def _parse_iso(value: str) -> datetime:
    if not value:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
