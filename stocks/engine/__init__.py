"""stocks-claw Engine 主入口 — 编排所有核心模块

StocksEngine 是 stocks-claw 的核心门面类，负责：
1. 配置加载（资产、约束、画像、关注列表）
2. Provider 注册与初始化
3. Engine 组件编排（fetcher、scaffolds、context_builder、persistence、llm）
4. 向 Adapters 暴露统一接口

使用方式：
    from stocks.engine import StocksEngine
    engine = StocksEngine()
    context = await engine.build_context()
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from stocks.domain.models import (
    AnalysisContext,
    FinancialAsset,
    Instrument,
    NewsItem,
    PortfolioMapping,
    Quote,
)
from stocks.engine.context_builder import ContextBuilder
from stocks.engine.fetchers import DataFetcher
from stocks.engine.llm_analysis import LLMAnalysis
from stocks.engine.llm_enhancer import LLMEnhancer
from stocks.engine.llm_utils import validate_llm_models
from stocks.engine.persistence import DataPersistence
from stocks.engine.scaffolds import MarketScaffold, PortfolioScaffold
from stocks.providers.eastmoney_a import EastmoneyAQuoteProvider
from stocks.providers.finnhub_quote import FinnhubQuoteProvider
from stocks.providers.registry import ProviderRegistry
from stocks.providers.tencent_a import TencentAQuoteProvider

# 默认配置路径
DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SECRET_DIR = Path(__file__).resolve().parents[2] / ".secret"


class StocksEngine:
    """stocks-claw 核心引擎 — 数据获取、分析上下文构建、LLM 增强的统一入口。"""

    def __init__(
        self,
        config_dir: Optional[str] = None,
        data_dir: Optional[str] = None,
        llm_enhancer_enabled: bool = True,
        llm_analysis_enabled: bool = True,
        openai_api_key: Optional[str] = None,
        openai_base_url: Optional[str] = None,
    ):
        """初始化 Engine。

        Args:
            config_dir: 配置文件目录，默认 ``stocks/config/``。
            data_dir: 数据文件目录，默认 ``stocks/data/``。
            llm_enhancer_enabled: 是否启用 LLM 数据增强（默认 True，无 key 时自动降级禁用）。
            llm_analysis_enabled: 是否启用 LLM 深度分析（默认 True，无 key 时自动降级禁用）。
            openai_api_key: OpenAI 兼容 API Key（传参优先，其次环境变量，其次.secret文件）。
            openai_base_url: OpenAI 兼容 API Base URL（传参优先，其次环境变量，其次.secret文件）。
        """
        self.config_dir = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR
        self.data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR

        # 自动加载 LLM 配置（传参 > 环境变量 > .secret 文件）
        api_key, base_url = self._load_openai_config(openai_api_key, openai_base_url)

        # 1. 初始化 Provider Registry
        self.registry = ProviderRegistry()
        self.registry.register(TencentAQuoteProvider())
        self.registry.register(EastmoneyAQuoteProvider())
        self.registry.register(FinnhubQuoteProvider())

        # 2. 初始化 Engine 组件
        self.fetcher = DataFetcher(self.registry)
        self.portfolio_scaffold = PortfolioScaffold()
        self.market_scaffold = MarketScaffold()
        self.context_builder = ContextBuilder(
            fetcher=self.fetcher,
            portfolio_scaffold=self.portfolio_scaffold,
            market_scaffold=self.market_scaffold,
        )
        self.persistence = DataPersistence()

        # 3. 初始化 LLM 模块（默认启用，如有配置则自动启用）
        self.llm_enhancer = LLMEnhancer(
            enabled=llm_enhancer_enabled and bool(api_key),
            api_key=api_key,
            base_url=base_url,
        )
        self.llm_analysis = LLMAnalysis(
            enabled=llm_analysis_enabled and bool(api_key),
            api_key=api_key,
            base_url=base_url,
        )

        # 3.5 校验模型可用性（代理不可达时保留原配置，模型不存在时自动降级）
        if api_key and base_url:
            resolved_e, resolved_a, e_available, a_available = validate_llm_models(
                enhancer_model=self.llm_enhancer.model,
                analysis_model=self.llm_analysis.model,
                api_key=api_key,
                base_url=base_url,
            )
            self.llm_enhancer.model = resolved_e
            self.llm_analysis.model = resolved_a
            if not e_available:
                self.llm_enhancer.enabled = False
            if not a_available:
                self.llm_analysis.enabled = False

        # 4. 加载配置
        self._assets: list[FinancialAsset] = []
        self._constraints: dict = {}
        self._profile: dict = {}
        self._watchlist: list[Instrument] = []
        self._load_configs()

    # ------------------------------------------------------------------
    # 配置加载
    # ------------------------------------------------------------------

    def _load_configs(self) -> None:
        """加载所有配置文件。"""
        self._assets = self._load_assets_from_file()
        self._constraints = self._load_json("portfolio_constraints.json") or {}
        self._profile = self._load_json("investor_profile.json") or {}
        self._watchlist = self._load_watchlist()

    def _load_openai_config(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> tuple[Optional[str], Optional[str]]:
        """加载 OpenAI 兼容 LLM 配置。

        优先级：传参 > 环境变量 > .secret 文件。
        返回 (api_key, base_url)。
        """
        # API Key
        key = api_key
        if not key:
            env_key = os.environ.get("OPENAI_API_KEY", "").strip()
            if env_key:
                key = env_key
            else:
                key_file = SECRET_DIR / "openai-key.md"
                if key_file.exists():
                    key = key_file.read_text(encoding="utf-8").strip()

        # Base URL
        url = base_url
        if not url:
            env_url = os.environ.get("OPENAI_BASE_URL", "").strip()
            if env_url:
                url = env_url
            else:
                url_file = SECRET_DIR / "openai-base-url.md"
                if url_file.exists():
                    url = url_file.read_text(encoding="utf-8").strip()

        return (key or None, url or None)

    def _load_json(self, filename: str) -> Optional[dict]:
        """从配置目录加载 JSON 文件。"""
        path = self.config_dir / filename
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def _load_assets_from_file(self) -> list[FinancialAsset]:
        """从 ``financial_assets.json`` 加载资产列表。

        支持两种格式：
        1. 旧版嵌套格式：{"assets": [...], "portfolio_constraints": ...}
        2. 新版扁平格式：[...]
        """
        path = self.data_dir / "financial_assets.json"
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

        # 处理嵌套格式
        if isinstance(data, dict) and "assets" in data:
            raw_assets = data["assets"]
            # 同时提取约束和画像（如果存在）
            if "portfolio_constraints" in data and not self._constraints:
                self._constraints = data["portfolio_constraints"]
            if "portfolio_profile_notes" in data and not self._profile:
                self._profile = data["portfolio_profile_notes"]
        elif isinstance(data, list):
            raw_assets = data
        else:
            return []

        assets: list[FinancialAsset] = []
        for item in raw_assets:
            if not isinstance(item, dict):
                continue
            try:
                # 兼容旧版字段名
                name = item.get("asset_name") or item.get("name", "未知")
                platform = item.get("platform", "未知")
                amount = float(item.get("amount", 0))
                asset_type = item.get("asset_type") or item.get("type", "unknown")
                notes = item.get("notes")
                confirmed = item.get("confirmed_by_user", item.get("confirmed", True))
                assets.append(
                    FinancialAsset(
                        name=name,
                        platform=platform,
                        amount=amount,
                        asset_type=asset_type,
                        notes=notes,
                        confirmed=bool(confirmed),
                    )
                )
            except (ValueError, TypeError):
                continue
        return assets

    def _load_watchlist(self) -> list[Instrument]:
        """从 ``watchlist.json`` 加载关注列表。

        支持两种格式：
        1. 旧版嵌套格式：{"markets": {"a": {"watchlist": [...]}}}
        2. 新版扁平格式：{"instruments": [...]}
        """
        data = self._load_json("watchlist.json")
        if not data:
            return []

        raw_instruments: list[dict] = []

        # 尝试嵌套格式
        if isinstance(data, dict) and "markets" in data:
            for market_key, market_data in data["markets"].items():
                if isinstance(market_data, dict) and "watchlist" in market_data:
                    for item in market_data["watchlist"]:
                        if isinstance(item, dict):
                            # 补充 market 字段（如果缺失）
                            if "market" not in item:
                                item = dict(item)
                                item["market"] = market_key
                            raw_instruments.append(item)
        # 尝试扁平格式
        elif isinstance(data, dict) and "instruments" in data:
            raw_instruments = data["instruments"]
        elif isinstance(data, list):
            raw_instruments = data

        instruments: list[Instrument] = []
        for item in raw_instruments:
            if not isinstance(item, dict):
                continue
            try:
                code = item.get("code", "")
                name = item.get("name", "")
                market = item.get("market", "a")
                # 标准化 market 字段
                if market in ("sh", "sz", "sh_index", "sz_index"):
                    market = "a"
                elif market == "us":
                    market = "us"
                exchange = item.get("exchange")
                if not exchange and market == "a":
                    exchange = item.get("market")  # 保留原始交易所信息

                instruments.append(
                    Instrument(
                        code=code,
                        name=name,
                        market=market,
                        exchange=exchange,
                    )
                )
            except (ValueError, TypeError):
                continue
        return instruments

    # ------------------------------------------------------------------
    # 对外接口 — Adapters 调用
    # ------------------------------------------------------------------

    def load_assets(self) -> list[FinancialAsset]:
        """加载并返回用户资产列表。"""
        return list(self._assets)

    def analyze_portfolio(self, assets: list[FinancialAsset]) -> PortfolioMapping:
        """分析投资组合结构。"""
        return self.portfolio_scaffold.build(assets, self._constraints)

    def detect_drift(
        self,
        mapping: PortfolioMapping,
        constraints: Optional[dict] = None,
    ) -> list:
        """检查组合约束偏离。"""
        return self.portfolio_scaffold.check_drift(
            mapping, constraints or self._constraints
        )

    async def fetch_quotes(
        self,
        market: Optional[str] = None,
    ) -> dict[str, list[Quote]]:
        """获取行情数据。

        Args:
            market: 指定市场（"a" / "us"），None 则获取全部。

        Returns:
            按市场分组的行情字典。
        """
        instruments = list(self._watchlist)
        if market:
            instruments = [i for i in instruments if i.market == market]
        if not instruments:
            return {}
        return await self.fetcher.fetch_quotes(instruments)

    async def fetch_news(
        self,
        sources: Optional[list[str]] = None,
        limit: int = 10,
    ) -> list[NewsItem]:
        """获取新闻数据（当前返回空列表，待新闻 Provider 实现）。"""
        # TODO: 接入新闻 Provider 后实现
        return []

    async def enhance_news(self, news: list[NewsItem]) -> list[NewsItem]:
        """使用 LLM 增强新闻数据。"""
        return await self.llm_enhancer.enhance_news(news)

    async def generate_report(self, context: AnalysisContext) -> str:
        """使用 LLM 生成投资分析报告。"""
        return await self.llm_analysis.generate_report(context)

    async def build_context(
        self,
        include_news: bool = True,
        include_quotes: bool = True,
        include_history: bool = True,
    ) -> AnalysisContext:
        """构建完整分析上下文 — 核心接口。

        这是 stocks-claw 向 Agent 提供的"完整分析原料包"。
        """
        # 1. 确定要获取行情的标的
        instruments = self._watchlist if include_quotes else []

        # 2. 获取新闻（或空列表）
        news: list[NewsItem] = []
        if include_news:
            news = await self.fetch_news()

        # 3. 如启用 LLM 增强，增强新闻
        enhanced_news: list[NewsItem] = list(news)
        if self.llm_enhancer.enabled and news:
            enhanced_news = await self.llm_enhancer.enhance_news(news)

        # 4. 加载历史快照
        recent_snapshots: list[dict] = []
        if include_history:
            recent_snapshots = self.persistence.load_recent(count=5)

        # 5. 构建 AnalysisContext
        context = await self.context_builder.build(
            assets=self._assets,
            constraints=self._constraints,
            profile=self._profile,
            instruments=instruments,
            recent_snapshots=recent_snapshots,
            llm_enhancer_enabled=self.llm_enhancer.enabled,
            llm_enhancer_model=self.llm_enhancer.model if self.llm_enhancer.enabled else "",
            enhanced_news=enhanced_news if self.llm_enhancer.enabled else None,
        )

        # 6. 如启用 LLM 增强，生成行情摘要
        if self.llm_enhancer.enabled and context.quotes:
            market_summary = await self.llm_enhancer.generate_market_summary(
                context.quotes,
                context.market_state.to_dict(),
            )
            # 由于 AnalysisContext 是 frozen dataclass，需要重建
            if market_summary:
                context = self._replace_context_field(
                    context, "market_summary_nl", market_summary
                )

        return context

    def health_check(self) -> dict:
        """健康检查 — 返回各组件状态。"""
        return {
            "status": "ok",
            "providers": [p.name for p in self.registry.all()],
            "assets_loaded": len(self._assets),
            "watchlist_loaded": len(self._watchlist),
            "llm_enhancer_enabled": self.llm_enhancer.enabled,
            "llm_analysis_enabled": self.llm_analysis.enabled,
        }

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _replace_context_field(
        context: AnalysisContext, field: str, value
    ) -> AnalysisContext:
        """替换 AnalysisContext 的单个字段（frozen dataclass 辅助）。"""
        # 使用 dataclasses.replace 的等效实现
        from dataclasses import fields

        kwargs = {}
        for f in fields(context):
            kwargs[f.name] = getattr(context, f.name)
        kwargs[field] = value
        return AnalysisContext(**kwargs)
