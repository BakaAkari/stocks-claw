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
import logging
import os
import re
from dataclasses import replace
from datetime import datetime, timezone
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
from stocks.engine.config_loader import load_engine_config
from stocks.engine.context_builder import ContextBuilder
from stocks.engine.exchange_rate import convert_to_cny
from stocks.engine.fetchers import DataFetcher
from stocks.engine.history_cache import HistoryCache
from stocks.engine.history_provider import CompositeKLineProvider, warm_history_cache
from stocks.engine.llm_analysis import LLMAnalysis
from stocks.engine.macro_data import (
    CompositeMacroProvider,
    StaticMacroProvider,
    YahooFinanceMacroProvider,
)
from stocks.engine.news_sources import NewsAggregator
from stocks.engine.persistence import DataPersistence
from stocks.engine.scaffolds import MarketScaffold, PortfolioScaffold
from stocks.providers.eastmoney_a import EastmoneyAQuoteProvider
from stocks.providers.finnhub_quote import FinnhubQuoteProvider
from stocks.providers.registry import ProviderRegistry
from stocks.providers.rss_news import RSSNewsProvider
from stocks.providers.tencent_a import TencentAQuoteProvider

logger = logging.getLogger(__name__)

# 默认配置路径
DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
LOCAL_DATA_DIR = Path(__file__).resolve().parents[2] / ".local"
SECRET_DIR = Path(__file__).resolve().parents[2] / ".secret"
_LEGACY_CONVERSION_NOTE = re.compile(
    r"(?:^|\s*\|\s*)原始:\s*(?P<amount>\d+(?:\.\d+)?)\s+"
    r"(?P<currency>[A-Za-z]{3})\s*\(汇率\s*\d+(?:\.\d+)?\)\s*$"
)


