"""中国宏观数据 Provider — 东方财富数据中心公开 API

数据源：东方财富 datacenter-web.eastmoney.com（公开接口，无需 API key）
覆盖指标：PMI、CPI 同比、PPI 同比、M2 同比、社融增量、LPR、社零、工业增加值

实现原则：
- 纯 urllib，不引入 AKShare/gopup 等重依赖
- 单指标失败不阻断整体流程
- 每个字段携带 source / as_of / error
- 可选本地 JSON 缓存（24h TTL）
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from stocks.logging_utils import get_logger

logger = get_logger("cn_macro")

# 东方财富数据中心 API 基地址
_EASTMONEY_DATACENTER_BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"

# 指标 → reportName 映射（东方财富数据中心）
_EASTMONEY_REPORT_NAMES = {
    "pmi": "RPT_ECONOMY_PMI",
    "cpi": "RPT_ECONOMY_CPI",
    "ppi": "RPT_ECONOMY_PPI",
    "m2": "RPT_ECONOMY_CURRENCY_SUPPLY",
    "social_financing": "RPT_ECONOMICVALUE_SOCIALFINANCING",
    "lpr": "RPTA_WEB_RATE",
    "retail_sales": "RPT_ECONOMY_TOTAL_RETAIL",
    "industrial_production": "RPT_ECONOMY_INDUS_GROW",
}

# 指标字段映射（东方财富返回字段 → 内部字段名）
_EASTMONEY_FIELD_MAP = {
    "pmi": {
        "date_field": "REPORT_DATE",
        "value_field": "MAKE_INDEX",
    },
    "cpi": {
        "date_field": "REPORT_DATE",
        "value_field": "NATIONAL_SAME",  # 同比
    },
    "ppi": {
        "date_field": "REPORT_DATE",
        "value_field": "BASE_SAME",
    },
    "m2": {
        "date_field": "REPORT_DATE",
        "value_field": "BASIC_CURRENCY_SAME",
    },
    "social_financing": {
        "date_field": "REPORT_DATE",
        "value_field": "SOCIAL_FINANCING",
    },
    "lpr": {
        "date_field": "TRADE_DATE",
        "value_field_1y": "LPR1Y",
        "value_field_5y": "LPR5Y",
    },
    "retail_sales": {
        "date_field": "REPORT_DATE",
        "value_field": "RETAIL_TOTAL_SAME",
    },
    "industrial_production": {
        "date_field": "REPORT_DATE",
        "value_field": "BASE_SAME",
    },
}


@dataclass(frozen=True)
class CnMacroSnapshot:
    """中国宏观数据快照"""

    # 官方 PMI
    pmi: Optional[float] = None
    pmi_as_of: Optional[str] = None

    # CPI 同比 (%)
    cpi_yoy: Optional[float] = None
    cpi_as_of: Optional[str] = None

    # PPI 同比 (%)
    ppi_yoy: Optional[float] = None
    ppi_as_of: Optional[str] = None

    # M2 同比 (%)
    m2_yoy: Optional[float] = None
    m2_as_of: Optional[str] = None

    # 社会融资规模增量（亿元）
    social_financing: Optional[float] = None
    social_financing_as_of: Optional[str] = None

    # LPR (%)
    lpr_1y: Optional[float] = None
    lpr_5y: Optional[float] = None
    lpr_as_of: Optional[str] = None

    # 社会消费品零售总额 同比 (%)
    retail_sales_yoy: Optional[float] = None
    retail_sales_as_of: Optional[str] = None

    # 工业增加值 同比 (%)
    industrial_production_yoy: Optional[float] = None
    industrial_production_as_of: Optional[str] = None

    # 人民币兑美元中间价（作为 USD/CNY 辅助）
    usd_cny_mid: Optional[float] = None
    usd_cny_mid_as_of: Optional[str] = None

    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source: str = "eastmoney_datacenter"
    errors: dict[str, str] = field(default_factory=dict)
    field_sources: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "pmi": self.pmi,
            "pmi_as_of": self.pmi_as_of,
            "cpi_yoy": self.cpi_yoy,
            "cpi_as_of": self.cpi_as_of,
            "ppi_yoy": self.ppi_yoy,
            "ppi_as_of": self.ppi_as_of,
            "m2_yoy": self.m2_yoy,
            "m2_as_of": self.m2_as_of,
            "social_financing": self.social_financing,
            "social_financing_as_of": self.social_financing_as_of,
            "lpr_1y": self.lpr_1y,
            "lpr_5y": self.lpr_5y,
            "lpr_as_of": self.lpr_as_of,
            "retail_sales_yoy": self.retail_sales_yoy,
            "retail_sales_as_of": self.retail_sales_as_of,
            "industrial_production_yoy": self.industrial_production_yoy,
            "industrial_production_as_of": self.industrial_production_as_of,
            "usd_cny_mid": self.usd_cny_mid,
            "usd_cny_mid_as_of": self.usd_cny_mid_as_of,
            "timestamp": self.timestamp,
            "source": self.source,
            "errors": self.errors,
            "field_sources": self.field_sources,
        }


class CnMacroProvider:
    """中国宏观数据 Provider（东方财富数据中心公开 API）

    所有网络请求使用标准库 urllib，无需额外依赖。
    """

    def __init__(
        self,
        *,
        cache_dir: Optional[Path] = None,
        cache_ttl: int = 86400,
        timeout: float = 15.0,
    ):
        self._cache_dir = Path(cache_dir) if cache_dir else None
        self._cache_ttl = max(0, int(cache_ttl))
        self._timeout = timeout

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    async def fetch(self) -> CnMacroSnapshot:
        """获取所有中国宏观指标，并行请求，失败项记录到 errors"""
        import asyncio

        tasks = {
            "pmi": asyncio.to_thread(self._fetch_pmi),
            "cpi": asyncio.to_thread(self._fetch_cpi),
            "ppi": asyncio.to_thread(self._fetch_ppi),
            "m2": asyncio.to_thread(self._fetch_m2),
            "social_financing": asyncio.to_thread(self._fetch_social_financing),
            "lpr": asyncio.to_thread(self._fetch_lpr),
            "retail_sales": asyncio.to_thread(self._fetch_retail_sales),
            "industrial_production": asyncio.to_thread(self._fetch_industrial_production),
            "usd_cny_mid": asyncio.to_thread(self._fetch_usd_cny_mid),
        }

        tasks = {key: asyncio.create_task(task) for key, task in tasks.items()}
        results: dict[str, tuple[Optional[float], Optional[str]]] = {}
        errors: dict[str, str] = {}

        for key, task in tasks.items():
            try:
                value, as_of = await task
                results[key] = (value, as_of)
                if value is None or (isinstance(value, tuple) and any(v is None for v in value)):
                    errors[key] = "observation_missing"
            except Exception as exc:
                logger.warning(f"CnMacroProvider {key} failed: {exc}")
                errors[key] = f"{type(exc).__name__}: {exc}"
                results[key] = (None, None)

        # LPR 特殊处理：返回两个值
        lpr_1y = None
        lpr_5y = None
        lpr_as_of = None
        lpr_result = results.get("lpr", (None, None))
        if isinstance(lpr_result[0], tuple) and len(lpr_result[0]) == 2:
            lpr_1y, lpr_5y = lpr_result[0]
            lpr_as_of = lpr_result[1]

        field_sources: dict[str, dict] = {}
        for key, (value, as_of) in results.items():
            if key == "lpr":
                if lpr_1y is not None:
                    field_sources["lpr_1y"] = {"source": "eastmoney", "as_of": lpr_as_of}
                if lpr_5y is not None:
                    field_sources["lpr_5y"] = {"source": "eastmoney", "as_of": lpr_as_of}
            elif value is not None:
                field_sources[key] = {"source": {"social_financing": "mofcom", "usd_cny_mid": "safe"}.get(key, "eastmoney"), "as_of": as_of}

        return CnMacroSnapshot(
            pmi=results.get("pmi", (None, None))[0],
            pmi_as_of=results.get("pmi", (None, None))[1],
            cpi_yoy=results.get("cpi", (None, None))[0],
            cpi_as_of=results.get("cpi", (None, None))[1],
            ppi_yoy=results.get("ppi", (None, None))[0],
            ppi_as_of=results.get("ppi", (None, None))[1],
            m2_yoy=results.get("m2", (None, None))[0],
            m2_as_of=results.get("m2", (None, None))[1],
            social_financing=results.get("social_financing", (None, None))[0],
            social_financing_as_of=results.get("social_financing", (None, None))[1],
            lpr_1y=lpr_1y,
            lpr_5y=lpr_5y,
            lpr_as_of=lpr_as_of,
            retail_sales_yoy=results.get("retail_sales", (None, None))[0],
            retail_sales_as_of=results.get("retail_sales", (None, None))[1],
            industrial_production_yoy=results.get("industrial_production", (None, None))[0],
            industrial_production_as_of=results.get("industrial_production", (None, None))[1],
            usd_cny_mid=results.get("usd_cny_mid", (None, None))[0],
            usd_cny_mid_as_of=results.get("usd_cny_mid", (None, None))[1],
            errors=errors,
            field_sources=field_sources,
        )

    # ------------------------------------------------------------------
    # 各指标抓取
    # ------------------------------------------------------------------
    def _fetch_pmi(self) -> tuple[Optional[float], Optional[str]]:
        return self._fetch_eastmoney_indicator("pmi")

    def _fetch_cpi(self) -> tuple[Optional[float], Optional[str]]:
        return self._fetch_eastmoney_indicator("cpi")

    def _fetch_ppi(self) -> tuple[Optional[float], Optional[str]]:
        return self._fetch_eastmoney_indicator("ppi")

    def _fetch_m2(self) -> tuple[Optional[float], Optional[str]]:
        return self._fetch_eastmoney_indicator("m2")

    def _fetch_social_financing(self) -> tuple[Optional[float], Optional[str]]:
        req = urllib.request.Request(
            "https://data.mofcom.gov.cn/datamofcom/front/gnmy/shrzgmQuery", data=b"",
            headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=self._timeout) as response:
            rows = json.loads(response.read().decode("utf-8"))
        rows = sorted(rows, key=lambda row: str(row.get("date", "")), reverse=True)
        for row in rows:
            period = str(row.get("date", ""))
            value = _to_float(row.get("tiosfs"))
            if len(period) == 6 and value is not None:
                return value, f"{period[:4]}-{period[4:]}-01"
        return None, None

    def _fetch_lpr(self) -> tuple[tuple[Optional[float], Optional[float]], Optional[str]]:
        """返回 ((lpr_1y, lpr_5y), as_of)"""
        cfg = _EASTMONEY_FIELD_MAP["lpr"]
        data = self._request_eastmoney("lpr", page_size=3)
        if not data:
            return ((None, None), None)
        for row in data:
            date_val = row.get(cfg["date_field"])
            v1y = row.get(cfg["value_field_1y"])
            v5y = row.get(cfg["value_field_5y"])
            if v1y is not None or v5y is not None:
                return (
                    (_to_float(v1y), _to_float(v5y)),
                    _parse_date(date_val),
                )
        return ((None, None), None)

    def _fetch_retail_sales(self) -> tuple[Optional[float], Optional[str]]:
        return self._fetch_eastmoney_indicator("retail_sales")

    def _fetch_industrial_production(self) -> tuple[Optional[float], Optional[str]]:
        return self._fetch_eastmoney_indicator("industrial_production")

    def _fetch_usd_cny_mid(self) -> tuple[Optional[float], Optional[str]]:
        """SAFE official central parity, CNY per USD (table quotes per USD 100)."""
        from io import StringIO

        import pandas as pd

        end = datetime.now(timezone.utc).date()
        params = urllib.parse.urlencode({"startDate": str(end - timedelta(days=14)),
                                         "endDate": str(end), "queryYN": "true"}).encode()
        req = urllib.request.Request("https://www.safe.gov.cn/AppStructured/hlw/RMBQuery.do",
                                     data=params, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=self._timeout) as response:
            html = response.read().decode("utf-8")
        for table in pd.read_html(StringIO(html), flavor="lxml"):
            if "日期" not in table or "美元" not in table:
                continue
            rows = table.sort_values("日期", ascending=False)
            for _, row in rows.iterrows():
                value, day = _to_float(row["美元"]), _parse_date(str(row["日期"]))
                if value is not None and day:
                    return round(value / 100.0, 8), day
        return None, None

    # ------------------------------------------------------------------
    # 通用东方财富请求
    # ------------------------------------------------------------------
    def _fetch_eastmoney_indicator(self, indicator: str) -> tuple[Optional[float], Optional[str]]:
        """抓取单个东方财富指标的最新值"""
        cfg = _EASTMONEY_FIELD_MAP.get(indicator)
        if not cfg:
            return (None, None)
        data = self._request_eastmoney(indicator, page_size=3)
        if not data:
            return (None, None)
        for row in data:
            date_val = row.get(cfg["date_field"])
            value = row.get(cfg["value_field"])
            if value is not None:
                return (_to_float(value), _parse_date(date_val))
        return (None, None)

    def _request_eastmoney(self, indicator: str, page_size: int = 10) -> list[dict]:
        """请求东方财富数据中心 API，返回数据行列表"""
        report_name = _EASTMONEY_REPORT_NAMES.get(indicator)
        if not report_name:
            return []

        params = {
            "sortColumns": _EASTMONEY_FIELD_MAP[indicator]["date_field"],
            "columns": "ALL", "source": "WEB", "client": "WEB",
            "sortTypes": "-1",
            "pageSize": str(page_size),
            "pageNumber": "1",
            "reportName": report_name,
        }
        url = f"{_EASTMONEY_DATACENTER_BASE}?{urllib.parse.urlencode(params)}"

        data = self._request_json(url)
        if not data or not isinstance(data.get("result"), dict):
            raise ValueError("eastmoney_missing_result:" + indicator)
        return data["result"].get("data", []) or []

    def _request_json(self, url: str) -> Optional[dict]:
        """发起 HTTP GET 请求并返回 JSON"""
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        if not text.strip():
            return None
        return json.loads(text)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _to_float(value) -> Optional[float]:
    if value in (None, "", "-", "—", "null", "None"):
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _parse_date(value) -> Optional[str]:
    if not value:
        return None
    try:
        # 尝试多种格式
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y%m%d"):
            try:
                dt = datetime.strptime(str(value).split(" ")[0], fmt)
                return dt.date().isoformat()
            except ValueError:
                continue
        # 尝试 ISO 格式
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.date().isoformat()
    except (ValueError, TypeError):
        return None
