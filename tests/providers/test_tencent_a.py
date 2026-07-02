"""腾讯 A 股完整行情格式解析测试。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from stocks.domain.models import Instrument
from stocks.providers.tencent_a import TencentAQuoteProvider

# 2026-07-02 真实完整格式响应；量额已与同一时点东方财富响应逐字段核对：
# index 6 == f5（成交量），index 37 == f6 / 10000（成交额万元）。
FULL_SH_000300 = (
    'v_sh000300="1~沪深300~000300~4812.30~4958.98~4865.17~343243758~0~0~0.00~0~0.00~'
    '0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~~20260702161406~'
    '-146.68~-2.96~4896.99~4800.48~4812.30/343243758/1027796834233~343243758~102779683~'
    '1.03~14.24~~4896.99~4800.48~1.95~523762.89~553320.25~0.00~-1~-1~1.03~0~4856.49~'
    '~~~~~102779683.4233~0.0000~0~ ~ZS~3.94~-4.14~~~~5064.27~3945.14~-2.42~-2.56~7.44~'
    '3341313124466~~-4.82~5.76~3341313124466~~~22.03~0.07~~CNY~0~~0.00~0~";'
)
FULL_SH_518880 = (
    'v_sh518880="1~黄金ETF华安~518880~8.475~8.271~8.425~5149790~2668691~2481099~8.474~'
    '40221~8.473~624~8.472~3840~8.471~892~8.470~453~8.475~478~8.476~1044~8.477~856~'
    '8.478~343~8.479~689~~20260702161439~0.204~2.47~8.484~8.397~'
    '8.475/5149790/4346984456~5149790~434698~5.01~~~8.484~8.397~1.05~870.58~870.58~'
    '0.00~9.098~7.444~1.31~42620~8.441~~~~~~434698.4456~0.0000~0~ ~ETF~-8.88~2.27~~~~'
    '11.977~7.304~-5.38~-8.68~-12.97~10272340800~10272340800~86.21~3.86~10272340800~'
    '0.21~8.4573~14.62~0.08~8.2753~CNY~0~___D__F__Y~8.469~8871~";'
)


class FakeResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _response(*lines: str) -> FakeResponse:
    return FakeResponse("\n".join(lines).encode("gbk"))


@pytest.fixture
def provider():
    return TencentAQuoteProvider()


async def test_fetch_single_full_quote_with_ohlc_and_real_as_of(provider):
    instrument = Instrument("000300", "沪深300", "a", "sh_index")

    with patch("urllib.request.urlopen", return_value=_response(FULL_SH_000300)):
        quote = await provider.fetch(instrument)

    assert quote is not None
    assert quote.instrument is instrument
    assert quote.price == 4812.30
    assert quote.prev_close == 4958.98
    assert quote.open_price == 4865.17
    assert quote.change == -146.68
    assert quote.pct_change == -2.96
    assert quote.high == 4896.99
    assert quote.low == 4800.48
    assert quote.volume_lot == 343243758
    assert quote.amount_10k == 102779683
    assert quote.source == "tencent_a"
    assert quote.as_of == "2026-07-02T08:14:06+00:00"


async def test_fetch_batch_full_quotes(provider):
    instruments = [
        Instrument("000300", "沪深300", "a", "sh_index"),
        Instrument("518880", "黄金ETF", "a", "sh"),
    ]

    with patch(
        "urllib.request.urlopen",
        return_value=_response(FULL_SH_000300, FULL_SH_518880),
    ):
        quotes = await provider.fetch_batch(instruments)

    assert [quote.instrument for quote in quotes] == instruments
    assert quotes[1].price == 8.475
    assert quotes[1].prev_close == 8.271
    assert quotes[1].open_price == 8.425
    assert quotes[1].high == 8.484
    assert quotes[1].low == 8.397
    assert quotes[1].volume_lot == 5149790
    assert quotes[1].amount_10k == 434698
    assert quotes[1].as_of == "2026-07-02T08:14:39+00:00"


def test_build_symbol_uses_full_format_without_s_prefix(provider):
    assert provider._build_symbol(Instrument("600519", "贵州茅台", "a", "sh")) == "sh600519"
    assert provider._build_symbol(Instrument("000001", "平安银行", "a", "sz")) == "sz000001"
    assert provider._prefix(Instrument("600000", "浦发银行", "a")) == "sh"
    assert provider._prefix(Instrument("000002", "万科", "a")) == "sz"


async def test_network_timeout_returns_none(provider):
    instrument = Instrument("000300", "沪深300", "a", "sh_index")
    with patch("urllib.request.urlopen", side_effect=TimeoutError("连接超时")):
        assert await provider.fetch(instrument) is None


async def test_empty_response_returns_none(provider):
    instrument = Instrument("000300", "沪深300", "a", "sh_index")
    with patch("urllib.request.urlopen", return_value=FakeResponse(b"")):
        assert await provider.fetch(instrument) is None


@pytest.mark.parametrize(
    "line",
    [
        "random text without delimiter",
        'v_sh000300="1~沪深300~000300~4812.30";',
        'v_sh000300="' + "~".join(["1"] * 37) + '";',
    ],
)
def test_malformed_or_incomplete_line_returns_none(provider, line):
    assert provider._parse_line(line) is None


def test_invalid_time_keeps_quote_but_as_of_is_none(provider):
    line = FULL_SH_000300.replace("20260702161406", "20260702")
    quote = provider._parse_line(line)

    assert quote is not None
    assert quote.price == 4812.30
    assert quote.as_of is None


def test_parse_line_without_instrument_map_builds_instrument(provider):
    quote = provider._parse_line(FULL_SH_000300)

    assert quote is not None
    assert quote.instrument.code == "000300"
    assert quote.instrument.name == "沪深300"


async def test_batch_skips_invalid_lines(provider):
    instruments = [
        Instrument("000300", "沪深300", "a", "sh_index"),
        Instrument("INVALID", "无效代码", "a"),
    ]
    with patch(
        "urllib.request.urlopen",
        return_value=_response(FULL_SH_000300, "invalid"),
    ):
        quotes = await provider.fetch_batch(instruments)

    assert len(quotes) == 1
    assert quotes[0].instrument is instruments[0]


async def test_fetch_batch_empty_does_not_request_network(provider):
    with patch("urllib.request.urlopen") as urlopen:
        assert await provider.fetch_batch([]) == []
    urlopen.assert_not_called()
