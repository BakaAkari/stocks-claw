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
    BinanceKLineProvider,
    CompositeKLineProvider,
    EastmoneyKLineProvider,
    NasdaqKLineProvider,
    TencentKLineProvider,
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

TENCENT_QFQ_RESPONSE = {
    "code": 0,
    "data": {
        "sh000300": {
            "qfqday": [
                ["2024-01-01", "100.0", "101.0", "102.0", "99.0", "1000000"],
                ["2024-01-02", "101.0", "103.0", "104.0", "100.0", "2000000"],
            ]
        }
    },
}

NASDAQ_RESPONSE = {
    "status": {"rCode": 200},
    "data": {
        "tradesTable": {
            "rows": [
                {"date": "01/04/2024", "close": "N/A", "volume": "N/A", "open": "N/A", "high": "N/A", "low": "N/A"},
                {"date": "01/03/2024", "close": "$103.00", "volume": "3,000", "open": "$102.00", "high": "$104.00", "low": "$101.00"},
                {"date": "01/02/2024", "close": "$102.00", "volume": "2,000", "open": "$101.00", "high": "$103.00", "low": "$100.00"},
                {"date": "01/01/2024", "close": "$101.00", "volume": "1,000", "open": "$100.00", "high": "$102.00", "low": "$99.00"},
            ]
        }
    },
}

BINANCE_KLINES = [
    [1704067200000, "40000", "42000", "39000", "41000", "100", 1704153599999, "0", 1, "0", "0", "0"],
    [1704153600000, "41000", "43000", "40500", "42500", "120", 1704239999999, "0", 1, "0", "0", "0"],
]


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
            "high", "low", "prev_close", "volume_lot", "data_source",
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
# TencentKLineProvider
# ------------------------------------------------------------------

class TestTencentKLineProvider:
    @pytest.mark.parametrize("series_key", ["qfqday", "day"])
    async def test_fetch_qfq_and_day_payloads(
        self, sample_instrument_a, series_key
    ):
        payload = json.loads(json.dumps(TENCENT_QFQ_RESPONSE))
        node = payload["data"]["sh000300"]
        if series_key == "day":
            node["day"] = node.pop("qfqday")
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(payload).encode("utf-8")
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            frame = await TencentKLineProvider().fetch(
                sample_instrument_a, lookback_days=2
            )

        assert len(frame) == 2
        assert frame.iloc[0]["price"] == 101.0
        assert frame.iloc[0]["prev_close"] == 101.0
        assert frame.iloc[1]["prev_close"] == 101.0
        assert frame.iloc[1]["volume_lot"] == 2_000_000.0
        assert frame.iloc[1]["data_source"] == "provider"

    async def test_fetch_uses_shared_sh_sz_prefix(self):
        instrument = Instrument("159110", "科创债", "a", exchange="sz")
        payload = {"code": 0, "data": {"sz159110": {"qfqday": []}}}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(payload).encode("utf-8")
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp) as urlopen:
            frame = await TencentKLineProvider().fetch(instrument, lookback_days=5)

        assert frame.empty
        assert "param=sz159110,day,,,5,qfq" in urlopen.call_args.args[0].full_url


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
# Nasdaq / Binance 独立历史源
# ------------------------------------------------------------------

class TestNasdaqKLineProvider:
    async def test_fetch_parses_currency_and_sorts_ascending(
        self, sample_instrument_us
    ):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(NASDAQ_RESPONSE).encode("utf-8")
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            frame = await NasdaqKLineProvider().fetch(
                sample_instrument_us, lookback_days=3
            )

        assert list(frame["price"]) == [101.0, 102.0, 103.0]
        assert list(frame["prev_close"]) == [101.0, 101.0, 102.0]
        assert list(frame["volume_lot"]) == [1000.0, 2000.0, 3000.0]
        assert frame.iloc[0]["data_source"] == "provider"

    async def test_etf_assetclass_fallback(self):
        instrument = Instrument("QQQ", "Nasdaq 100", "us")
        empty = {"status": {"rCode": 400}, "data": None}
        responses = []
        for payload in (empty, NASDAQ_RESPONSE):
            response = MagicMock()
            response.read.return_value = json.dumps(payload).encode("utf-8")
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            responses.append(response)

        with patch("urllib.request.urlopen", side_effect=responses) as urlopen:
            frame = await NasdaqKLineProvider().fetch(instrument, lookback_days=3)

        assert len(frame) == 3
        assert "assetclass=stocks" in urlopen.call_args_list[0].args[0].full_url
        assert "assetclass=etf" in urlopen.call_args_list[1].args[0].full_url


