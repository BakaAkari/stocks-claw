"""历史 K 线数据提供者 — 启动时批量回填历史数据到 HistoryCache

为技术指标计算提供足够的历史数据底座，避免"从今天开始记日记"的 20 天空窗期。

数据源：
- A 股/ETF: 东方财富日 K 接口（免费，无需 key）
- 美股/加密货币: Yahoo Finance v8 chart API（免费，无需 key）

使用方式：
    provider = CompositeKLineProvider()
    df = await provider.fetch(instrument, lookback_days=60)
    await cache.warm(instrument, df)
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pandas as pd

from stocks.domain.models import Instrument
from stocks.engine.history_cache import HistoryCache
from stocks.logging_utils import get_logger

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
                })

            df = pd.DataFrame(records, columns=_HISTORY_COLUMNS)
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            logger.info(f"Eastmoney fetched {len(df)} rows for {instrument.code}")
            return df

        except Exception as e:
            logger.warning(f"Eastmoney fetch failed for {instrument.code}: {e}")
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
                })

            df = pd.DataFrame(records, columns=_HISTORY_COLUMNS)
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            logger.info(f"Yahoo fetched {len(df)} rows for {instrument.code}")
            return df

        except Exception as e:
            logger.warning(f"Yahoo fetch failed for {instrument.code}: {e}")
            return pd.DataFrame(columns=_HISTORY_COLUMNS)


# ------------------------------------------------------------------
# 组合提供者（按 market 路由）
# ------------------------------------------------------------------

class CompositeKLineProvider:
    """组合 K 线提供者，按 market 自动路由到对应数据源"""

    def __init__(self):
        self._eastmoney = EastmoneyKLineProvider()
        self._yahoo = YahooKLineProvider()

    async def fetch(self, instrument: Instrument, lookback_days: int = 60) -> pd.DataFrame:
        """根据 market 选择数据源"""
        if instrument.market == "a":
            return await self._eastmoney.fetch(instrument, lookback_days)
        elif instrument.market in ("us", "crypto"):
            return await self._yahoo.fetch(instrument, lookback_days)
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
    "us": "yahoo_kline",
    "crypto": "yahoo_kline",
}


def _resolve_history_source(market: str) -> str:
    return _MARKET_TO_HISTORY_SOURCE.get(market, "unknown")


async def warm_history_cache(
    cache: HistoryCache,
    provider: CompositeKLineProvider,
    instruments: list[Instrument],
    lookback_days: int = 60,
) -> list[dict]:
    """为给定标的列表 warm HistoryCache,返回结构化回填报告(D0-3)。

    Returns:
        list[dict]: 每标的一项 {symbol, market, source, rows, status, error},
        status ∈ {"ok","skipped_cached","failed"}。上层据此可拼装 data_quality.history_backfill。
    """
    report: list[dict] = []

    for inst in instruments:
        key = f"{inst.market}:{inst.code}"
        source = _resolve_history_source(inst.market)

        # 检查当前缓存数据量;≥80% 视为已足
        try:
            df = await cache.get_history(inst, lookback_bars=lookback_days, include_disk=True)
        except Exception:
            df = pd.DataFrame()
        if len(df) >= lookback_days * 0.8:
            report.append({
                "symbol": key, "market": inst.market, "source": source,
                "rows": len(df), "status": "skipped_cached", "error": None,
            })
            continue

        # 拉取历史数据(带一次退避重试)
        hist_df = pd.DataFrame(columns=_HISTORY_COLUMNS)
        last_error: str | None = None
        for attempt in range(2):
            try:
                hist_df = await provider.fetch(inst, lookback_days)
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
                "rows": 0, "status": "failed", "error": last_error,
            })
            continue

        try:
            await cache.warm(inst, hist_df)
            rows = len(hist_df)
            logger.info(f"Warmed {inst.code} with {rows} rows")
            report.append({
                "symbol": key, "market": inst.market, "source": source,
                "rows": rows, "status": "ok", "error": None,
            })
        except Exception as e:
            logger.info(f"Warm cache write failed for {inst.code}: {e}")
            report.append({
                "symbol": key, "market": inst.market, "source": source,
                "rows": 0, "status": "failed",
                "error": f"cache_write:{type(e).__name__}: {e}",
            })

    return report
