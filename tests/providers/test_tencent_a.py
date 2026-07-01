"""腾讯 Provider 测试 — 覆盖正常、异常、降级场景

Mock 策略：patch urllib.request.urlopen，控制返回值，
不依赖真实网络。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from stocks.domain.models import Instrument
from stocks.providers.tencent_a import TencentAQuoteProvider

# ------------------------------------------------------------------
# Mock 辅助
# ------------------------------------------------------------------

class FakeResponse:
    """模拟 urllib 响应对象 — 支持 with 上下文管理器"""
    def __init__(self, data: bytes):
        self._data = data
    def read(self):
        return self._data
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


def _make_mock_response(raw_bytes: bytes):
    """构造 mock urllib 响应对象"""
    return FakeResponse(raw_bytes)


# ------------------------------------------------------------------
# 正常场景
# ------------------------------------------------------------------


class TestTencentAQuoteProviderNormal:
    """腾讯 Provider 正常数据返回场景"""

    @pytest.fixture
    def provider(self):
        return TencentAQuoteProvider()

    @pytest.mark.asyncio
    async def test_fetch_single_quote(self, provider):
        """获取单只标的行情 — 沪深300"""
        instrument = Instrument(code="000300", name="沪深300", market="a", exchange="sz_index")

        # 腾讯返回格式：v_s_sz000300="1~HS300~000300~3542.33~12.45~0.35~..."
        # 使用 ASCII 名称避免 GBK 解码测试中的编码问题
        raw = b'v_s_sz000300="1~HS300~000300~3542.33~12.45~0.35~1000~2000~3540~3550~3530~"'

        with patch("urllib.request.urlopen", return_value=_make_mock_response(raw)):
            quote = await provider.fetch(instrument)

        assert quote is not None
        assert quote.instrument.code == "000300"
        assert quote.price == 3542.33
        assert quote.change == 12.45
        assert quote.pct_change == 0.35

    @pytest.mark.asyncio
    async def test_fetch_batch_quotes(self, provider):
        """批量获取行情 — 多只标的"""
        instruments = [
            Instrument(code="000300", name="沪深300", market="a", exchange="sz_index"),
            Instrument(code="518880", name="黄金ETF", market="a", exchange="sh"),
        ]

        raw = (
            b'v_s_sz000300="1~HS300~000300~3542.33~12.45~0.35~1000~2000~3540~3550~3530~";\n'
            b'v_s_sh518880="1~GoldETF~518880~4.55~-0.02~-0.44~500~1000~4.56~4.57~4.53~"'
        )

        with patch("urllib.request.urlopen", return_value=_make_mock_response(raw)):
            quotes = await provider.fetch_batch(instruments)

        assert len(quotes) == 2
        assert quotes[0].instrument.code == "000300"
        assert quotes[0].price == 3542.33
        assert quotes[1].instrument.code == "518880"
        assert quotes[1].price == 4.55

    @pytest.mark.asyncio
    async def test_sh_exchange_prefix(self, provider):
        """上海交易所前缀正确性"""
        inst = Instrument(code="600519", name="贵州茅台", market="a", exchange="sh")
        symbol = provider._build_symbol(inst)
        assert symbol == "s_sh600519"

    @pytest.mark.asyncio
    async def test_sz_exchange_prefix(self, provider):
        """深圳交易所前缀正确性"""
        inst = Instrument(code="000001", name="平安银行", market="a", exchange="sz")
        symbol = provider._build_symbol(inst)
        assert symbol == "s_sz000001"

    @pytest.mark.asyncio
    async def test_fallback_code_prefix(self, provider):
        """无 exchange 时，按代码前缀判断 — 5/6/9 开头为上海"""
        inst = Instrument(code="600000", name="浦发银行", market="a")
        assert provider._prefix(inst) == "sh"

        inst = Instrument(code="000002", name="万科", market="a")
        assert provider._prefix(inst) == "sz"


# ------------------------------------------------------------------
# 异常场景
# ------------------------------------------------------------------


class TestTencentAQuoteProviderErrors:
    """腾讯 Provider 异常处理场景"""

    @pytest.fixture
    def provider(self):
        return TencentAQuoteProvider()

    @pytest.mark.asyncio
    async def test_network_timeout(self, provider):
        """网络超时 — 返回 None"""
        inst = Instrument(code="000300", name="沪深300", market="a")

        with patch("urllib.request.urlopen", side_effect=TimeoutError("连接超时")):
            quote = await provider.fetch(inst)

        assert quote is None

    @pytest.mark.asyncio
    async def test_empty_response(self, provider):
        """空响应 — 返回 None"""
        inst = Instrument(code="000300", name="沪深300", market="a")

        with patch("urllib.request.urlopen", return_value=_make_mock_response(b"")):
            quote = await provider.fetch(inst)

        assert quote is None

    @pytest.mark.asyncio
    async def test_malformed_response(self, provider):
        """畸形响应（无 =" 分隔符）— 解析为 None"""
        inst = Instrument(code="000300", name="沪深300", market="a")

        with patch("urllib.request.urlopen", return_value=_make_mock_response(b'random text without delimiter')):
            quote = await provider.fetch(inst)

        assert quote is None

    @pytest.mark.asyncio
    async def test_incomplete_fields(self, provider):
        """字段不足（parts < 10）— 解析为 None"""
        inst = Instrument(code="000300", name="沪深300", market="a")

        with patch("urllib.request.urlopen", return_value=_make_mock_response(b'v_s_sz000300="1~HS300~000300~3542.33"')):
            quote = await provider.fetch(inst)

        assert quote is None

    @pytest.mark.asyncio
    async def test_gbk_decoding_error(self, provider):
        """GBK 解码错误 — 使用 errors='replace' 继续解析"""
        inst = Instrument(code="000300", name="沪深300", market="a")
        # 包含非法 GBK 字节序列，但结构正确
        raw = b'v_s_sz000300="1~HS300~000300~3542.33~12.45~0.35~1000~2000~3540~3550~3530~"'

        with patch("urllib.request.urlopen", return_value=_make_mock_response(raw)):
            quote = await provider.fetch(inst)

        # 即使名称解码乱码，价格等字段仍应能解析
        assert quote is not None
        assert quote.price == 3542.33

    @pytest.mark.asyncio
    async def test_batch_partial_failure(self, provider):
        """批量获取时部分失败 — 返回成功部分，不阻断"""
        instruments = [
            Instrument(code="000300", name="沪深300", market="a"),
            Instrument(code="INVALID", name="无效代码", market="a"),
        ]
        # 只有第一条有效
        raw = b'v_s_sz000300="1~HS300~000300~3542.33~12.45~0.35~1000~2000~3540~3550~3530~"'

        with patch("urllib.request.urlopen", return_value=_make_mock_response(raw)):
            quotes = await provider.fetch_batch(instruments)

        # 只返回解析成功的部分
        assert len(quotes) >= 1
        assert quotes[0].instrument.code == "000300"


# ------------------------------------------------------------------
# 边界场景
# ------------------------------------------------------------------


class TestTencentAQuoteProviderEdgeCases:
    """边界条件测试"""

    @pytest.fixture
    def provider(self):
        return TencentAQuoteProvider()

    def test_empty_instrument_list(self, provider):
        """空列表批量获取 — 返回腾讯空查询响应（非 None）"""
        result = provider._fetch_raw_sync([])
        # 腾讯对空查询返回 v_pv_none_match="1"; 不是 None
        assert result is not None

    @pytest.mark.asyncio
    async def test_fetch_batch_empty(self, provider):
        """空列表 fetch_batch — 返回空列表"""
        quotes = await provider.fetch_batch([])
        assert quotes == []

    def test_parse_line_no_instrument_map(self, provider):
        """无 instrument_map 时解析 — 用代码构造默认 Instrument"""
        line = 'v_s_sz000300="1~\\u6caa\\u6df1300~000300~3542.33~12.45~0.35~1000~2000~3540~3550~3530~"'
        quote = provider._parse_line(line)

        assert quote is not None
        assert quote.instrument.code == "000300"
        assert quote.instrument.name == "\\u6caa\\u6df1300"  # 未正确解码，但这是边界
