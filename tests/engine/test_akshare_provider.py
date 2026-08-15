"""A股财报日历 (AkShareEarningsProvider) 测试 (2026-08-15)。

monkeypatch akshare 的 cninfo 接口, 让真实 _fetch_sync 过滤逻辑被执行, 锁定:
1. 只查 market == "a" 标的; 美股/非6位代码跳过
2. 标题关键词过滤 (只保留财报类公告)
3. KeyError (非上市标的) 静默跳过, 不记 error
4. 事件转换
5. 缓存命中/未命中
6. akshare 未安装时返回空
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

from stocks.domain.models import Instrument
from stocks.engine.event_calendar import AkShareEarningsProvider

_NOW = date(2026, 8, 15)


def _run(coro):
    return asyncio.run(coro)


def _install_fake_akshare(monkeypatch, rows_by_symbol):
    """monkeypatch akshare.stock_zh_a_disclosure_report_cninfo 返回预设 DataFrame。"""
    import pandas as pd

    import akshare as ak

    def fake_cninfo(symbol, market, start_date, end_date):
        rows = rows_by_symbol.get(symbol, [])
        if rows is None:  # 模拟 cninfo 查无(ETF) -> KeyError
            raise KeyError(symbol)
        df = pd.DataFrame(rows, columns=["代码", "简称", "公告标题", "公告时间", "公告链接"])
        return df

    monkeypatch.setattr(ak, "stock_zh_a_disclosure_report_cninfo", fake_cninfo)


def test_only_queries_a_market_and_six_digit_codes(monkeypatch):
    """只查 A股 (market=a) 且 6 位数字代码; 美股/非6位跳过。"""
    import akshare as ak
    import pandas as pd

    called = []

    def fake_cninfo(symbol, market, start_date, end_date):
        called.append(symbol)
        return pd.DataFrame(columns=["代码", "简称", "公告标题", "公告时间", "公告链接"])

    monkeypatch.setattr(ak, "stock_zh_a_disclosure_report_cninfo", fake_cninfo)
    prov = AkShareEarningsProvider()
    watchlist = [
        Instrument(code="000001", name="平安银行", market="a"),
        Instrument(code="512690", name="招商中证白酒", market="a"),
        Instrument(code="AAPL", name="Apple", market="us"),
        Instrument(code="A.B", name="非6位", market="a"),
    ]
    events = _run(prov.fetch(start=_NOW, end=_NOW + timedelta(days=7), watchlist=watchlist))
    # 只应查询两个 6位 A股代码: 000001 和 512690
    assert sorted(called) == ["000001", "512690"]


def test_filter_keeps_only_earnings_keywords(monkeypatch):
    """标题含财报关键词才保留; 董事会决议/分红/选举等非财报公告被过滤。"""
    _install_fake_akshare(monkeypatch, {
        "000001": [
            ("000001", "平安银行", "2026年半年度财务报告", "2026-08-15", "x"),
            ("000001", "平安银行", "董事会决议公告", "2026-08-15", "x"),
        ],
        "600519": [
            ("600519", "贵州茅台", "2026年年度报告", "2026-08-18", "x"),
        ],
        "600000": [
            ("600000", "浦发银行", "选举董事公告", "2026-08-15", "x"),
        ],
    })
    prov = AkShareEarningsProvider()
    watchlist = [
        Instrument(code="000001", name="平安银行", market="a"),
        Instrument(code="600519", name="贵州茅台", market="a"),
        Instrument(code="600000", name="浦发银行", market="a"),
    ]
    events = _run(prov.fetch(
        start=_NOW - timedelta(days=1), end=_NOW + timedelta(days=7), watchlist=watchlist
    ))
    notes = [e.note for e in events]
    # 只保留 2 条财报类 (半年度财务报告 / 年度报告); 董事会决议/选举被过滤
    assert len(events) == 2
    assert any("半年度财务报告" in n for n in notes)
    assert any("年度报告" in n for n in notes)
    assert all(e.event_type == "earnings" for e in events)
    assert all(e.market == "a" for e in events)
    assert all(e.source == "akshare_earnings" for e in events)


def test_keyerror_silently_skips_no_error(monkeypatch):
    """cninfo 查无该代码(ETF/非上市)抛 KeyError -> 静默跳过, 不记 error。"""
    _install_fake_akshare(monkeypatch, {
        "000001": [("000001", "平安银行", "2026年半年度财务报告", "2026-08-15", "x")],
        "512690": None,  # ETF -> cninfo 抛 KeyError
    })
    prov = AkShareEarningsProvider()
    watchlist = [
        Instrument(code="000001", name="平安银行", market="a"),
        Instrument(code="512690", name="招商中证白酒", market="a"),
    ]
    events = _run(prov.fetch(start=_NOW, end=_NOW + timedelta(days=7), watchlist=watchlist))
    assert len(events) == 1
    assert prov.last_errors == {}


def test_cache_hit_avoids_refetch(tmp_path, monkeypatch):
    """缓存命中时不重复 fetch。"""
    import akshare as ak
    import pandas as pd

    calls = {"n": 0}

    def fake_cninfo(symbol, market, start_date, end_date):
        calls["n"] += 1
        return pd.DataFrame(
            [("000001", "平安银行", "2026年半年度财务报告", "2026-08-15", "x")],
            columns=["代码", "简称", "公告标题", "公告时间", "公告链接"],
        )

    monkeypatch.setattr(ak, "stock_zh_a_disclosure_report_cninfo", fake_cninfo)
    prov = AkShareEarningsProvider(cache_dir=tmp_path)
    watchers = [Instrument(code="000001", name="平安银行", market="a")]
    _run(prov.fetch(start=_NOW, end=_NOW + timedelta(days=7), watchlist=watchers))
    miss_n = calls["n"]
    _run(prov.fetch(start=_NOW, end=_NOW + timedelta(days=7), watchlist=watchers))
    assert miss_n == 1
    assert calls["n"] == 1  # 第二次命中缓存, 不 fetch
    assert prov.last_cache["hits"] == 1


def test_akshare_missing_returns_empty_without_error(monkeypatch):
    """akshare 未安装时 fetch 返回空, 不抛异常, 记 akshare 缺失。"""
    monkeypatch.setattr(AkShareEarningsProvider, "_akshare_available",
                        staticmethod(lambda: False))
    prov = AkShareEarningsProvider()
    watchlist = [Instrument(code="000001", name="平安银行", market="a")]
    events = _run(prov.fetch(start=_NOW, end=_NOW + timedelta(days=7), watchlist=watchlist))
    assert events == []
    assert "akshare" in prov.last_errors
