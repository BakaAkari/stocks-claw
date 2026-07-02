"""HistoryProvider 测试 — 覆盖东财、Yahoo、组合提供者、warm_history_cache

测试策略：
- Mock urllib 响应，验证数据解析和格式转换
- 验证 CompositeKLineProvider 按 market 路由正确
- 验证 warm_history_cache 只 warm 数据不足的标的
- 所有测试独立，不依赖外部网络
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pandas as pd
import pytest

from stocks.domain.models import Instrument
from stocks.engine.history_cache import HistoryCache
from stocks.engine.history_provider import (
    CompositeKLineProvider,
    EastmoneyKLineProvider,
    YahooKLineProvider,
    warm_history_cache,
)

# Mock 东财响应
EASTMONEY_RESPONSE = {
    "rc": 0,
    "rt": 17,
    "data": {
        "code": "000300",
        "klines": [
            "2024-01-01,100.0,101.0,102.0,99.0,1000000,1000000000.0",
            "2024-01-02,101.0,102.0,103.0,100.0,2000000,2000000000.0",
            "2024-01-03,102.0,103.0,104.0,101.0,3000000,3000000000.0",
            "2024-01-04,103.0,104.0,105.0,102.0,4000000,4000000000.0",
            "2024-01-05,104.0,105.0,106.0,103.0,5000000,5000000000.0",
        ]
    }
}


# Mock Yahoo 响应
YAHOO_RESPONSE = {
    "chart": {
        "result": [{
            "meta": {"symbol": "QQQ"},
            "timestamp": [
                int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp()),
                int(datetime(2024, 1, 2, tzinfo=timezone.utc).timestamp()),
                int(datetime(2024, 1, 3, tzinfo=timezone.utc).timestamp()),
            ],
            "indicators": {
                "quote": [{
                    "open": [400.0, 405.0, 410.0],
                    "high": [402.0, 407.0, 412.0],
                    "low": [398.0, 403.0, 408.0],
                    "close": [401.0, 406.0, 411.0],
                    "volume": [1000000, 2000000, 3000000],
                }]
            }
        }]
    }
}


@pytest.fixture
def sample_instrument_a():
    return Instrument(code="000300", name="沪深300", market="a", exchange="sh_index")


@pytest.fixture
def sample_instrument_us():
    return Instrument(code="QQQ", name="纳斯达克100", market="us")


@pytest.fixture
def sample_instrument_crypto():
    return Instrument(code="BTCUSDT", name="比特币", market="crypto")


# ------------------------------------------------------------------
# EastmoneyKLineProvider
# ------------------------------------------------------------------

class TestEastmoneyKLineProvider:
    async def test_fetch_basic(self, sample_instrument_a):
        """东财接口正常返回"""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(EASTMONEY_RESPONSE).encode("utf-8")
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)

        provider = EastmoneyKLineProvider()
        with patch("urllib.request.urlopen", return_value=mock_resp):
            df = await provider.fetch(sample_instrument_a, lookback_days=5)

        assert len(df) == 5
        assert list(df.columns) == [
            "timestamp", "code", "name", "market", "price", "open_price",
            "high", "low", "prev_close", "volume_lot",
        ]
        assert df.iloc[0]["code"] == "000300"
        assert df.iloc[0]["price"] == 101.0  # 第一日收盘
        assert df.iloc[0]["open_price"] == 100.0
        assert df.iloc[0]["volume_lot"] == 1000000.0
        # prev_close: 第一日无前一日，等于自身收盘
        assert df.iloc[0]["prev_close"] == 101.0
        # 第二日 prev_close = 第一日收盘
        assert df.iloc[1]["prev_close"] == 101.0

    async def test_fetch_empty_response(self, sample_instrument_a):
        """空响应返回空 DataFrame"""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"rc": 0, "data": {"code": "000300", "klines": []}}).encode("utf-8")
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)

        provider = EastmoneyKLineProvider()
        with patch("urllib.request.urlopen", return_value=mock_resp):
            df = await provider.fetch(sample_instrument_a, lookback_days=5)

        assert df.empty

    async def test_fetch_network_error(self, sample_instrument_a):
        """网络错误返回空 DataFrame"""
        provider = EastmoneyKLineProvider()
        with patch("urllib.request.urlopen", side_effect=Exception("Network error")):
            df = await provider.fetch(sample_instrument_a, lookback_days=5)

        assert df.empty

    def test_secid_sh(self):
        """上海代码返回 1. 前缀"""
        inst = Instrument(code="600000", name="浦发银行", market="a", exchange="sh")
        provider = EastmoneyKLineProvider()
        assert provider._secid(inst) == "1.600000"

    def test_secid_sz(self):
        """深圳代码返回 0. 前缀"""
        inst = Instrument(code="000001", name="平安银行", market="a", exchange="sz")
        provider = EastmoneyKLineProvider()
        assert provider._secid(inst) == "0.000001"

    def test_secid_default(self):
        """无 exchange 时，5/6/9 开头为上海"""
        inst = Instrument(code="518880", name="黄金ETF", market="a")
        provider = EastmoneyKLineProvider()
        assert provider._secid(inst) == "1.518880"

        inst2 = Instrument(code="159110", name="科创债", market="a")
        assert provider._secid(inst2) == "0.159110"


# ------------------------------------------------------------------
# YahooKLineProvider
# ------------------------------------------------------------------

class TestYahooKLineProvider:
    async def test_fetch_basic(self, sample_instrument_us):
        """Yahoo 接口正常返回"""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(YAHOO_RESPONSE).encode("utf-8")
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)

        provider = YahooKLineProvider()
        with patch("urllib.request.urlopen", return_value=mock_resp):
            df = await provider.fetch(sample_instrument_us, lookback_days=5)

        assert len(df) == 3
        assert df.iloc[0]["code"] == "QQQ"
        assert df.iloc[0]["price"] == 401.0
        assert df.iloc[0]["open_price"] == 400.0
        assert df.iloc[0]["volume_lot"] == 1000000.0

    async def test_fetch_crypto(self, sample_instrument_crypto):
        """加密货币 ticker 转换"""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(YAHOO_RESPONSE).encode("utf-8")
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)

        provider = YahooKLineProvider()
        with patch("urllib.request.urlopen", return_value=mock_resp):
            df = await provider.fetch(sample_instrument_crypto, lookback_days=5)

        assert len(df) == 3
        assert df.iloc[0]["code"] == "BTCUSDT"

    async def test_fetch_empty_response(self, sample_instrument_us):
        """空响应返回空 DataFrame"""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"chart": {"result": [None]}}).encode("utf-8")
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)

        provider = YahooKLineProvider()
        with patch("urllib.request.urlopen", return_value=mock_resp):
            df = await provider.fetch(sample_instrument_us, lookback_days=5)

        assert df.empty

    async def test_fetch_network_error(self, sample_instrument_us):
        """网络错误返回空 DataFrame"""
        provider = YahooKLineProvider()
        with patch("urllib.request.urlopen", side_effect=Exception("Network error")):
            df = await provider.fetch(sample_instrument_us, lookback_days=5)

        assert df.empty


# ------------------------------------------------------------------
# CompositeKLineProvider
# ------------------------------------------------------------------

class TestCompositeKLineProvider:
    async def test_routes_a_to_eastmoney(self, sample_instrument_a):
        """A 股路由到 Eastmoney"""
        provider = CompositeKLineProvider()
        with patch.object(provider._eastmoney, "fetch", return_value=pd.DataFrame({"col": [1]})) as mock_e:
            with patch.object(provider._yahoo, "fetch") as mock_y:
                await provider.fetch(sample_instrument_a, lookback_days=5)
                mock_e.assert_called_once()
                mock_y.assert_not_called()

    async def test_routes_us_to_yahoo(self, sample_instrument_us):
        """美股路由到 Yahoo"""
        provider = CompositeKLineProvider()
        with patch.object(provider._yahoo, "fetch", return_value=pd.DataFrame({"col": [1]})) as mock_y:
            with patch.object(provider._eastmoney, "fetch") as mock_e:
                await provider.fetch(sample_instrument_us, lookback_days=5)
                mock_y.assert_called_once()
                mock_e.assert_not_called()

    async def test_routes_crypto_to_yahoo(self, sample_instrument_crypto):
        """加密货币路由到 Yahoo"""
        provider = CompositeKLineProvider()
        with patch.object(provider._yahoo, "fetch", return_value=pd.DataFrame({"col": [1]})) as mock_y:
            with patch.object(provider._eastmoney, "fetch") as mock_e:
                await provider.fetch(sample_instrument_crypto, lookback_days=5)
                mock_y.assert_called_once()
                mock_e.assert_not_called()

    async def test_fetch_batch(self, sample_instrument_a, sample_instrument_us):
        """批量并行获取"""
        provider = CompositeKLineProvider()
        with patch.object(provider._eastmoney, "fetch", return_value=pd.DataFrame({"col": [1]})):
            with patch.object(provider._yahoo, "fetch", return_value=pd.DataFrame({"col": [2]})):
                results = await provider.fetch_batch([sample_instrument_a, sample_instrument_us], 5)

        assert len(results) == 2
        assert "a:000300" in results
        assert "us:QQQ" in results

    async def test_unknown_market(self):
        """未知 market 返回空 DataFrame"""
        inst = Instrument(code="UNKNOWN", name="未知", market="xx")
        provider = CompositeKLineProvider()
        df = await provider.fetch(inst, lookback_days=5)
        assert df.empty


# ------------------------------------------------------------------
# warm_history_cache
# ------------------------------------------------------------------

class TestWarmHistoryCache:
    async def test_warm_empty_cache(self, tmp_path, sample_instrument_a):
        """空缓存应 warm 数据"""
        cache = HistoryCache(base_dir=str(tmp_path), ttl=86400)
        provider = Mock()
        provider.fetch = AsyncMock(return_value=pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=5, freq="D"),
            "code": ["000300"] * 5,
            "name": ["沪深300"] * 5,
            "market": ["a"] * 5,
            "price": [100.0, 101.0, 102.0, 103.0, 104.0],
            "open_price": [100.0, 101.0, 102.0, 103.0, 104.0],
            "high": [102.0, 103.0, 104.0, 105.0, 106.0],
            "low": [98.0, 99.0, 100.0, 101.0, 102.0],
            "prev_close": [100.0, 100.0, 101.0, 102.0, 103.0],
            "volume_lot": [1_000_000] * 5,
        }))

        warmed = await warm_history_cache(cache, provider, [sample_instrument_a], 5)
        await cache.close()

        assert warmed["a:000300"] == 5
        provider.fetch.assert_called_once_with(sample_instrument_a, 5)

    async def test_skip_sufficient_cache(self, tmp_path, sample_instrument_a):
        """数据充足时跳过 warm"""
        cache = HistoryCache(base_dir=str(tmp_path), ttl=86400)
        # 先 warm 足够数据
        df = pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=10, freq="D"),
            "code": ["000300"] * 10,
            "name": ["沪深300"] * 10,
            "market": ["a"] * 10,
            "price": [100.0] * 10,
            "open_price": [100.0] * 10,
            "high": [102.0] * 10,
            "low": [98.0] * 10,
            "prev_close": [100.0] * 10,
            "volume_lot": [1_000_000] * 10,
        })
        await cache.warm(sample_instrument_a, df)

        provider = Mock()
        provider.fetch = AsyncMock(return_value=pd.DataFrame())

        warmed = await warm_history_cache(cache, provider, [sample_instrument_a], 5)
        await cache.close()

        assert warmed["a:000300"] == 0
        provider.fetch.assert_not_called()

    async def test_warm_failure_continues(self, tmp_path, sample_instrument_a, sample_instrument_us):
        """单个标的失败不影响其他"""
        cache = HistoryCache(base_dir=str(tmp_path), ttl=86400)
        provider = Mock()
        provider.fetch = AsyncMock(side_effect=[
            Exception("API down"),
            Exception("API down"),  # retry also fails
            pd.DataFrame({
                "timestamp": pd.date_range("2024-01-01", periods=3, freq="D"),
                "code": ["QQQ"] * 3,
                "name": ["纳斯达克100"] * 3,
                "market": ["us"] * 3,
                "price": [400.0, 401.0, 402.0],
                "open_price": [400.0, 401.0, 402.0],
                "high": [402.0, 403.0, 404.0],
                "low": [398.0, 399.0, 400.0],
                "prev_close": [400.0, 400.0, 401.0],
                "volume_lot": [1_000_000] * 3,
            }),
        ])

        warmed = await warm_history_cache(cache, provider, [sample_instrument_a, sample_instrument_us], 5)
        await cache.close()

        assert warmed["a:000300"] == 0
        assert warmed["us:QQQ"] == 3

    async def test_warm_empty_provider_response(self, tmp_path, sample_instrument_a):
        """Provider 返回空数据时 warmed=0"""
        cache = HistoryCache(base_dir=str(tmp_path), ttl=86400)
        provider = Mock()
        provider.fetch = AsyncMock(return_value=pd.DataFrame(columns=[
            "timestamp", "code", "name", "market", "price", "open_price",
            "high", "low", "prev_close", "volume_lot",
        ]))

        warmed = await warm_history_cache(cache, provider, [sample_instrument_a], 5)
        await cache.close()

        assert warmed["a:000300"] == 0
