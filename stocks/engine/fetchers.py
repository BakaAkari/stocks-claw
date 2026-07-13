"""行情获取编排 — 按配置执行 Provider 降级链。"""

from __future__ import annotations

import asyncio
import urllib.error
from typing import Optional

from stocks.domain.models import Instrument, Quote
from stocks.errors import (
    DegradationRecord,
    ProviderDataError,
    ProviderError,
    ProviderNetworkError,
    ProviderTimeoutError,
)
from stocks.providers.registry import ProviderRegistry


class DataFetcher:
    """数据获取器 — 带降级链的行情获取

    降级链策略：
        1. 主 Provider 获取（含重试）
        2. 可恢复异常 → 重试 max_retries 次
        3. 仍失败 → 切备用 Provider（fallback）
        4. 备用也失败 → 返回空列表 + 降级记录

    降级记录通过 `get_degradation_log()` 获取，供上层分析使用。
    """

    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        max_retries: int = 1,
        retry_delay: float = 1.0,
        fallback_order: Optional[dict[str, list[str]]] = None,
    ):
        self.registry = registry
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.fallback_order = fallback_order or {}
        self._degradation_log: list[DegradationRecord] = []
        self._tracker = None  # FallbackTracker, 惰性注入
        self._tracker = None  # set via set_fallback_tracker()

    def set_fallback_tracker(self, tracker) -> None:
        """注入 FallbackTracker 实例（惰性，避免启动时依赖）。"""
        self._tracker = tracker

    def set_fallback_tracker(self, tracker) -> None:
        """注入 FallbackTracker（惰性，避免测试环境依赖目录结构）。"""
        self._tracker = tracker

    def get_degradation_log(self) -> list[DegradationRecord]:
        """获取最近一次 fetch 的降级记录"""
        return self._degradation_log.copy()

    async def fetch_quotes(
        self,
        instruments: list[Instrument],
        preferred_provider: Optional[str] = None
    ) -> dict[str, list[Quote]]:
        """获取行情，按市场分组并行获取，返回 {"a": [Quote, ...], ...}"""
        self._degradation_log.clear()

        if not instruments:
            return {}

        # 按市场分组
        by_market: dict[str, list[Instrument]] = {}
        for inst in instruments:
            by_market.setdefault(inst.market, []).append(inst)

        # 为每个市场并行获取（带降级链）
        tasks = []
        for market, insts in by_market.items():
            tasks.append(self._fetch_market_with_fallback(market, insts, preferred_provider))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        quotes_by_market: dict[str, list[Quote]] = {}
        for market, result in zip(by_market.keys(), results):
            if isinstance(result, Exception):
                # 降级链内部不应抛异常到此处，但做兜底保护
                quotes_by_market[market] = []
                self._degradation_log.append(
                    DegradationRecord(
                        market=market,
                        primary_provider="unknown",
                        result="empty",
                        message=f"未捕获异常: {type(result).__name__}: {result}",
                    )
                )
            else:
                quotes, record = result
                quotes_by_market[market] = quotes
                if record is not None:
                    self._degradation_log.append(record)

        return quotes_by_market

    # ------------------------------------------------------------------
    # 内部降级链
    # ------------------------------------------------------------------

    async def _fetch_market_with_fallback(
        self,
        market: str,
        instruments: list[Instrument],
        preferred: Optional[str] = None,
    ) -> tuple[list[Quote], Optional[DegradationRecord]]:
        """为单个市场获取行情，带降级链

        Returns: (quotes, degradation_record)
        """
        primary = self._pick_provider(market, preferred)
        if primary is None:
            return [], DegradationRecord(
                market=market,
                primary_provider="none",
                result="empty",
                message=f"市场 {market} 无可用 Provider",
            )

        primary_name = getattr(primary, "name", type(primary).__name__)

        # 1. 尝试主 Provider（含重试）
        last_error: Optional[ProviderError] = None
        for attempt in range(self.max_retries + 1):
            try:
                quotes = await self._call_provider(primary, instruments)
                if self._tracker:
                    self._tracker.record(
                        symbol=market, market=market, data_type="quote",
                        requested_sources=[primary_name], used_source=primary_name,
                    )
                return quotes, DegradationRecord(
                    market=market,
                    primary_provider=primary_name,
                    result="success",
                    message=f"主 Provider {primary_name} 成功获取 {len(quotes)} 条行情",
                )
            except ProviderError as e:
                last_error = e
                if not e.is_retryable:
                    # 不可恢复异常，直接失败，不尝试 fallback 也不重试
                    return [], DegradationRecord(
                        market=market,
                        primary_provider=primary_name,
                        error=e,
                        result="empty",
                        message=f"主 Provider {primary_name} 不可恢复异常: {type(e).__name__}",
                    )
                if attempt >= self.max_retries:
                    break
                await asyncio.sleep(self.retry_delay)

        # 2. 主 Provider 失败（可恢复异常重试耗尽），尝试备用 Provider
        fallback = self._pick_fallback_provider(market, primary_name)
        if fallback is not None:
            fallback_name = getattr(fallback, "name", type(fallback).__name__)
            try:
                quotes = await self._call_provider(fallback, instruments)
                if self._tracker:
                    self._tracker.record(
                        symbol=market, market=market, data_type="quote",
                        requested_sources=[primary_name, fallback_name],
                        used_source=fallback_name,
                        failed_sources=[primary_name],
                        failure_reasons={primary_name: str(last_error) if last_error else "retry exhausted"},
                    )
                return quotes, DegradationRecord(
                    market=market,
                    primary_provider=primary_name,
                    fallback_provider=fallback_name,
                    result="fallback_success",
                    message=f"主 Provider {primary_name} 失败，备用 {fallback_name} 成功获取 {len(quotes)} 条行情",
                )
            except ProviderError as e_fallback:
                return [], DegradationRecord(
                    market=market,
                    primary_provider=primary_name,
                    fallback_provider=fallback_name,
                    error=e_fallback,
                    result="empty",
                    message=f"主 Provider {primary_name} 及备用 {fallback_name} 均失败",
                )

        # 3. 全部失败，返回空 + 降级记录
        return [], DegradationRecord(
            market=market,
            primary_provider=primary_name,
            error=last_error,
            result="empty",
            message=f"主 Provider {primary_name} 失败，无备用 Provider",
        )

    async def _call_provider(self, provider, instruments: list[Instrument]) -> list[Quote]:
        """调用 Provider 获取行情，将标准异常转换为 ProviderError 分层异常"""
        try:
            return await provider.fetch_batch(instruments)
        except ProviderError:
            # 已转换过的异常直接透传
            raise
        except TimeoutError as e:
            raise ProviderTimeoutError(
                f"Provider 超时: {e}",
                source=getattr(provider, "name", type(provider).__name__),
                detail=str(e),
            )
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            raise ProviderNetworkError(
                f"Provider 网络错误: {e}",
                source=getattr(provider, "name", type(provider).__name__),
                detail=str(e),
            )
        except (ValueError, IndexError, KeyError) as e:
            raise ProviderDataError(
                f"Provider 数据解析错误: {e}",
                source=getattr(provider, "name", type(provider).__name__),
                detail=str(e),
            )
        except Exception as e:
            # 兜底：未分类异常标记为不可恢复（保守策略）
            raise ProviderError(
                f"Provider 未分类异常: {e}",
                source=getattr(provider, "name", type(provider).__name__),
                detail=str(e),
            )

    def _pick_provider(
        self, market: str, preferred: Optional[str] = None
    ) -> Optional[object]:
        """为指定市场选择主 Provider"""
        if preferred:
            p = self.registry.get(preferred)
            if p and market in p.supported_markets:
                return p
        return self.registry.get_default_for_market(market)

    def _pick_fallback_provider(
        self, market: str, exclude: str
    ) -> Optional[object]:
        """为指定市场选择备用 Provider（排除已失败的主 Provider）"""
        for name in self.independent_fallback_names(market, exclude):
            provider = self.registry.get(name)
            if provider is not None:
                return provider
        return None

    def independent_fallback_names(self, market: str, exclude: str) -> list[str]:
        """返回配置允许且真实可用的独立备用源名。"""
        if market in self.fallback_order:
            configured = self.fallback_order.get(market)
            candidates = configured if isinstance(configured, list) else []
        else:
            candidates = [
                provider.name for provider in self.registry.list_for_market(market)
            ]
        names: list[str] = []
        for name in candidates:
            provider = self.registry.get(name)
            if (
                provider is not None
                and name != exclude
                and market in getattr(provider, "supported_markets", [])
                and name not in names
            ):
                names.append(name)
        return names

    def is_single_source(
        self, market: str, primary_name: Optional[str] = None
    ) -> bool:
        """该市场在当前 registry/config 下是否没有独立行情备用源。"""
        if not primary_name:
            primary = self._pick_provider(market)
            primary_name = getattr(primary, "name", "") if primary else ""
        return not self.independent_fallback_names(market, primary_name)
