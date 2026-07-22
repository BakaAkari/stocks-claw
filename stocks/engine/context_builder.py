"""AnalysisContext 组装器 — 编排数据获取与脚手架计算，生成统一分析上下文"""

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from stocks.domain.models import (
    Account,
    AnalysisContext,
    DriftCheck,
    FinancialAsset,
    MarketEvent,
    MarketState,
    NewsItem,
    PortfolioMapping,
    Position,
    Quote,
    financial_asset_to_position_v2,
)
from stocks.engine.action_signals import compute_action_signals
from stocks.engine.advice_review import attach_advice_performance, attach_execution_review
from stocks.engine.data_quality_gate import compute_action_eligible, detect_price_anomalies
from stocks.engine.event_calendar import EventCalendar
from stocks.engine.exchange_rate import convert_to_cny
from stocks.engine.fetchers import DataFetcher
from stocks.engine.history_cache import HistoryCache
from stocks.engine.indicators import TechnicalIndicators
from stocks.engine.macro_data import MacroProvider
from stocks.engine.market_events import MarketEventExtractor
from stocks.engine.news_intelligence_store import IntelligenceSignal, NewsIntelligenceStore
from stocks.engine.rotation import compute_rotation
from stocks.engine.scaffolds import MarketScaffold, PortfolioScaffold
from stocks.logging_utils import get_logger


def _optional_float(value) -> Optional[float]:
    if value is None or value != value:
        return None
    return float(value)


def _instrument_key(instrument) -> str:
    return f"{instrument.market}:{instrument.code}"


