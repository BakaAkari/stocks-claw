"""宏观数据采集 — 关键全球市场指标实时获取

使用 Yahoo Finance 免费 API（urllib 标准库）获取宏观指标，
支持多源降级和本地配置兜底。

核心指标：
- 美元指数 (DXY): 美元强弱，影响全球资金流向
- VIX 恐慌指数 (^VIX): 市场情绪，波动率预期
- 10 年期美债收益率 (^TNX): 全球无风险利率锚
- 美元兑人民币 (USDCNY=X): 汇率，影响 A 股外资流动
- 黄金 (GC=F): 避险资产价格
- 原油 (CL=F): 通胀预期与能源成本

使用方式：
    provider = YahooFinanceMacroProvider()
    snapshot = await provider.fetch()
    print(snapshot.vix, snapshot.usd_cny)
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Protocol

from stocks.logging_utils import get_logger

logger = get_logger("macro_data")

_MARKET_FIELDS = ("usd_cny", "vix", "us_10y_yield", "dxy", "gold", "crude_oil")
_OFFICIAL_FIELDS = ("cpi_yoy", "us_unemployment", "fed_funds_rate")


# ------------------------------------------------------------------
# 数据模型
# ------------------------------------------------------------------

@dataclass(frozen=True)
class MacroSnapshot:
    """宏观数据快照 — 关键全球市场指标

    所有字段均为 Optional，数据源失败时自动为 None，不阻断整体流程。
    """
    usd_cny: Optional[float] = None          # 美元兑人民币
    vix: Optional[float] = None               # VIX 恐慌指数
    us_10y_yield: Optional[float] = None      # 10 年期美债收益率 (%)
    dxy: Optional[float] = None               # 美元指数
    gold: Optional[float] = None              # 黄金期货 (USD/oz)
    crude_oil: Optional[float] = None         # 原油期货 (USD/bbl)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source: str = "yahoo_finance"
    errors: dict[str, str] = field(default_factory=dict)
    field_sources: dict[str, dict] = field(default_factory=dict)
    official_stats: dict[str, Optional[float]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        market_as_of = _compute_tier_as_of(self.field_sources, _MARKET_FIELDS)
        official_as_of = _compute_tier_as_of(self.field_sources,
            [f"official_stats.{f}" for f in _OFFICIAL_FIELDS])
        return {
            "usd_cny": self.usd_cny,
            "vix": self.vix,
            "us_10y_yield": self.us_10y_yield,
            "dxy": self.dxy,
            "gold": self.gold,
            "crude_oil": self.crude_oil,
            "timestamp": self.timestamp,
            "source": self.source,
            "errors": self.errors,
            "field_sources": self.field_sources,
            "official_stats": self.official_stats,
            "market_as_of": market_as_of,
            "official_as_of": official_as_of,
            "next_official_release": _next_cpi_release_estimate(),
        }


def _compute_tier_as_of(field_sources: dict, fields: tuple[str, ...]) -> Optional[str]:
    """从 field_sources 提取指定字段的最旧 as_of（用于分层新鲜度）。"""
    dates = []
    for fld in fields:
        meta = field_sources.get(fld, {})
        as_of = meta.get("as_of")
        if as_of:
            dates.append(as_of)
    return min(dates) if dates else None


def _next_cpi_release_estimate() -> str:
    """估算下一次 CPI 发布日期（每月 10-14 号）。"""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    # CPI 通常在每月 10-14 日发布前月数据
    # 当前月 >= 15 号 → 下月 12 号发布；否则本月 12 号可能尚未发布
    year, month = now.year, now.month
    if now.day >= 15:
        month += 1
        if month > 12:
            month = 1
            year += 1
    return f"{year}-{month:02d}-12"


# ------------------------------------------------------------------
# 提供者接口
# ------------------------------------------------------------------

class MacroProvider(Protocol):
    """宏观数据提供者接口"""

    async def fetch(self) -> MacroSnapshot:
        ...


# ------------------------------------------------------------------
# Yahoo Finance 实现
# ------------------------------------------------------------------

# Yahoo Finance 标的映射
_YAHOO_TICKERS = {
    "usd_cny": "USDCNY=X",
    "vix": "^VIX",
    "us_10y_yield": "^TNX",
    "dxy": "DX-Y.NYB",  # 美元指数期货
    "gold": "GC=F",
    "crude_oil": "CL=F",
}

_YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


class YahooFinanceMacroProvider:
    """使用 Yahoo Finance API 获取宏观数据

    实现细节：
    - 使用 urllib（标准库）发起 HTTP 请求
    - 并行获取所有指标，超时 10 秒
    - 单个指标失败不影响其他指标
    - 返回的收益率已经是百分比（如 4.2 表示 4.2%）
    """

    def __init__(self, timeout: float = 10.0):
        self._timeout = timeout

    async def fetch(self) -> MacroSnapshot:
        """获取所有宏观指标，并行请求，失败项记录到 errors"""
        results: dict[str, Optional[float]] = {}
        errors: dict[str, str] = {}
        field_sources: dict[str, dict] = {}

        # 并行获取所有指标
        tasks = {
            key: asyncio.create_task(self._fetch_one(key, ticker))
            for key, ticker in _YAHOO_TICKERS.items()
        }

        for key, task in tasks.items():
            try:
                value, as_of = await asyncio.wait_for(task, timeout=self._timeout + 2)
                if value is not None:
                    results[key] = value
                    field_sources[key] = {
                        "source": "yahoo_finance",
                        "as_of": as_of,
                    }
                else:
                    errors[key] = "API returned empty data"
            except asyncio.TimeoutError:
                errors[key] = f"Timeout after {self._timeout}s"
                logger.warning(f"Macro fetch timeout: {key}")
            except Exception as e:
                errors[key] = str(e)
                logger.warning(f"Macro fetch failed for {key}: {e}")

        return MacroSnapshot(
            usd_cny=results.get("usd_cny"),
            vix=results.get("vix"),
            us_10y_yield=results.get("us_10y_yield"),
            dxy=results.get("dxy"),
            gold=results.get("gold"),
            crude_oil=results.get("crude_oil"),
            errors=errors,
            field_sources=field_sources,
        )

    async def _fetch_one(
        self, key: str, ticker: str
    ) -> tuple[Optional[float], Optional[str]]:
        """获取单个指标的价格数据"""
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d"

        def _request():
            req = urllib.request.Request(url, headers=_YAHOO_HEADERS)
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))

        try:
            data = await asyncio.to_thread(_request)
            result = data.get("chart", {}).get("result", [None])[0]
            if not result:
                return None, None

            meta = result.get("meta", {})
            price = meta.get("regularMarketPrice") or meta.get("previousClose")
            raw_time = meta.get("regularMarketTime")
            if raw_time is None:
                timestamps = result.get("timestamp") or []
                raw_time = timestamps[-1] if timestamps else None
            as_of = None
            if raw_time:
                as_of = datetime.fromtimestamp(
                    float(raw_time), tz=timezone.utc
                ).isoformat()
            return (float(price) if price is not None else None), as_of

        except urllib.error.HTTPError as e:
            logger.warning(f"Yahoo Finance HTTP {e.code} for {key} ({ticker})")
            return None, None
        except Exception as e:
            logger.warning(f"Yahoo Finance error for {key} ({ticker}): {e}")
            return None, None


# ------------------------------------------------------------------
# FRED 权威日度/月度数据
# ------------------------------------------------------------------

_FRED_MARKET_SERIES = {
    "vix": "VIXCLS",
    "us_10y_yield": "DGS10",
    "dxy": "DTWEXBGS",
    "usd_cny": "DEXCHUS",
    "crude_oil": "DCOILWTICO",
}
_FRED_OFFICIAL_SERIES = {
    "cpi_yoy": "CPIAUCSL",
    "us_unemployment": "UNRATE",
    "fed_funds_rate": "FEDFUNDS",
}


class FredMacroProvider:
    """FRED CSV 免 key Provider；月度官方统计使用 24h 磁盘缓存。"""

    def __init__(
        self,
        *,
        cache_dir: Optional[Path] = None,
        cache_ttl: int = 86400,
        timeout: float = 8.0,
    ):
        self._cache_dir = Path(cache_dir) if cache_dir else None
        self._cache_ttl = max(0, int(cache_ttl))
        self._timeout = timeout

    def _fetch_series_sync(self, series_id: str) -> list[tuple[str, float]]:
        return self._fetch_many_sync([series_id]).get(series_id, [])

    def _fetch_many_sync(
        self, series_ids: list[str]
    ) -> dict[str, list[tuple[str, float]]]:
        start = (datetime.now(timezone.utc).date() - timedelta(days=800)).isoformat()
        query = urllib.parse.urlencode({"id": ",".join(series_ids), "cosd": start})
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?{query}"
        raw = self._fetch_url_with_fallback(url)
        text = self._decode_csv_response(raw)
        reader = csv.DictReader(io.StringIO(text))
        observations: dict[str, list[tuple[str, float]]] = {
            series_id: [] for series_id in series_ids
        }
        for row in reader:
            date_value = row.get("DATE") or row.get("observation_date")
            if not date_value:
                continue
            for series_id in series_ids:
                raw_value = row.get(series_id)
                if raw_value in (None, "", "."):
                    continue
                try:
                    observations[series_id].append((date_value, float(raw_value)))
                except ValueError:
                    continue
        return observations

    def _fetch_url_with_fallback(self, url: str) -> bytes:
        """Fetch URL; fallback to curl if urllib times out or is blocked."""
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "stocks-claw/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return response.read()
        except Exception as urllib_exc:  # noqa: BLE001
            logger = get_logger("fred_macro")
            logger.warning(f"urllib fetch failed, trying curl: {urllib_exc}")
            import subprocess as sp
            result = sp.run(
                ["curl", "-sS", "-L", "-m", "30", url],
                capture_output=True,
                timeout=35,
            )
            if result.returncode != 0:
                raise RuntimeError(f"curl failed: {result.stderr.decode('utf-8', errors='ignore')}") from urllib_exc
            return result.stdout

    def _decode_csv_response(self, raw: bytes) -> str:
        """Decode FRED response, handling both plain CSV and ZIP archives."""
        if raw.startswith(b"PK\x03\x04"):
            import zipfile
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                csv_files = [n for n in zf.namelist() if n.endswith(".csv")]
                if not csv_files:
                    raise ValueError("ZIP archive contains no CSV files")
                # Merge all CSV files into a single observation table keyed by date.
                merged: dict[str, dict[str, str]] = {}
                for csv_name in csv_files:
                    with zf.open(csv_name) as f:
                        csv_text = f.read().decode("utf-8-sig")
                    reader = csv.DictReader(io.StringIO(csv_text))
                    for row in reader:
                        date = row.get("DATE") or row.get("observation_date")
                        if not date:
                            continue
                        merged.setdefault(date, {}).update(row)
                output = io.StringIO()
                # Use union of all keys from merged rows; ensure observation_date first.
                fieldnames = {"observation_date"}
                for row in merged.values():
                    fieldnames.update(row.keys())
                fieldnames = ["observation_date"] + [k for k in fieldnames if k != "observation_date"]
                writer = csv.DictWriter(output, fieldnames=fieldnames)
                writer.writeheader()
                for date in sorted(merged.keys()):
                    row = merged[date]
                    row["observation_date"] = date
                    writer.writerow(row)
                return output.getvalue()
        return raw.decode("utf-8-sig")

    async def _fetch_series(self, series_id: str) -> list[tuple[str, float]]:
        return await asyncio.to_thread(self._fetch_series_sync, series_id)

    async def _fetch_many(
        self, series_ids: list[str]
    ) -> dict[str, list[tuple[str, float]]]:
        return await asyncio.to_thread(self._fetch_many_sync, series_ids)

    @property
    def _official_cache_path(self) -> Optional[Path]:
        return self._cache_dir / "fred_official_stats.json" if self._cache_dir else None

    def _load_official_cache(self) -> Optional[dict]:
        path = self._official_cache_path
        if path is None or not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            fetched_at = datetime.fromisoformat(payload["fetched_at"])
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
            if age <= self._cache_ttl:
                return payload
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        return None

    def _save_official_cache(self, payload: dict) -> None:
        path = self._official_cache_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)

    async def fetch(self) -> MacroSnapshot:
        values: dict[str, float] = {}
        field_sources: dict[str, dict] = {}
        errors: dict[str, str] = {}

        market_observations: dict[str, list[tuple[str, float]]] = {}
        try:
            market_observations = await self._fetch_many(
                list(_FRED_MARKET_SERIES.values())
            )
        except Exception as exc:
            for field_name in _FRED_MARKET_SERIES:
                errors[field_name] = f"{type(exc).__name__}: {exc}"
        for field_name, series_id in _FRED_MARKET_SERIES.items():
            series_id = _FRED_MARKET_SERIES[field_name]
            try:
                observations = market_observations.get(series_id, [])
                if not observations:
                    raise ValueError("no observations")
                as_of, value = observations[-1]
                values[field_name] = value
                field_sources[field_name] = {
                    "source": f"fred:{series_id}",
                    "as_of": as_of,
                }
            except Exception as exc:
                errors.setdefault(field_name, f"{type(exc).__name__}: {exc}")

        official_values: dict[str, Optional[float]] = {}
        cached = self._load_official_cache()
        if cached:
            official_values.update(cached.get("values", {}))
            field_sources.update(cached.get("field_sources", {}))
        else:
            official_observations: dict[str, list[tuple[str, float]]] = {}
            try:
                official_observations = await self._fetch_many(
                    list(_FRED_OFFICIAL_SERIES.values())
                )
            except Exception as exc:
                for field_name in _FRED_OFFICIAL_SERIES:
                    errors[f"official_stats.{field_name}"] = (
                        f"{type(exc).__name__}: {exc}"
                    )
            for field_name, series_id in _FRED_OFFICIAL_SERIES.items():
                series_id = _FRED_OFFICIAL_SERIES[field_name]
                source_key = f"official_stats.{field_name}"
                try:
                    observations = official_observations.get(series_id, [])
                    if not observations:
                        raise ValueError("no observations")
                    as_of, value = observations[-1]
                    if field_name == "cpi_yoy":
                        if len(observations) < 13 or observations[-13][1] == 0:
                            raise ValueError("insufficient CPI observations for YoY")
                        value = (value / observations[-13][1] - 1.0) * 100
                    official_values[field_name] = round(value, 4)
                    field_sources[source_key] = {
                        "source": f"fred:{series_id}",
                        "as_of": as_of,
                    }
                except Exception as exc:
                    errors.setdefault(source_key, f"{type(exc).__name__}: {exc}")
            if official_values:
                self._save_official_cache({
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "values": official_values,
                    "field_sources": {
                        key: value
                        for key, value in field_sources.items()
                        if key.startswith("official_stats.")
                    },
                })

        return MacroSnapshot(
            usd_cny=values.get("usd_cny"),
            vix=values.get("vix"),
            us_10y_yield=values.get("us_10y_yield"),
            dxy=values.get("dxy"),
            crude_oil=values.get("crude_oil"),
            source="fred",
            errors=errors,
            field_sources=field_sources,
            official_stats=official_values,
        )


# ------------------------------------------------------------------
# 静态配置兜底
# ------------------------------------------------------------------

class StaticMacroProvider:
    """从本地配置读取宏观数据（用于离线或测试环境）"""

    def __init__(self, config: dict[str, Optional[float]]):
        self._config = config

    async def fetch(self) -> MacroSnapshot:
        values = {field_name: self._config.get(field_name) for field_name in _MARKET_FIELDS}
        official_stats = dict(self._config.get("official_stats", {}))
        field_sources = {
            field_name: {"source": "static_config", "as_of": None}
            for field_name, value in values.items()
            if value is not None
        }
        field_sources.update({
            f"official_stats.{field_name}": {
                "source": "static_config",
                "as_of": None,
            }
            for field_name, value in official_stats.items()
            if value is not None
        })
        return MacroSnapshot(
            usd_cny=self._config.get("usd_cny"),
            vix=self._config.get("vix"),
            us_10y_yield=self._config.get("us_10y_yield"),
            dxy=self._config.get("dxy"),
            gold=self._config.get("gold"),
            crude_oil=self._config.get("crude_oil"),
            source="static_config",
            field_sources=field_sources,
            official_stats=official_stats,
        )


# ------------------------------------------------------------------
# 组合提供者（降级链）
# ------------------------------------------------------------------

class CompositeMacroProvider:
    """组合多个提供者，按优先级降级

    使用方式：
        provider = CompositeMacroProvider([
            YahooFinanceMacroProvider(),
            StaticMacroProvider({"vix": 20.0}),
        ])
        snapshot = await provider.fetch()
    """

    def __init__(self, providers: list[MacroProvider]):
        self._providers = providers

    async def fetch(self) -> MacroSnapshot:
        """按字段优先级合并；上游缺失字段由下游补齐。"""
        values: dict[str, Optional[float]] = {field_name: None for field_name in _MARKET_FIELDS}
        official_stats: dict[str, Optional[float]] = {
            field_name: None for field_name in _OFFICIAL_FIELDS
        }
        field_sources: dict[str, dict] = {}
        errors: dict[str, str] = {}
        provider_names: list[str] = []

        for provider in self._providers:
            try:
                snapshot = await provider.fetch()
                provider_names.append(snapshot.source)
                for field_name in _MARKET_FIELDS:
                    value = getattr(snapshot, field_name)
                    if values[field_name] is None and value is not None:
                        values[field_name] = value
                        field_sources[field_name] = snapshot.field_sources.get(
                            field_name,
                            {"source": snapshot.source, "as_of": None},
                        )
                for field_name in _OFFICIAL_FIELDS:
                    value = snapshot.official_stats.get(field_name)
                    source_key = f"official_stats.{field_name}"
                    if official_stats[field_name] is None and value is not None:
                        official_stats[field_name] = value
                        field_sources[source_key] = snapshot.field_sources.get(
                            source_key,
                            {"source": snapshot.source, "as_of": None},
                        )
                for field_name, error in snapshot.errors.items():
                    errors[f"{snapshot.source}:{field_name}"] = error
            except Exception as e:
                provider_name = type(provider).__name__
                errors[f"{provider_name}:provider"] = f"{type(e).__name__}: {e}"
                logger.warning(f"Macro provider {provider_name} failed: {e}")

        has_data = any(value is not None for value in values.values()) or any(
            value is not None for value in official_stats.values()
        )
        return MacroSnapshot(
            **values,
            source="composite" if has_data else "all_failed",
            errors=errors,
            field_sources=field_sources,
            official_stats=official_stats,
        )
