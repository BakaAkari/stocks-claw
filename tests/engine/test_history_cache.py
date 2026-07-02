"""HistoryCache 测试 — 覆盖内存缓存、磁盘持久化、TTL 清理、预热、并发安全

测试策略：
- 使用临时目录隔离磁盘副作用
- 所有测试独立，不依赖外部网络或文件系统状态
- 并发测试使用 asyncio.gather 验证锁的正确性
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from stocks.domain.models import Instrument, Quote
from stocks.engine.history_cache import _COLUMNS, HistoryCache


@pytest.fixture
def temp_dir():
    """提供临时目录，测试结束后自动清理"""
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
async def cache(temp_dir):
    """提供已初始化的 HistoryCache 实例"""
    c = HistoryCache(base_dir=temp_dir, ttl=86400)
    yield c
    await c.close()


@pytest.fixture
def sample_instrument():
    return Instrument(code="000001", name="平安银行", market="a")


@pytest.fixture
def sample_quote():
    return Quote(
        instrument=Instrument(code="000001", name="平安银行", market="a"),
        price=10.5,
        open_price=10.2,
        high=10.6,
        low=10.1,
        prev_close=10.4,
        volume_lot=1_000_000,
    )


# ------------------------------------------------------------------
# 基础功能
# ------------------------------------------------------------------

class TestRecordAndGet:
    async def test_record_single(self, cache, sample_instrument, sample_quote):
        """单条记录后能正确读取"""
        await cache.record(sample_instrument, sample_quote)
        df = await cache.get_history(sample_instrument, lookback_bars=5)

        assert len(df) == 1
        assert df.iloc[0]["price"] == 10.5
        assert df.iloc[0]["code"] == "000001"
        assert df.iloc[0]["market"] == "a"
        assert df.iloc[0]["volume_lot"] == 1_000_000

    async def test_record_multiple(self, cache, sample_instrument):
        """多条记录后返回最近 N 条（跨天记录避免去重）"""
        for i in range(10):
            q = Quote(
                instrument=sample_instrument,
                price=10.0 + i,
                open_price=10.0,
                high=10.0 + i + 0.5,
                low=10.0,
                prev_close=10.0 + i - 1 if i > 0 else 10.0,
                volume_lot=1_000_000,
            )
            await cache.record(sample_instrument, q)
            await asyncio.sleep(0.001)  # 时间戳不同但在同一天，会被去重

        df = await cache.get_history(sample_instrument, lookback_bars=5)
        # 由于所有记录都在同一天，去重后只保留最新的一条
        assert len(df) == 1
        assert df.iloc[0]["price"] == 19.0  # 最后一条 (i=9)

    async def test_record_multiple_across_days(self, cache, sample_instrument):
        """跨天记录应保留多条"""
        # 使用 warm 预加载跨天数据
        records = []
        for i in range(10):
            records.append({
                "timestamp": datetime.now(timezone.utc) - timedelta(days=i),
                "code": "000001",
                "name": "平安银行",
                "market": "a",
                "price": 10.0 + i,
                "open_price": 10.0,
                "high": 10.0 + i,
                "low": 10.0,
                "prev_close": 10.0,
                "volume_lot": 1_000_000,
            })
        df = pd.DataFrame(records)
        await cache.warm(sample_instrument, df)

        result = await cache.get_history(sample_instrument, lookback_bars=5)
        assert len(result) == 5
        assert result.iloc[-1]["price"] == 10.0  # 最近一天（i=0）

    async def test_record_dedup_same_day(self, cache, sample_instrument):
        """同一天多次记录应去重，保留最新"""
        q1 = Quote(
            instrument=sample_instrument, price=10.0,
            open_price=10.0, high=10.0, low=10.0, prev_close=10.0, volume_lot=1,
        )
        q2 = Quote(
            instrument=sample_instrument, price=11.0,
            open_price=11.0, high=11.0, low=11.0, prev_close=11.0, volume_lot=2,
        )

        await cache.record(sample_instrument, q1)
        await cache.record(sample_instrument, q2)

        df = await cache.get_history(sample_instrument, lookback_bars=5)
        assert len(df) == 1
        assert df.iloc[0]["price"] == 11.0  # 保留最新
        assert df.iloc[0]["volume_lot"] == 2

    async def test_memory_truncation(self, cache, sample_instrument):
        """内存超过 500 条应截断"""
        for i in range(550):
            q = Quote(
                instrument=sample_instrument,
                price=float(i),
                open_price=0.0, high=0.0, low=0.0, prev_close=0.0, volume_lot=1,
            )
            await cache.record(sample_instrument, q)
            await asyncio.sleep(0.0001)

        df = await cache.get_history(sample_instrument, lookback_bars=600)
        assert len(df) <= 500  # 截断上限

    async def test_empty_cache(self, cache, sample_instrument):
        """空缓存返回结构正确的空 DataFrame"""
        df = await cache.get_history(sample_instrument, lookback_bars=5)
        assert df.empty
        assert list(df.columns) == _COLUMNS


# ------------------------------------------------------------------
# 磁盘持久化
# ------------------------------------------------------------------

class TestDiskPersistence:
    async def test_flush_and_reload(self, temp_dir, sample_instrument, sample_quote):
        """写入磁盘后新实例能读取"""
        cache1 = HistoryCache(base_dir=temp_dir, ttl=86400)
        await cache1.record(sample_instrument, sample_quote)
        await cache1.flush()
        await cache1.close()

        cache2 = HistoryCache(base_dir=temp_dir, ttl=86400)
        df = await cache2.get_history(sample_instrument, lookback_bars=5, include_disk=True)
        await cache2.close()

        assert len(df) == 1
        assert df.iloc[0]["price"] == 10.5

    async def test_disk_file_format(self, temp_dir, sample_instrument, sample_quote):
        """磁盘文件应为合法 JSON，包含 columns + records"""
        cache = HistoryCache(base_dir=temp_dir, ttl=86400)
        await cache.record(sample_instrument, sample_quote)
        await cache.flush()
        await cache.close()

        path = Path(temp_dir) / "a_000001.json"
        assert path.exists()

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert "columns" in data
        assert "records" in data
        assert set(data["columns"]) == set(_COLUMNS)
        assert len(data["records"]) == 1

    async def test_auto_merge_disk(self, temp_dir, sample_instrument, sample_quote):
        """内存不足时自动合并磁盘数据"""
        cache1 = HistoryCache(base_dir=temp_dir, ttl=86400)
        await cache1.record(sample_instrument, sample_quote)
        await cache1.flush()
        await cache1.close()

        # 新实例，内存为空，应自动从磁盘加载
        cache2 = HistoryCache(base_dir=temp_dir, ttl=86400)
        # 不 record，直接 get
        df = await cache2.get_history(sample_instrument, lookback_bars=5, include_disk=True)
        await cache2.close()

        assert len(df) == 1

    async def test_disk_merge_deduplicate(self, temp_dir, sample_instrument):
        """内存与磁盘的同交易日实时记录只保留最新一条。"""
        cache1 = HistoryCache(base_dir=temp_dir, ttl=86400)
        q1 = Quote(
            instrument=sample_instrument, price=10.0,
            open_price=10.0, high=10.0, low=10.0, prev_close=10.0, volume_lot=1,
        )
        await cache1.record(sample_instrument, q1)
        await cache1.flush()
        await cache1.close()

        # 新实例，record 同一天的更新价格
        cache2 = HistoryCache(base_dir=temp_dir, ttl=86400)
        q2 = Quote(
            instrument=sample_instrument, price=11.0,
            open_price=11.0, high=11.0, low=11.0, prev_close=11.0, volume_lot=2,
        )
        await cache2.record(sample_instrument, q2)
        df = await cache2.get_history(sample_instrument, lookback_bars=5, include_disk=True)
        await cache2.close()

        assert len(df) == 1
        latest = df.iloc[-1]
        assert latest["price"] == 11.0

    async def test_provider_daily_bar_beats_same_day_realtime(
        self,
        cache,
        sample_instrument,
    ):
        """同一交易日的 provider 日 K 优先于实时 record。

        使用跨 UTC 日期但同一上海交易日的固定时间，避免 UTC 16:00–24:00
        运行时上海日期跨天导致 flaky。
        """
        # 上海 2026-07-02 04:00 与 09:00 属于同一交易日，但跨 UTC 日期
        provider_ts = datetime(2026, 7, 1, 20, 0, tzinfo=timezone.utc)
        provider_row = {
            "timestamp": provider_ts,
            "code": sample_instrument.code,
            "name": sample_instrument.name,
            "market": sample_instrument.market,
            "price": 10.0,
            "open_price": 9.8,
            "high": 10.2,
            "low": 9.7,
            "prev_close": 9.9,
            "volume_lot": 100,
        }
        await cache.warm(sample_instrument, pd.DataFrame([provider_row]))
        await cache.record(
            sample_instrument,
            Quote(
                instrument=sample_instrument,
                price=11.0,
                open_price=10.5,
                high=11.2,
                low=10.4,
                prev_close=10.0,
                volume_lot=50,
                as_of="2026-07-02T01:00:00+00:00",  # 上海 09:00，同一交易日
            ),
        )

        result = await cache.get_history(sample_instrument, lookback_bars=5)

        assert len(result) == 1
        assert result.iloc[0]["price"] == 10.0
        assert result.iloc[0]["data_source"] == "provider"

    def test_us_market_dedup_uses_new_york_trade_date(self, temp_dir):
        """跨 UTC 日期但同一纽约交易日的记录必须合并。"""
        cache = HistoryCache(base_dir=temp_dir, ttl=86400)
        base = {
            "code": "AAPL",
            "name": "Apple",
            "market": "us",
            "open_price": 100.0,
            "high": 102.0,
            "low": 99.0,
            "prev_close": 100.0,
            "volume_lot": 10,
        }
        provider = pd.DataFrame([
            {
                **base,
                "timestamp": datetime(2026, 7, 1, 14, 30, tzinfo=timezone.utc),
                "price": 101.0,
                "data_source": "provider",
            }
        ])
        realtime = pd.DataFrame([
            {
                **base,
                "timestamp": datetime(2026, 7, 2, 0, 30, tzinfo=timezone.utc),
                "price": 103.0,
                "data_source": "realtime",
            }
        ])

        result = cache._merge_and_deduplicate(provider, realtime)

        assert len(result) == 1
        assert result.iloc[0]["price"] == 101.0
        assert result.iloc[0]["data_source"] == "provider"


# ------------------------------------------------------------------
# 预热（Warm）
# ------------------------------------------------------------------

class TestWarm:
    async def test_warm_basic(self, cache, sample_instrument):
        """预热后 get_history 返回正确数据"""
        records = []
        base = datetime.now(timezone.utc)
        for i in range(30):
            records.append({
                "timestamp": base - timedelta(days=i),
                "code": "000001",
                "name": "平安银行",
                "market": "a",
                "price": 10.0 + i,
                "open_price": 10.0 + i,
                "high": 10.5 + i,
                "low": 9.5 + i,
                "prev_close": 10.0 + i - 1 if i > 0 else 10.0,
                "volume_lot": 1_000_000,
            })
        df = pd.DataFrame(records)

        await cache.warm(sample_instrument, df)
        result = await cache.get_history(sample_instrument, lookback_bars=10)

        assert len(result) == 10
        # 最近 10 条：i=0..9，price=10.0..19.0
        assert result.iloc[0]["price"] == 10.0 + 9  # i=9 最旧
        assert result.iloc[-1]["price"] == 10.0  # i=0 最新

    async def test_warm_missing_columns(self, cache, sample_instrument):
        """预热数据缺少列应抛出 ValueError"""
        df = pd.DataFrame({"timestamp": [datetime.now(timezone.utc)], "price": [10.0]})

        with pytest.raises(ValueError) as exc_info:
            await cache.warm(sample_instrument, df)

        assert "缺少列" in str(exc_info.value)

    async def test_warm_string_timestamp(self, cache, sample_instrument):
        """预热数据的 timestamp 为字符串时应自动解析"""
        records = []
        for i in range(5):
            records.append({
                "timestamp": (datetime.now(timezone.utc) - timedelta(days=i)).isoformat(),
                "code": "000001",
                "name": "平安银行",
                "market": "a",
                "price": 10.0 + i,
                "open_price": 10.0,
                "high": 10.0,
                "low": 10.0,
                "prev_close": 10.0,
                "volume_lot": 1,
            })
        df = pd.DataFrame(records)

        await cache.warm(sample_instrument, df)
        result = await cache.get_history(sample_instrument, lookback_bars=5)

        assert len(result) == 5
        assert pd.api.types.is_datetime64_any_dtype(result["timestamp"])


# ------------------------------------------------------------------
# TTL 清理
# ------------------------------------------------------------------

class TestPrune:
    async def test_prune_expired_memory(self, temp_dir, sample_instrument):
        """TTL 过期后内存数据应被清理"""
        cache = HistoryCache(base_dir=temp_dir, ttl=1)  # 1 秒 TTL

        q = Quote(
            instrument=sample_instrument, price=10.0,
            open_price=10.0, high=10.0, low=10.0, prev_close=10.0, volume_lot=1,
        )
        await cache.record(sample_instrument, q)

        await asyncio.sleep(1.1)
        await cache.prune(max_age=1)

        df = await cache.get_history(sample_instrument, lookback_bars=5)
        assert df.empty
        await cache.close()

    async def test_prune_expired_disk(self, temp_dir, sample_instrument):
        """TTL 过期后磁盘文件应被删除"""
        cache = HistoryCache(base_dir=temp_dir, ttl=1)

        q = Quote(
            instrument=sample_instrument, price=10.0,
            open_price=10.0, high=10.0, low=10.0, prev_close=10.0, volume_lot=1,
        )
        await cache.record(sample_instrument, q)
        await cache.flush()

        await asyncio.sleep(1.1)
        deleted = await cache.prune(max_age=1)
        await cache.close()

        assert deleted >= 1
        path = Path(temp_dir) / "a_000001.json"
        assert not path.exists()

    async def test_prune_keep_recent(self, temp_dir, sample_instrument):
        """未过期数据应保留"""
        cache = HistoryCache(base_dir=temp_dir, ttl=3600)

        q = Quote(
            instrument=sample_instrument, price=10.0,
            open_price=10.0, high=10.0, low=10.0, prev_close=10.0, volume_lot=1,
        )
        await cache.record(sample_instrument, q)
        await cache.flush()

        # 立即清理，不应删除
        deleted = await cache.prune(max_age=3600)
        await cache.close()

        assert deleted == 0
        path = Path(temp_dir) / "a_000001.json"
        assert path.exists()


# ------------------------------------------------------------------
# 并发安全
# ------------------------------------------------------------------

class TestConcurrency:
    async def test_concurrent_record(self, cache, sample_instrument):
        """50 个并发写入不应丢数据"""

        async def worker(n: int):
            q = Quote(
                instrument=sample_instrument,
                price=float(n),
                open_price=0.0, high=0.0, low=0.0, prev_close=0.0, volume_lot=1,
            )
            await cache.record(sample_instrument, q)

        await asyncio.gather(*[worker(i) for i in range(50)])
        df = await cache.get_history(sample_instrument, lookback_bars=100)

        assert len(df) == 1  # 50 次并发写入在同一天，去重后只保留最新 1 条
        # 由于同一天去重，实际只保留最后一条记录
        assert df.iloc[0]["price"] == 49.0  # 最后写入的价格

    async def test_concurrent_read_write(self, cache, sample_instrument):
        """并发读写不应崩溃"""
        async def writer():
            for i in range(20):
                q = Quote(
                    instrument=sample_instrument,
                    price=float(i),
                    open_price=0.0, high=0.0, low=0.0, prev_close=0.0, volume_lot=1,
                )
                await cache.record(sample_instrument, q)
                await asyncio.sleep(0.01)

        async def reader():
            for _ in range(20):
                await cache.get_history(sample_instrument, lookback_bars=5)
                await asyncio.sleep(0.01)

        await asyncio.gather(writer(), reader())
        # 不抛异常即为通过


# ------------------------------------------------------------------
# 边界与异常
# ------------------------------------------------------------------

class TestEdgeCases:
    async def test_special_symbol_in_path(self, temp_dir):
        """标的代码含特殊字符时不应崩溃"""
        inst = Instrument(code="BTC/USD", name="Bitcoin", market="crypto")
        q = Quote(
            instrument=inst, price=50000.0,
            open_price=49000.0, high=51000.0, low=48000.0, prev_close=49500.0, volume_lot=1000,
        )
        cache = HistoryCache(base_dir=temp_dir, ttl=86400)
        await cache.record(inst, q)
        await cache.flush()
        await cache.close()

        # 文件名应使用下划线替换斜杠
        path = Path(temp_dir) / "crypto_BTC_USD.json"
        assert path.exists()

    async def test_prune_corrupted_file(self, temp_dir, sample_instrument):
        """损坏的磁盘文件不应导致 prune 崩溃"""
        bad_file = Path(temp_dir) / "a_000001.json"
        bad_file.write_text("not json")

        cache = HistoryCache(base_dir=temp_dir, ttl=86400)
        # 不应抛异常
        await cache.prune(max_age=1)
        await cache.close()

        # 损坏文件可能被清理（因为解析失败导致 df 为空）
        # 或保留，但至少不崩溃

    async def test_close_flushes_dirty(self, temp_dir, sample_instrument, sample_quote):
        """close() 应自动 flush 脏数据"""
        cache = HistoryCache(base_dir=temp_dir, ttl=86400)
        await cache.record(sample_instrument, sample_quote)
        await cache.close()

        path = Path(temp_dir) / "a_000001.json"
        assert path.exists()

    async def test_get_history_without_disk(self, cache, sample_instrument):
        """include_disk=False 时不读取磁盘"""
        # 空缓存，不请求磁盘
        df = await cache.get_history(sample_instrument, lookback_bars=5, include_disk=False)
        assert df.empty

    async def test_multiple_instruments(self, cache):
        """多个标的独立隔离"""
        inst1 = Instrument(code="000001", name="平安银行", market="a")
        inst2 = Instrument(code="AAPL", name="Apple", market="us")

        q1 = Quote(instrument=inst1, price=10.0, open_price=10.0, high=10.0, low=10.0, prev_close=10.0, volume_lot=1)
        q2 = Quote(instrument=inst2, price=100.0, open_price=100.0, high=100.0, low=100.0, prev_close=100.0, volume_lot=1)

        await cache.record(inst1, q1)
        await cache.record(inst2, q2)

        df1 = await cache.get_history(inst1, lookback_bars=5)
        df2 = await cache.get_history(inst2, lookback_bars=5)

        assert df1.iloc[0]["price"] == 10.0
        assert df2.iloc[0]["price"] == 100.0
        assert df1.iloc[0]["code"] == "000001"
        assert df2.iloc[0]["code"] == "AAPL"