class TestBinanceKLineProvider:
    async def test_fetch_parses_daily_klines(self, sample_instrument_crypto):
        rows = BINANCE_KLINES + [
            [4102358400000, "1", "2", "1", "2", "1", 4102444799999]
        ]
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(rows).encode("utf-8")
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp) as urlopen:
            frame = await BinanceKLineProvider().fetch(
                sample_instrument_crypto, lookback_days=2
            )

        assert list(frame["price"]) == [41000.0, 42500.0]
        assert list(frame["prev_close"]) == [41000.0, 41000.0]
        assert frame.iloc[1]["high"] == 43000.0
        assert frame.iloc[1]["volume_lot"] == 120.0
        assert str(frame.iloc[0]["timestamp"]).startswith("2024-01-01 23:59:59.999")
        assert "limit=3" in urlopen.call_args.args[0].full_url

    def test_symbol_normalization(self):
        provider = BinanceKLineProvider()
        assert provider._symbol(Instrument("BINANCE:BTCUSDT", "", "crypto")) == "BTCUSDT"
        assert provider._symbol(Instrument("BTC/USDT", "", "crypto")) == "BTCUSDT"


# ------------------------------------------------------------------
# CompositeKLineProvider
# ------------------------------------------------------------------

class TestCompositeKLineProvider:
    async def test_routes_a_to_eastmoney(self, sample_instrument_a):
        """A 股路由到 Eastmoney"""
        provider = CompositeKLineProvider()
        with patch.object(provider._eastmoney, "fetch", return_value=pd.DataFrame({"col": [1]})) as mock_e:
            with patch.object(provider._tencent, "fetch") as mock_t:
                with patch.object(provider._yahoo, "fetch") as mock_y:
                    frame = await provider.fetch(sample_instrument_a, lookback_days=5)
                    mock_e.assert_called_once()
                    mock_t.assert_not_called()
                    mock_y.assert_not_called()
        assert frame.attrs["source"] == "eastmoney_kline"
        assert frame.attrs["degradation_result"] == "success"

    async def test_a_falls_back_to_tencent(self, sample_instrument_a):
        provider = CompositeKLineProvider()
        fallback_frame = pd.DataFrame({"price": [101.0]})
        with patch.object(
            provider._eastmoney, "fetch", return_value=pd.DataFrame()
        ):
            with patch.object(
                provider._tencent, "fetch", return_value=fallback_frame
            ):
                frame = await provider.fetch(sample_instrument_a, lookback_days=5)

        assert frame.attrs["source"] == "tencent_kline"
        assert frame.attrs["primary_source"] == "eastmoney_kline"
        assert frame.attrs["fallback_source"] == "tencent_kline"
        assert frame.attrs["degradation_result"] == "fallback_success"
        assert frame.attrs["errors"] == {
            "eastmoney_kline": "provider returned empty frame"
        }

    async def test_a_all_sources_empty_reports_errors(self, sample_instrument_a):
        provider = CompositeKLineProvider()
        with patch.object(
            provider._eastmoney, "fetch", return_value=pd.DataFrame()
        ):
            with patch.object(
                provider._tencent, "fetch", return_value=pd.DataFrame()
            ):
                frame = await provider.fetch(sample_instrument_a, lookback_days=5)

        assert frame.empty
        assert frame.attrs["degradation_result"] == "empty"
        assert frame.attrs["errors"] == {
            "eastmoney_kline": "provider returned empty frame",
            "tencent_kline": "provider returned empty frame",
        }

    async def test_routes_us_to_nasdaq(self, sample_instrument_us):
        """美股主路由到 Nasdaq"""
        provider = CompositeKLineProvider()
        with patch.object(provider._nasdaq, "fetch", return_value=pd.DataFrame({"col": [1]})) as mock_n:
            with patch.object(provider._yahoo, "fetch") as mock_y:
                await provider.fetch(sample_instrument_us, lookback_days=5)
                mock_n.assert_called_once()
                mock_y.assert_not_called()

    async def test_us_falls_back_to_yahoo(self, sample_instrument_us):
        provider = CompositeKLineProvider()
        with patch.object(provider._nasdaq, "fetch", return_value=pd.DataFrame()):
            with patch.object(
                provider._yahoo, "fetch", return_value=pd.DataFrame({"price": [1]})
            ):
                frame = await provider.fetch(sample_instrument_us, lookback_days=5)
        assert frame.attrs["source"] == "yahoo_kline"
        assert frame.attrs["degradation_result"] == "fallback_success"

    async def test_routes_crypto_to_binance(self, sample_instrument_crypto):
        """加密货币主路由到 Binance"""
        provider = CompositeKLineProvider()
        with patch.object(provider._binance, "fetch", return_value=pd.DataFrame({"col": [1]})) as mock_b:
            with patch.object(provider._yahoo, "fetch") as mock_y:
                await provider.fetch(sample_instrument_crypto, lookback_days=5)
                mock_b.assert_called_once()
                mock_y.assert_not_called()

    async def test_crypto_falls_back_to_yahoo(self, sample_instrument_crypto):
        provider = CompositeKLineProvider()
        with patch.object(provider._binance, "fetch", return_value=pd.DataFrame()):
            with patch.object(
                provider._yahoo, "fetch", return_value=pd.DataFrame({"price": [1]})
            ):
                frame = await provider.fetch(sample_instrument_crypto, lookback_days=5)
        assert frame.attrs["source"] == "yahoo_kline"
        assert frame.attrs["primary_source"] == "binance_kline"
        assert frame.attrs["degradation_result"] == "fallback_success"

    async def test_fetch_batch(self, sample_instrument_a, sample_instrument_us):
        """批量并行获取"""
        provider = CompositeKLineProvider()
        with patch.object(provider._eastmoney, "fetch", return_value=pd.DataFrame({"col": [1]})):
            with patch.object(provider._tencent, "fetch"):
                with patch.object(provider._nasdaq, "fetch", return_value=pd.DataFrame({"col": [2]})):
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

