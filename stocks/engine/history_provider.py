"""历史 K 线数据提供者 — 启动时批量回填历史数据到 HistoryCache

为技术指标计算提供足够的历史数据底座，避免"从今天开始记日记"的 20 天空窗期。

数据源：
- A 股/ETF: 东方财富 → 腾讯
- 美股: Nasdaq → Yahoo
- 加密货币: Binance → Yahoo

使用方式：
    provider = CompositeKLineProvider()
    df = await provider.fetch(instrument, lookback_days=60)
    await cache.warm(instrument, df)
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from stocks.domain.models import Instrument
from stocks.engine.config_loader import provider_base_url
from stocks.engine.history_cache import HistoryCache
from stocks.logging_utils import get_logger
from stocks.providers.tencent_a import tencent_market_prefix

# Binance 历史 K 线端点，与行情 Provider 共用同一配置源
_BINANCE_BASE_URL = provider_base_url("binance", "https://api.binance.com/api/v3")

logger = get_logger("history_provider")


# ------------------------------------------------------------------
# 列定义（与 HistoryCache 对齐，增加 volume 标准化）
# ------------------------------------------------------------------
_HISTORY_COLUMNS = [
    "timestamp",
    "code",
    "name",
    "market",
    "price",
    "open_price",
    "high",
    "low",
    "prev_close",
    "volume_lot",
    "data_source",
]


# ------------------------------------------------------------------
# 东方财富 A 股/ETF 日 K
# ------------------------------------------------------------------

class EastmoneyKLineProvider:
    """东方财富日 K 接口 Provider

    接口: https://push2his.eastmoney.com/api/qt/stock/kline/get
    参数:
        secid: 1.{code} (上海) / 0.{code} (深圳)
        fields2: f51=日期,f52=开盘,f53=收盘,f54=最高,f55=最低,f56=成交量,f57=成交额
        klt=101: 日 K
        lmt: 条数
    """

    _HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    def _secid(self, instrument: Instrument) -> str:
        """根据 exchange 判断上海/深圳前缀"""
        exchange = (instrument.exchange or "").lower()
        if exchange in ("sh", "sh_stock", "sh_a", "sh_index"):
            return f"1.{instrument.code}"
        if exchange in ("sz", "sz_stock", "sz_a", "sz_index"):
            return f"0.{instrument.code}"
        # 默认：5/6/9 开头为上海，其余深圳
        if instrument.code.startswith(("5", "6", "9")):
            return f"1.{instrument.code}"
        return f"0.{instrument.code}"

    async def fetch(self, instrument: Instrument, lookback_days: int = 60) -> pd.DataFrame:
        """获取历史日 K 数据

        Returns:
            DataFrame，与 HistoryCache 列格式对齐
            失败时返回空 DataFrame（保持列结构）
        """
        secid = self._secid(instrument)
        url = (
            f"https://push2his.eastmoney.com/api/qt/stock/kline/get"
            f"?secid={secid}&fields1=f1&fields2=f51,f52,f53,f54,f55,f56,f57"
            f"&klt=101&fqt=1&end=20500101&lmt={lookback_days}"
        )

        def _request():
            req = urllib.request.Request(url, headers=self._HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))

        try:
            data = await asyncio.to_thread(_request)
            klines = data.get("data", {}).get("klines", [])
            if not klines:
                logger.warning(f"Eastmoney klines empty for {instrument.code}")
                return pd.DataFrame(columns=_HISTORY_COLUMNS)

            records = []
            for i, line in enumerate(klines):
                parts = line.split(",")
                if len(parts) < 6:
                    continue
                # 日期,开盘,收盘,最高,最低,成交量,成交额
                dt_str, open_p, close_p, high_p, low_p, volume = parts[:6]
                dt = datetime.strptime(dt_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                prev_close = float(klines[i - 1].split(",")[2]) if i > 0 else float(close_p)
                records.append({
                    "timestamp": dt,
                    "code": instrument.code,
                    "name": instrument.name,
                    "market": instrument.market,
                    "price": float(close_p),  # 收盘价作为 price
                    "open_price": float(open_p),
                    "high": float(high_p),
                    "low": float(low_p),
                    "prev_close": prev_close,
                    "volume_lot": float(volume),
                    "data_source": "provider",
                })

            df = pd.DataFrame(records, columns=_HISTORY_COLUMNS)
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            logger.info(f"Eastmoney fetched {len(df)} rows for {instrument.code}")
            return df

        except Exception as e:
            logger.warning(f"Eastmoney fetch failed for {instrument.code}: {e}")
            return pd.DataFrame(columns=_HISTORY_COLUMNS)


# ------------------------------------------------------------------
# 腾讯 A 股/ETF 日 K（东方财富备用源）
# ------------------------------------------------------------------

class TencentKLineProvider:
    """腾讯前复权日 K；响应优先使用 qfqday，缺失时回落 day。"""

    _HEADERS = {"User-Agent": "Mozilla/5.0"}

    async def fetch(
        self, instrument: Instrument, lookback_days: int = 60
    ) -> pd.DataFrame:
        symbol = f"{tencent_market_prefix(instrument)}{instrument.code}"
        url = (
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
            f"?param={symbol},day,,,{lookback_days},qfq"
        )

        def _request():
            req = urllib.request.Request(url, headers=self._HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))

        try:
            payload = await asyncio.to_thread(_request)
            node = payload.get("data", {}).get(symbol, {})
            klines = node.get("qfqday") or node.get("day") or []
            records: list[dict] = []
            previous_close: float | None = None
            for row in klines:
                if not isinstance(row, list) or len(row) < 6:
                    continue
                dt_str, open_p, close_p, high_p, low_p, volume = row[:6]
                close = float(close_p)
                records.append({
                    "timestamp": datetime.strptime(dt_str, "%Y-%m-%d").replace(
                        tzinfo=timezone.utc
                    ),
                    "code": instrument.code,
                    "name": instrument.name,
                    "market": instrument.market,
                    "price": close,
                    "open_price": float(open_p),
                    "high": float(high_p),
                    "low": float(low_p),
                    "prev_close": previous_close if previous_close is not None else close,
                    "volume_lot": float(volume),
                    "data_source": "provider",
                })
                previous_close = close
            frame = pd.DataFrame(records, columns=_HISTORY_COLUMNS)
            if not frame.empty:
                frame["timestamp"] = pd.to_datetime(frame["timestamp"])
            logger.info(f"Tencent fetched {len(frame)} rows for {instrument.code}")
            return frame
        except Exception as exc:
            logger.warning(f"Tencent fetch failed for {instrument.code}: {exc}")
            return pd.DataFrame(columns=_HISTORY_COLUMNS)


# ------------------------------------------------------------------
# Yahoo Finance 美股/加密货币日 K
# ------------------------------------------------------------------

class YahooKLineProvider:
    """Yahoo Finance 日 K 接口 Provider

    接口: https://query1.finance.yahoo.com/v8/finance/chart/{ticker}
    参数:
        interval=1d: 日 K
        range=60d: 60 天
    """

    _HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    def _ticker(self, instrument: Instrument) -> str:
        """根据 market 返回 Yahoo ticker"""
        if instrument.market == "crypto":
            # BTCUSDT → BTC-USD
            return instrument.code.replace("USDT", "-USD")
        return instrument.code

    async def fetch(self, instrument: Instrument, lookback_days: int = 60) -> pd.DataFrame:
        """获取历史日 K 数据"""
        ticker = self._ticker(instrument)
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
            f"?interval=1d&range={lookback_days}d"
        )

        def _request():
            req = urllib.request.Request(url, headers=self._HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))

        try:
            data = await asyncio.to_thread(_request)
            result = data.get("chart", {}).get("result", [None])[0]
            if not result:
                logger.warning(f"Yahoo chart empty for {instrument.code}")
                return pd.DataFrame(columns=_HISTORY_COLUMNS)

            timestamps = result.get("timestamp", [])
            quote = result.get("indicators", {}).get("quote", [{}])[0]
            opens = quote.get("open", [])
            highs = quote.get("high", [])
            lows = quote.get("low", [])
            closes = quote.get("close", [])
            volumes = quote.get("volume", [])

            if not timestamps or not closes:
                return pd.DataFrame(columns=_HISTORY_COLUMNS)

            records = []
            for i, ts in enumerate(timestamps):
                if closes[i] is None:
                    continue
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                prev_close = closes[i - 1] if i > 0 and closes[i - 1] is not None else closes[i]
                records.append({
                    "timestamp": dt,
                    "code": instrument.code,
                    "name": instrument.name,
                    "market": instrument.market,
                    "price": float(closes[i]),
                    "open_price": float(opens[i]) if opens[i] is not None else float(closes[i]),
                    "high": float(highs[i]) if highs[i] is not None else float(closes[i]),
                    "low": float(lows[i]) if lows[i] is not None else float(closes[i]),
                    "prev_close": float(prev_close),
                    "volume_lot": float(volumes[i]) if volumes[i] is not None else 0.0,
                    "data_source": "provider",
                })

            df = pd.DataFrame(records, columns=_HISTORY_COLUMNS)
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            logger.info(f"Yahoo fetched {len(df)} rows for {instrument.code}")
            return df

        except Exception as e:
            logger.warning(f"Yahoo fetch failed for {instrument.code}: {e}")
            return pd.DataFrame(columns=_HISTORY_COLUMNS)


# ------------------------------------------------------------------
# Nasdaq 美股日 K（免 key 主源）
# ------------------------------------------------------------------

class NasdaqKLineProvider:
    """Nasdaq 公开历史行情端点；返回按日期升序的 OHLCV。"""

    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 Chrome/124 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
    }

    @staticmethod
    def _number(value) -> float:
        cleaned = str(value).replace("$", "").replace(",", "").strip()
        return float(cleaned)

    async def fetch(
        self, instrument: Instrument, lookback_days: int = 60
    ) -> pd.DataFrame:
        today = datetime.now(timezone.utc).date()
        start = today - timedelta(days=max(90, lookback_days * 2))
        def _request(assetclass: str):
            query = urllib.parse.urlencode({
                "assetclass": assetclass,
                "fromdate": start.isoformat(),
                "todate": today.isoformat(),
                "limit": min(lookback_days + 10, 5000),
            })
            url = (
                f"https://api.nasdaq.com/api/quote/{instrument.code}/historical"
                f"?{query}"
            )
            req = urllib.request.Request(url, headers=self._HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))

        try:
            rows = []
            for assetclass in ("stocks", "etf"):
                payload = await asyncio.to_thread(_request, assetclass)
                data = payload.get("data") or {}
                rows = data.get("tradesTable", {}).get("rows", []) or []
                if rows:
                    break
            parsed: list[tuple[datetime, dict]] = []
            for row in rows:
                try:
                    timestamp = datetime.strptime(row["date"], "%m/%d/%Y").replace(
                        tzinfo=timezone.utc
                    )
                    parsed.append((timestamp, row))
                except (KeyError, TypeError, ValueError):
                    continue
            parsed.sort(key=lambda item: item[0])
            records: list[dict] = []
            previous_close: float | None = None
            for timestamp, row in parsed:
                try:
                    close = self._number(row["close"])
                    open_price = self._number(row["open"])
                    high = self._number(row["high"])
                    low = self._number(row["low"])
                    volume = self._number(row["volume"])
                except (KeyError, TypeError, ValueError):
                    continue
                records.append({
                    "timestamp": timestamp,
                    "code": instrument.code,
                    "name": instrument.name,
                    "market": instrument.market,
                    "price": close,
                    "open_price": open_price,
                    "high": high,
                    "low": low,
                    "prev_close": previous_close if previous_close is not None else close,
                    "volume_lot": volume,
                    "data_source": "provider",
                })
                previous_close = close
            records = records[-lookback_days:]
            frame = pd.DataFrame(records, columns=_HISTORY_COLUMNS)
            if not frame.empty:
                frame["timestamp"] = pd.to_datetime(frame["timestamp"])
            logger.info(f"Nasdaq fetched {len(frame)} rows for {instrument.code}")
            return frame
        except Exception as exc:
            logger.warning(f"Nasdaq fetch failed for {instrument.code}: {exc}")
            return pd.DataFrame(columns=_HISTORY_COLUMNS)


# ------------------------------------------------------------------
# Binance 加密货币日 K（免 key 主源）
# ------------------------------------------------------------------

class BinanceKLineProvider:
    """Binance UTC 日 K；Yahoo 仅作为备用。"""

    _HEADERS = {"User-Agent": "stocks-claw/1.0"}

    @staticmethod
    def _symbol(instrument: Instrument) -> str:
        return instrument.code.split(":", 1)[-1].replace("/", "").upper()

    async def fetch(
        self, instrument: Instrument, lookback_days: int = 60
    ) -> pd.DataFrame:
        query = urllib.parse.urlencode({
            "symbol": self._symbol(instrument),
            "interval": "1d",
            # 多取一根并剔除尚未收盘的当日 K，避免未来 closeTime 污染指标。
            "limit": min(max(1, lookback_days + 1), 1000),
        })
        url = f"{_BINANCE_BASE_URL}/klines?{query}"

        def _request():
            req = urllib.request.Request(url, headers=self._HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))

        try:
            rows = await asyncio.to_thread(_request)
            records: list[dict] = []
            previous_close: float | None = None
            now_ms = datetime.now(timezone.utc).timestamp() * 1000
            for row in rows:
                if not isinstance(row, list) or len(row) < 7:
                    continue
                if float(row[6]) > now_ms:
                    continue
                close = float(row[4])
                records.append({
                    "timestamp": datetime.fromtimestamp(
                        float(row[6]) / 1000, tz=timezone.utc
                    ),
                    "code": instrument.code,
                    "name": instrument.name,
                    "market": instrument.market,
                    "price": close,
                    "open_price": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "prev_close": previous_close if previous_close is not None else close,
                    "volume_lot": float(row[5]),
                    "data_source": "provider",
                })
                previous_close = close
            records = records[-lookback_days:]
            frame = pd.DataFrame(records, columns=_HISTORY_COLUMNS)
            if not frame.empty:
                frame["timestamp"] = pd.to_datetime(frame["timestamp"])
            logger.info(f"Binance fetched {len(frame)} rows for {instrument.code}")
            return frame
        except Exception as exc:
            logger.warning(f"Binance fetch failed for {instrument.code}: {exc}")
            return pd.DataFrame(columns=_HISTORY_COLUMNS)


# ------------------------------------------------------------------
# 组合提供者（按 market 路由）
# ------------------------------------------------------------------

class CompositeKLineProvider:
    """组合 K 线提供者，按 market 自动路由到对应数据源"""

    def __init__(self):
        self._eastmoney = EastmoneyKLineProvider()
        self._tencent = TencentKLineProvider()
        self._nasdaq = NasdaqKLineProvider()
        self._binance = BinanceKLineProvider()
        self._yahoo = YahooKLineProvider()

    async def _fetch_chain(
        self,
        instrument: Instrument,
        lookback_days: int,
        providers: list[tuple[str, object]],
    ) -> pd.DataFrame:
        errors: dict[str, str] = {}
        primary_source = providers[0][0]
        for index, (source, provider) in enumerate(providers):
            try:
                frame = await provider.fetch(instrument, lookback_days)
            except Exception as exc:
                frame = pd.DataFrame(columns=_HISTORY_COLUMNS)
                errors[source] = f"{type(exc).__name__}: {exc}"
            if frame.empty:
                errors.setdefault(source, "provider returned empty frame")
                continue
            frame.attrs.update({
                "source": source,
                "primary_source": primary_source,
                "fallback_source": source if index > 0 else None,
                "degradation_result": "fallback_success" if index > 0 else "success",
                "errors": errors,
            })
            return frame

        frame = pd.DataFrame(columns=_HISTORY_COLUMNS)
        frame.attrs.update({
            "source": primary_source,
            "primary_source": primary_source,
            "fallback_source": providers[-1][0] if len(providers) > 1 else None,
            "degradation_result": "empty",
            "errors": errors,
        })
        return frame

    async def fetch(self, instrument: Instrument, lookback_days: int = 60) -> pd.DataFrame:
        """根据 market 选择数据源"""
        if instrument.market == "a":
            return await self._fetch_chain(
                instrument,
                lookback_days,
                [
                    ("eastmoney_kline", self._eastmoney),
                    ("tencent_kline", self._tencent),
                ],
            )
        elif instrument.market == "us":
            return await self._fetch_chain(
                instrument,
                lookback_days,
                [
                    ("nasdaq_kline", self._nasdaq),
                    ("yahoo_kline", self._yahoo),
                ],
            )
        elif instrument.market == "crypto":
            return await self._fetch_chain(
                instrument,
                lookback_days,
                [
                    ("binance_kline", self._binance),
                    ("yahoo_kline", self._yahoo),
                ],
            )
        else:
            logger.warning(f"Unknown market {instrument.market} for {instrument.code}")
            return pd.DataFrame(columns=_HISTORY_COLUMNS)

    async def fetch_batch(
        self, instruments: list[Instrument], lookback_days: int = 60
    ) -> dict[str, pd.DataFrame]:
        """并行获取多个标的的历史数据

        Returns:
            dict: {instrument_key: DataFrame}
        """
        async def _fetch_one(inst):
            df = await self.fetch(inst, lookback_days)
            return (f"{inst.market}:{inst.code}", df)

        results = await asyncio.gather(*[_fetch_one(i) for i in instruments], return_exceptions=True)

        dataframes = {}
        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"Batch fetch failed: {result}")
                continue
            key, df = result
            dataframes[key] = df

        return dataframes


# ------------------------------------------------------------------
# 辅助：初始化时 warm HistoryCache
# ------------------------------------------------------------------

# D0-3:市场 → 主历史 provider 名称映射,与 CompositeKLineProvider.fetch 路由保持一致。
_MARKET_TO_HISTORY_SOURCE: dict[str, str] = {
    "a": "eastmoney_kline",
    "us": "nasdaq_kline",
    "crypto": "binance_kline",
}


def _resolve_history_source(market: str) -> str:
    return _MARKET_TO_HISTORY_SOURCE.get(market, "unknown")

def _last_closed_trading_day(
    market: str,
    now_local,
    holidays: frozenset,
    close_hm: str | None,
) -> object:
    """返回该市场"最近一个已收盘交易日"的 date。

    market: instrument.market ('a'/'us'/'crypto')
    now_local: 该市场交易所本地时区的当前 datetime
    holidays: frozenset['YYYY-MM-DD']（该市场）
    close_hm: 'HH:MM' 收盘时刻；crypto 或 None 时视为 7x24 无休(直接 today)

    语义: 当前本地时刻已过收盘点 → 今天就是一个已收盘交易日；
          否则回溯到上一个 工作日 且 非节假日 的日期。
    用于替代"today - stale_days"日历日窗口——日历日遇周末/节假日会误判
    新鲜度(8/6 停更案例: 缓存停在 stale_cutoff 当日, `<` 比较当成新鲜),
    交易日历判定对任意周末/节假日/停更组合都自洽。
    """
    if market == "crypto" or not close_hm:
        return now_local.date()
    try:
        close_h, close_m = (int(x) for x in close_hm.split(":"))
    except (ValueError, TypeError):
        return now_local.date()
    closed_today = (now_local.hour, now_local.minute) >= (close_h, close_m)
    day = now_local.date() if closed_today else now_local.date() - timedelta(days=1)
    while day.weekday() >= 5 or day.isoformat() in holidays:
        day -= timedelta(days=1)
    return day



async def warm_history_cache(
    cache: HistoryCache,
    provider: CompositeKLineProvider,
    instruments: list[Instrument],
    lookback_days: int = 60,
    stale_days: int = 4,
    market_holidays: dict | None = None,
    market_close: dict | None = None,
    market_tz: dict | None = None,
) -> list[dict]:
    """为给定标的列表 warm HistoryCache,返回结构化回填报告(D0-3)。

    Args:
        cache: HistoryCache
        provider: CompositeKLineProvider
        instruments: 目标标的
        lookback_days: 需要的历史条数（日频场景下即天数）
        stale_days: 缓存新鲜度容忍窗口（日历日）。缓存最后一根 K 线
            早于 ``今天 - stale_days`` 视为过时,即使数据量已足也强制
            重拉。默认 4 天：覆盖周末 + 常见节假日,同时让 7/23→8/6
            这种两周断档无法再以 skipped_cached 蒙混过关（对抗性校验
            P2-1：候选价格曾停留在两周前的 K 线,quotes 层却显示
            fresh,门控只看 quotes 层放行）。

    Returns:
        list[dict]: 每标的一项 {symbol, market, source, rows, status, error},
        status ∈ {"ok","skipped_cached","failed"}。上层据此可拼装 data_quality.history_backfill。
    """
    report: list[dict] = []

    today = datetime.now(timezone.utc).date()
    stale_cutoff = today - timedelta(days=max(1, int(stale_days)))
    tz_by_market = dict(market_tz or {})

    for inst in instruments:
        key = f"{inst.market}:{inst.code}"
        source = _resolve_history_source(inst.market)
        primary_source = source
        fallback_source = None
        degradation_result = "not_requested"
        provider_errors: dict[str, str] = {}

        # 检查当前缓存数据量;≥80% 视为已足。同时校验新鲜度：即使量足,
        # 若最后一根 K 线早于 stale_cutoff 也强制重拉,避免停更缓存
        # 被当作当日数据继续输出精确价格/指标。
        try:
            df = await cache.get_history(inst, lookback_bars=lookback_days, include_disk=True)
        except Exception:
            df = pd.DataFrame()
        if len(df) >= lookback_days * 0.8:
            cache_stale = True
            try:
                if not df.empty and "timestamp" in df.columns:
                    last_ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce").dropna()
                    if not last_ts.empty:
                        last_date = last_ts.iloc[-1].date()
                        close_hm = (market_close or {}).get(inst.market)
                        if close_hm is not None:
                            # 交易日历判据: 缓存缺了"最近已收盘交易日"即过时
                            tz_name = tz_by_market.get(inst.market)
                            try:
                                now_local = datetime.now(ZoneInfo(tz_name)) if tz_name else datetime.now(timezone.utc)
                            except Exception:
                                now_local = datetime.now(timezone.utc)
                            horizon = _last_closed_trading_day(
                                inst.market,
                                now_local,
                                frozenset((market_holidays or {}).get(inst.market, set())),
                                close_hm,
                            )
                            cache_stale = last_date < horizon
                        else:
                            cache_stale = last_date < stale_cutoff
            except Exception:
                cache_stale = True
            if not cache_stale:
                report.append({
                    "symbol": key, "market": inst.market, "source": source,
                    "primary_source": primary_source, "fallback_source": fallback_source,
                    "degradation_result": "skipped_cached", "errors": provider_errors,
                    "rows": len(df), "status": "skipped_cached", "error": None,
                })
                continue
            # 数据量足但新鲜度不足 → 落到下方重拉,并标注原因
            degradation_result = "stale_cache"
            provider_errors["cache"] = (
                f"cache last bar {df['timestamp'].iloc[-1] if not df.empty else '?'} "
                f"older than freshness cutoff"
            )

        # 拉取历史数据(带一次退避重试)
        hist_df = pd.DataFrame(columns=_HISTORY_COLUMNS)
        last_error: str | None = None
        for attempt in range(2):
            try:
                hist_df = await provider.fetch(inst, lookback_days)
                source = hist_df.attrs.get("source", source)
                primary_source = hist_df.attrs.get("primary_source", primary_source)
                fallback_source = hist_df.attrs.get("fallback_source")
                degradation_result = hist_df.attrs.get(
                    "degradation_result", "success" if not hist_df.empty else "empty"
                )
                provider_errors = dict(hist_df.attrs.get("errors", {}))
                if not hist_df.empty:
                    last_error = None
                    break
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                if attempt == 0:
                    await asyncio.sleep(1.0)
                    continue
                logger.info(f"Warm failed for {inst.code} after retry: {e}")

        if hist_df.empty:
            if last_error is None:
                last_error = "provider returned empty frame"
            logger.info(f"Warm empty for {inst.code}: {last_error}")
            report.append({
                "symbol": key, "market": inst.market, "source": source,
                "primary_source": primary_source, "fallback_source": fallback_source,
                "degradation_result": degradation_result, "errors": provider_errors,
                "rows": 0, "status": "failed", "error": last_error,
            })
            continue

        try:
            await cache.warm(inst, hist_df)
            rows = len(hist_df)
            logger.info(f"Warmed {inst.code} with {rows} rows")
            report.append({
                "symbol": key, "market": inst.market, "source": source,
                "primary_source": primary_source, "fallback_source": fallback_source,
                "degradation_result": degradation_result, "errors": provider_errors,
                "rows": rows, "status": "ok", "error": None,
            })
        except Exception as e:
            logger.info(f"Warm cache write failed for {inst.code}: {e}")
            report.append({
                "symbol": key, "market": inst.market, "source": source,
                "primary_source": primary_source, "fallback_source": fallback_source,
                "degradation_result": degradation_result, "errors": provider_errors,
                "rows": 0, "status": "failed",
                "error": f"cache_write:{type(e).__name__}: {e}",
            })

    return report
