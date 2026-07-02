"""AnalysisContext 组装器 — 编排数据获取与脚手架计算，生成统一分析上下文"""

from dataclasses import replace
from datetime import datetime
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
    ):
        self.fetcher = fetcher
        self.portfolio_scaffold = portfolio_scaffold
        self.market_scaffold = market_scaffold
        self.history_cache = history_cache
        self.macro_provider = macro_provider

    async def build(
        self,
        assets: list[FinancialAsset],
        constraints: dict,
        profile: dict,
        instruments: list,  # 要获取行情的标的列表
        recent_snapshots: list[dict],
        llm_enhancer_enabled: bool = False,
        llm_enhancer_model: str = "",
        market_summary_nl: str = "",
        enhanced_news: list = None,
    ) -> AnalysisContext:
        """构建完整分析上下文"""
        # 1. 获取行情
        quotes: dict[str, list[Quote]] = {}
        if instruments:
            quotes = await self.fetcher.fetch_quotes(instruments)

        # 2. 记录行情到历史缓存，并计算技术指标
        if self.history_cache and quotes:
            quotes = await self._enrich_with_indicators(quotes)
        technical_indicators = self._collect_technical_indicators(quotes)

        # 3. 获取宏观数据
        macro_snapshot = None
        if self.macro_provider:
            try:
                macro_snapshot = await self.macro_provider.fetch()
            except Exception as e:
                logger = get_logger("context_builder")
                logger.warning(f"Macro data fetch failed: {e}")

        # 4. 获取新闻（或接收已增强的新闻）
        news: list[NewsItem] = enhanced_news if enhanced_news is not None else []
        if not news:
            # 预留：后续可通过 fetcher 获取新闻
            news = []

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
        )

        # 9. 组装 AnalysisContext
        return AnalysisContext(
            generated_at=datetime.now().isoformat(),
            schema_version=3,
            assets=assets,
            asset_count=len(assets),
            portfolio_constraints=constraints,
            portfolio_profile=profile,
            quotes=quotes,
            news=news,
            news_count=len(news),
            market_summary_nl=market_summary_nl,
            enhanced_news_count=len(enhanced_news) if enhanced_news else 0,
            market_state=market_state,
            portfolio_mapping=mapping,
            drift_checks=drift_checks,
            recent_snapshots=recent_snapshots,
            raw_prompt_input=raw_prompt,
            macro_snapshot=macro_snapshot.to_dict() if macro_snapshot else None,
            technical_indicators=technical_indicators,
            llm_enhancer_enabled=llm_enhancer_enabled,
            llm_enhancer_model=llm_enhancer_model,
        )

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
        total = sum(a.amount for a in assets) if assets else 0
        lines.append(f" 总资产: {total:,.2f}")
        lines.append(f" 资产数量: {len(assets)}")
        for asset in assets:
            pct = (asset.amount / total * 100) if total > 0 else 0
            status = "" if asset.confirmed else "?"
            lines.append(
                f" {status} {asset.name} ({asset.platform}) | "
                f"类型: {asset.asset_type} | 金额: {asset.amount:,.2f} ({pct:.1f}%)"
            )
            if asset.notes:
                lines.append(f" 备注: {asset.notes}")
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
        if market_state.cross_asset_summary:
            lines.append(" 跨资产摘要:")
            for s in market_state.cross_asset_summary:
                lines.append(f" - {s}")
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
