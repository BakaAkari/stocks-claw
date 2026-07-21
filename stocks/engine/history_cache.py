"""历史数据缓存 — 标的级别内存 + 磁盘两级存储

提供按 Instrument 隔离的历史价格缓存，为技术指标计算提供数据底座。
存储格式：JSON（DataFrame records 序列化），按标的独立文件。

核心设计：
- 内存缓存：pandas DataFrame，支持快速切片和指标计算
- 磁盘缓存：JSON 原子写入，进程重启后自动恢复
- 并发安全：asyncio.Lock 保护内存状态
- 容量控制：内存截断至最近 500 条，磁盘由 TTL 控制
- 时间对齐：所有 timestamp 统一为 UTC

使用方式：
    cache = HistoryCache(base_dir="./data/history", ttl=86400)
    await cache.record(instrument, quote)
    df = await cache.get_history(instrument, lookback_bars=30)
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

from stocks.domain.models import Instrument, Quote
from stocks.logging_utils import get_logger

logger = get_logger("history_cache")

# DataFrame 列定义（与 Quote 字段对齐，增加 timestamp 和 code/name/market）
_COLUMNS = [
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

# 内存单标的上限（防止高频记录导致内存膨胀）
_MEMORY_MAX_ROWS = 500
_MARKET_TIMEZONES = {
    "a": ZoneInfo("Asia/Shanghai"),
    "us": ZoneInfo("America/New_York"),
    "crypto": timezone.utc,
}
_SOURCE_PRIORITY = {"realtime": 0, "unknown": 1, "provider": 2}


class HistoryCache:
    """历史数据缓存，支持内存 + 磁盘两级存储

    Args:
        base_dir: 磁盘缓存根目录，会自动创建
        ttl: 数据最大存活时间（秒），默认 86400（1 天）
    """

    def __init__(self, base_dir: str = "./data/history", ttl: int = 86400):
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._ttl = ttl
        self._memory: dict[str, pd.DataFrame] = {}
        self._lock = asyncio.Lock()
        self._dirty: set[str] = set()

    # ------------------------------------------------------------------
    # 键值与路径
    # ------------------------------------------------------------------

    def _key(self, instrument: Instrument) -> str:
        return f"{instrument.market}:{instrument.code}"

    def _path(self, instrument: Instrument) -> Path:
        safe_code = instrument.code.replace("/", "_")
        return self._base_dir / f"{instrument.market}_{safe_code}.json"

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    async def record(self, instrument: Instrument, quote: Quote) -> None:
        """记录一条行情快照到内存缓存

        自动去重同一天数据（保留最新），并截断至内存上限。
        优先使用 quote.as_of 作为行情时间戳，避免深夜抓数据时
        按到达时刻跨天归属错误交易日。
        """
        key = self._key(instrument)
        if quote.as_of:
            try:
                ts = datetime.fromisoformat(quote.as_of.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                ts = datetime.now(timezone.utc)
        else:
            ts = datetime.now(timezone.utc)

        # Normalize prev_close against the previous trading day's close to avoid
        # anomalies when provider-provided prev_close differs from the actual
        # historical close (e.g. data source hand-off, stale fields).
        prev_close = quote.prev_close
        last_stored = self._memory.get(key)
        if (
            prev_close is not None
            and last_stored is not None
            and not last_stored.empty
            and len(last_stored) >= 2
        ):
            # Use the second-to-last bar as the previous day's close.
            prior_close = float(last_stored.iloc[-2]["price"])
            if prior_close > 0 and abs(prev_close - prior_close) / prior_close > 0.05:
                prev_close = prior_close
        elif (
            prev_close is not None
            and last_stored is not None
            and not last_stored.empty
            and len(last_stored) == 1
        ):
            # Fallback for single-bar history: compare against the only stored close.
            last_close = float(last_stored.iloc[-1]["price"])
            if last_close > 0 and abs(prev_close - last_close) / last_close > 0.05:
                prev_close = last_close

        row = {
            "timestamp": ts,
            "code": instrument.code,
            "name": instrument.name,
            "market": instrument.market,
            "price": quote.price,
            "open_price": quote.open_price,
            "high": quote.high,
            "low": quote.low,
            "prev_close": prev_close,
            "volume_lot": quote.volume_lot,
            "data_source": "realtime",
        }

        async with self._lock:
            self._record_impl(key, row)

        logger.debug(
            f"Recorded {instrument.code} @ {quote.price} "
            f"(memory rows: {len(self._memory.get(key, pd.DataFrame()))})"
        )

    async def get_history(
        self,
        instrument: Instrument,
        lookback_bars: int = 30,
        include_disk: bool = True,
    ) -> pd.DataFrame:
        """获取最近 N 条历史数据（内存 + 磁盘合并）

        Args:
            instrument: 目标标的
            lookback_bars: 需要的历史条数（日频场景下即天数）
            include_disk: 内存不足时是否合并磁盘缓存

        Returns:
            DataFrame，columns: timestamp, symbol, market, price, open, high, low, prev_close, volume
            若没有任何数据，返回空 DataFrame（保持列结构）
        """
        key = self._key(instrument)

        async with self._lock:
            mem_df = self._memory.get(key, pd.DataFrame(columns=_COLUMNS))

            if include_disk and len(mem_df) < lookback_bars:
                disk_df = await self._load_from_disk_impl(instrument)
                if not disk_df.empty:
                    combined = self._merge_and_deduplicate(disk_df, mem_df)
                    self._memory[key] = combined
                    mem_df = combined

            if mem_df.empty:
                return pd.DataFrame(columns=_COLUMNS)

            # 按时间排序，取最近 lookback_bars
            result = mem_df.sort_values("timestamp").iloc[-lookback_bars:].reset_index(drop=True)
            return result

    async def warm(self, instrument: Instrument, df: pd.DataFrame) -> None:
        """批量预热历史数据（如从外部 K 线 API 加载）

        Args:
            instrument: 目标标的
            df: 历史数据 DataFrame，必须包含 _COLUMNS 中所有列

        Raises:
            ValueError: 列缺失或类型不匹配
        """
        key = self._key(instrument)

        required_columns = set(_COLUMNS) - {"data_source"}
        missing = required_columns - set(df.columns)
        if missing:
            raise ValueError(f"预热数据缺少列: {missing}")

        # 确保 timestamp 为 datetime 类型
        df = df.copy()
        if "data_source" not in df.columns:
            df["data_source"] = "provider"
        if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601")

        async with self._lock:
            self._memory[key] = self._merge_and_deduplicate(
                pd.DataFrame(columns=_COLUMNS),
                df,
            )
            self._dirty.add(key)

        logger.info(f"Warmed {instrument.code} with {len(df)} rows")

    async def prune(self, max_age: Optional[int] = None) -> int:
        """清理所有过期数据（内存 + 磁盘）

        Args:
            max_age: 最大存活秒数，默认使用构造时的 ttl

        Returns:
            清理的标的数量（仅统计被完全删除的磁盘文件）
        """
        max_age = max_age or self._ttl
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age)
        deleted_count = 0

        # 先清理内存（需要锁）
        async with self._lock:
            for key in list(self._memory.keys()):
                df = self._memory[key]
                if df.empty:
                    del self._memory[key]
                    self._dirty.discard(key)
                    continue

                df = df[df["timestamp"] >= cutoff].reset_index(drop=True)
                self._memory[key] = df

                if df.empty:
                    del self._memory[key]
                    self._dirty.discard(key)

        # 再清理磁盘（无需锁，IO 独立）
        for f in self._base_dir.glob("*.json"):
            try:
                deleted = await self._prune_file(f, cutoff)
                if deleted:
                    deleted_count += 1
            except Exception as e:
                logger.warning(f"Prune failed for {f.name}: {e}")

        return deleted_count

    async def flush(self) -> None:
        """将所有脏数据原子写入磁盘"""
        # 收集脏数据（加锁），然后释放锁后写入
        async with self._lock:
            dirty_keys = list(self._dirty)
            dirty_data: dict[str, pd.DataFrame] = {}
            for key in dirty_keys:
                if key in self._memory:
                    dirty_data[key] = self._memory[key].copy()
            self._dirty.clear()

        for key, df in dirty_data.items():
            try:
                market, symbol = key.split(":", 1)
                inst = Instrument(code=symbol, name=symbol, market=market)
                await self._save_to_disk_impl(inst, df)
            except Exception as e:
                logger.warning(f"Flush failed for {key}: {e}")
                # 标记回脏队列，下次重试
                async with self._lock:
                    self._dirty.add(key)

    async def close(self) -> None:
        """关闭缓存，确保所有脏数据落盘"""
        await self.flush()

    # ------------------------------------------------------------------
    # 私有实现（无锁版本，假设调用者已持有锁或在锁外独立执行）
    # ------------------------------------------------------------------

    def _record_impl(self, key: str, row: dict) -> None:
        """无锁版本：插入单条记录，同一天去重，截断内存上限"""
        if key not in self._memory or self._memory[key].empty:
            self._memory[key] = pd.DataFrame(columns=_COLUMNS)

        df = self._memory[key]
        # 同一交易日只保留一根 bar；provider 日 K 优先于实时快照。
        new_df = pd.DataFrame([row], columns=_COLUMNS)
        df = self._merge_and_deduplicate(df, new_df)

        # 内存截断
        if len(df) > _MEMORY_MAX_ROWS:
            df = df.iloc[-_MEMORY_MAX_ROWS:].reset_index(drop=True)

        df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601", utc=True)
        self._memory[key] = df
        self._dirty.add(key)

    def _merge_and_deduplicate(
        self, disk_df: pd.DataFrame, mem_df: pd.DataFrame
    ) -> pd.DataFrame:
        """按市场交易日合并；同日 provider 日 K 优先，其次取最新记录。"""
        frames = [frame.copy() for frame in (disk_df, mem_df) if not frame.empty]
        if not frames:
            return pd.DataFrame(columns=_COLUMNS)
        for frame in frames:
            if "data_source" not in frame.columns:
                frame["data_source"] = "unknown"
            frame["timestamp"] = pd.to_datetime(
                frame["timestamp"],
                format="ISO8601",
                utc=True,
            )
        combined = pd.concat(frames, ignore_index=True)
        combined["_trade_date"] = [
            timestamp.tz_convert(_MARKET_TIMEZONES.get(market, timezone.utc)).date()
            for timestamp, market in zip(combined["timestamp"], combined["market"])
        ]
        combined["_source_priority"] = (
            combined["data_source"].map(_SOURCE_PRIORITY).fillna(1)
        )
        combined = combined.sort_values(
            ["_trade_date", "_source_priority", "timestamp"]
        )
        combined = combined.drop_duplicates(
            subset=["market", "code", "_trade_date"],
            keep="last",
        )
        return (
            combined.drop(columns=["_trade_date", "_source_priority"])
            .sort_values("timestamp")
            .reindex(columns=_COLUMNS)
            .reset_index(drop=True)
        )

    async def _save_to_disk_impl(self, instrument: Instrument, df: pd.DataFrame) -> None:
        """无锁版本：原子写入 JSON 到磁盘"""
        path = self._path(instrument)
        if df.empty:
            return

        def _write():
            data = {
                "columns": list(df.columns),
                "records": df.to_dict(orient="records"),
            }
            tmp_path = path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, default=_serialize_datetime)
            os.replace(tmp_path, path)

        await asyncio.to_thread(_write)
        logger.debug(f"Saved {len(df)} rows to disk for {instrument.code}")

    async def _load_from_disk_impl(self, instrument: Instrument) -> pd.DataFrame:
        """无锁版本：从磁盘加载 JSON 并解析为 DataFrame"""
        path = self._path(instrument)
        if not path.exists():
            return pd.DataFrame(columns=_COLUMNS)

        def _read():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            df = pd.DataFrame(data["records"], columns=data["columns"])
            if "data_source" not in df.columns:
                df["data_source"] = "unknown"
            df = df.reindex(columns=_COLUMNS)
            df["timestamp"] = pd.to_datetime(
                df["timestamp"], format="ISO8601", utc=True
            )
            return df

        try:
            df = await asyncio.to_thread(_read)
            logger.debug(f"Loaded {len(df)} rows from disk for {instrument.code}")
            return df
        except Exception as e:
            logger.warning(f"Failed to load disk cache for {instrument.code}: {e}")
            return pd.DataFrame(columns=_COLUMNS)

    async def _prune_file(self, path: Path, cutoff: datetime) -> bool:
        """清理单个磁盘文件，返回是否被删除"""

        def _read_and_filter():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            df = pd.DataFrame(data["records"], columns=data["columns"])
            if "data_source" not in df.columns:
                df["data_source"] = "unknown"
            df = df.reindex(columns=_COLUMNS)
            df["timestamp"] = pd.to_datetime(
                df["timestamp"], format="ISO8601", utc=True
            )
            df = df[df["timestamp"] >= cutoff]
            return df

        df = await asyncio.to_thread(_read_and_filter)

        if df.empty:
            await asyncio.to_thread(path.unlink)
            return True

        def _write_back():
            data = {
                "columns": list(df.columns),
                "records": df.to_dict(orient="records"),
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, default=_serialize_datetime)

        await asyncio.to_thread(_write_back)
        return False


# ------------------------------------------------------------------
# 工具函数
# ------------------------------------------------------------------

def _serialize_datetime(obj):
    """json.dump 的 default 处理函数，序列化 datetime 为 ISO 格式"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
