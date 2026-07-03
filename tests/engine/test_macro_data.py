"""MacroData 测试 — 覆盖快照模型、静态提供者、组合降级链、Yahoo 模拟

测试策略：
- StaticMacroProvider 不依赖网络，直接验证
- CompositeMacroProvider 使用 Mock 验证降级逻辑
- YahooFinanceMacroProvider 使用 unittest.mock 模拟 urllib 响应
- 所有测试独立，不依赖外部网络或 Yahoo Finance API 可用性
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from stocks.engine.macro_data import (
    CompositeMacroProvider,
    FredMacroProvider,
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
        assert s.field_sources == {}
        assert s.official_stats == {}

    def test_to_dict(self):
        """to_dict 应包含所有字段"""
        s = MacroSnapshot(vix=20.5, usd_cny=7.25, source="test")
        d = s.to_dict()
        assert d["vix"] == 20.5
        assert d["usd_cny"] == 7.25
        assert d["us_10y_yield"] is None
        assert d["source"] == "test"
        assert d["official_stats"] == {}


# ------------------------------------------------------------------
# FRED Provider
# ------------------------------------------------------------------

def _fred_csv(series_id: str) -> str:
    if series_id == "CPIAUCSL":
        rows = ["observation_date,CPIAUCSL"]
        for month in range(1, 13):
            rows.append(f"2025-{month:02d}-01,{99 + month}")
        rows.append("2026-01-01,112")
        return "\n".join(rows)
    values = {
        "VIXCLS": ("2026-07-01", "18.5"),
        "DGS10": ("2026-07-01", "4.25"),
        "DTWEXBGS": ("2026-06-26", "119.5"),
        "DEXCHUS": ("2026-06-26", "6.81"),
        "DCOILWTICO": ("2026-07-01", "72.4"),
        "UNRATE": ("2026-06-01", "4.1"),
        "FEDFUNDS": ("2026-06-01", "3.64"),
    }
    date_value, value = values[series_id]
    return f"observation_date,{series_id}\n{date_value},{value}\n"


def _fred_urlopen(request, **kwargs):
    query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
    series_ids = query["id"][0].split(",")
    series_values = {}
    dates = set()
    for series_id in series_ids:
        rows = _fred_csv(series_id).splitlines()[1:]
        values = {}
        for row in rows:
            date_value, value = row.split(",", 1)
            dates.add(date_value)
            values[date_value] = value
        series_values[series_id] = values
    output = ["observation_date," + ",".join(series_ids)]
    for date_value in sorted(dates):
        output.append(
            date_value
            + ","
            + ",".join(
                series_values[series_id].get(date_value, ".")
                for series_id in series_ids
            )
        )
    response = Mock()
    response.read.return_value = "\n".join(output).encode("utf-8")
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    return response


class TestFredMacroProvider:
    async def test_parses_market_and_official_series_with_cpi_yoy(self, tmp_path):
        with patch("urllib.request.urlopen", side_effect=_fred_urlopen):
            snapshot = await FredMacroProvider(cache_dir=tmp_path).fetch()

        assert snapshot.vix == 18.5
        assert snapshot.us_10y_yield == 4.25
        assert snapshot.dxy == 119.5
        assert snapshot.usd_cny == 6.81
        assert snapshot.crude_oil == 72.4
        assert snapshot.gold is None
        assert snapshot.official_stats == {
            "cpi_yoy": 12.0,
            "us_unemployment": 4.1,
            "fed_funds_rate": 3.64,
        }
        assert snapshot.field_sources["usd_cny"] == {
            "source": "fred:DEXCHUS",
            "as_of": "2026-06-26",
        }
        assert snapshot.field_sources["official_stats.cpi_yoy"]["as_of"] == "2026-01-01"

    async def test_official_stats_cache_hit_skips_monthly_requests(self, tmp_path):
        cache_path = tmp_path / "fred_official_stats.json"
        cache_path.write_text(
            json.dumps({
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "values": {
                    "cpi_yoy": 2.5,
                    "us_unemployment": 4.0,
                    "fed_funds_rate": 3.5,
                },
                "field_sources": {
                    "official_stats.cpi_yoy": {
                        "source": "fred:CPIAUCSL",
                        "as_of": "2026-05-01",
                    }
                },
            }),
            encoding="utf-8",
        )
        requested_series = []

        def recording_urlopen(request, **kwargs):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
            requested_series.extend(query["id"][0].split(","))
            return _fred_urlopen(request, **kwargs)

        with patch("urllib.request.urlopen", side_effect=recording_urlopen):
            snapshot = await FredMacroProvider(cache_dir=tmp_path).fetch()

        assert snapshot.official_stats["cpi_yoy"] == 2.5
        assert set(requested_series) == {
            "VIXCLS", "DGS10", "DTWEXBGS", "DEXCHUS", "DCOILWTICO"
        }


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
        assert snapshot.field_sources["vix"]["source"] == "yahoo_finance"

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
        """第一个有部分数据时保留优先值并逐字段向下补齐"""
        p1 = StaticMacroProvider({"vix": 20.0})  # 只有 vix 有数据
        p2 = StaticMacroProvider({"vix": 30.0, "usd_cny": 7.0})

        composite = CompositeMacroProvider([p1, p2])
        snapshot = await composite.fetch()

        assert snapshot.vix == 20.0
        assert snapshot.usd_cny == 7.0
        assert snapshot.field_sources["vix"]["source"] == "static_config"
        assert snapshot.source == "composite"

    async def test_fred_still_provides_five_market_fields_when_yahoo_fails(
        self, tmp_path
    ):
        class FailedYahoo:
            async def fetch(self):
                return MacroSnapshot(
                    source="yahoo_finance",
                    errors={field_name: "HTTP 429" for field_name in (
                        "usd_cny", "vix", "us_10y_yield", "dxy", "gold", "crude_oil"
                    )},
                )

        composite = CompositeMacroProvider([
            FredMacroProvider(cache_dir=tmp_path),
            FailedYahoo(),
        ])
        with patch("urllib.request.urlopen", side_effect=_fred_urlopen):
            snapshot = await composite.fetch()

        market_values = [
            snapshot.usd_cny,
            snapshot.vix,
            snapshot.us_10y_yield,
            snapshot.dxy,
            snapshot.gold,
            snapshot.crude_oil,
        ]
        assert sum(value is not None for value in market_values) == 5
        assert snapshot.gold is None
        assert len(snapshot.official_stats) == 3
        assert snapshot.field_sources["vix"]["source"] == "fred:VIXCLS"
        assert any(key.startswith("yahoo_finance:") for key in snapshot.errors)
