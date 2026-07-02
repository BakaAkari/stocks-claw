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
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Protocol

from stocks.logging_utils import get_logger

logger = get_logger("macro_data")


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

    def to_dict(self) -> dict:
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
        }


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

        # 并行获取所有指标
        tasks = {
            key: asyncio.create_task(self._fetch_one(key, ticker))
            for key, ticker in _YAHOO_TICKERS.items()
        }

        for key, task in tasks.items():
            try:
                value = await asyncio.wait_for(task, timeout=self._timeout + 2)
                if value is not None:
                    results[key] = value
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
        )

    async def _fetch_one(self, key: str, ticker: str) -> Optional[float]:
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
                return None

            meta = result.get("meta", {})
            price = meta.get("regularMarketPrice") or meta.get("previousClose")
            return float(price) if price is not None else None

        except urllib.error.HTTPError as e:
            logger.warning(f"Yahoo Finance HTTP {e.code} for {key} ({ticker})")
            return None
        except Exception as e:
            logger.warning(f"Yahoo Finance error for {key} ({ticker}): {e}")
            return None


# ------------------------------------------------------------------
# 静态配置兜底
# ------------------------------------------------------------------

class StaticMacroProvider:
    """从本地配置读取宏观数据（用于离线或测试环境）"""

    def __init__(self, config: dict[str, Optional[float]]):
        self._config = config

    async def fetch(self) -> MacroSnapshot:
        return MacroSnapshot(
            usd_cny=self._config.get("usd_cny"),
            vix=self._config.get("vix"),
            us_10y_yield=self._config.get("us_10y_yield"),
            dxy=self._config.get("dxy"),
            gold=self._config.get("gold"),
            crude_oil=self._config.get("crude_oil"),
            source="static_config",
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
        """按优先级获取，第一个成功即返回"""
        for provider in self._providers:
            try:
                snapshot = await provider.fetch()
                # 只要有任意数据即返回（不要求全部成功）
                has_data = any([
                    snapshot.usd_cny, snapshot.vix, snapshot.us_10y_yield,
                    snapshot.dxy, snapshot.gold, snapshot.crude_oil,
                ])
                if has_data:
                    return snapshot
            except Exception as e:
                logger.warning(f"Macro provider {type(provider).__name__} failed: {e}")
                continue

        # 全部失败，返回空快照
        return MacroSnapshot(source="all_failed")
