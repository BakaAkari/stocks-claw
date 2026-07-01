"""DataFetcher 降级链测试 — 覆盖主 Provider 成功/重试/切备用/全失败场景"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from stocks.domain.models import Instrument, Quote
from stocks.engine.fetchers import DataFetcher
from stocks.errors import (
    DegradationRecord,
    ProviderAuthError,
    ProviderDataError,
    ProviderNetworkError,
    ProviderTimeoutError,
)
from stocks.providers.registry import ProviderRegistry

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def sample_instruments():
    return [
        Instrument(code="000300", name="沪深300", market="a"),
        Instrument(code="000001", name="平安银行", market="a"),
    ]


@pytest.fixture
def success_quotes():
    return [
        Quote(Instrument(code="000300", name="沪深300", market="a"), price=3540.0, change=10.0, pct_change=0.28),
        Quote(Instrument(code="000001", name="平安银行", market="a"), price=11.5, change=0.1, pct_change=0.88),
    ]


@pytest.fixture
def registry_with_single_provider():
    """只有一个 Provider 的 Registry"""
    registry = ProviderRegistry()
    provider = Mock()
    provider.name = "tencent_a"
    provider.supported_markets = ["a"]
    provider.fetch_batch = AsyncMock()
    registry.register(provider)
    # 设置 default
    registry._defaults = {"a": provider}
    return registry, provider


@pytest.fixture
def registry_with_two_providers():
    """有两个 Provider（主 + 备用）的 Registry"""
    registry = ProviderRegistry()

    primary = Mock()
    primary.name = "tencent_a"
    primary.supported_markets = ["a"]
    primary.fetch_batch = AsyncMock()
    registry.register(primary)

    fallback = Mock()
    fallback.name = "eastmoney_a"
    fallback.supported_markets = ["a"]
    fallback.fetch_batch = AsyncMock()
    registry.register(fallback)

    registry._defaults = {"a": primary}
    return registry, primary, fallback


# ------------------------------------------------------------------
# 正常场景
# ------------------------------------------------------------------

class TestFetchQuotesNormal:
    """主 Provider 正常返回场景"""

    @pytest.mark.asyncio
    async def test_single_market_success(self, registry_with_single_provider, sample_instruments, success_quotes):
        """单市场、单 Provider、正常返回"""
        registry, provider = registry_with_single_provider
        provider.fetch_batch.return_value = success_quotes

        fetcher = DataFetcher(registry, max_retries=0)
        result = await fetcher.fetch_quotes(sample_instruments)

        assert "a" in result
        assert len(result["a"]) == 2
        assert result["a"][0].instrument.code == "000300"
        assert result["a"][1].instrument.code == "000001"

        # 无降级记录
        log = fetcher.get_degradation_log()
        assert len(log) == 1
        assert log[0].result == "success"
        assert log[0].primary_provider == "tencent_a"
        assert log[0].fallback_provider is None

    @pytest.mark.asyncio
    async def test_multiple_markets(self, registry_with_single_provider):
        """多市场并行获取"""
        registry, provider = registry_with_single_provider
        # 只支持 a 市场，us 市场无 Provider
        a_instruments = [
            Instrument(code="000300", name="沪深300", market="a"),
        ]
        us_instruments = [
            Instrument(code="QQQ", name="纳指100", market="us"),
        ]
        provider.fetch_batch.return_value = [
            Quote(a_instruments[0], price=3540.0, change=10.0, pct_change=0.28),
        ]

        fetcher = DataFetcher(registry, max_retries=0)
        result = await fetcher.fetch_quotes(a_instruments + us_instruments)

        assert "a" in result
        assert len(result["a"]) == 1
        # us 市场无 Provider，返回空列表
        assert "us" in result
        assert result["us"] == []

    @pytest.mark.asyncio
    async def test_empty_instruments(self, registry_with_single_provider):
        """空列表 — 返回空字典，无降级记录"""
        registry, _ = registry_with_single_provider
        fetcher = DataFetcher(registry, max_retries=0)
        result = await fetcher.fetch_quotes([])

        assert result == {}
        assert fetcher.get_degradation_log() == []


# ------------------------------------------------------------------
# 降级链：重试场景
# ------------------------------------------------------------------

class TestFetchQuotesRetry:
    """主 Provider 可恢复失败 → 重试成功"""

    @pytest.mark.asyncio
    async def test_retry_once_then_success(self, registry_with_single_provider, sample_instruments, success_quotes):
        """第一次超时，第二次成功"""
        registry, provider = registry_with_single_provider
        provider.fetch_batch.side_effect = [
            ProviderTimeoutError("连接超时"),  # 第一次失败
            success_quotes,                       # 第二次成功
        ]

        fetcher = DataFetcher(registry, max_retries=1, retry_delay=0.01)
        result = await fetcher.fetch_quotes(sample_instruments)

        assert "a" in result
        assert len(result["a"]) == 2

        # 验证降级记录
        log = fetcher.get_degradation_log()
        assert len(log) == 1
        assert log[0].result == "success"
        assert log[0].message == "主 Provider tencent_a 成功获取 2 条行情"

        # 验证调用了两次
        assert provider.fetch_batch.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_exhausted_then_fallback(self, registry_with_two_providers, sample_instruments, success_quotes):
        """重试耗尽后切备用 Provider 成功"""
        registry, primary, fallback = registry_with_two_providers
        primary.fetch_batch.side_effect = [
            ProviderTimeoutError("连接超时"),  # 第一次
            ProviderTimeoutError("连接超时"),  # 重试后仍失败
        ]
        fallback.fetch_batch.return_value = success_quotes

        fetcher = DataFetcher(registry, max_retries=1, retry_delay=0.01)
        result = await fetcher.fetch_quotes(sample_instruments)

        assert "a" in result
        assert len(result["a"]) == 2

        # 验证降级记录
        log = fetcher.get_degradation_log()
        assert len(log) == 1
        assert log[0].result == "fallback_success"
        assert log[0].primary_provider == "tencent_a"
        assert log[0].fallback_provider == "eastmoney_a"

        # 主 Provider 被调用 2 次（原始 + 1 次重试），备用 1 次
        assert primary.fetch_batch.call_count == 2
        assert fallback.fetch_batch.call_count == 1


# ------------------------------------------------------------------
# 降级链：切备用失败
# ------------------------------------------------------------------

class TestFetchQuotesFallbackFail:
    """主 Provider 和备用 Provider 都失败"""

    @pytest.mark.asyncio
    async def test_all_providers_fail(self, registry_with_two_providers, sample_instruments):
        """主 Provider 超时，备用 Provider 也超时"""
        registry, primary, fallback = registry_with_two_providers
        primary.fetch_batch.side_effect = ProviderTimeoutError("连接超时")
        fallback.fetch_batch.side_effect = ProviderNetworkError("DNS 错误")

        fetcher = DataFetcher(registry, max_retries=0)
        result = await fetcher.fetch_quotes(sample_instruments)

        assert "a" in result
        assert result["a"] == []

        # 验证降级记录
        log = fetcher.get_degradation_log()
        assert len(log) == 1
        assert log[0].result == "empty"
        assert log[0].primary_provider == "tencent_a"
        assert log[0].fallback_provider == "eastmoney_a"
        assert "均失败" in log[0].message

    @pytest.mark.asyncio
    async def test_no_fallback_provider(self, registry_with_single_provider, sample_instruments):
        """只有一个 Provider，失败时无备用可切"""
        registry, provider = registry_with_single_provider
        provider.fetch_batch.side_effect = ProviderTimeoutError("连接超时")

        fetcher = DataFetcher(registry, max_retries=0)
        result = await fetcher.fetch_quotes(sample_instruments)

        assert result["a"] == []

        log = fetcher.get_degradation_log()
        assert log[0].result == "empty"
        assert log[0].fallback_provider is None
        assert log[0].primary_provider == "tencent_a"


# ------------------------------------------------------------------
# 降级链：不可恢复异常
# ------------------------------------------------------------------

class TestFetchQuotesNonRetryable:
    """不可恢复异常 — 不切备用，直接标记失败"""

    @pytest.mark.asyncio
    async def test_auth_error_no_fallback(self, registry_with_two_providers, sample_instruments):
        """认证错误（不可恢复）— 不应重试，也不切备用"""
        registry, primary, fallback = registry_with_two_providers
        primary.fetch_batch.side_effect = ProviderAuthError("API Key 无效")

        fetcher = DataFetcher(registry, max_retries=1, retry_delay=0.01)
        result = await fetcher.fetch_quotes(sample_instruments)

        assert result["a"] == []

        # 认证错误不可恢复，不调用备用 Provider
        assert fallback.fetch_batch.call_count == 0
        # 主 Provider 只调用 1 次（不重试）
        assert primary.fetch_batch.call_count == 1

        log = fetcher.get_degradation_log()
        assert log[0].result == "empty"
        assert log[0].error_type == "ProviderAuthError"
        assert log[0].error_retryable is False

    @pytest.mark.asyncio
    async def test_data_error_with_fallback(self, registry_with_two_providers, sample_instruments, success_quotes):
        """数据解析错误（可恢复）— 应切备用"""
        registry, primary, fallback = registry_with_two_providers
        primary.fetch_batch.side_effect = ProviderDataError("返回格式不匹配")
        fallback.fetch_batch.return_value = success_quotes

        fetcher = DataFetcher(registry, max_retries=0)
        result = await fetcher.fetch_quotes(sample_instruments)

        assert len(result["a"]) == 2
        assert fallback.fetch_batch.call_count == 1
        log = fetcher.get_degradation_log()
        assert log[0].result == "fallback_success"


# ------------------------------------------------------------------
# 降级链：Provider 批量失败 → 无备用时返回空
# ------------------------------------------------------------------

class TestFetchQuotesBatchFail:
    """Provider 批量接口失败，无备用 Provider 时返回空数据"""

    @pytest.mark.asyncio
    async def test_batch_fail_then_empty(self, registry_with_single_provider, sample_instruments):
        """fetch_batch 失败且无备用 Provider — 返回空 + 降级记录"""
        registry, provider = registry_with_single_provider

        async def _raise_timeout(*args, **kwargs):
            raise ProviderTimeoutError("批量超时")

        provider.fetch_batch = _raise_timeout

        fetcher = DataFetcher(registry, max_retries=0)
        result = await fetcher.fetch_quotes(sample_instruments)

        assert result["a"] == []
        log = fetcher.get_degradation_log()
        assert log[0].result == "empty"
        assert log[0].error_type == "ProviderTimeoutError"


# ------------------------------------------------------------------
# 异常转换测试
# ------------------------------------------------------------------

class TestExceptionConversion:
    """标准异常转换为 ProviderError 分层异常"""

    @pytest.mark.asyncio
    async def test_timeout_error_conversion(self, registry_with_single_provider, sample_instruments):
        """TimeoutError → ProviderTimeoutError"""
        registry, provider = registry_with_single_provider

        async def _raise_timeout(*args, **kwargs):
            raise TimeoutError("连接超时")
        provider.fetch_batch = _raise_timeout

        fetcher = DataFetcher(registry, max_retries=0)
        result = await fetcher.fetch_quotes(sample_instruments)

        assert result["a"] == []
        log = fetcher.get_degradation_log()
        assert log[0].error_type == "ProviderTimeoutError"
        assert log[0].error_retryable is True

    @pytest.mark.asyncio
    async def test_connection_error_conversion(self, registry_with_single_provider, sample_instruments):
        """ConnectionError → ProviderNetworkError"""
        registry, provider = registry_with_single_provider

        async def _raise_conn(*args, **kwargs):
            raise ConnectionError("连接被拒绝")
        provider.fetch_batch = _raise_conn

        fetcher = DataFetcher(registry, max_retries=0)
        await fetcher.fetch_quotes(sample_instruments)

        log = fetcher.get_degradation_log()
        assert log[0].error_type == "ProviderNetworkError"
        assert log[0].error_retryable is True

    @pytest.mark.asyncio
    async def test_value_error_conversion(self, registry_with_single_provider, sample_instruments):
        """ValueError → ProviderDataError"""
        registry, provider = registry_with_single_provider

        async def _raise_value(*args, **kwargs):
            raise ValueError("JSON 解析失败")
        provider.fetch_batch = _raise_value

        fetcher = DataFetcher(registry, max_retries=0)
        await fetcher.fetch_quotes(sample_instruments)

        log = fetcher.get_degradation_log()
        assert log[0].error_type == "ProviderDataError"
        assert log[0].error_retryable is True

    @pytest.mark.asyncio
    async def test_generic_exception_conversion(self, registry_with_single_provider, sample_instruments):
        """未分类异常 → ProviderError（is_retryable=False）"""
        registry, provider = registry_with_single_provider

        async def _raise_runtime(*args, **kwargs):
            raise RuntimeError("未知错误")
        provider.fetch_batch = _raise_runtime

        fetcher = DataFetcher(registry, max_retries=0)
        await fetcher.fetch_quotes(sample_instruments)

        log = fetcher.get_degradation_log()
        assert log[0].error_type == "ProviderError"
        assert log[0].error_retryable is False

    @pytest.mark.asyncio
    async def test_auth_error_conversion(self, registry_with_single_provider, sample_instruments):
        """ProviderAuthError 直接透传，不转换"""
        registry, provider = registry_with_single_provider

        async def _raise_auth(*args, **kwargs):
            raise ProviderAuthError("API Key 无效")
        provider.fetch_batch = _raise_auth

        fetcher = DataFetcher(registry, max_retries=0)
        await fetcher.fetch_quotes(sample_instruments)

        log = fetcher.get_degradation_log()
        assert log[0].error_type == "ProviderAuthError"
        assert log[0].error_retryable is False


# ------------------------------------------------------------------
# DegradationRecord 测试
# ------------------------------------------------------------------

class TestDegradationRecord:
    """降级记录序列化与展示"""

    def test_to_dict_success(self):
        record = DegradationRecord(
            market="a",
            primary_provider="tencent_a",
            result="success",
            message="正常",
        )
        d = record.to_dict()
        assert d["market"] == "a"
        assert d["primary_provider"] == "tencent_a"
        assert d["fallback_provider"] is None
        assert d["error_type"] is None
        assert d["result"] == "success"

    def test_to_dict_with_error(self):
        error = ProviderTimeoutError("超时", source="tencent_a")
        record = DegradationRecord(
            market="a",
            primary_provider="tencent_a",
            fallback_provider="eastmoney_a",
            error=error,
            result="empty",
            message="全部失败",
        )
        d = record.to_dict()
        assert d["error_type"] == "ProviderTimeoutError"
        assert d["error_retryable"] is True

    def test_repr(self):
        record = DegradationRecord(
            market="a",
            primary_provider="tencent_a",
            fallback_provider="eastmoney_a",
            result="fallback_success",
        )
        assert "tencent_a" in repr(record)
        assert "eastmoney_a" in repr(record)
        assert "fallback_success" in repr(record)