class StocksEngine:
    """stocks-claw 核心引擎 — 数据获取与分析上下文构建入口。"""

    def __init__(
        self,
        config_dir: Optional[str] = None,
        data_dir: Optional[str] = None,
        llm_analysis_enabled: Optional[bool] = None,
        openai_api_key: Optional[str] = None,
        openai_base_url: Optional[str] = None,
    ):
        """初始化 Engine。

        Args:
            config_dir: 配置文件目录，默认 ``stocks/config/``。
            data_dir: 数据文件目录，默认 ``stocks/data/``。
            llm_analysis_enabled: 是否启用 LLM 深度分析（传参优先于配置）。
            openai_api_key: OpenAI 兼容 API Key（传参优先，其次环境变量，其次.secret文件）。
            openai_base_url: OpenAI 兼容 API Base URL（传参优先，其次环境变量，其次.secret文件）。
        """
        # 加载 engine.yaml 配置（环境变量 > YAML > 代码默认值）
        self._config = load_engine_config()

        # 路径配置：传参 > YAML > 代码默认值
        self.config_dir = (
            Path(config_dir) if config_dir
            else Path(self._config["paths"]["config_dir"]) if self._config["paths"]["config_dir"]
            else DEFAULT_CONFIG_DIR
        )
        self.data_dir = (
            Path(data_dir) if data_dir
            else Path(self._config["paths"]["data_dir"]) if self._config["paths"]["data_dir"]
            else DEFAULT_DATA_DIR
        )
        # 允许 YAML 覆盖 local/secret 路径
        local_dir_cfg = self._config["paths"]["local_data_dir"]
        self._local_data_dir = Path(local_dir_cfg) if local_dir_cfg else LOCAL_DATA_DIR
        secret_dir_cfg = self._config["paths"]["secret_dir"]
        self._secret_dir = Path(secret_dir_cfg) if secret_dir_cfg else SECRET_DIR

        # 自动加载 LLM 配置（传参 > 环境变量 > .secret 文件）
        api_key, base_url = self._load_openai_config(openai_api_key, openai_base_url)

        # 1. 初始化 Provider Registry（根据配置启用/禁用）
        self.registry = ProviderRegistry()
        prov_cfg = self._config["providers"]
        if prov_cfg.get("tencent_a", {}).get("enabled", True):
            self.registry.register(TencentAQuoteProvider())
        if prov_cfg.get("eastmoney_a", {}).get("enabled", True):
            self.registry.register(EastmoneyAQuoteProvider())
        if prov_cfg.get("finnhub", {}).get("enabled", True):
            self.registry.register(FinnhubQuoteProvider())

        # 2. 初始化 Engine 组件（使用配置参数）
        fetcher_cfg = self._config["fetcher"]
        self.fetcher = DataFetcher(
            self.registry,
            max_retries=fetcher_cfg.get("max_retries", 1),
            retry_delay=fetcher_cfg.get("retry_delay", 1.0),
        )
        self.portfolio_scaffold = PortfolioScaffold()
        self.market_scaffold = MarketScaffold()

        # 2.1 初始化历史缓存（用于技术指标计算）
        cache_cfg = self._config.get("cache", {})
        self.history_cache = None
        if cache_cfg.get("enabled", True):
            history_dir_cfg = cache_cfg.get("history_dir")
            history_dir = Path(history_dir_cfg) if history_dir_cfg else self._local_data_dir / "history"
            history_dir.mkdir(parents=True, exist_ok=True)
            self.history_cache = HistoryCache(
                base_dir=str(history_dir),
                ttl=cache_cfg.get("history_ttl", 86400),
            )

        # 2.2 初始化新闻聚合器
        news_cfg = self._config.get("news_sources", {})
        rss_urls = news_cfg.get("rss", ["https://www.36kr.com/feed"])
        news_providers = [RSSNewsProvider(url) for url in rss_urls]
        self.news_aggregator = NewsAggregator(
            providers=news_providers,
            max_source_items=news_cfg.get("max_source_items", 20),
        )

        # 2.3 初始化宏观数据提供者
        macro_cfg = self._config.get("macro", {})
        self.macro_provider = None
        if macro_cfg.get("enabled", True):
            static_config = macro_cfg.get("static_config", {})
            providers = []
            if static_config:
                providers.append(StaticMacroProvider(static_config))
            providers.append(YahooFinanceMacroProvider())
            self.macro_provider = CompositeMacroProvider(providers)

        self.context_builder = ContextBuilder(
            fetcher=self.fetcher,
            portfolio_scaffold=self.portfolio_scaffold,
            market_scaffold=self.market_scaffold,
            history_cache=self.history_cache,
            macro_provider=self.macro_provider,
        )
        self.persistence = DataPersistence(
            base_dir=str(self._local_data_dir / "snapshots"),
            enabled=cache_cfg.get("save_to_file", True),
            max_snapshots=cache_cfg.get("max_snapshots", 30),
        )

        # 2.4 初始化历史 K 线提供者（用于启动时回填历史数据）
        self._history_provider = CompositeKLineProvider()
        self._history_warmed = False

        # 3. 初始化可选的兼容分析模块（主分析仍由外部 Agent 完成）
        llm_cfg = self._config["llm"]
        analysis_on = llm_analysis_enabled if llm_analysis_enabled is not None else llm_cfg.get("analysis_enabled", False)
        self.llm_analysis = LLMAnalysis(
            model=llm_cfg.get("analysis_model", "kimi-k2.6"),
            enabled=analysis_on and bool(api_key),
            api_key=api_key,
            base_url=base_url,
        )

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
        self._profile = self._load_profile()
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
                key_file = self._secret_dir / "openai-key.md"
                if key_file.exists():
                    key = key_file.read_text(encoding="utf-8").strip()

        # Base URL
        url = base_url
        if not url:
            env_url = os.environ.get("OPENAI_BASE_URL", "").strip()
            if env_url:
                url = env_url
            else:
                url_file = self._secret_dir / "openai-base-url.md"
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

        加载优先级：
        1. ``.local/financial_assets.json`` — 本地隐私数据（不提交 git）
        2. ``stocks/data/financial_assets.json`` — 项目默认数据

        数据格式：扁平数组 [{"name": ..., "platform": ..., ...}, ...]
        """
        # 优先加载本地隐私数据
        local_path = self._local_data_dir / "financial_assets.json"
        path = local_path if local_path.exists() else (self.data_dir / "financial_assets.json")

        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

        if not isinstance(data, list):
            return []

        assets: list[FinancialAsset] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                name = item.get("name", "未知")
                platform = item.get("platform", "未知")
                amount = float(item.get("amount", 0))
                asset_type = item.get("asset_type", "unknown")
                notes = item.get("notes")
                confirmed = item.get("confirmed", True)
                currency = item.get("currency", "CNY")
                amount, currency, notes = self._recover_legacy_currency(
                    amount,
                    currency,
                    notes,
                )

                assets.append(
                    self._with_cny_valuation(FinancialAsset(
                        name=name,
                        platform=platform,
                        amount=amount,
                        asset_type=asset_type,
                        notes=notes,
                        confirmed=bool(confirmed),
                        currency=(currency or "CNY").upper(),
                    ))
                )
            except (ValueError, TypeError):
                continue
        return assets

    def _load_profile(self) -> dict:
        """加载本地投资者画像；兼容旧配置目录但不加载 example。"""
        local_path = self._local_data_dir / "investor_profile.json"
        if local_path.exists():
            try:
                data = json.loads(local_path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except (json.JSONDecodeError, OSError):
                return {}
        legacy = self._load_json("investor_profile.json")
        return legacy if isinstance(legacy, dict) else {}

    @staticmethod
    def _recover_legacy_currency(
        amount: float,
        currency: str,
        notes: Optional[str],
    ) -> tuple[float, str, Optional[str]]:
        """恢复旧版曾写入 notes 的原始外币值，兼容已经腐蚀的数据文件。"""
        normalized = (currency or "CNY").upper()
        if normalized != "CNY" or not notes:
            return amount, normalized, notes
        match = _LEGACY_CONVERSION_NOTE.search(notes)
        if not match:
            return amount, normalized, notes
        original_amount = float(match.group("amount"))
        original_currency = match.group("currency").upper()
        cleaned_notes = notes[:match.start()].rstrip(" |") or None
        return original_amount, original_currency, cleaned_notes

    def _load_watchlist(self) -> list[Instrument]:
        """从 ``watchlist.json`` 加载关注列表。

        数据格式：扁平数组 [{"code": ..., "name": ..., "market": ..., "exchange": ...}, ...]
        """
        data = self._load_json("watchlist.json")
        if not data:
            return []

        if not isinstance(data, list):
            return []

        instruments: list[Instrument] = []
        for item in data:
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
                category = item.get("category")
                if not exchange and market == "a":
                    exchange = item.get("market")  # 保留原始交易所信息

                instruments.append(
                    Instrument(
                        code=code,
                        name=name,
                        market=market,
                        exchange=exchange,
                        category=category,
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

    def get_profile(self) -> dict:
        """返回投资者偏好记忆副本。"""
        return dict(self._profile)

    def update_profile(self, updates: dict) -> dict:
        """校验、合并并持久化投资者偏好记忆。"""
        if not isinstance(updates, dict):
            raise ValueError("Profile update must be an object")
        allowed = {
            "risk_tolerance",
            "investment_horizon",
            "preferences",
            "constraints",
        }
        unknown = set(updates) - allowed
        if unknown:
            raise ValueError(f"Unsupported profile fields: {sorted(unknown)}")
        if "risk_tolerance" in updates and not isinstance(updates["risk_tolerance"], str):
            raise ValueError("risk_tolerance must be a string")
        if "investment_horizon" in updates and not isinstance(
            updates["investment_horizon"], str
        ):
            raise ValueError("investment_horizon must be a string")
        if "preferences" in updates and not (
            isinstance(updates["preferences"], list)
            and all(isinstance(item, str) for item in updates["preferences"])
        ):
            raise ValueError("preferences must be a string array")
        if "constraints" in updates and not isinstance(
            updates["constraints"], (list, dict)
        ):
            raise ValueError("constraints must be an array or object")

        self._profile.update(updates)
        self._profile["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._local_data_dir.mkdir(parents=True, exist_ok=True)
        path = self._local_data_dir / "investor_profile.json"
        path.write_text(
            json.dumps(self._profile, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return dict(self._profile)

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
        """获取新闻数据 — 使用 NewsAggregator 多源聚合。"""
        try:
            return await self.news_aggregator.fetch(max_items=limit)
        except Exception as e:
            logger.warning(f"News fetch failed: {e}")
            return []

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
        # 0. 确定要获取行情的标的
        instruments = self._watchlist if include_quotes else []

        # 1. Warm history cache if not yet warmed (fetch historical klines for technical indicators)
        if not self._history_warmed and self.history_cache and instruments:
            try:
                logger.info(f"Warming history cache for {len(instruments)} instruments...")
                await warm_history_cache(
                    self.history_cache,
                    self._history_provider,
                    instruments,
                    lookback_days=60,
                )
                self._history_warmed = True
                logger.info("History cache warm complete")
            except Exception as e:
                logger.warning(f"History cache warm failed: {e}")

        # 2. 获取新闻（或空列表）
        news: list[NewsItem] = []
        if include_news:
            news = await self.fetch_news()

        # 3. 加载历史快照
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
            news=news,
            news_requested=include_news,
        )

        # 保存最小快照，供下一次上下文进行前后对照。
        self.persistence.save_context(context)

        # 6. Flush history cache if present (ensure today's data persisted)
        if self.history_cache:
            try:
                await self.history_cache.flush()
            except Exception as e:
                logger.warning(f"History cache flush failed: {e}")

        return context

    def health_check(self) -> dict:
        """健康检查 — 返回各组件状态。"""
        return {
            "status": "ok",
            "providers": [p.name for p in self.registry.all()],
            "assets_loaded": len(self._assets),
            "watchlist_loaded": len(self._watchlist),
            "history_cache_enabled": self.history_cache is not None,
            "news_providers": len(self.news_aggregator._providers) if self.news_aggregator else 0,
            "macro_provider_enabled": self.macro_provider is not None,
            "llm_analysis_enabled": self.llm_analysis.enabled,
        }

    # ------------------------------------------------------------------
    # 资产 CRUD 接口
    # ------------------------------------------------------------------

    def add_asset(self, asset: FinancialAsset) -> None:
        """添加资产并持久化到本地文件。"""
        self._assets.append(self._with_cny_valuation(asset))
        self._save_assets()

    def remove_asset(self, name: str) -> bool:
        """按名称移除资产并持久化。"""
        original_len = len(self._assets)
        self._assets = [a for a in self._assets if a.name != name]
        if len(self._assets) < original_len:
            self._save_assets()
            return True
        return False

    def update_asset(self, name: str, **kwargs) -> bool:
        """按名称更新资产字段并持久化。"""
        for i, asset in enumerate(self._assets):
            if asset.name == name:
                # 由于 dataclass 是 frozen，需要重建
                current = asset.to_storage_dict()
                current.update(kwargs)
                current.pop("amount_cny", None)
                self._assets[i] = self._with_cny_valuation(FinancialAsset(**current))
                self._save_assets()
                return True
        return False

    def _save_assets(self) -> None:
        """将当前资产列表保存到本地隐私文件。"""
        local_path = self._local_data_dir / "financial_assets.json"
        self._local_data_dir.mkdir(parents=True, exist_ok=True)
        with open(local_path, "w", encoding="utf-8") as f:
            json.dump(
                [a.to_storage_dict() for a in self._assets],
                f,
                ensure_ascii=False,
                indent=2,
            )

    @staticmethod
    def _with_cny_valuation(asset: FinancialAsset) -> FinancialAsset:
        """保留原始金额/币种，并补充只在内存中使用的 CNY 估值。"""
        currency = (asset.currency or "CNY").upper()
        if currency == "CNY":
            return replace(
                asset,
                currency=currency,
                amount_cny=asset.amount,
                conversion_status="ok",
                conversion_source="identity",
                conversion_rate=1.0,
            )
        conversion = convert_to_cny(asset.amount, currency)
        return replace(
            asset,
            currency=currency,
            amount_cny=conversion.amount_cny,
            conversion_status=conversion.status,
            conversion_source=conversion.source,
            conversion_rate=conversion.rate,
        )

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