def _dedupe_instruments(instruments: list) -> list:
    result = []
    seen = set()
    for instrument in instruments:
        key = _instrument_key(instrument)
        if key in seen:
            continue
        result.append(instrument)
        seen.add(key)
    return result


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
        event_calendar: Optional[EventCalendar] = None,
        config: Optional[dict] = None,
        fund_nav_provider = None,
    ):
        self.fetcher = fetcher
        self.portfolio_scaffold = portfolio_scaffold
        self.market_scaffold = market_scaffold
        self.history_cache = history_cache
        self.macro_provider = macro_provider
        self.market_event_extractor = market_event_extractor or MarketEventExtractor()
        self.event_calendar = event_calendar
        self._config = config or {}
        self.fund_nav_provider = fund_nav_provider

    async def build(
        self,
        assets: list[FinancialAsset],
        constraints: dict,
        profile: dict,
        instruments: list,  # 要获取行情的标的列表
        recent_snapshots: list[dict],
        recent_advice: Optional[list[dict]] = None,
        watchlist: Optional[list] = None,
        news: Optional[list[NewsItem]] = None,
        news_requested: Optional[bool] = None,
        news_provider_errors: Optional[dict[str, str]] = None,
        history_backfill_report: Optional[list[dict]] = None,
        scan_instruments: Optional[list] = None,
        execution_records: Optional[list[dict]] = None,
        forecast_summary: Optional[dict] = None,
        asset_schema_version: int = 1,
        asset_load_warning: Optional[str] = None,
        asset_base_currency: str = "CNY",
        asset_accounts_v2: Optional[list[Account]] = None,
        asset_positions_v2: Optional[list[Position]] = None,
        auto_included_holdings: Optional[list[str]] = None,
        exposure_proxy: Optional[dict] = None,
    ) -> AnalysisContext:
        """构建完整分析上下文"""
        generated_at = datetime.now(timezone.utc).isoformat()
        news_was_requested = news_requested if news_requested is not None else news is not None

        # 1. 获取行情
        quotes: dict[str, list[Quote]] = {}
        if instruments:
            quotes = await self.fetcher.fetch_quotes(instruments)
        degradation_log = self._get_fetcher_degradation_log()
        quotes = await self._backfill_stale_us_quotes(
            instruments,
            quotes,
            degradation_log,
        )

        # 2. 记录行情到历史缓存，并计算技术指标
        if self.history_cache and quotes:
            quotes = await self._enrich_with_indicators(quotes)
        technical_indicators = self._collect_technical_indicators(quotes)

        positions = list(asset_positions_v2 or [])
        if not positions and assets:
            positions = [financial_asset_to_position_v2(asset) for asset in assets]
        position_valuations = self._build_position_valuations(
            positions=positions,
            quotes=quotes,
            generated_at=generated_at,
            exposure_proxy=exposure_proxy or {},
            action_signals=None,
        )
        valuation_assets = self._assets_from_position_valuations(
            positions,
            position_valuations,
        )
        analysis_assets = valuation_assets if positions else assets

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
            assets=analysis_assets,
            instruments=instruments,
            generated_at=generated_at,
        )

        # 5. 构建 PortfolioMapping
        mapping = self.portfolio_scaffold.build(analysis_assets, constraints)

        # 6. 检查 Drift
        drift_checks = self.portfolio_scaffold.check_drift(mapping, constraints)

        # 7. 构建 MarketState
        market_state = self.market_scaffold.build(quotes)

        # 7.5 未来催化剂日历（官方日程 + 财报日历）
        upcoming_events: list = []
        calendar_quality: Optional[dict] = None
        if self.event_calendar is not None:
            try:
                upcoming_events, calendar_quality = await self.event_calendar.fetch(
                    now=datetime.fromisoformat(generated_at),
                    watchlist=list(instruments) or list(watchlist or []),
                )
            except Exception as e:
                logger = get_logger("context_builder")
                logger.warning(f"Event calendar fetch failed: {e}")
                calendar_quality = {
                    "status": "missing",
                    "source": "EventCalendar",
                    "event_count": 0,
                    "errors": {"calendar": f"{type(e).__name__}: {e}"},
                }

        # 财报日历复用同一事件对象投影到 market_events，不另建平行模块。
        for event in upcoming_events:
            if event.event_type != "earnings":
                continue
            market_events.append(
                MarketEvent(
                    title=event.name,
                    url="",
                    source_name=event.source,
                    source_type="calendar",
                    published_at=self._parse_iso_datetime(event.scheduled_at),
                    summary=event.note,
                    event_type="earnings",
                    themes=["earnings"],
                    affected_markets=[event.market],
                    affected_symbols=list(event.affected_symbols),
                    matched_holdings=list(event.affected_symbols),
                    sentiment="unknown",
                    urgency="high" if (event.days_until or 0) <= 3 else "medium",
                    impact_horizon="immediate",
                    confidence=1.0,
                    rationale="官方财报日历中的未来事件",
                    raw_news_index=-1,
                )
            )

        # 7.6 板块轮动脚手架（watchlist + 扫描池，基于历史收盘）
        # 只依赖历史缓存，不依赖实时行情，因此 --no-quotes 时仍以 watchlist 计算
        rotation, rotation_frames, rotation_universe, scan_keys = (
            await self._build_rotation(
                list(watchlist or instruments),
                list(scan_instruments or []),
            )
        )

        # 7.65 加载全局情报巡逻聚合结果（时间事实源）
        intelligence_digest = self._build_intelligence_digest(
            repo_root=Path(__file__).resolve().parents[2],
            generated_at=generated_at,
            positions=[position.to_dict() for position in positions],
        )

        # 7.7 引擎动作信号（方向性候选动作，2026-07-02 用户裁决启用）
        action_signals = compute_action_signals(
            rotation_frames,
            rotation_universe,
            rotation,
            upcoming_events=upcoming_events,
            scan_keys=scan_keys,
        )
        rule_scorecard = self._build_rule_scorecard(
            rotation_frames, action_signals
        )
        position_valuations = self._build_position_valuations(
            positions=positions,
            quotes=quotes,
            generated_at=generated_at,
            exposure_proxy=exposure_proxy or {},
            action_signals=action_signals,
            technical_indicators=technical_indicators,
        )
        valuation_assets = self._assets_from_position_valuations(
            positions,
            position_valuations,
        )
        analysis_assets = valuation_assets if positions else assets
        exposure_summary = self._build_exposure_summary(position_valuations)
        liquidity_summary = self._build_liquidity_summary(position_valuations)
        asset_data_boundaries = self._build_asset_data_boundaries(position_valuations)
        advice_granularity = self._build_advice_granularity_summary(position_valuations)

        # 8. 对最近建议做历史表现事实回看与触发器核对
        reviewed_advice = await attach_advice_performance(
            recent_advice or [],
            watchlist=_dedupe_instruments(list(watchlist or []) + list(instruments)),
            history_cache=self.history_cache,
            positions=positions,
        )
        reviewed_advice = attach_execution_review(
            reviewed_advice,
            execution_records or [],
        )

        # 9. 生成 raw_prompt_input（人类可读文本）
        raw_prompt = self._build_raw_prompt(
            assets=analysis_assets,
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
            recent_advice=reviewed_advice,
            upcoming_events=upcoming_events,
            intelligence_digest=intelligence_digest,
            rotation=rotation,
            action_signals=action_signals,
            forecast_summary=forecast_summary,
            asset_accounts=asset_accounts_v2 or [],
            asset_positions=positions,
            position_valuations=position_valuations,
            exposure_summary=exposure_summary,
            liquidity_summary=liquidity_summary,
            asset_data_boundaries=asset_data_boundaries,
            advice_granularity=advice_granularity,
        )

        data_quality = self._build_data_quality(
            generated_at=generated_at,
            assets=assets,
            positions=positions,
            instruments=instruments,
            quotes=quotes,
            degradation_log=degradation_log,
            news=news,
            news_requested=news_was_requested,
            news_provider_errors=news_provider_errors or {},
            macro_snapshot=macro_snapshot.to_dict() if macro_snapshot else None,
            macro_error=macro_error,
            technical_indicators=technical_indicators,
            market_events=market_events,
            news_digest=news_digest,
            history_backfill_report=history_backfill_report or [],
            calendar_quality=calendar_quality,
            rotation=rotation,
            action_signals=action_signals,
            asset_schema_version=asset_schema_version,
            asset_load_warning=asset_load_warning,
            asset_base_currency=asset_base_currency,
            auto_included_holdings=auto_included_holdings or [],
            position_valuations=position_valuations,
            exposure_summary=exposure_summary,
            liquidity_summary=liquidity_summary,
            asset_data_boundaries=asset_data_boundaries,
            advice_granularity=advice_granularity,
        )

        # 10. 组装 AnalysisContext
        return AnalysisContext(
            generated_at=generated_at,
            assets=analysis_assets,
            asset_count=len(analysis_assets),
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
            recent_advice=reviewed_advice,
            upcoming_events=upcoming_events,
            intelligence_digest=intelligence_digest,
            rotation=rotation,
            action_signals=action_signals,
            rule_scorecard=rule_scorecard,
            forecast_summary=forecast_summary or {},
            asset_accounts=[account.to_dict() for account in (asset_accounts_v2 or [])],
            asset_positions=[position.to_dict() for position in positions],
            position_valuations=position_valuations,
            exposure_summary=exposure_summary,
            liquidity_summary=liquidity_summary,
            asset_data_boundaries=asset_data_boundaries,
            advice_granularity=advice_granularity,
            schema_version=12,
        )

    async def _build_rotation(
        self,
        instruments: list,
        scan_instruments: list,
    ) -> tuple[dict, dict, dict, set[str]]:
        """基于历史缓存计算 watchlist + 扫描池的轮动排名。

        返回 (rotation, frames, universe, scan_keys)，frames/universe 供
        动作信号层复用，避免重复读历史。
        """
        universe: dict[str, object] = {}
        scan_keys: set[str] = set()
        for instrument in instruments:
            universe[f"{instrument.market}:{instrument.code}"] = instrument
        for instrument in scan_instruments:
            key = f"{instrument.market}:{instrument.code}"
            if key not in universe:
                universe[key] = instrument
                scan_keys.add(key)

        if self.history_cache is None or not universe:
            empty_rotation = {
                "schema_version": 1,
                "status": "no_data",
                "as_of": None,
                "window": {"short_bars": 5, "long_bars": 20},
                "items": [],
                "category_momentum": {},
                "leaders": [],
                "laggards": [],
                "missing": sorted(universe),
            }
            return empty_rotation, {}, universe, scan_keys

        frames = {}
        for key, instrument in universe.items():
            try:
                frames[key] = await self.history_cache.get_history(
                    instrument, lookback_bars=30
                )
            except Exception as e:
                logger = get_logger("context_builder")
                logger.warning(f"Rotation history load failed for {key}: {e}")
        return compute_rotation(frames, universe, scan_keys), frames, universe, scan_keys

    def _build_rule_scorecard(
        self,
        frames: dict[str, pd.DataFrame],
        action_signals: dict,
    ) -> dict:
        """用历史数据回测 action signal 规则，计算记分卡。"""
        from stocks.engine.quant_action import backtest_action_signals

        items = action_signals.get("items", []) or []
        result = backtest_action_signals(frames, items)
        return result

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

    def _build_position_valuations(
        self,
        *,
        positions: list[Position],
        quotes: dict[str, list[Quote]],
        generated_at: str,
        exposure_proxy: dict,
        action_signals: Optional[dict],
        technical_indicators: Optional[dict[str, dict]] = None,
    ) -> list[dict]:
        """按 v2 position 生成运行时估值快照；只进入上下文，不持久化。"""
        if not positions:
            return []
        quotes_by_key = {
            f"{quote.instrument.market}:{quote.instrument.code}": quote
            for items in quotes.values()
            for quote in items
        }
        signal_by_symbol = {
            item.get("symbol"): item
            for item in (action_signals or {}).get("items", [])
            if item.get("symbol")
        }
        items: list[dict] = []
        for position in positions:
            indicators = (technical_indicators or {}).get(position.instrument_key or "", {})
            item = self._value_position(
                position,
                quotes_by_key=quotes_by_key,
                generated_at=generated_at,
                exposure_proxy=exposure_proxy,
                signal_by_symbol=signal_by_symbol,
                indicators=indicators,
            )
            items.append(item)

        total_cny = sum(item.get("market_value_cny") or 0.0 for item in items)
        for item in items:
            value_cny = item.get("market_value_cny")
            item["portfolio_weight"] = (
                round(value_cny / total_cny, 6)
                if total_cny > 0 and value_cny is not None
                else None
            )
        return items

    def _value_position(
        self,
        position: Position,
        *,
        quotes_by_key: dict[str, Quote],
        generated_at: str,
        exposure_proxy: dict,
        signal_by_symbol: dict[str, dict],
        indicators: Optional[dict] = None,
    ) -> dict:
        flags: list[str] = []
        method = position.valuation_input.method
        quote = quotes_by_key.get(position.instrument_key or "")
        price = None
        price_source = method
        as_of = position.valuation_input.as_of
        market_value = None

        if method == "market_quote":
            quantity = position.holding.quantity if position.holding else None
            if quote and quote.price is not None and quantity is not None:
                price = float(quote.price)
                market_value = price * quantity
                price_source = quote.source or "quote"
                as_of = quote.as_of
                if quote.stale:
                    flags.append("stale_quote")
            elif position.valuation_input.manual_amount is not None:
                market_value = position.valuation_input.manual_amount
                price_source = "manual_fallback"
                flags.extend(["missing_quote", "manual_fallback"])
            else:
                cost_amount = self._position_cost_amount(position)
                if cost_amount is not None:
                    market_value = cost_amount
                    price_source = "cost_basis_fallback"
                    flags.extend(["missing_quote", "cost_basis_fallback"])
                else:
                    flags.append("missing_quote")
        elif method == "fund_nav":
            # 从天天基金拉取实时净值（同步调用，Provider 内部做了缓存和节流）
            fund_code = (position.instrument or {}).get("fund_code")
            if fund_code and self.fund_nav_provider:
                try:
                    nav = self.fund_nav_provider._fetch_sync(str(fund_code))
                except Exception:
                    nav = None
                if nav and nav.confirmed_nav > 0 and position.holding and position.holding.quantity:
                    price = nav.confirmed_nav
                    market_value = price * position.holding.quantity
                    price_source = f"fund_nav:{nav.source}"
                    as_of = nav.confirmed_date
                    if nav.estimated_nav:
                        flags.append(f"est_nav={nav.estimated_nav}")
                else:
                    market_value = position.valuation_input.manual_amount
                    price_source = "fund_nav_fallback"
                    flags.append("fund_nav_unavailable")
            else:
                market_value = position.valuation_input.manual_amount
                price_source = method
        elif method in {
            "manual_amount",
            "insurance_value",
            "precious_metal_quote",
        }:
            market_value = position.valuation_input.manual_amount
            price_source = method
            if market_value is None:
                flags.append("missing_manual_amount")
            if self._is_stale_as_of(position.valuation_input.as_of, generated_at):
                flags.append("stale_manual")

        conversion = None
        market_value_cny = None
        if market_value is not None:
            conversion = convert_to_cny(market_value, position.currency)
            market_value_cny = conversion.amount_cny
            if conversion.status != "ok":
                flags.append(f"fx_{conversion.status}")
        elif position.currency != "CNY":
            flags.append("fx_not_computed")

        cost_amount = self._position_cost_amount(position)
        cost_currency = (
            position.holding.cost_basis.currency
            if position.holding and position.holding.cost_basis
            else None
        )
        pnl = None
        pnl_pct = None
        pnl_cny = None
        if cost_amount is not None and market_value is not None:
            if cost_currency and cost_currency != position.currency:
                flags.append("cost_currency_mismatch")
            else:
                pnl = market_value - cost_amount
                pnl_pct = (pnl / cost_amount * 100) if cost_amount > 0 else None
                if pnl is not None:
                    pnl_conversion = convert_to_cny(pnl, position.currency)
                    pnl_cny = pnl_conversion.amount_cny

        missing_fields = list(position.data_completeness.get("missing_fields", []))
        if market_value is None:
            missing_fields.append("valuation")
        if position.currency not in {"CNY", "USD"}:
            missing_fields.append("supported_fx")

        granularity = self._position_advice_granularity(position)
        proxy = self._position_proxy(position, exposure_proxy, signal_by_symbol)
        if granularity == "sector" and proxy is None:
            flags.append("missing_proxy")
        elif proxy is not None and proxy.get("signal") is None:
            flags.append("proxy_not_in_universe")

        # ── 逐持仓证据（freshness 等）──
        evidence = {}
        if as_of:
            parsed_as_of = self._parse_iso_datetime(as_of)
            if parsed_as_of:
                fi = self._freshness_from_datetime(parsed_as_of, generated_at)
                evidence["price_freshness"] = fi["freshness"]
                evidence["indicator_freshness"] = fi["freshness"]
            else:
                evidence["price_freshness"] = "missing"
                evidence["indicator_freshness"] = "missing"
        else:
            evidence["price_freshness"] = "missing"
            evidence["indicator_freshness"] = "missing"
        # 若无有效 indicators，indicator_freshness 单独标注
        ind = indicators or {}
        if not ind.get("data_points") or ind.get("data_points") < 15:
            evidence["indicator_freshness"] = "missing"

        # ── 数据异常守门（Task 2）──
        raw_anomalies = ind.get("_data_anomalies", []) if isinstance(ind, dict) else []
        if raw_anomalies:
            evidence["data_anomalies"] = raw_anomalies
            eligible, reasons = compute_action_eligible(raw_anomalies)
            evidence["action_eligible"] = eligible
            evidence["blocked_reasons"] = reasons
        else:
            evidence["data_anomalies"] = []
            evidence["action_eligible"] = True
            evidence["blocked_reasons"] = []

        return {
            "position_id": position.position_id,
            "account_id": position.account_id,
            "display_name": position.display_name,
            "instrument_key": position.instrument_key,
            "public_code": (position.instrument or {}).get("fund_code") or "",
            "currency": position.currency,
            "classification": position.classification.to_dict(),
            "liquidity": position.liquidity.to_dict(),
            "valuation_method": method,
            "quantity": position.holding.quantity if position.holding else None,
            "price": price,
            "price_source": price_source,
            "as_of": as_of,
            "market_value": round(market_value, 4) if market_value is not None else None,
            "market_value_cny": round(market_value_cny, 4) if market_value_cny is not None else None,
            "fx_rate": conversion.rate if conversion else None,
            "fx_source": conversion.source if conversion else None,
            "conversion_status": conversion.status if conversion else "not_computed",
            "cost_amount": round(cost_amount, 4) if cost_amount is not None else None,
            "cost_currency": cost_currency,
            "unrealized_pnl": round(pnl, 4) if pnl is not None else None,
            "unrealized_pnl_cny": round(pnl_cny, 4) if pnl_cny is not None else None,
            "pnl_pct": round(pnl_pct, 4) if pnl_pct is not None else None,
            "one_day_change_pct": round(quote.pct_change, 4) if quote and quote.pct_change is not None else None,
            "indicators": indicators or {},
            "portfolio_weight": None,
            "advice_granularity": granularity,
            "proxy": proxy,
            "flags": sorted(set(flags)),
            "missing_fields": sorted(set(missing_fields)),
            "confirmed": position.confirmed,
            "notes": position.notes,
            "evidence": evidence,
        }

    @staticmethod
    def _position_cost_amount(position: Position) -> Optional[float]:
        if not position.holding or not position.holding.cost_basis:
            return None
        cost_basis = position.holding.cost_basis
        if cost_basis.cost_amount is not None:
            return cost_basis.cost_amount
        if cost_basis.unit_cost is not None:
            return cost_basis.unit_cost * position.holding.quantity
        return None

    @staticmethod
    def _position_advice_granularity(position: Position) -> str:
        if position.liquidity.rebalance_eligible is False or position.liquidity.tier == "locked":
            return "fixed"
        if (
            position.valuation_input.method == "market_quote"
            and position.instrument_key
            and position.holding
            and position.holding.quantity is not None
        ):
            return "detailed"
        if (
            position.classification.exposure_tags
            and position.classification.asset_class
            in {"equity", "commodity", "alternative", "fixed_income"}
            and position.classification.product_type
            not in {"cash", "money_market_fund", "bank_wealth_management"}
        ):
            return "sector"
        return "manual"

    @staticmethod
    def _position_proxy(
        position: Position,
        exposure_proxy: dict,
        signal_by_symbol: dict[str, dict],
    ) -> Optional[dict]:
        if not position.classification.exposure_tags:
            return None
        for tag in position.classification.exposure_tags:
            proxy = exposure_proxy.get(tag)
            if not isinstance(proxy, dict):
                continue
            instrument_key = proxy.get("instrument_key")
            if not isinstance(instrument_key, str):
                continue
            signal = signal_by_symbol.get(instrument_key)
            return {
                "tag": tag,
                "instrument_key": instrument_key,
                "note": proxy.get("note"),
                "signal": signal.get("signal") if signal else None,
                "action_hint": signal.get("action_hint") if signal else None,
                "rank": signal.get("rank") if signal else None,
            }
        return None

    def _assets_from_position_valuations(
        self,
        positions: list[Position],
        valuations: list[dict],
    ) -> list[FinancialAsset]:
        assets: list[FinancialAsset] = []
        by_id = {position.position_id: position for position in positions}
        for item in valuations:
            position = by_id.get(item["position_id"])
            if position is None:
                continue
            amount = item.get("market_value")
            if amount is None:
                amount = position.valuation_input.manual_amount or 0.0
            asset_type = position.classification.asset_class
            if "gold" in position.classification.exposure_tags:
                asset_type = "gold"
            elif position.classification.asset_class == "insurance":
                asset_type = "locked"
            elif position.classification.asset_class == "cash_equivalent":
                asset_type = "cash"
            asset = FinancialAsset(
                name=position.display_name,
                platform=position.account_id,
                amount=amount,
                asset_type=asset_type,
                notes=position.notes,
                confirmed=position.confirmed,
                currency=position.currency,
                instrument_key=position.instrument_key,
                quantity=position.holding.quantity if position.holding else None,
                tradable=position.liquidity.tradable,
                amount_cny=item.get("market_value_cny"),
                conversion_status=item.get("conversion_status", "not_computed"),
                conversion_source=item.get("fx_source") or "not_computed",
                conversion_rate=item.get("fx_rate"),
            )
            assets.append(asset)
        return assets

    @staticmethod
    def _build_exposure_summary(position_valuations: list[dict]) -> dict:
        total = sum(item.get("market_value_cny") or 0.0 for item in position_valuations)
        exposures: dict[str, dict] = {}
        for item in position_valuations:
            value = item.get("market_value_cny")
            if value is None:
                continue
            classification = item.get("classification") or {}
            tags = classification.get("exposure_tags") or [classification.get("asset_class", "unknown")]
            for tag in tags:
                bucket = exposures.setdefault(
                    tag,
                    {"value_cny": 0.0, "ratio": 0.0, "positions": []},
                )
                bucket["value_cny"] += value
                bucket["positions"].append(item["position_id"])
        for bucket in exposures.values():
            bucket["value_cny"] = round(bucket["value_cny"], 4)
            bucket["ratio"] = round(bucket["value_cny"] / total, 6) if total > 0 else None
        top = sorted(
            (
                {"tag": tag, **value}
                for tag, value in exposures.items()
            ),
            key=lambda item: item.get("value_cny") or 0.0,
            reverse=True,
        )
        return {
            "total_value_cny": round(total, 4),
            "exposures": exposures,
            "top": top[:10],
        }

    @staticmethod
    def _build_liquidity_summary(position_valuations: list[dict]) -> dict:
        buckets = {
            "cash_or_t0": {"value_cny": 0.0, "positions": []},
            "t1_t2": {"value_cny": 0.0, "positions": []},
            "locked_or_ineligible": {"value_cny": 0.0, "positions": []},
            "unknown": {"value_cny": 0.0, "positions": []},
        }
        for item in position_valuations:
            value = item.get("market_value_cny") or 0.0
            liquidity = item.get("liquidity") or {}
            tier = liquidity.get("tier") or "unknown"
            eligible = liquidity.get("rebalance_eligible")
            tradable = liquidity.get("tradable")
            if eligible is False or tradable is False or tier in {"locked", "periodic_open"}:
                bucket_name = "locked_or_ineligible"
            elif tier in {"cash", "t0"}:
                bucket_name = "cash_or_t0"
            elif tier in {"t1", "t2_plus"} and tradable is not False:
                bucket_name = "t1_t2"
            else:
                bucket_name = "unknown"
            buckets[bucket_name]["value_cny"] += value
            buckets[bucket_name]["positions"].append(item["position_id"])
        for bucket in buckets.values():
            bucket["value_cny"] = round(bucket["value_cny"], 4)
        deployable = buckets["cash_or_t0"]["value_cny"] + buckets["t1_t2"]["value_cny"]
        return {
            "deployable_value_cny": round(deployable, 4),
            "buckets": buckets,
        }

    @staticmethod
    def _build_asset_data_boundaries(position_valuations: list[dict]) -> dict:
        issues: list[dict] = []
        for item in position_valuations:
            missing = set(item.get("missing_fields") or [])
            flags = set(item.get("flags") or [])
            if "cost_basis" in missing:
                issues.append({
                    "position_id": item["position_id"],
                    "severity": "degraded",
                    "capability": "pnl",
                    "message": f"{item['display_name']} 缺成本价，无法计算未实现盈亏和 pnl 型触发器",
                })
            if "valuation_as_of" in missing:
                issues.append({
                    "position_id": item["position_id"],
                    "severity": "degraded",
                    "capability": "freshness",
                    "message": f"{item['display_name']} 缺估值日期，手工金额无法判断时效",
                })
            if "stale_manual" in flags:
                issues.append({
                    "position_id": item["position_id"],
                    "severity": "degraded",
                    "capability": "valuation",
                    "message": f"{item['display_name']} 手工估值超过 30 天，精确调仓需先更新金额",
                })
            if "missing_quote" in flags:
                issues.append({
                    "position_id": item["position_id"],
                    "severity": "degraded",
                    "capability": "market_quote",
                    "message": f"{item['display_name']} 缺最新行情，估值使用降级路径或无法估值",
                })
            if "proxy_not_in_universe" in flags:
                proxy = item.get("proxy") or {}
                issues.append({
                    "position_id": item["position_id"],
                    "severity": "degraded",
                    "capability": "proxy",
                    "message": (
                        f"{item['display_name']} 的代理 {proxy.get('instrument_key')} "
                        "不在 watchlist/扫描池，本轮无代理信号"
                    ),
                })
            if "supported_fx" in missing:
                issues.append({
                    "position_id": item["position_id"],
                    "severity": "blocked",
                    "capability": "cny_valuation",
                    "message": f"{item['display_name']} 币种 {item['currency']} 暂不支持自动换算",
                })
        return {
            "issue_count": len(issues),
            "issues": issues,
        }

    @staticmethod
    def _build_advice_granularity_summary(position_valuations: list[dict]) -> dict:
        counts: dict[str, int] = {}
        items = []
        for item in position_valuations:
            granularity = item.get("advice_granularity", "unknown")
            counts[granularity] = counts.get(granularity, 0) + 1
            items.append({
                "position_id": item["position_id"],
                "instrument_key": item.get("instrument_key"),
                "granularity": granularity,
                "proxy": item.get("proxy"),
            })
        return {"counts": counts, "items": items}

    def _is_stale_as_of(self, value: Optional[str], generated_at: str) -> bool:
        parsed = self._parse_iso_datetime(value)
        if parsed is None and value:
            try:
                parsed = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
            except ValueError:
                return True
        if parsed is None:
            return False
        try:
            now = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        except ValueError:
            now = datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return (now - parsed).days > 30

    async def _backfill_stale_us_quotes(
        self,
        instruments: list,
        quotes: dict[str, list[Quote]],
        degradation_log: list[dict],
    ) -> dict[str, list[Quote]]:
        """Finnhub 单源失败时，用最近历史收盘价补充显式 stale 行情。"""
        if self.history_cache is None:
            return quotes
        us_failed = any(
            record.get("market") == "us"
            and record.get("primary_provider") == "finnhub"
            and record.get("fallback_provider") is None
            and record.get("result") == "empty"
            for record in degradation_log
        )
        if not us_failed:
            return quotes

        result = {market: list(items) for market, items in quotes.items()}
        existing = {quote.instrument.code for quote in result.get("us", [])}
        stale_quotes = result.setdefault("us", [])
        for instrument in instruments:
            if instrument.market != "us" or instrument.code in existing:
                continue
            history = await self.history_cache.get_history(instrument, lookback_bars=1)
            if history.empty or _optional_float(history.iloc[-1].get("price")) is None:
                continue
            row = history.iloc[-1]
            timestamp = row.get("timestamp")
            stale_quotes.append(
                Quote(
                    instrument=instrument,
                    price=float(row["price"]),
                    open_price=_optional_float(row.get("open_price")),
                    high=_optional_float(row.get("high")),
                    low=_optional_float(row.get("low")),
                    prev_close=_optional_float(row.get("prev_close")),
                    volume_lot=_optional_float(row.get("volume_lot")),
                    source="history_cache",
                    stale=True,
                    as_of=timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp),
                )
            )
        return result

    def _build_data_quality(
        self,
        *,
        generated_at: str,
        assets: list[FinancialAsset],
        positions: list[Position],
        instruments: list,
        quotes: dict[str, list[Quote]],
        degradation_log: list[dict],
        news: list[NewsItem],
        news_requested: bool,
        news_provider_errors: dict[str, str],
        macro_snapshot: Optional[dict],
        macro_error: Optional[str],
        technical_indicators: dict[str, dict],
        market_events: list,
        news_digest: dict,
        history_backfill_report: list[dict],
        calendar_quality: Optional[dict] = None,
        rotation: Optional[dict] = None,
        action_signals: Optional[dict] = None,
        asset_schema_version: int = 1,
        asset_load_warning: Optional[str] = None,
        asset_base_currency: str = "CNY",
        auto_included_holdings: Optional[list[str]] = None,
        position_valuations: Optional[list[dict]] = None,
        exposure_summary: Optional[dict] = None,
        liquidity_summary: Optional[dict] = None,
        asset_data_boundaries: Optional[dict] = None,
        advice_granularity: Optional[dict] = None,
    ) -> dict:
        """生成统一数据质量与溯源摘要。"""
        return {
            "schema_version": 10,
            "generated_at": generated_at,
            "asset_format": self._asset_format_quality(
                assets,
                positions=positions,
                schema_version=asset_schema_version,
                base_currency=asset_base_currency,
                load_warning=asset_load_warning,
            ),
            "currency_conversion": self._currency_conversion_quality(assets),
            "asset_completeness": self._asset_completeness_quality(
                position_valuations or [],
                asset_data_boundaries or {},
            ),
            "quotes": self._quote_quality(generated_at, instruments, quotes, degradation_log),
            "news": self._news_quality(
                generated_at, news, news_requested, news_provider_errors
            ),
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
            "history_backfill": self._history_backfill_quality(history_backfill_report),
            "upcoming_events": calendar_quality
            or {
                "status": "not_configured",
                "source": "none",
                "event_count": 0,
                "expired_count": 0,
                "cache": {"hits": 0, "misses": 0},
                "sources": {},
                "errors": {},
            },
            "rotation": self._rotation_quality(rotation),
            "action_signals": self._action_signal_quality(action_signals),
            "auto_included_holdings": {
                "count": len(auto_included_holdings or []),
                "items": list(auto_included_holdings or []),
            },
            "exposure_summary": exposure_summary or {},
            "liquidity_summary": liquidity_summary or {},
            "advice_granularity": advice_granularity or {},
        }

    @staticmethod
    def _asset_format_quality(
        assets: list[FinancialAsset],
        *,
        positions: list[Position],
        schema_version: int,
        base_currency: str,
        load_warning: Optional[str],
    ) -> dict:
        """资产格式兼容提示；v2 是权威入口，v1 只作为兼容层。"""
        if schema_version == 2:
            status = "ok"
            message = "schema_version=2 Position/Account loaded"
        elif assets:
            status = "migration_recommended"
            message = "v1 FinancialAsset compatibility layer loaded; migrate to schema_version=2 when ready"
        else:
            status = "no_assets"
            message = "no financial assets loaded"
        return {
            "status": status,
            "schema_version": schema_version,
            "base_currency": base_currency,
            "loaded_count": len(assets),
            "position_count": len(positions),
            "warning": load_warning,
            "message": message,
        }

    @staticmethod
    def _asset_completeness_quality(
        position_valuations: list[dict],
        asset_data_boundaries: dict,
    ) -> dict:
        issue_count = int(asset_data_boundaries.get("issue_count") or 0)
        blocked = sum(
            1
            for issue in asset_data_boundaries.get("issues", [])
            if issue.get("severity") == "blocked"
        )
        missing_by_position = {
            item["position_id"]: item.get("missing_fields", [])
            for item in position_valuations
            if item.get("missing_fields")
        }
        if blocked:
            status = "blocked"
        elif issue_count:
            status = "degraded"
        elif position_valuations:
            status = "ok"
        else:
            status = "no_assets"
        return {
            "status": status,
            "issue_count": issue_count,
            "blocked_count": blocked,
            "missing_by_position": missing_by_position,
            "issues": asset_data_boundaries.get("issues", []),
        }

    @staticmethod
    def _action_signal_quality(action_signals: Optional[dict]) -> dict:
        """汇总动作信号覆盖为 data_quality 节点。"""
        if not action_signals:
            return {
                "status": "not_configured",
                "source": "none",
                "item_count": 0,
                "counts": {},
            }
        return {
            "status": action_signals.get("status", "no_data"),
            "source": "action_signals",
            "item_count": len(action_signals.get("items", [])),
            "counts": action_signals.get("counts", {}),
        }

    @staticmethod
    def _rotation_quality(rotation: Optional[dict]) -> dict:
        """汇总轮动脚手架的覆盖与时效为 data_quality 节点。"""
        if not rotation:
            return {
                "status": "not_configured",
                "source": "none",
                "as_of": None,
                "freshness": "not_configured",
                "item_count": 0,
                "missing_count": 0,
            }
        return {
            "status": rotation.get("status", "no_data"),
            "source": "history_cache",
            "as_of": rotation.get("as_of"),
            "freshness": rotation.get("data_freshness", "unknown"),
            "item_count": len(rotation.get("items", [])),
            "missing_count": len(rotation.get("missing", [])),
            "missing": rotation.get("missing", []),
        }

    @staticmethod
    def _history_backfill_quality(report: list[dict]) -> dict:
        """D0-3: 汇总历史回填结果为 data_quality 节点。

        - status = ok:       所有标的 ok 或 skipped_cached
        - status = partial:  至少一个 ok/skipped_cached 且至少一个 failed
        - status = failed:   全部 failed
        - status = not_requested: report 为空(未尝试回填)
        """
        if not report:
            return {
                "status": "not_requested",
                "requested_count": 0,
                "ok_count": 0,
                "skipped_cached_count": 0,
                "failed_count": 0,
                "items": [],
            }
        ok_count = sum(1 for r in report if r.get("status") == "ok")
        skipped = sum(1 for r in report if r.get("status") == "skipped_cached")
        failed = sum(1 for r in report if r.get("status") == "failed")
        effective = ok_count + skipped
        total = len(report)
        if failed == 0:
            status = "ok"
        elif effective == 0:
            status = "failed"
        else:
            status = "partial"
        return {
            "status": status,
            "requested_count": total,
            "ok_count": ok_count,
            "skipped_cached_count": skipped,
            "failed_count": failed,
            "items": list(report),
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
        quote_as_of = [
            parsed
            for items in quotes.values()
            for quote in items
            if (parsed := self._parse_iso_datetime(quote.as_of)) is not None
        ]
        oldest_as_of = min(quote_as_of) if quote_as_of else None
        missing_as_of = quote_count - len(quote_as_of)
        has_stale = any(quote.stale for items in quotes.values() for quote in items)
        us_single_source_failed = any(
            record.get("market") == "us"
            and record.get("primary_provider") == "finnhub"
            and record.get("fallback_provider") is None
            and record.get("result") == "empty"
            for record in degradation_log
        )

        if requested_count == 0:
            status = "not_requested"
            freshness = "not_requested"
        elif quote_count == 0:
            status = "missing"
            freshness = "missing"
        else:
            freshness = (
                self._freshness_from_datetime(oldest_as_of, generated_at)["freshness"]
                if oldest_as_of
                else "unknown"
            )
            if has_stale:
                status = "degraded"
            elif quote_count < requested_count:
                status = "partial"
            elif any(record.get("result") == "fallback_success" for record in degradation_log):
                status = "degraded"
            else:
                status = "ok"

        by_market = {}
        records_by_market = {record.get("market"): record for record in degradation_log}
        for market in sorted(set(requested_by_market) | set(quotes)):
            market_quotes = quotes.get(market, [])
            market_quote_count = len(market_quotes)
            market_requested = requested_by_market.get(market, 0)
            record = records_by_market.get(market, {})
            market_has_stale = any(quote.stale for quote in market_quotes)
            market_as_of_values = [
                parsed
                for quote in market_quotes
                if (parsed := self._parse_iso_datetime(quote.as_of)) is not None
            ]
            market_oldest_as_of = min(market_as_of_values) if market_as_of_values else None
            if market_requested == 0:
                market_status = "not_requested"
            elif market_quote_count == 0:
                market_status = "missing"
            elif market_has_stale:
                market_status = "stale_fallback"
            elif market_quote_count < market_requested:
                market_status = "partial"
            elif record.get("result") == "fallback_success":
                market_status = "degraded"
            else:
                market_status = "ok"
            market_freshness_info = (
                self._freshness_from_datetime(market_oldest_as_of, generated_at)
                if market_oldest_as_of
                else {"freshness": "unknown", "age_seconds": None}
            )
            by_market[market] = {
                "status": market_status,
                "freshness": market_freshness_info["freshness"],
                "as_of": market_oldest_as_of.isoformat() if market_oldest_as_of else None,
                "single_source": self._is_single_source(
                    market, record.get("primary_provider")
                ),
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
            "as_of": oldest_as_of.isoformat() if oldest_as_of else None,
            "freshness": freshness,
            "requested_count": requested_count,
            "item_count": quote_count,
            "missing_as_of": missing_as_of,
            "providers": providers,
            "us_quotes": "single_source_failed" if us_single_source_failed else "ok",
            "by_market": by_market,
            "degradation": degradation_log,
        }

    def _is_single_source(self, market: str, primary_provider: Optional[str]) -> bool:
        """从 DataFetcher 的实际 registry/config 计算单源事实。"""
        checker = getattr(self.fetcher, "is_single_source", None)
        if callable(checker):
            result = checker(market, primary_provider)
            if isinstance(result, bool):
                return result
        # 测试替身或旧 fetcher 无法证明存在独立备用源时，保守标为单源。
        return True

    def _news_quality(
        self,
        generated_at: str,
        news: list[NewsItem],
        requested: bool,
        provider_errors: Optional[dict[str, str]] = None,
    ) -> dict:
        provider_errors = provider_errors or {}
        if not requested:
            return {
                "status": "not_requested",
                "source": "none",
                "as_of": None,
                "freshness": "not_requested",
                "item_count": 0,
                "sources": {},
                "scopes": {},
                "errors": {},
            }

        sources: dict[str, int] = {}
        scopes: dict[str, int] = {}
        missing_published_at = 0
        newest = None
        for item in news:
            source_key = f"{item.source_type}:{item.source_name}"
            sources[source_key] = sources.get(source_key, 0) + 1
            scopes[item.scope] = scopes.get(item.scope, 0) + 1
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
        elif missing_published_at or provider_errors:
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
            "scopes": scopes,
            "missing_published_at": missing_published_at,
            "errors": provider_errors,
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

        market_fields = ["usd_cny", "vix", "us_10y_yield", "dxy", "gold", "crude_oil"]
        official_fields = [
            "official_stats.cpi_yoy",
            "official_stats.us_unemployment",
            "official_stats.fed_funds_rate",
        ]
        official_stats = macro_snapshot.get("official_stats") or {}
        field_sources = macro_snapshot.get("field_sources") or {}

        filled = [field for field in market_fields if macro_snapshot.get(field) is not None]
        filled.extend(
            field for field in official_fields
            if official_stats.get(field.split(".", 1)[1]) is not None
        )
        missing = [field for field in market_fields if macro_snapshot.get(field) is None]
        missing.extend(
            field for field in official_fields
            if official_stats.get(field.split(".", 1)[1]) is None
        )
        errors = macro_snapshot.get("errors") or {}
        if not filled:
            status = "missing"
        elif errors or missing:
            status = "partial"
        else:
            status = "ok"

        # ── 分层 as_of：市场数据(日频) vs 官方统计(月频) ──
        market_as_of_raw = macro_snapshot.get("market_as_of")
        official_as_of_raw = macro_snapshot.get("official_as_of")
        market_dt = self._parse_iso_datetime(market_as_of_raw) if market_as_of_raw else None
        official_dt = self._parse_iso_datetime(official_as_of_raw) if official_as_of_raw else None

        # 市场数据新鲜度
        market_fresh = self._freshness_from_datetime(market_dt, generated_at) if market_dt else {
            "freshness": "unknown", "age_seconds": None}
        # 官方统计新鲜度
        official_fresh = self._freshness_from_datetime(official_dt, generated_at) if official_dt else {
            "freshness": "unknown", "age_seconds": None}

        # 整体 as_of 仍保留为市场数据 as_of（兼容旧引用）
        as_of_values = [
            parsed
            for metadata in field_sources.values()
            if (parsed := self._parse_iso_datetime(metadata.get("as_of"))) is not None
        ]
        oldest_as_of = min(as_of_values) if as_of_values else None

        sources = sorted({
            metadata.get("source")
            for metadata in field_sources.values()
            if metadata.get("source")
        })
        missing_as_of = max(0, len(filled) - len(as_of_values))
        if status == "ok" and missing_as_of:
            status = "partial"
        return {
            "status": status,
            "source": macro_snapshot.get("source", "unknown"),
            "as_of": oldest_as_of.isoformat() if oldest_as_of else None,
            "freshness": market_fresh["freshness"],
            "age_seconds": market_fresh["age_seconds"],
            "filled_fields": len(filled),
            "missing_fields": missing,
            "missing_as_of": missing_as_of,
            "sources": sources,
            "field_sources": field_sources,
            # 分层新鲜度
            "market": {
                "as_of": market_as_of_raw,
                "freshness": market_fresh["freshness"],
                "age_seconds": market_fresh["age_seconds"],
                "fields": market_fields,
            },
            "official": {
                "as_of": official_as_of_raw,
                "freshness": official_fresh["freshness"],
                "age_seconds": official_fresh["age_seconds"],
                "fields": official_fields,
                "next_release": macro_snapshot.get("next_official_release"),
            },
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
            statuses = [item.get("status") for item in technical_indicators.values()]
            ok_count = sum(1 for s in statuses if s == "ok")
            partial_count = sum(1 for s in statuses if s == "partial")
            total = len(statuses)
            # D0-1:全 ok → ok;混合(至少一个 ok 但非全) 或至少一个 partial → partial;全非 ok → missing
            if ok_count == total:
                status = "ok"
            elif ok_count == 0 and partial_count == 0:
                status = "missing"
            else:
                status = "partial"
            freshness = "fresh" if ok_count or partial_count else "missing"

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
        calendar_event_count = sum(
            1
            for event in market_events
            if getattr(event, "source_type", "") == "calendar"
        )
        if calendar_event_count and not news:
            status = "ok"
            freshness = "fresh"
        elif not news_requested:
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

        matched_holdings = {
            symbol
            for event in market_events
            for symbol in getattr(event, "matched_holdings", [])
        }
        return {
            "status": status,
            "source": (
                "MarketEventExtractor+EventCalendar"
                if calendar_event_count
                else "MarketEventExtractor"
            ),
            "freshness": freshness,
            "news_count": len(news),
            "event_count": len(market_events),
            "calendar_event_count": calendar_event_count,
            "top_urgency": next(iter(news_digest.get("urgency", {})), None),
            "matched_holdings_count": len(matched_holdings),
        }

    def _freshness_from_iso(self, value: Optional[str], generated_at: str) -> dict:
        dt = self._parse_iso_datetime(value)
        if dt is None:
            return {"freshness": "unknown", "age_seconds": None}
        return self._freshness_from_datetime(dt, generated_at)

    @staticmethod
    def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

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
                # stale 回退不能伪造成今天的新行情写回历史。
                if not q.stale:
                    await self.history_cache.record(q.instrument, q)
                # 获取历史数据计算指标
                df = await self.history_cache.get_history(q.instrument, lookback_bars=60)
                indicators = TechnicalIndicators.calculate(df)
                # ── 数据异常检测（Task 2）──
                current_price = q.price
                ma20 = indicators.get('ma_20') if indicators else None
                data_anomalies = detect_price_anomalies(
                    df, current_price=current_price, ma20=ma20,
                )
                if data_anomalies:
                    indicators['_data_anomalies'] = data_anomalies
                # 使用 dataclasses.replace 创建带指标的新 Quote（frozen dataclass）
                enriched_q = replace(q, indicators=indicators)
                enriched_quotes.append(enriched_q)
            enriched[market] = enriched_quotes
        return enriched

    # 技术指标可用性判级阈值(D0-1)：
    # - ok:  data_points >= _INDICATOR_OK_MIN_BARS 且核心指标全部非 None
    # - partial: 不满足 ok 但至少一个核心指标非 None(通常 >= 15 bars 后 MA/RSI 可算)
    # - missing: 核心指标全 None,或 data_points < _INDICATOR_MISSING_MAX_BARS
    _INDICATOR_OK_MIN_BARS = 35
    _INDICATOR_MISSING_MAX_BARS = 15
    _INDICATOR_CORE_KEYS = ("ma_20", "rsi_14", "macd.hist", "bollinger.upper")

    @classmethod
    def _classify_indicator_item(cls, indicators: dict) -> tuple[str, list[str]]:
        """按 data_points 与核心指标可用性判级,返回 (status, unavailable 列表)。

        核心指标:ma_20 / rsi_14 / macd.hist / bollinger.upper。
        """
        data_points = indicators.get("data_points") or 0
        core_present: dict[str, bool] = {}
        for key in cls._INDICATOR_CORE_KEYS:
            if "." in key:
                parent, child = key.split(".", 1)
                sub = indicators.get(parent) or {}
                value = sub.get(child) if isinstance(sub, dict) else None
            else:
                value = indicators.get(key)
            core_present[key] = value is not None

        unavailable = [k for k, present in core_present.items() if not present]

        if data_points < cls._INDICATOR_MISSING_MAX_BARS or not any(core_present.values()):
            status = "missing"
        elif data_points >= cls._INDICATOR_OK_MIN_BARS and all(core_present.values()):
            status = "ok"
        else:
            status = "partial"
        return status, unavailable

    def _collect_technical_indicators(self, quotes: dict[str, list[Quote]]) -> dict[str, dict]:
        """汇总 Quote 上的技术指标，按 data_points 三态判级(D0-1)。"""
        indicators_by_symbol: dict[str, dict] = {}
        for market, market_quotes in quotes.items():
            for q in market_quotes:
                key = f"{market}:{q.instrument.code}"
                if q.indicators:
                    status, unavailable = self._classify_indicator_item(q.indicators)
                    indicators_by_symbol[key] = {
                        "status": status,
                        "source": "history_cache",
                        "unavailable": unavailable,
                        **q.indicators,
                    }
                else:
                    indicators_by_symbol[key] = {
                        "status": "missing",
                        "source": "none",
                        "data_points": 0,
                        "unavailable": list(self._INDICATOR_CORE_KEYS),
                    }
        return indicators_by_symbol

    @staticmethod
    def _public_cluster_articles(articles: list[dict], *, limit: int = 5) -> list[dict]:
        allowed = ("source", "title", "url", "published_at")
        result: list[dict] = []
        for raw in articles or []:
            item = {key: raw.get(key) for key in allowed if raw.get(key) not in (None, "")}
            if item.get("title") and item.get("url") and item.get("published_at"):
                result.append(item)
            if len(result) == limit:
                break
        return result

    def _build_intelligence_digest(
        self,
        *,
        repo_root: Path,
        generated_at: Optional[str] = None,
        positions: Optional[list[dict]] = None,
    ) -> dict:
        """Load auditable intelligence facts for trading-session consumption."""
        from stocks.engine.intelligence_analyzer import (
            _compute_brief_health,
            _compute_coverage,
            match_intelligence,
        )

        intelligence_dir = self._config.get("intelligence_dir")
        if not intelligence_dir:
            return {"status": "not_configured"}
        path = Path(intelligence_dir)
        if not path.is_absolute():
            path = repo_root / path
        if not path.exists():
            return {"status": "not_found"}
        try:
            store = NewsIntelligenceStore(path)
            snapshot = store.latest_snapshot()
            clusters_payload = store.latest_clusters()
            signals_payload = store.latest_signals()
            if snapshot is None and clusters_payload is None and signals_payload is None:
                return {"status": "empty"}

            raw_signals = (signals_payload or {}).get("signals", [])
            parsed_signals = [IntelligenceSignal.from_dict(item) for item in raw_signals]
            matched = []
            for position in positions or []:
                matched.extend(match_intelligence(position, parsed_signals))
            coverage = _compute_coverage(matched)

            brief_path = repo_root / ".local" / "intelligence" / "latest_brief.json"
            brief = {}
            if brief_path.exists():
                brief = json.loads(brief_path.read_text(encoding="utf-8"))
            source_at = (
                brief.get("source_generated_at")
                or (signals_payload or {}).get("generated_at")
                or (clusters_payload or {}).get("formed_at")
                or (snapshot.collected_at.isoformat() if snapshot else None)
            )
            now_dt = self._parse_iso_datetime(generated_at) or datetime.now(timezone.utc)
            source_dt = self._parse_iso_datetime(source_at)
            if source_dt is None:
                health = {"status": "missing", "age_minutes": None, "risk_eligible": False}
            else:
                health = _compute_brief_health(now_dt, source_dt)

            clusters = (clusters_payload or {}).get("clusters", [])
            risk_eligible = bool(health.get("risk_eligible"))
            return {
                "status": "ok" if risk_eligible else health["status"],
                "snapshot_at": snapshot.collected_at.isoformat() if snapshot else None,
                "source_run_id": brief.get("source_run_id"),
                "source_generated_at": source_at,
                "brief_generated_at": brief.get("brief_generated_at"),
                "article_count": len(snapshot.articles) if snapshot else 0,
                "cluster_count": len(clusters),
                "signal_count": len(raw_signals),
                "intelligence_health": health,
                "intelligence_coverage": coverage,
                "top_clusters": [
                    {
                        "cluster_id": c.get("cluster_id"),
                        "theme": c.get("theme"),
                        "event_type": c.get("event_type"),
                        "summary": c.get("summary"),
                        "articles": self._public_cluster_articles(c.get("articles", [])),
                        "affected_markets": c.get("affected_markets", []),
                        "affected_symbols": c.get("affected_symbols", []),
                        "sentiment": c.get("sentiment"),
                        "urgency": c.get("urgency"),
                        "confidence": c.get("confidence"),
                        "formed_at": c.get("formed_at"),
                    }
                    for c in clusters[:5]
                ] if risk_eligible else [],
                "top_signals": [dict(signal) for signal in raw_signals] if risk_eligible else [],
            }
        except Exception as exc:  # noqa: BLE001
            logger = get_logger("context_builder")
            logger.warning(f"Intelligence digest load failed: {exc}")
            return {"status": "error", "error": str(exc)}


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
        recent_advice: Optional[list[dict]] = None,
        upcoming_events: Optional[list] = None,
        intelligence_digest: Optional[dict] = None,
        rotation: Optional[dict] = None,
        action_signals: Optional[dict] = None,
        forecast_summary: Optional[dict] = None,
        asset_accounts: Optional[list[Account]] = None,
        asset_positions: Optional[list[Position]] = None,
        position_valuations: Optional[list[dict]] = None,
        exposure_summary: Optional[dict] = None,
        liquidity_summary: Optional[dict] = None,
        asset_data_boundaries: Optional[dict] = None,
        advice_granularity: Optional[dict] = None,
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
        holdings_by_key = {
            asset.instrument_key: asset
            for asset in assets
            if asset.instrument_key
        }
        lines.append(f" 总资产: {self._money(total, 'CNY')}")
        lines.append(f" 资产数量: {len(assets)}")
        for asset in assets:
            value_cny = asset.valuation_cny
            numeric_value = value_cny or 0.0
            pct = (numeric_value / total * 100) if total > 0 else 0
            status = "" if asset.confirmed else "?"
            value_text = (
                f"{self._money(value_cny, 'CNY')} | 占比 {pct:.1f}%"
                if value_cny is not None
                else "换算失败（未计入合计）"
            )
            lines.append(
                f" {status} {asset.name} ({asset.platform}) | "
                f"类型: {asset.asset_type} | CNY估值: {value_text}"
                f"{self._asset_mapping_text(asset)}"
            )
            if asset.notes:
                lines.append(f" 备注: {asset.notes}")
        lines.append("")

        self._append_asset_boundary_section(
            lines,
            asset_data_boundaries=asset_data_boundaries or {},
        )
        self._append_position_valuation_section(
            lines,
            accounts=asset_accounts or [],
            positions=asset_positions or [],
            valuations=position_valuations or [],
        )
        self._append_exposure_section(lines, exposure_summary or {})
        self._append_liquidity_section(lines, liquidity_summary or {})
        self._append_granularity_section(lines, advice_granularity or {})

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

        self._append_review_section(
            lines,
            recent_advice=recent_advice or [],
            forecast_summary=forecast_summary or {},
        )

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
                    stale_str = " [stale历史收盘]" if q.stale else ""
                    holding = holdings_by_key.get(_instrument_key(q.instrument))
                    holding_str = (
                        f" | {self._holding_text(holding)}" if holding else ""
                    )
                    lines.append(
                        f" {q.instrument.name} ({q.instrument.code}): "
                        f"{price_str}{change_str}{stale_str}{holding_str}"
                    )
                    # 附加技术指标(D0-1:按 data_points 判级,非 ok 显式标注不可用)
                    if q.indicators:
                        ind = q.indicators
                        status, _ = self._classify_indicator_item(ind)
                        data_points = ind.get("data_points") or 0
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
                        if status == "missing":
                            lines.append(
                                f"  指标: (历史仅 {data_points} bars,指标不可用)"
                            )
                        elif status == "partial":
                            suffix = f" | (历史仅 {data_points} bars,指标部分可用)"
                            body = " | ".join(ind_parts) if ind_parts else ""
                            lines.append(f"  指标: {body}{suffix}" if body else f"  指标:{suffix}")
                        elif ind_parts:
                            lines.append(f"  指标: {' | '.join(ind_parts)}")
        else:
            lines.append(" 暂无行情数据")
        lines.append("")

        # 宏观数据
        if macro_snapshot:
            lines.append("【宏观环境】")
            lines.append(" 市场定价代理（日度/实时，以字段观测日为准）:")
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
                lines.append(f" 美元广义指数代理: {macro_snapshot['dxy']:.2f}")
            if macro_snapshot.get("gold") is not None:
                lines.append(f" 黄金: {macro_snapshot['gold']:.2f} USD/oz")
            if macro_snapshot.get("crude_oil") is not None:
                lines.append(f" 原油: {macro_snapshot['crude_oil']:.2f} USD/bbl")
            official_stats = macro_snapshot.get("official_stats") or {}
            lines.append(" 官方统计（滞后月度，不代表实时）:")
            if official_stats.get("cpi_yoy") is not None:
                lines.append(f" 美国 CPI 同比: {official_stats['cpi_yoy']:.2f}%")
            if official_stats.get("us_unemployment") is not None:
                lines.append(f" 美国失业率: {official_stats['us_unemployment']:.2f}%")
            if official_stats.get("fed_funds_rate") is not None:
                lines.append(f" 联邦基金有效利率: {official_stats['fed_funds_rate']:.2f}%")
            if macro_snapshot.get("errors"):
                lines.append(f" 数据源降级: {', '.join(macro_snapshot['errors'].keys())}")
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

        # 未来催化剂日历
        lines.append("【未来催化剂日历】")
        if upcoming_events:
            lines.append(
                " 以下为已官方公布的未来日程事实(非预测),按日期排列:"
            )
            for event in upcoming_events:
                day_text = (
                    f"T+{event.days_until}天"
                    if event.days_until is not None and event.days_until > 0
                    else "今天"
                    if event.days_until == 0
                    else "?"
                )
                time_text = f" {event.time_utc} UTC" if event.time_utc else ""
                lines.append(
                    f" {event.date}{time_text} ({day_text}) {event.name} "
                    f"[{event.event_type}/{event.market}]"
                )
                if event.affected_categories:
                    lines.append(
                        f"  敏感类别: {', '.join(event.affected_categories)}"
                    )
                if event.affected_symbols:
                    lines.append(
                        f"  关联标的: {', '.join(event.affected_symbols[:8])}"
                    )
                if event.note:
                    lines.append(f"  路径: {event.note}")
        else:
            lines.append(" 未配置事件日历或窗口内无已知事件(缺失即缺失,不要虚构日程)")
        lines.append("")

        # 板块轮动排名
        lines.append("【板块轮动排名】")
        rotation = rotation or {}
        rotation_items = rotation.get("items", [])
        if rotation_items:
            window = rotation.get("window", {})
            lines.append(
                f" 基于历史收盘的相对强弱(近{window.get('short_bars', 5)}/"
                f"{window.get('long_bars', 20)}根K线累计涨跌幅, as_of "
                f"{rotation.get('as_of', 'unknown')}, 非实时):"
            )
            for item in rotation_items:
                r5 = f"{item['r5']:+.2f}%" if item.get("r5") is not None else "n/a"
                r20 = f"{item['r20']:+.2f}%" if item.get("r20") is not None else "n/a"
                ma_flag = (
                    "MA20上方"
                    if item.get("above_ma20")
                    else "MA20下方"
                    if item.get("above_ma20") is False
                    else ""
                )
                universe = "关注" if item.get("universe") == "watchlist" else "扫描"
                lines.append(
                    f" #{item.get('rank')} {item.get('name')} ({item.get('symbol')}) "
                    f"[{item.get('category')}/{universe}] 5日 {r5} | 20日 {r20}"
                    + (f" | {ma_flag}" if ma_flag else "")
                )
            momentum = rotation.get("category_momentum", {})
            if momentum:
                sorted_momentum = sorted(
                    momentum.items(),
                    key=lambda kv: kv[1].get("r20") if kv[1].get("r20") is not None else -1e9,
                    reverse=True,
                )
                parts = []
                for category, stats in sorted_momentum:
                    r20 = stats.get("r20")
                    parts.append(
                        f"{category} {r20:+.2f}%" if r20 is not None else f"{category} n/a"
                    )
                lines.append(f" 类别20日动量排序: {'; '.join(parts)}")
            missing = rotation.get("missing", [])
            if missing:
                lines.append(
                    f" 历史不足未参与排名: {', '.join(missing)} (缺失即缺失,不要推断)"
                )
        else:
            lines.append(" 无可用历史数据,轮动排名缺失(不要虚构强弱)")
        lines.append("")

        # 引擎动作信号
        lines.append("【引擎动作信号】")
        signals = (action_signals or {}).get("items", [])
        if signals:
            lines.append(
                " 规则化候选动作(附触发事实,非指令;逐条确认或推翻后再输出最终建议):"
            )
            for item in signals:
                if item.get("signal") in ("neutral_hold", "no_data"):
                    continue
                header = (
                    f" [{item.get('signal')}] {item.get('name')} "
                    f"({item.get('symbol')}) [{item.get('pool')}]"
                )
                lines.append(header)
                for reason in item.get("reasons", []):
                    lines.append(f"  - {reason}")
                if item.get("event_watch"):
                    lines.append(
                        f"  - 事件叠加: {'; '.join(item['event_watch'])}"
                    )
                lines.append(f"  建议动作: {item.get('action_hint')}")
            neutral = [i for i in signals if i.get("signal") == "neutral_hold"]
            if neutral:
                lines.append(
                    " 无方向信号(neutral_hold): "
                    + ", ".join(i.get("symbol", "?") for i in neutral)
                )
            missing_signals = [i for i in signals if i.get("signal") == "no_data"]
            if missing_signals:
                lines.append(
                    f" 历史/指标不足不给方向(no_data) {len(missing_signals)} 个: "
                    + ", ".join(i.get("symbol", "?") for i in missing_signals[:12])
                    + ("..." if len(missing_signals) > 12 else "")
                )
        else:
            lines.append(" 无可用动作信号(历史或指标不足,不给方向)")
        lines.append("")

        # 全局情报巡逻事实源
        lines.append("【全局情报巡逻事实源】")
        digest = intelligence_digest or {}
        if digest.get("status") == "ok":
            lines.append(f" 最新快照: {digest.get('snapshot_at')}")
            lines.append(f" 文章数: {digest.get('article_count', 0)}, 事件簇: {digest.get('cluster_count', 0)}, 信号: {digest.get('signal_count', 0)}")
            for cluster in digest.get("top_clusters", []):
                lines.append(
                    f" - [{cluster.get('urgency')}] {cluster.get('theme')}: {cluster.get('summary')[:100]}"
                )
            for signal in digest.get("top_signals", []):
                lines.append(
                    f" - [{signal.get('urgency')}] {signal.get('symbol')}: {signal.get('direction')} - {signal.get('rationale')[:80]}"
                )
        else:
            lines.append(f" 状态: {digest.get('status', 'not_loaded')}")
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
        lines.append(
            "以上 raw_prompt_input 仅为兼容数据摘要；生产报告以 scheduled artifact 的 "
            "agent_task v5 与 portfolio_decision.user_view 为唯一输出契约。"
        )
        lines.append("=" * 50)

        return "\n".join(lines)

    def _append_review_section(
        self,
        lines: list[str],
        *,
        recent_advice: list[dict],
        forecast_summary: dict,
    ) -> None:
        lines.append("【复盘】")
        self._append_review_actions(lines, recent_advice)
        self._append_review_triggers(lines, recent_advice)
        self._append_review_executions(lines, recent_advice)
        self._append_review_forecasts(lines, forecast_summary)
        lines.append("")

    def _append_asset_boundary_section(
        self,
        lines: list[str],
        *,
        asset_data_boundaries: dict,
    ) -> None:
        lines.append("【数据边界】")
        issues = asset_data_boundaries.get("issues", [])
        if not issues:
            lines.append(" 资产事实完整性足够支撑当前估值、暴露和流动性分析。")
            lines.append("")
            return
        for issue in issues[:12]:
            lines.append(
                f" [{issue.get('severity', 'degraded')}/{issue.get('capability', 'unknown')}] "
                f"{issue.get('message', '')}"
            )
        if len(issues) > 12:
            lines.append(f" 另有 {len(issues) - 12} 条数据边界见 data_quality.asset_completeness。")
        lines.append("")

    def _append_position_valuation_section(
        self,
        lines: list[str],
        *,
        accounts: list[Account],
        positions: list[Position],
        valuations: list[dict],
    ) -> None:
        if not valuations:
            return
        account_names = {account.account_id: account.display_name for account in accounts}
        positions_by_id = {position.position_id: position for position in positions}
        lines.append("【逐持仓估值】")
        for item in valuations:
            position = positions_by_id.get(item["position_id"])
            account = account_names.get(item.get("account_id"), item.get("account_id"))
            value = (
                self._money(item.get("market_value"), item.get("currency", "CNY"))
                if item.get("market_value") is not None
                else "无法估值"
            )
            value_cny = (
                self._money(item.get("market_value_cny"), "CNY")
                if item.get("market_value_cny") is not None
                else "CNY换算失败"
            )
            weight = item.get("portfolio_weight")
            weight_text = f" | 组合权重 {weight * 100:.2f}%" if weight is not None else ""
            pnl = ""
            if item.get("pnl_pct") is not None:
                pnl = (
                    f" | 未实现盈亏 {self._money(item.get('unrealized_pnl'), item.get('currency', 'CNY'))}"
                    f" ({item['pnl_pct']:+.2f}%)"
                )
            elif position and position.valuation_input.method == "market_quote":
                pnl = " | 盈亏: 缺成本价不可计"
            price = ""
            if item.get("price") is not None:
                price = f" | 最新价 {item['price']:.4f}"
            flags = f" | flags: {', '.join(item['flags'])}" if item.get("flags") else ""
            lines.append(
                f" {item['display_name']} [{item['position_id']}] ({account})"
                f"{price} | 市值 {value} / {value_cny}{weight_text}{pnl}"
                f" | 粒度 {item.get('advice_granularity')}{flags}"
            )
            proxy = item.get("proxy")
            if proxy:
                signal_text = proxy.get("signal") or "no_signal"
                action_hint = proxy.get("action_hint") or "无"
                lines.append(
                    f"  代理参考: {proxy.get('tag')} -> {proxy.get('instrument_key')} "
                    f"| signal={signal_text} | hint={action_hint} "
                    "(代理只用于板块信号,不得直接当作该持仓价格触发器)"
                )
        lines.append("")

    def _append_exposure_section(self, lines: list[str], exposure_summary: dict) -> None:
        lines.append("【暴露集中度】")
        top = exposure_summary.get("top", [])
        if not top:
            lines.append(" 暴露标签不足或无可估值持仓。")
            lines.append("")
            return
        for item in top[:8]:
            ratio = item.get("ratio")
            ratio_text = f"{ratio * 100:.1f}%" if ratio is not None else "n/a"
            lines.append(
                f" {item.get('tag')}: {self._money(item.get('value_cny'), 'CNY')} "
                f"| 占净值 {ratio_text} | positions: {', '.join(item.get('positions', [])[:5])}"
            )
        lines.append("")

    def _append_liquidity_section(self, lines: list[str], liquidity_summary: dict) -> None:
        lines.append("【可动用资金】")
        buckets = liquidity_summary.get("buckets", {})
        if not buckets:
            lines.append(" 缺少流动性分层。")
            lines.append("")
            return
        labels = {
            "cash_or_t0": "立即可动用(cash/t0)",
            "t1_t2": "短期可交易(t1/t2+)",
            "locked_or_ineligible": "锁定或不参与调仓",
            "unknown": "未知流动性",
        }
        for key in ("cash_or_t0", "t1_t2", "locked_or_ineligible", "unknown"):
            bucket = buckets.get(key, {})
            lines.append(
                f" {labels[key]}: {self._money(bucket.get('value_cny', 0.0), 'CNY')} "
                f"| {len(bucket.get('positions', []))} 项"
            )
        lines.append(
            f" 可动用合计: {self._money(liquidity_summary.get('deployable_value_cny', 0.0), 'CNY')}"
        )
        lines.append("")

    @staticmethod
    def _append_granularity_section(lines: list[str], advice_granularity: dict) -> None:
        lines.append("【建议粒度】")
        counts = advice_granularity.get("counts", {})
        if not counts:
            lines.append(" 暂无可判定持仓粒度。")
            lines.append("")
            return
        parts = [f"{key}: {value}" for key, value in sorted(counts.items())]
        lines.append(" " + " | ".join(parts))
        sector_items = [
            item
            for item in advice_granularity.get("items", [])
            if item.get("granularity") == "sector"
        ]
        for item in sector_items[:8]:
            proxy = item.get("proxy") or {}
            if proxy:
                lines.append(
                    f" sector {item.get('position_id')} 代理 {proxy.get('instrument_key')} "
                    f"| signal={proxy.get('signal') or 'no_signal'}"
                )
            else:
                lines.append(f" sector {item.get('position_id')} 缺代理配置")
        lines.append("")

    def _append_review_actions(self, lines: list[str], recent_advice: list[dict]) -> None:
        lines.append("1. 上期建议 actions")
        if not recent_advice:
            lines.append(" - 缺失: 无已确认保存的上期建议。")
            return

        found = False
        for advice in recent_advice[:3]:
            actions = advice.get("actions", [])
            if not actions:
                continue
            found = True
            lines.append(
                f" - {advice.get('created_at', 'unknown')} | "
                f"摘要: {advice.get('rationale_summary', '')}"
            )
            for item in actions:
                detail = (
                    f"   - {item.get('target')} | {item.get('action')} | "
                    f"{item.get('size_hint')} | {item.get('horizon')}"
                )
                if item.get("trigger"):
                    detail += f" | trigger: {item.get('trigger')}"
                if item.get("invalidation"):
                    detail += f" | invalidation: {item.get('invalidation')}"
                lines.append(detail)
        if not found:
            lines.append(" - 缺失: 上期建议未保存结构化 actions。")

    def _append_review_triggers(self, lines: list[str], recent_advice: list[dict]) -> None:
        lines.append("2. 触发核对")
        if not recent_advice:
            lines.append(" - 缺失: 无上期建议，无法核对触发器。")
            return

        found = False
        for advice in recent_advice[:3]:
            for item in advice.get("trigger_review", []):
                found = True
                status = item.get("status", "no_data")
                head = (
                    f" - {item.get('instrument')} {item.get('type')} "
                    f"{item.get('level')} → {status}"
                )
                observed = item.get("observed") or {}
                if status in ("fired", "not_fired") and observed:
                    head += (
                        f" | 期间最高 {observed.get('max_price')} / "
                        f"最低 {observed.get('min_price')} / "
                        f"最新 {observed.get('latest_price')}"
                    )
                    if observed.get("pct_change") is not None:
                        head += f" | 累计 {observed['pct_change']:+.2f}%"
                    if observed.get("pnl_pct") is not None:
                        head += f" | 当前浮盈 {observed['pnl_pct']:+.2f}%"
                    if observed.get("max_pnl_pct") is not None:
                        head += (
                            f" | 期间浮盈区间 "
                            f"{observed.get('min_pnl_pct'):+.2f}%~{observed.get('max_pnl_pct'):+.2f}%"
                        )
                elif status == "no_data":
                    head += f" ({item.get('reason', 'unknown')})"
                lines.append(head)
                if item.get("action"):
                    lines.append(f"   预设动作: {item['action']}")
        if not found:
            lines.append(" - 缺失: 上期建议没有 triggers，或尚无可核对历史。")

    def _append_review_executions(
        self,
        lines: list[str],
        recent_advice: list[dict],
    ) -> None:
        lines.append("3. 执行对照")
        if not recent_advice:
            lines.append(" - 缺失: 无上期建议，无法匹配执行记录。")
            return

        found = False
        for advice in recent_advice[:3]:
            for item in advice.get("execution_review", []):
                found = True
                line = (
                    f" - {item.get('target')} | 建议 {item.get('recommended_action')} "
                    f"→ {item.get('status')}"
                )
                execution = item.get("execution") or {}
                if execution.get("action"):
                    line += f" | 记录 {execution.get('action')}"
                    if execution.get("extent"):
                        line += f"/{execution.get('extent')}"
                if execution.get("note"):
                    line += f" | note: {execution.get('note')}"
                lines.append(line)
        if not found:
            lines.append(" - 缺失: 上期建议没有结构化 actions，无法按 advice_id + target 对照。")

    def _append_review_forecasts(self, lines: list[str], forecast_summary: dict) -> None:
        lines.append("4. 到期预测结算")
        if not forecast_summary:
            lines.append(" - 缺失: 未加载预测台账或暂无预测记录。")
            return

        lines.append(f" - open 条数: {forecast_summary.get('open_count', 0)}")
        sample_count = int(forecast_summary.get("sample_count") or 0)
        hit_count = int(forecast_summary.get("hit_count") or 0)
        hit_rate = forecast_summary.get("hit_rate")
        if hit_rate is None:
            lines.append(f" - 累计统计: 样本不足 (hit/miss 样本 {sample_count}/10)")
        else:
            lines.append(f" - 累计命中率: {hit_rate * 100:.1f}% ({hit_count}/{sample_count})")

        settlements = forecast_summary.get("recent_settlements", [])
        if not settlements:
            lines.append(" - 缺失: 暂无到期预测结算。")
            return
        for item in settlements[:5]:
            target = item.get("target") or "manual"
            line = (
                f" - {item.get('deadline', 'unknown')} | {target} | "
                f"{item.get('status', 'unknown')} | {item.get('statement', '')}"
            )
            if item.get("resolution_note"):
                line += f" | {item.get('resolution_note')}"
            lines.append(line)

    @staticmethod
    def _money(value: Optional[float], currency: str = "CNY") -> str:
        if value is None:
            return "N/A"
        return f"{float(value):,.2f} {currency}"

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

    @staticmethod
    def _quantity_text(quantity: Optional[float]) -> str:
        if quantity is None:
            return ""
        if float(quantity).is_integer():
            return str(int(quantity))
        return f"{quantity:g}"

    def _holding_text(self, asset: FinancialAsset) -> str:
        quantity = self._quantity_text(asset.quantity)
        return f"当前持有 {quantity}" if quantity else "当前持有"

    def _asset_mapping_text(self, asset: FinancialAsset) -> str:
        if not asset.instrument_key:
            return ""
        parts = [f"标的: {asset.instrument_key}", self._holding_text(asset)]
        if asset.tradable is not None:
            parts.append("可交易" if asset.tradable else "不可交易")
        return " | " + " | ".join(parts)