def _find_report(report: list[dict], symbol: str) -> dict:
    """辅助: 从结构化报告中按 symbol 挑一项。"""
    for item in report:
        if item["symbol"] == symbol:
            return item
    raise AssertionError(f"symbol {symbol} not in report: {report}")


class TestWarmHistoryCache:
    async def test_warm_empty_cache(self, tmp_path, sample_instrument_a):
        """空缓存应 warm 数据,并返回 status=ok 的结构化条目(D0-3)"""
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

        report = await warm_history_cache(cache, provider, [sample_instrument_a], 5)
        await cache.close()

        assert isinstance(report, list) and len(report) == 1
        item = _find_report(report, "a:000300")
        assert item["status"] == "ok"
        assert item["rows"] == 5
        assert item["source"] == "eastmoney_kline"
        assert item["error"] is None
        provider.fetch.assert_called_once_with(sample_instrument_a, 5)

    async def test_warm_report_exposes_tencent_fallback(
        self, tmp_path, sample_instrument_a
    ):
        cache = HistoryCache(base_dir=str(tmp_path), ttl=86400)
        provider = CompositeKLineProvider()
        fallback = pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=5, freq="D"),
            "code": ["000300"] * 5,
            "name": ["沪深300"] * 5,
            "market": ["a"] * 5,
            "price": [100.0, 101.0, 102.0, 103.0, 104.0],
            "open_price": [100.0] * 5,
            "high": [102.0] * 5,
            "low": [98.0] * 5,
            "prev_close": [100.0, 100.0, 101.0, 102.0, 103.0],
            "volume_lot": [1_000_000] * 5,
            "data_source": ["provider"] * 5,
        })
        with patch.object(
            provider._eastmoney, "fetch", return_value=pd.DataFrame()
        ):
            with patch.object(provider._tencent, "fetch", return_value=fallback):
                report = await warm_history_cache(
                    cache, provider, [sample_instrument_a], 5
                )
        await cache.close()

        item = _find_report(report, "a:000300")
        assert item["status"] == "ok"
        assert item["source"] == "tencent_kline"
        assert item["primary_source"] == "eastmoney_kline"
        assert item["fallback_source"] == "tencent_kline"
        assert item["degradation_result"] == "fallback_success"
        assert item["errors"] == {
            "eastmoney_kline": "provider returned empty frame"
        }

    async def test_skip_sufficient_cache(self, tmp_path, sample_instrument_a):
        """数据充足时跳过 warm,状态为 skipped_cached(D0-3)"""
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

        report = await warm_history_cache(cache, provider, [sample_instrument_a], 5)
        await cache.close()

        item = _find_report(report, "a:000300")
        assert item["status"] == "skipped_cached"
        assert item["error"] is None
        assert item["rows"] >= 5
        provider.fetch.assert_not_called()

    async def test_warm_failure_continues(self, tmp_path, sample_instrument_a, sample_instrument_us):
        """单个标的失败不影响其他;失败项记录 error 字符串(D0-3)"""
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

        report = await warm_history_cache(cache, provider, [sample_instrument_a, sample_instrument_us], 5)
        await cache.close()

        a_item = _find_report(report, "a:000300")
        us_item = _find_report(report, "us:QQQ")
        assert a_item["status"] == "failed"
        assert a_item["rows"] == 0
        assert a_item["error"] is not None and "API down" in a_item["error"]
        assert a_item["source"] == "eastmoney_kline"
        assert us_item["status"] == "ok"
        assert us_item["rows"] == 3
        assert us_item["source"] == "nasdaq_kline"
        assert us_item["error"] is None

    async def test_warm_empty_provider_response(self, tmp_path, sample_instrument_a):
        """Provider 返回空数据时 status=failed(D0-3)"""
        cache = HistoryCache(base_dir=str(tmp_path), ttl=86400)
        provider = Mock()
        provider.fetch = AsyncMock(return_value=pd.DataFrame(columns=[
            "timestamp", "code", "name", "market", "price", "open_price",
            "high", "low", "prev_close", "volume_lot",
        ]))

        report = await warm_history_cache(cache, provider, [sample_instrument_a], 5)
        await cache.close()

        item = _find_report(report, "a:000300")
        assert item["status"] == "failed"
        assert item["rows"] == 0
        assert item["error"] == "provider returned empty frame"
