"""MacroData 测试 — 覆盖快照模型、静态提供者、组合降级链、Yahoo 模拟

测试策略：
- StaticMacroProvider 不依赖网络，直接验证
- CompositeMacroProvider 使用 Mock 验证降级逻辑
- YahooFinanceMacroProvider 使用 unittest.mock 模拟 urllib 响应
- 所有测试独立，不依赖外部网络或 Yahoo Finance API 可用性
"""

from __future__ import annotations

import json
from unittest.mock import Mock, patch

from stocks.engine.macro_data import (
    CompositeMacroProvider,
    MacroSnapshot,
    StaticMacroProvider,
    YahooFinanceMacroProvider,
)

# ------------------------------------------------------------------
# 数据模型
# ------------------------------------------------------------------

class TestMacroSnapshot:
    def test_default_empty(self):
        """默认空快照所有字段为 None"""
        s = MacroSnapshot()
        assert s.usd_cny is None
        assert s.vix is None
        assert s.us_10y_yield is None
        assert s.dxy is None
        assert s.gold is None
        assert s.crude_oil is None
        assert s.source == "yahoo_finance"
        assert s.errors == {}

    def test_to_dict(self):
        """to_dict 应包含所有字段"""
        s = MacroSnapshot(vix=20.5, usd_cny=7.25, source="test")
        d = s.to_dict()
        assert d["vix"] == 20.5
        assert d["usd_cny"] == 7.25
        assert d["us_10y_yield"] is None
        assert d["source"] == "test"


# ------------------------------------------------------------------
# 静态提供者
# ------------------------------------------------------------------

class TestStaticMacroProvider:
    async def test_fetch_basic(self):
        """静态配置应正确返回"""
        config = {
            "usd_cny": 7.25,
            "vix": 20.5,
            "us_10y_yield": 4.2,
        }
        provider = StaticMacroProvider(config)
        snapshot = await provider.fetch()

        assert snapshot.usd_cny == 7.25
        assert snapshot.vix == 20.5
        assert snapshot.us_10y_yield == 4.2
        assert snapshot.dxy is None
        assert snapshot.source == "static_config"

    async def test_fetch_empty(self):
        """空配置返回空快照"""
        provider = StaticMacroProvider({})
        snapshot = await provider.fetch()
        assert snapshot.usd_cny is None
        assert snapshot.source == "static_config"


# ------------------------------------------------------------------
# Yahoo Finance 提供者（Mock 测试）
# ------------------------------------------------------------------

class TestYahooFinanceMacroProvider:
    async def test_fetch_success(self):
        """模拟 Yahoo Finance 成功响应"""
        mock_response = Mock()
        mock_response.read.return_value = json.dumps({
            "chart": {
                "result": [{
                    "meta": {"regularMarketPrice": 20.5, "previousClose": 19.0}
                }]
            }
        }).encode("utf-8")
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            provider = YahooFinanceMacroProvider(timeout=5.0)
            snapshot = await provider.fetch()

        # 所有 6 个指标都会成功（mock 返回相同数据）
        assert snapshot.vix is not None
        assert snapshot.vix == 20.5
        assert snapshot.usd_cny is not None
        assert snapshot.errors == {}

    async def test_fetch_partial_failure(self):
        """部分指标失败时，其他指标应正常"""
        call_count = 0

        def mock_urlopen(req, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_resp = Mock()
            if call_count % 2 == 0:
                # 偶数次调用失败
                raise Exception("Simulated error")
            mock_resp.read.return_value = json.dumps({
                "chart": {"result": [{"meta": {"regularMarketPrice": 10.0}}]}
            }).encode("utf-8")
            mock_resp.__enter__ = Mock(return_value=mock_resp)
            mock_resp.__exit__ = Mock(return_value=False)
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            provider = YahooFinanceMacroProvider(timeout=5.0)
            snapshot = await provider.fetch()

        # 一半成功一半失败
        assert len(snapshot.errors) > 0
        assert len(snapshot.errors) < 6
        # 至少有一些成功数据
        assert any([snapshot.vix, snapshot.usd_cny, snapshot.dxy])

    async def test_fetch_all_failure(self):
        """全部失败时返回空数据 + 错误记录"""
        def mock_urlopen(req, **kwargs):
            raise Exception("Network error")

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            provider = YahooFinanceMacroProvider(timeout=5.0)
            snapshot = await provider.fetch()

        assert snapshot.vix is None
        assert snapshot.usd_cny is None
        assert len(snapshot.errors) == 6  # 6 个指标全部失败

    async def test_fetch_empty_response(self):
        """API 返回空结果时记录错误"""
        mock_response = Mock()
        mock_response.read.return_value = json.dumps({
            "chart": {"result": [None]}
        }).encode("utf-8")
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            provider = YahooFinanceMacroProvider(timeout=5.0)
            snapshot = await provider.fetch()

        assert len(snapshot.errors) > 0

    async def test_fetch_previous_close_fallback(self):
        """regularMarketPrice 缺失时应使用 previousClose"""
        mock_response = Mock()
        mock_response.read.return_value = json.dumps({
            "chart": {
                "result": [{
                    "meta": {"previousClose": 15.0}
                }]
            }
        }).encode("utf-8")
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            provider = YahooFinanceMacroProvider(timeout=5.0)
            snapshot = await provider.fetch()

        assert snapshot.vix == 15.0


# ------------------------------------------------------------------
# 组合提供者（降级链）
# ------------------------------------------------------------------

class TestCompositeMacroProvider:
    async def test_first_success(self):
        """第一个提供者成功时直接返回"""
        p1 = StaticMacroProvider({"vix": 20.0})
        p2 = StaticMacroProvider({"vix": 30.0})

        composite = CompositeMacroProvider([p1, p2])
        snapshot = await composite.fetch()

        assert snapshot.vix == 20.0  # 使用第一个

    async def test_fallback_to_second(self):
        """第一个失败时降级到第二个"""
        class FailingProvider:
            async def fetch(self):
                raise Exception("Always fails")

        p1 = FailingProvider()
        p2 = StaticMacroProvider({"vix": 30.0})

        composite = CompositeMacroProvider([p1, p2])
        snapshot = await composite.fetch()

        assert snapshot.vix == 30.0

    async def test_all_fail(self):
        """全部失败时返回空快照"""
        class FailingProvider:
            async def fetch(self):
                raise Exception("Always fails")

        composite = CompositeMacroProvider([FailingProvider(), FailingProvider()])
        snapshot = await composite.fetch()

        assert snapshot.vix is None
        assert snapshot.source == "all_failed"

    async def test_empty_data_fallback(self):
        """第一个返回空数据时降级到第二个"""
        p1 = StaticMacroProvider({})  # 空数据
        p2 = StaticMacroProvider({"vix": 25.0})

        composite = CompositeMacroProvider([p1, p2])
        snapshot = await composite.fetch()

        assert snapshot.vix == 25.0

    async def test_no_empty_data_fallback(self):
        """第一个有部分数据时直接使用（不要求全部成功）"""
        p1 = StaticMacroProvider({"vix": 20.0})  # 只有 vix 有数据
        p2 = StaticMacroProvider({"vix": 30.0, "usd_cny": 7.0})

        composite = CompositeMacroProvider([p1, p2])
        snapshot = await composite.fetch()

        assert snapshot.vix == 20.0  # 使用第一个，即使不完整
