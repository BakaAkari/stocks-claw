"""AnalysisContext 组装器 — 编排数据获取与脚手架计算，生成统一分析上下文"""

from dataclasses import replace
from datetime import datetime, timezone
from typing import Optional

from stocks.domain.models import (
    AnalysisContext,
    DriftCheck,
    FinancialAsset,
    MarketState,
    NewsItem,
    PortfolioMapping,
    Quote,
)
from stocks.engine.fetchers import DataFetcher
from stocks.engine.history_cache import HistoryCache
from stocks.engine.indicators import TechnicalIndicators
from stocks.engine.macro_data import MacroProvider
from stocks.engine.market_events import MarketEventExtractor
from stocks.engine.scaffolds import MarketScaffold, PortfolioScaffold
from stocks.logging_utils import get_logger


class ContextBuilder:
    """构建统一分析上下文 — 将行情、新闻、组合、市场状态、技术指标、宏观数据组装为 AnalysisContext"""

    def __init__(
        self,
        fetcher: DataFetcher,
        portfolio_scaffold: PortfolioScaffold,
        market_scaffold: MarketScaffold,
        history_cache: Optional[HistoryCache] = None,
        macro_provider: Optional[MacroProvider] = None,
        market_event_extractor: Optional[MarketEventExtractor] = None,
    ):
        self.fetcher = fetcher
        self.portfolio_scaffold = portfolio_scaffold
        self.market_scaffold = market_scaffold
        self.history_cache = history_cache
        self.macro_provider = macro_provider
        self.market_event_extractor = market_event_extractor or MarketEventExtractor()

    async def build(
        self,
        assets: list[FinancialAsset],
        constraints: dict,
        profile: dict,
        instruments: list,  # 要获取行情的标的列表
        recent_snapshots: list[dict],
        news: Optional[list[NewsItem]] = None,
        news_requested: Optional[bool] = None,
    ) -> AnalysisContext:
        """构建完整分析上下文"""
        generated_at = datetime.now(timezone.utc).isoformat()
        news_was_requested = news_requested if news_requested is not None else news is not None

        # 1. 获取行情
        quotes: dict[str, list[Quote]] = {}
        if instruments:
            quotes = await self.fetcher.fetch_quotes(instruments)
        degradation_log = self._get_fetcher_degradation_log()

        # 2. 记录行情到历史缓存，并计算技术指标
        if self.history_cache and quotes:
            quotes = await self._enrich_with_indicators(quotes)
        technical_indicators = self._collect_technical_indicators(quotes)

        # 3. 获取宏观数据
        macro_snapshot = None
        macro_error = None
        if self.macro_provider:
            try:
                macro_snapshot = await self.macro_provider.fetch()
            except Exception as e:
                macro_error = f"{type(e).__name__}: {e}"
                logger = get_logger("context_builder")
                logger.warning(f"Macro data fetch failed: {e}")

        # 4. 接收规则事件提取使用的原始新闻
        news = news or []

        market_events, news_digest = self.market_event_extractor.extract(
            news,
            assets=assets,
            instruments=instruments,
            generated_at=generated_at,
        )

        # 5. 构建 PortfolioMapping
        mapping = self.portfolio_scaffold.build(assets, constraints)

        # 6. 检查 Drift
        drift_checks = self.portfolio_scaffold.check_drift(mapping, constraints)

        # 7. 构建 MarketState
        market_state = self.market_scaffold.build(quotes)

        # 8. 生成 raw_prompt_input（人类可读文本）
        raw_prompt = self._build_raw_prompt(
            assets=assets,
            quotes=quotes,
            news=news,
            mapping=mapping,
            market_state=market_state,
            drift_checks=drift_checks,
            constraints=constraints,
            profile=profile,
            macro_snapshot=macro_snapshot.to_dict() if macro_snapshot else None,
            market_events=market_events,
            news_digest=news_digest,
            recent_snapshots=recent_snapshots,
        )

        data_quality = self._build_data_quality(
            generated_at=generated_at,
            assets=assets,
            instruments=instruments,
            quotes=quotes,
            degradation_log=degradation_log,
            news=news,
            news_requested=news_was_requested,
            macro_snapshot=macro_snapshot.to_dict() if macro_snapshot else None,
            macro_error=macro_error,
            technical_indicators=technical_indicators,
            market_events=market_events,
            news_digest=news_digest,
        )

        # 9. 组装 AnalysisContext
        return AnalysisContext(
            generated_at=generated_at,
            assets=assets,
            asset_count=len(assets),
            portfolio_constraints=constraints,
            portfolio_profile=profile,
            quotes=quotes,
            news=news,
            news_count=len(news),
            market_events=market_events,
            news_digest=news_digest,
            market_state=market_state,
            portfolio_mapping=mapping,
            drift_checks=drift_checks,
            recent_snapshots=recent_snapshots,
            raw_prompt_input=raw_prompt,
            macro_snapshot=macro_snapshot.to_dict() if macro_snapshot else None,
            technical_indicators=technical_indicators,
            data_quality=data_quality,
            schema_version=5,
        )

    def _get_fetcher_degradation_log(self) -> list[dict]:
        """读取 DataFetcher 降级日志，兼容测试中的轻量 mock。"""
        if not hasattr(self.fetcher, "get_degradation_log"):
            return []
        try:
            records = self.fetcher.get_degradation_log()
        except Exception:
            return []
        if not isinstance(records, (list, tuple)):
            return []
        result = []
        for record in records:
            if hasattr(record, "to_dict"):
                result.append(record.to_dict())
            elif isinstance(record, dict):
                result.append(record)
        return result

    def _build_data_quality(
        self,
        *,
        generated_at: str,
        assets: list[FinancialAsset],
        instruments: list,
        quotes: dict[str, list[Quote]],
        degradation_log: list[dict],
        news: list[NewsItem],
        news_requested: bool,
        macro_snapshot: Optional[dict],
        macro_error: Optional[str],
        technical_indicators: dict[str, dict],
        market_events: list,
        news_digest: dict,
    ) -> dict[str, dict]:
        """生成统一数据质量与溯源摘要。"""
        return {
            "schema_version": 1,
            "generated_at": generated_at,
            "currency_conversion": self._currency_conversion_quality(assets),
            "quotes": self._quote_quality(generated_at, instruments, quotes, degradation_log),
            "news": self._news_quality(generated_at, news, news_requested),
            "macro": self._macro_quality(generated_at, macro_snapshot, macro_error),
            "technical_indicators": self._indicator_quality(
                generated_at,
                instruments,
                quotes,
                technical_indicators,
            ),
            "market_events": self._market_event_quality(
                news_requested,
                news,
                market_events,
                news_digest,
            ),
        }

    @staticmethod
    def _currency_conversion_quality(assets: list[FinancialAsset]) -> dict:
        items = [
            {
                "name": asset.name,
                "currency": asset.currency,
                "status": asset.conversion_status,
                "source": asset.conversion_source,
                "rate": asset.conversion_rate,
            }
            for asset in assets
            if asset.currency.upper() != "CNY"
            or asset.conversion_status != "ok"
        ]
        failed = sum(item["status"] == "failed" for item in items)
        degraded = sum(item["status"] == "degraded" for item in items)
        if failed:
            status = "failed"
        elif degraded:
            status = "degraded"
        else:
            status = "ok"
        return {
            "status": status,
            "failed_count": failed,
            "degraded_count": degraded,
            "items": items,
        }

    def _quote_quality(
        self,
        generated_at: str,
        instruments: list,
        quotes: dict[str, list[Quote]],
        degradation_log: list[dict],
    ) -> dict:
        requested_by_market: dict[str, int] = {}
        for inst in instruments:
            requested_by_market[inst.market] = requested_by_market.get(inst.market, 0) + 1

        quote_count = sum(len(items) for items in quotes.values())
        requested_count = len(instruments)

        if requested_count == 0:
            status = "not_requested"
            freshness = "not_requested"
        elif quote_count == 0:
            status = "missing"
            freshness = "missing"
        elif quote_count < requested_count:
            status = "partial"
            freshness = "fresh"
        elif any(record.get("result") == "fallback_success" for record in degradation_log):
            status = "degraded"
            freshness = "fresh"
        else:
            status = "ok"
            freshness = "fresh"

        by_market = {}
        records_by_market = {record.get("market"): record for record in degradation_log}
        for market in sorted(set(requested_by_market) | set(quotes)):
            market_quote_count = len(quotes.get(market, []))
            market_requested = requested_by_market.get(market, 0)
            record = records_by_market.get(market, {})
            if market_requested == 0:
                market_status = "not_requested"
            elif market_quote_count == 0:
                market_status = "missing"
            elif market_quote_count < market_requested:
                market_status = "partial"
            elif record.get("result") == "fallback_success":
                market_status = "degraded"
            else:
                market_status = "ok"
            by_market[market] = {
                "status": market_status,
                "requested_count": market_requested,
                "item_count": market_quote_count,
                "primary_provider": record.get("primary_provider"),
                "fallback_provider": record.get("fallback_provider"),
                "degradation_result": record.get("result"),
                "message": record.get("message"),
            }

        providers = sorted({
            provider
            for record in degradation_log
            for provider in (record.get("primary_provider"), record.get("fallback_provider"))
            if provider
        })

        return {
            "status": status,
            "source": "DataFetcher",
            "as_of": generated_at if quote_count else None,
            "freshness": freshness,
            "requested_count": requested_count,
            "item_count": quote_count,
            "providers": providers,
            "by_market": by_market,
            "degradation": degradation_log,
        }

    def _news_quality(self, generated_at: str, news: list[NewsItem], requested: bool) -> dict:
        if not requested:
            return {
                "status": "not_requested",
                "source": "none",
                "as_of": None,
                "freshness": "not_requested",
                "item_count": 0,
                "sources": {},
            }

        sources: dict[str, int] = {}
        missing_published_at = 0
        newest = None
        for item in news:
            source_key = f"{item.source_type}:{item.source_name}"
            sources[source_key] = sources.get(source_key, 0) + 1
            if item.published_at is None:
                missing_published_at += 1
                continue
            published_at = item.published_at
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
            if newest is None or published_at > newest:
                newest = published_at

        if not news:
            status = "missing"
            freshness = "missing"
        elif missing_published_at:
            status = "partial"
            freshness = self._freshness_from_datetime(newest, generated_at)["freshness"] if newest else "unknown"
        else:
            status = "ok"
            freshness = self._freshness_from_datetime(newest, generated_at)["freshness"] if newest else "unknown"

        age_info = self._freshness_from_datetime(newest, generated_at) if newest else {"age_seconds": None}
        return {
            "status": status,
            "source": "NewsAggregator",
            "as_of": newest.isoformat() if newest else None,
            "freshness": freshness,
            "age_seconds": age_info["age_seconds"],
            "item_count": len(news),
            "sources": sources,
            "missing_published_at": missing_published_at,
        }

    def _macro_quality(
        self,
        generated_at: str,
        macro_snapshot: Optional[dict],
        macro_error: Optional[str],
    ) -> dict:
        if macro_snapshot is None:
            return {
                "status": "missing" if self.macro_provider else "not_configured",
                "source": "none",
                "as_of": None,
                "freshness": "missing" if self.macro_provider else "not_configured",
                "filled_fields": 0,
                "missing_fields": [],
                "errors": {"provider": macro_error} if macro_error else {},
            }

        metric_fields = ["usd_cny", "vix", "us_10y_yield", "dxy", "gold", "crude_oil"]
        filled = [field for field in metric_fields if macro_snapshot.get(field) is not None]
        missing = [field for field in metric_fields if macro_snapshot.get(field) is None]
        errors = macro_snapshot.get("errors") or {}
        if not filled:
            status = "missing"
        elif errors or missing:
            status = "partial"
        else:
            status = "ok"

        timestamp = macro_snapshot.get("timestamp")
        freshness_info = self._freshness_from_iso(timestamp, generated_at)
        return {
            "status": status,
            "source": macro_snapshot.get("source", "unknown"),
            "as_of": timestamp,
            "freshness": freshness_info["freshness"],
            "age_seconds": freshness_info["age_seconds"],
            "filled_fields": len(filled),
            "missing_fields": missing,
            "errors": errors,
        }

    def _indicator_quality(
        self,
        generated_at: str,
        instruments: list,
        quotes: dict[str, list[Quote]],
        technical_indicators: dict[str, dict],
    ) -> dict:
        requested_count = sum(len(items) for items in quotes.values()) if quotes else len(instruments)
        if requested_count == 0:
            status = "not_requested"
            freshness = "not_requested"
        elif not technical_indicators:
            status = "missing"
            freshness = "missing"
        else:
            ok_count = sum(1 for item in technical_indicators.values() if item.get("status") == "ok")
            missing_count = len(technical_indicators) - ok_count
            if ok_count == 0:
                status = "missing"
            elif missing_count:
                status = "partial"
            else:
                status = "ok"
            freshness = "fresh" if ok_count else "missing"

        missing_symbols = [
            symbol
            for symbol, item in technical_indicators.items()
            if item.get("status") != "ok"
        ]
        return {
            "status": status,
            "source": "history_cache" if self.history_cache else "none",
            "as_of": generated_at if technical_indicators else None,
            "freshness": freshness,
            "requested_count": requested_count,
            "item_count": len(technical_indicators),
            "missing_symbols": missing_symbols,
        }

    def _market_event_quality(
        self,
        news_requested: bool,
        news: list[NewsItem],
        market_events: list,
        news_digest: dict,
    ) -> dict:
        if not news_requested:
            status = "not_requested"
            freshness = "not_requested"
        elif not news:
            status = "missing"
            freshness = "missing"
        elif not market_events:
            status = "missing"
            freshness = "missing"
        elif len(market_events) < len(news):
            status = "partial"
            freshness = "fresh"
        else:
            status = "ok"
            freshness = "fresh"

        return {
            "status": status,
            "source": "MarketEventExtractor",
            "freshness": freshness,
            "news_count": len(news),
            "event_count": len(market_events),
            "top_urgency": next(iter(news_digest.get("urgency", {})), None),
            "matched_holdings_count": len(news_digest.get("matched_holdings", [])),
        }

    def _freshness_from_iso(self, value: Optional[str], generated_at: str) -> dict:
        if not value:
            return {"freshness": "unknown", "age_seconds": None}
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return {"freshness": "unknown", "age_seconds": None}
        return self._freshness_from_datetime(dt, generated_at)

    def _freshness_from_datetime(self, value: Optional[datetime], generated_at: str) -> dict:
        if value is None:
            return {"freshness": "unknown", "age_seconds": None}
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        try:
            now = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        except ValueError:
            now = datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        age_seconds = max(0, int((now - value).total_seconds()))
        if age_seconds <= 2 * 60 * 60:
            freshness = "fresh"
        elif age_seconds <= 24 * 60 * 60:
            freshness = "stale"
        else:
            freshness = "old"
        return {"freshness": freshness, "age_seconds": age_seconds}

    async def _enrich_with_indicators(
        self, quotes: dict[str, list[Quote]]
    ) -> dict[str, list[Quote]]:
        """为每个 Quote 附加技术指标计算结果"""
        enriched: dict[str, list[Quote]] = {}
        for market, market_quotes in quotes.items():
            enriched_quotes: list[Quote] = []
            for q in market_quotes:
                # 记录到历史缓存
                await self.history_cache.record(q.instrument, q)
                # 获取历史数据计算指标
                df = await self.history_cache.get_history(q.instrument, lookback_bars=60)
                indicators = TechnicalIndicators.calculate(df)
                # 使用 dataclasses.replace 创建带指标的新 Quote（frozen dataclass）
                enriched_q = replace(q, indicators=indicators)
                enriched_quotes.append(enriched_q)
            enriched[market] = enriched_quotes
        return enriched

    def _collect_technical_indicators(self, quotes: dict[str, list[Quote]]) -> dict[str, dict]:
        """汇总 Quote 上的技术指标，便于 Agent 稳定按标的读取。"""
        indicators_by_symbol: dict[str, dict] = {}
        for market, market_quotes in quotes.items():
            for q in market_quotes:
                key = f"{market}:{q.instrument.code}"
                if q.indicators:
                    indicators_by_symbol[key] = {
                        "status": "ok",
                        "source": "history_cache",
                        **q.indicators,
                    }
                else:
                    indicators_by_symbol[key] = {
                        "status": "missing",
                        "source": "none",
                    }
        return indicators_by_symbol

    def _build_raw_prompt(
        self,
        assets: list[FinancialAsset],
        quotes: dict,
        news: list[NewsItem],
        mapping: PortfolioMapping,
        market_state: MarketState,
        drift_checks: list[DriftCheck],
        constraints: dict,
        profile: dict,
        macro_snapshot: Optional[dict] = None,
        market_events: Optional[list] = None,
        news_digest: Optional[dict] = None,
        recent_snapshots: Optional[list[dict]] = None,
    ) -> str:
        """生成人类可读的原始输入文本，供 LLM 阅读"""
        lines: list[str] = []

        lines.append("=" * 50)
        lines.append("【投资组合分析上下文】")
        lines.append("=" * 50)
        lines.append("")

        # 用户画像
        lines.append("【用户画像】")
        for k, v in profile.items():
            lines.append(f" {k}: {v}")
        lines.append("")

        # 资产明细
        lines.append("【资产明细】")
        total = sum(a.valuation_cny or 0.0 for a in assets) if assets else 0
        lines.append(f" 总资产量级: {self._amount_band(total)}")
        lines.append(f" 资产数量: {len(assets)}")
        for asset in assets:
            value_cny = asset.valuation_cny
            numeric_value = value_cny or 0.0
            pct = (numeric_value / total * 100) if total > 0 else 0
            status = "" if asset.confirmed else "?"
            value_text = (
                f"占比 {pct:.1f}% | 量级 {self._amount_band(value_cny)}"
                if value_cny is not None
                else "换算失败（未计入合计）"
            )
            lines.append(
                f" {status} {asset.name} ({asset.platform}) | "
                f"类型: {asset.asset_type} | CNY估值: {value_text}"
            )
            if asset.notes:
                lines.append(f" 备注: {asset.notes}")
        lines.append("")

        if recent_snapshots:
            lines.append("【上次快照】")
            for snapshot in recent_snapshots[:5]:
                lines.append(
                    f" {snapshot.get('generated_at', 'unknown')} | "
                    f"资产数: {snapshot.get('asset_count', 0)} | "
                    f"组合: {snapshot.get('portfolio_summary', {})} | "
                    f"偏离: {snapshot.get('drift_checks', [])}"
                )
            lines.append("")

        # 组合结构
        lines.append("【组合结构】")
        for bucket, ratio in sorted(mapping.ratios.items(), key=lambda x: -x[1]):
            lines.append(f" {bucket}: {ratio * 100:.1f}%")
        lines.append(f" 主导层: {', '.join(mapping.dominant_layers) if mapping.dominant_layers else '无'}")
        lines.append(f" 成长暴露: {mapping.growth_exposure}")
        lines.append(f" 缓冲强度: {mapping.buffer_strength}")
        lines.append(f" 流动性状态: {mapping.liquidity_status}")
        lines.append(f" 含锁定资产: {'是' if mapping.locked_assets_present else '否'}")
        lines.append("")

        # 约束偏离检查
        lines.append("【约束偏离检查】")
        if drift_checks:
            for dc in drift_checks:
                if dc.status == "within_range":
                    lines.append(f" {dc.bucket}: {dc.current_ratio * 100:.1f}% 在范围内")
                elif dc.status == "below_min":
                    lines.append(
                        f" {dc.bucket}: {dc.current_ratio * 100:.1f}% ↓ 低于下限 "
                        f"({dc.target_min * 100:.1f}%), 缺口 {dc.gap * 100:.1f}%"
                    )
                elif dc.status == "above_max":
                    lines.append(
                        f" {dc.bucket}: {dc.current_ratio * 100:.1f}% ↑ 高于上限 "
                        f"({dc.target_max * 100:.1f}%), 缺口 {dc.gap * 100:.1f}%"
                    )
        else:
            lines.append(" 无约束配置或全部在范围内")
        lines.append("")

        # 约束配置
        lines.append("【约束配置】")
        for bucket, cfg in constraints.items():
            min_v = cfg.get("min")
            max_v = cfg.get("max")
            min_str = f"{min_v * 100:.1f}%" if min_v is not None else "-"
            max_str = f"{max_v * 100:.1f}%" if max_v is not None else "-"
            lines.append(f" {bucket}: [{min_str}, {max_str}]")
        lines.append("")

        # 市场行情 + 技术指标
        lines.append("【市场行情与技术指标】")
        if quotes:
            for market, market_quotes in quotes.items():
                lines.append(f" [{market.upper()}市场]")
                for q in market_quotes:
                    price_str = f"{q.price:.2f}" if q.price is not None else "N/A"
                    change_str = ""
                    if q.pct_change is not None:
                        sign = "+" if q.pct_change >= 0 else ""
                        change_str = f" ({sign}{q.pct_change:.2f}%)"
                    lines.append(f" {q.instrument.name} ({q.instrument.code}): {price_str}{change_str}")
                    # 附加技术指标
                    if q.indicators:
                        ind = q.indicators
                        ind_parts = []
                        if ind.get("ma_5") is not None and ind.get("ma_20") is not None:
                            ind_parts.append(f"MA5={ind['ma_5']:.2f}, MA20={ind['ma_20']:.2f}")
                        if ind.get("rsi_14") is not None:
                            rsi = ind["rsi_14"]
                            rsi_state = "超买" if rsi >= 70 else "超卖" if rsi <= 30 else "中性"
                            ind_parts.append(f"RSI={rsi:.1f}({rsi_state})")
                        if ind.get("macd") and ind["macd"].get("hist") is not None:
                            macd_hist = ind["macd"]["hist"]
                            macd_sign = "↑" if macd_hist > 0 else "↓"
                            ind_parts.append(f"MACD_hist={macd_hist:.3f}{macd_sign}")
                        if ind.get("bollinger") and ind["bollinger"].get("upper") is not None:
                            boll = ind["bollinger"]
                            ind_parts.append(f"Boll=({boll['lower']:.2f}, {boll['upper']:.2f})")
                        if ind.get("volume_ratio") is not None:
                            vr = ind["volume_ratio"]
                            vr_state = "放量" if vr > 1.5 else "缩量" if vr < 0.8 else "平量"
                            ind_parts.append(f"量比={vr:.2f}({vr_state})")
                        if ind_parts:
                            lines.append(f"  指标: {' | '.join(ind_parts)}")
        else:
            lines.append(" 暂无行情数据")
        lines.append("")

        # 宏观数据
        if macro_snapshot:
            lines.append("【宏观环境】")
            if macro_snapshot.get("vix") is not None:
                vix = macro_snapshot["vix"]
                vix_state = "恐慌" if vix > 30 else "警惕" if vix > 20 else "平静"
                lines.append(f" VIX 恐慌指数: {vix:.2f} ({vix_state})")
            if macro_snapshot.get("us_10y_yield") is not None:
                yield_val = macro_snapshot["us_10y_yield"]
                lines.append(f" 10年期美债收益率: {yield_val:.2f}%")
            if macro_snapshot.get("usd_cny") is not None:
                lines.append(f" 美元兑人民币: {macro_snapshot['usd_cny']:.4f}")
            if macro_snapshot.get("dxy") is not None:
                lines.append(f" 美元指数: {macro_snapshot['dxy']:.2f}")
            if macro_snapshot.get("gold") is not None:
                lines.append(f" 黄金: {macro_snapshot['gold']:.2f} USD/oz")
            if macro_snapshot.get("crude_oil") is not None:
                lines.append(f" 原油: {macro_snapshot['crude_oil']:.2f} USD/bbl")
            if macro_snapshot.get("errors"):
                lines.append(f" 数据缺失: {', '.join(macro_snapshot['errors'].keys())}")
            lines.append("")

        # 市场状态
        lines.append("【市场状态】")
        lines.append(f" 风险情绪: {market_state.risk_appetite}")
        lines.append(f" 科技状态: {market_state.tech_state}")
        lines.append(f" 避险资产: {market_state.safe_haven_state}")
        lines.append(f" 中国市场: {market_state.china_state}")
        lines.append(f" 利率/债券: {market_state.rates_state}")
        lines.append(f" 加密资产: {market_state.crypto_state}")
        if market_state.cross_asset_summary:
            lines.append(" 跨资产摘要:")
            for s in market_state.cross_asset_summary:
                lines.append(f" - {s}")
        lines.append("")

        # 结构化新闻事件
        lines.append("【新闻事件摘要】")
        event_items = market_events or []
        digest = news_digest or {}
        if event_items:
            if digest.get("themes"):
                top_themes = ", ".join(list(digest["themes"].keys())[:5])
                lines.append(f" 主要主题: {top_themes}")
            if digest.get("affected_markets"):
                top_markets = ", ".join(list(digest["affected_markets"].keys())[:5])
                lines.append(f" 影响市场: {top_markets}")
            if digest.get("matched_holdings"):
                holdings = ", ".join(digest["matched_holdings"][:5])
                lines.append(f" 关联持仓: {holdings}")
            for event in event_items[:5]:
                themes = ",".join(event.themes[:3]) if event.themes else "无"
                markets = ",".join(event.affected_markets) if event.affected_markets else "unknown"
                holdings = ",".join(event.matched_holdings[:3]) if event.matched_holdings else "无"
                lines.append(
                    f" [{event.urgency}/{event.sentiment}/{event.impact_horizon}] "
                    f"{event.title} | 类型:{event.event_type} | 主题:{themes} | "
                    f"市场:{markets} | 持仓:{holdings}"
                )
        else:
            lines.append(" 暂无结构化新闻事件")
        lines.append("")

        # 新闻
        lines.append("【相关新闻】")
        if news:
            for item in news[:10]:  # 最多展示 10 条
                lines.append(f" [{item.source_name}] {item.title}")
                if item.summary:
                    lines.append(f" 摘要: {item.summary}")
        else:
            lines.append(" 暂无新闻")
        lines.append("")

        lines.append("=" * 50)
        lines.append("请基于以上上下文给出投资组合分析和建议。")
        lines.append("=" * 50)

        return "\n".join(lines)

    @staticmethod
    def _amount_band(value: float) -> str:
        if value < 1_000:
            return "不足 1 千元"
        if value < 10_000:
            return "1 千至 1 万元"
        if value < 100_000:
            return "1 万至 10 万元"
        if value < 1_000_000:
            return "10 万至 100 万元"
        return "100 万元以上"
