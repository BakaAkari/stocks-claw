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
import shutil
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from stocks.domain.models import (
    Account,
    AdviceRecord,
    AnalysisContext,
    ExecutionRecord,
    FinancialAsset,
    ForecastRecord,
    Instrument,
    NewsItem,
    PortfolioMapping,
    Position,
    Quote,
    account_from_v1_platform,
    financial_asset_to_position_v2,
    position_v2_to_financial_asset,
)
from stocks.engine.config_loader import load_engine_config
from stocks.engine.context_builder import ContextBuilder
from stocks.engine.event_calendar import (
    EventCalendar,
    FinnhubEarningsCalendarProvider,
    StaticEventCalendarProvider,
)
from stocks.engine.exchange_rate import convert_to_cny
from stocks.engine.fetchers import DataFetcher
from stocks.engine.forecasts import settle_due_forecasts, summarize_forecasts
from stocks.engine.history_cache import HistoryCache
from stocks.engine.history_provider import CompositeKLineProvider, warm_history_cache
from stocks.engine.llm_analysis import LLMAnalysis
from stocks.engine.macro_data import (
    CompositeMacroProvider,
    FredMacroProvider,
    StaticMacroProvider,
    YahooFinanceMacroProvider,
)
from stocks.engine.news_sources import NewsAggregator, WatchlistGoogleNewsProvider
from stocks.engine.persistence import DataPersistence
from stocks.engine.scaffolds import MarketScaffold, PortfolioScaffold
from stocks.engine.scheduled_analysis import (
    ScheduledAnalysisRunner,
    load_scheduled_config,
    parse_datetime,
    resolve_artifact_dir,
)
from stocks.logging_utils import setup_logging
from stocks.providers.binance_quote import BinanceQuoteProvider
from stocks.providers.eastmoney_a import EastmoneyAQuoteProvider
from stocks.providers.filings import CninfoFilingsProvider, SecEdgarFilingsProvider
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


class AdviceValidationError(ValueError):
    """结构化建议校验错误；adapters 会把 errors 原样返回给调用方。"""

    def __init__(self, errors: list[dict]):
        super().__init__("Advice validation failed")
        self.errors = errors


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
        logging_cfg = self._config.get("logging", {})
        setup_logging(
            level=logging_cfg.get("level", "INFO"),
            desensitize=logging_cfg.get("desensitize", True),
        )

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
        # 允许 YAML 覆盖 local/secret/intelligence 路径
        local_dir_cfg = self._config["paths"]["local_data_dir"]
        self._local_data_dir = Path(local_dir_cfg) if local_dir_cfg else LOCAL_DATA_DIR
        secret_dir_cfg = self._config["paths"]["secret_dir"]
        self._secret_dir = Path(secret_dir_cfg) if secret_dir_cfg else SECRET_DIR
        intelligence_dir_cfg = self._config["paths"].get("intelligence_dir")
        if intelligence_dir_cfg:
            self._config["intelligence_dir"] = intelligence_dir_cfg
        elif "intelligence_dir" not in self._config:
            self._config["intelligence_dir"] = str(self._local_data_dir / "news_intelligence")

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
        if prov_cfg.get("binance", {}).get("enabled", False):
            self.registry.register(BinanceQuoteProvider())

        # 2. 初始化 Engine 组件（使用配置参数）
        fetcher_cfg = self._config["fetcher"]
        self.fetcher = DataFetcher(
            self.registry,
            max_retries=fetcher_cfg.get("max_retries", 1),
            retry_delay=fetcher_cfg.get("retry_delay", 1.0),
            fallback_order=prov_cfg.get("fallback", {}),
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
        news_cfg = self._load_json("news_sources.json") or {}
        rss_sources = [
            source
            for source in news_cfg.get("sources", [])
            if source.get("type") == "rss" and source.get("enabled", True)
        ]
        news_providers = [
            RSSNewsProvider(
                source["url"],
                source_name=source.get("name", "RSS"),
                language=source.get("language", "unknown"),
            )
            for source in rss_sources
            if source.get("url")
        ]
        def instruments_getter() -> list[Instrument]:
            return list(getattr(self, "_watchlist", []))
        if self._config.get("news", {}).get(
            "watchlist_templates_enabled", False
        ) and any(
            template.get("type") == "google_news_rss"
            and template.get("enabled", True)
            for template in news_cfg.get("watchlist_templates", [])
        ):
            news_providers.append(WatchlistGoogleNewsProvider(instruments_getter))
        filings_cfg = self._config.get("filings", {})
        filing_symbols = self._load_json("filing_symbols.json") or {}
        if filings_cfg.get("enabled", False):
            if filings_cfg.get("sec", {}).get("enabled", True):
                news_providers.append(
                    SecEdgarFilingsProvider(
                        instruments_getter,
                        filing_symbols.get("sec_cik", {}),
                        user_agent=os.environ.get(
                            filing_symbols.get(
                                "sec_user_agent_env", "SEC_USER_AGENT"
                            ),
                            "",
                        ).strip(),
                    )
                )
            if filings_cfg.get("cninfo", {}).get("enabled", True):
                news_providers.append(
                    CninfoFilingsProvider(
                        instruments_getter,
                        filing_symbols.get("cninfo_org_id", {}),
                    )
                )
        self.news_aggregator = NewsAggregator(
            providers=news_providers,
            max_source_items=20,
        )

        # 2.3 初始化宏观数据提供者
        macro_cfg = self._config.get("macro", {})
        self.macro_provider = None
        if macro_cfg.get("enabled", True):
            static_config = macro_cfg.get("static_config", {})
            providers = [
                FredMacroProvider(cache_dir=self._local_data_dir / "macro_cache"),
                YahooFinanceMacroProvider(),
            ]
            if static_config:
                providers.append(StaticMacroProvider(static_config))
            self.macro_provider = CompositeMacroProvider(providers)

        # 2.35 初始化未来事件日历（官方日程 + 财报日历）
        calendar_cfg = self._config.get("calendar", {})
        self.event_calendar = None
        if calendar_cfg.get("enabled", True):
            calendar_providers = []
            static_events = self._load_json("event_calendar.json")
            if static_events:
                calendar_providers.append(StaticEventCalendarProvider(static_events))
            earnings_cfg = calendar_cfg.get("earnings", {})
            if earnings_cfg.get("enabled", True) and prov_cfg.get(
                "finnhub", {}
            ).get("enabled", True):
                finnhub_client = self.registry.get("finnhub")
                calendar_providers.append(
                    FinnhubEarningsCalendarProvider(
                        cache_dir=self._local_data_dir / "event_cache",
                        client=(
                            finnhub_client
                            if isinstance(finnhub_client, FinnhubQuoteProvider)
                            else None
                        ),
                    )
                )
            if calendar_providers:
                self.event_calendar = EventCalendar(
                    calendar_providers,
                    lookahead_days=calendar_cfg.get("lookahead_days", 14),
                )

        self.context_builder = ContextBuilder(
            fetcher=self.fetcher,
            portfolio_scaffold=self.portfolio_scaffold,
            market_scaffold=self.market_scaffold,
            history_cache=self.history_cache,
            macro_provider=self.macro_provider,
            event_calendar=self.event_calendar,
            config=self._config,
        )
        self.persistence = DataPersistence(
            base_dir=str(self._local_data_dir / "snapshots"),
            enabled=cache_cfg.get("save_to_file", True),
            max_snapshots=cache_cfg.get("max_snapshots", 30),
        )

        # 2.4 初始化历史 K 线提供者（用于启动时回填历史数据）
        self._history_provider = CompositeKLineProvider()
        self._history_warmed = False
        # D0-3:历史回填状态与冷却
        self._history_backfill_report: list[dict] = []
        self._history_warm_last_failed_at: datetime | None = None
        # 距上次失败 < 10 分钟内不重试,防止每次 build 打满上游
        self._history_warm_retry_cooldown = timedelta(minutes=10)

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
        self._asset_schema_version: int = 1
        self._asset_base_currency: str = "CNY"
        self._asset_accounts_v2: list[Account] = []
        self._asset_positions_v2: list[Position] = []
        self._asset_load_warning: Optional[str] = None
        self._constraints: dict = {}
        self._profile: dict = {}
        self._watchlist: list[Instrument] = []
        self._sector_scan: list[Instrument] = []
        self._exposure_proxy: dict = {}
        self._scheduled_runner: ScheduledAnalysisRunner | None = None
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
        self._sector_scan = self._load_sector_scan()
        self._exposure_proxy = self._load_exposure_proxy()

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
        llm_cfg = self._config.get("llm", {})
        key = api_key
        if not key:
            env_key = os.environ.get(
                llm_cfg.get("api_key_env", "OPENAI_API_KEY"),
                "",
            ).strip()
            if env_key:
                key = env_key
            else:
                key_file = self._secret_dir / "openai-key.md"
                if key_file.exists():
                    key = key_file.read_text(encoding="utf-8").strip()

        # Base URL
        url = base_url
        if not url:
            env_url = os.environ.get(
                llm_cfg.get("base_url_env", "OPENAI_BASE_URL"),
                "",
            ).strip()
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

    def _asset_file_path(self) -> Path:
        local_path = self._local_data_dir / "financial_assets.json"
        return local_path if local_path.exists() else (self.data_dir / "financial_assets.json")

    def _load_assets_from_file(self) -> list[FinancialAsset]:
        """从 ``financial_assets.json`` 加载资产列表，兼容 v1 list 与 v2 dict。"""
        self._asset_schema_version = 1
        self._asset_base_currency = "CNY"
        self._asset_accounts_v2 = []
        self._asset_positions_v2 = []
        self._asset_load_warning = None
        path = self._asset_file_path()

        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            self._asset_load_warning = "asset_file_unreadable"
            return []

        if isinstance(data, list):
            assets = self._load_assets_v1(data)
            self._asset_positions_v2 = [financial_asset_to_position_v2(asset) for asset in assets]
            accounts_by_id: dict[str, Account] = {}
            for asset in assets:
                account = account_from_v1_platform(asset.platform, asset.currency)
                accounts_by_id.setdefault(account.account_id, account)
            self._asset_accounts_v2 = list(accounts_by_id.values())
            self._asset_load_warning = "v1_format_migration_recommended"
            return assets

        if isinstance(data, dict):
            return self._load_assets_v2(data)

        self._asset_load_warning = "asset_file_invalid_top_level"
        return []

    def _load_assets_v1(self, data: list) -> list[FinancialAsset]:
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
                instrument_key = item.get("instrument_key")
                quantity = item.get("quantity")
                tradable = item.get("tradable")
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
                        instrument_key=instrument_key,
                        quantity=quantity,
                        tradable=tradable,
                    ))
                )
            except (ValueError, TypeError):
                continue
        return assets

    def _load_assets_v2(self, data: dict) -> list[FinancialAsset]:
        if data.get("schema_version") != 2:
            self._asset_load_warning = "asset_file_unsupported_schema"
            return []
        accounts = data.get("accounts")
        positions = data.get("positions")
        if not isinstance(accounts, list) or not isinstance(positions, list):
            self._asset_load_warning = "asset_file_invalid_v2_shape"
            return []
        try:
            self._asset_schema_version = 2
            self._asset_base_currency = (data.get("base_currency") or "CNY").upper()
            self._asset_accounts_v2 = [Account.from_dict(item) for item in accounts]
            self._asset_positions_v2 = [Position.from_dict(item) for item in positions]
        except (TypeError, ValueError, AttributeError):
            self._asset_load_warning = "asset_file_invalid_v2_record"
            self._asset_schema_version = 1
            self._asset_accounts_v2 = []
            self._asset_positions_v2 = []
            return []
        return [
            self._with_cny_valuation(position_v2_to_financial_asset(position))
            for position in self._asset_positions_v2
        ]

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

    def _load_exposure_proxy(self) -> dict:
        """加载 sector 粒度持仓的代理标的配置。"""
        data = self._load_json("exposure_proxy.json")
        if not isinstance(data, dict):
            return {}
        result = {}
        for tag, item in data.items():
            if not isinstance(tag, str) or not isinstance(item, dict):
                continue
            instrument_key = item.get("instrument_key")
            if not isinstance(instrument_key, str) or ":" not in instrument_key:
                continue
            result[tag.strip().lower()] = dict(item)
        return result

    def _load_sector_scan(self) -> list[Instrument]:
        """从 ``sector_scan.json`` 加载板块扫描池（轮动脚手架专用）。

        扫描池不进入用户 watchlist，不参与行情请求与 MarketState 判断，
        只通过历史 K 线缓存参与轮动排名；与 watchlist 重复的 key 被去重。
        """
        data = self._load_json("sector_scan.json")
        if not isinstance(data, list):
            return []
        watchlist_keys = {f"{i.market}:{i.code}" for i in self._watchlist}
        instruments: list[Instrument] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            code = item.get("code", "")
            market = item.get("market", "a")
            if market in ("sh", "sz", "sh_index", "sz_index"):
                market = "a"
            if not code or f"{market}:{code}" in watchlist_keys:
                continue
            instruments.append(
                Instrument(
                    code=code,
                    name=item.get("name", code),
                    market=market,
                    exchange=item.get("exchange"),
                    category=item.get("category"),
                    pool=item.get("pool", "sector"),
                )
            )
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

    def save_advice(self, payload: dict) -> dict:
        """保存用户确认过的建议摘要。"""
        if not isinstance(payload, dict):
            raise ValueError("Advice payload must be an object")
        allowed = {
            "instruments",
            "direction",
            "rationale_summary",
            "based_on",
            "boundary",
            "triggers",
            "actions",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"Unsupported advice fields: {sorted(unknown)}")
        try:
            record = AdviceRecord.create(
                instruments=payload.get("instruments", []),
                direction=payload.get("direction", {}),
                rationale_summary=payload.get("rationale_summary", ""),
                based_on=payload.get("based_on", []),
                boundary=payload.get("boundary", []),
                triggers=payload.get("triggers", []),
                actions=payload.get("actions", []),
            )
        except ValueError as exc:
            if "actions" in payload:
                raise AdviceValidationError([{
                    "field": "actions",
                    "message": str(exc),
                }]) from exc
            raise
        action_errors = self._validate_advice_action_targets(record.actions)
        trigger_errors = self._validate_advice_triggers(record.triggers)
        if action_errors or trigger_errors:
            raise AdviceValidationError(action_errors + trigger_errors)
        self.persistence.save_advice(record)
        return record.to_dict()

    def list_advice(self) -> list[dict]:
        """列出已确认保存的建议摘要。"""
        return self.persistence.list_advice()

    def save_execution(self, payload: dict) -> dict:
        """保存用户确认过的执行记录。"""
        if not isinstance(payload, dict):
            raise ValueError("Execution payload must be an object")
        allowed = {
            "id",
            "advice_id",
            "target",
            "action",
            "extent",
            "note",
            "executed_at",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"Unsupported execution fields: {sorted(unknown)}")
        record = ExecutionRecord.create(
            id=payload.get("id"),
            advice_id=payload.get("advice_id"),
            target=payload.get("target", ""),
            action=payload.get("action", ""),
            extent=payload.get("extent"),
            note=payload.get("note", ""),
            executed_at=payload.get("executed_at"),
        )
        self.persistence.save_execution(record)
        return record.to_dict()

    def list_executions(self) -> list[dict]:
        """列出已确认的执行记录。"""
        return self.persistence.list_executions()

    def save_forecast(self, payload: dict) -> dict:
        """保存用户确认过的预测记录。"""
        if not isinstance(payload, dict):
            raise ValueError("Forecast payload must be an object")
        allowed = {
            "id",
            "statement",
            "target",
            "metric",
            "comparator",
            "level",
            "deadline",
            "confidence",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"Unsupported forecast fields: {sorted(unknown)}")
        record = ForecastRecord.create(
            id=payload.get("id"),
            statement=payload.get("statement", ""),
            target=payload.get("target"),
            metric=payload.get("metric", "close"),
            comparator=payload.get("comparator", ""),
            level=payload.get("level"),
            deadline=payload.get("deadline", ""),
            confidence=payload.get("confidence", ""),
        )
        self.persistence.save_forecast(record)
        return record.to_dict()

    def list_forecasts(self) -> list[dict]:
        """列出已确认的预测记录。"""
        return self.persistence.list_forecasts()

    def _validate_advice_action_targets(self, actions: list[dict]) -> list[dict]:
        if not actions:
            return []
        valid_targets = {
            asset.instrument_key
            for asset in self._assets
            if asset.instrument_key
        }
        valid_targets.update(f"{item.market}:{item.code}" for item in self._watchlist)
        valid_targets.update(f"{item.market}:{item.code}" for item in self._sector_scan)
        valid_targets.update(str(bucket) for bucket in self._constraints)

        positions_by_key: dict[str, list[Position]] = {}
        positions_by_id: dict[str, Position] = {}
        for position in self._current_asset_positions():
            positions_by_id[position.position_id] = position
            valid_targets.add(position.position_id)
            if position.instrument_key:
                valid_targets.add(position.instrument_key)
                positions_by_key.setdefault(position.instrument_key, []).append(position)

        errors: list[dict] = []
        for index, action in enumerate(actions):
            target = action.get("target")
            if target not in valid_targets:
                errors.append({
                    "index": index,
                    "field": "target",
                    "target": target,
                    "message": (
                        "target must exist in mapped holdings, watchlist, "
                        "scan pool, or constraint buckets"
                    ),
                })
                continue
            mutable_action = action.get("action") in {"add", "increase", "reduce", "exit"}
            if not mutable_action:
                continue
            matched_positions: list[Position] = []
            if target in positions_by_id:
                matched_positions.append(positions_by_id[target])
            matched_positions.extend(positions_by_key.get(target, []))
            for position in matched_positions:
                if position.liquidity.rebalance_eligible is False:
                    errors.append({
                        "index": index,
                        "field": "target",
                        "target": target,
                        "message": (
                            f"{position.display_name} is marked rebalance_eligible=false; "
                            "mutable advice actions are not allowed"
                        ),
                    })
                    break
                if position.liquidity.tradable is False:
                    errors.append({
                        "index": index,
                        "field": "target",
                        "target": target,
                        "message": (
                            f"{position.display_name} is marked tradable=false; "
                            "mutable advice actions are not allowed"
                        ),
                    })
                    break
        return errors

    def _validate_advice_triggers(self, triggers: list[dict]) -> list[dict]:
        if not triggers:
            return []
        errors: list[dict] = []
        for index, trigger in enumerate(triggers):
            trigger_type = trigger.get("type")
            if trigger_type not in {"pnl_pct_above", "pnl_pct_below"}:
                continue
            key = trigger.get("instrument")
            matches = [
                position
                for position in self._current_asset_positions()
                if position.instrument_key == key
            ]
            if not matches:
                errors.append({
                    "index": index,
                    "field": "triggers",
                    "target": key,
                    "message": "pnl trigger requires a mapped detailed holding with cost_basis",
                })
                continue
            if not any(
                position.holding and position.holding.cost_basis
                for position in matches
            ):
                errors.append({
                    "index": index,
                    "field": "triggers",
                    "target": key,
                    "message": "pnl trigger requires cost_basis; fill position.holding.cost_basis first",
                })
        return errors

    def _current_asset_positions(self) -> list[Position]:
        if self._asset_schema_version == 2:
            return list(self._asset_positions_v2)
        return [financial_asset_to_position_v2(asset) for asset in self._assets]

    def _current_asset_accounts(self) -> list[Account]:
        if self._asset_schema_version == 2:
            return list(self._asset_accounts_v2)
        accounts_by_id: dict[str, Account] = {}
        for asset in self._assets:
            account = account_from_v1_platform(asset.platform, asset.currency)
            accounts_by_id.setdefault(account.account_id, account)
        return list(accounts_by_id.values())

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
            return await self.news_aggregator.fetch(max_items=limit, sources=sources)
        except Exception as e:
            logger.warning(f"News fetch failed: {e}")
            return []

    async def generate_report(self, context: AnalysisContext) -> str:
        """使用 LLM 生成投资分析报告。"""
        return await self.llm_analysis.generate_report(context)

    def _with_runtime_holding_instruments(
        self,
        instruments: list[Instrument],
    ) -> tuple[list[Instrument], list[str]]:
        """把 detailed 持仓临时加入本次行情/历史请求，不写 watchlist。"""
        result = list(instruments)
        existing = {f"{item.market}:{item.code}" for item in result}
        auto_included: list[str] = []
        for position in self._current_asset_positions():
            if (
                position.valuation_input.method != "market_quote"
                or not position.instrument_key
                or not position.holding
            ):
                continue
            key = position.instrument_key
            if key in existing:
                continue
            instrument = self._instrument_from_position(position)
            if instrument is None:
                continue
            result.append(instrument)
            existing.add(key)
            auto_included.append(key)
        return result, auto_included

    @staticmethod
    def _instrument_from_position(position: Position) -> Optional[Instrument]:
        if not position.instrument_key:
            return None
        instrument = position.instrument or {}
        return StocksEngine._instrument_from_key(
            position.instrument_key,
            name=position.display_name,
            exchange=instrument.get("exchange"),
            category=(
                position.classification.exposure_tags[0]
                if position.classification.exposure_tags
                else position.classification.asset_class
            ),
            pool="holding",
        )

    @staticmethod
    def _instrument_from_key(
        instrument_key: str,
        *,
        name: str,
        exchange: Optional[str] = None,
        category: Optional[str] = None,
        pool: Optional[str] = None,
    ) -> Optional[Instrument]:
        market, sep, code = instrument_key.partition(":")
        if not sep or not market or not code:
            return None
        if market not in {"a", "us", "crypto"}:
            return None
        return Instrument(
            code=code,
            name=name,
            market=market,
            exchange=exchange,
            category=category,
            pool=pool,
        )

    async def build_context(
        self,
        include_news: bool = True,
        include_quotes: bool = True,
        include_history: bool = True,
    ) -> AnalysisContext:
        """构建完整分析上下文 — 核心接口。

        这是 stocks-claw 向 Agent 提供的"完整分析原料包"。
        """
        # 0. 确定要获取行情的标的；扫描池只参与历史回填与轮动，不请求实时行情。
        # 轮动与动作信号基于历史缓存，--no-quotes 时仍可用（不触发新的历史回填）
        instruments = list(self._watchlist) if include_quotes else []
        scan_instruments = list(self._sector_scan)
        auto_included_holdings: list[str] = []
        if include_quotes:
            instruments, auto_included_holdings = self._with_runtime_holding_instruments(
                instruments,
            )

        # 1. Warm history cache if not yet warmed (D0-3:结构化上报 + 冷却重试)
        cooldown_active = False
        if self._history_warm_last_failed_at is not None:
            elapsed = datetime.now(timezone.utc) - self._history_warm_last_failed_at
            cooldown_active = elapsed < self._history_warm_retry_cooldown

        if (
            not self._history_warmed
            and not cooldown_active
            and self.history_cache
            and instruments
        ):
            try:
                warm_targets = list(instruments) + list(scan_instruments)
                logger.info(f"Warming history cache for {len(warm_targets)} instruments...")
                report = await warm_history_cache(
                    self.history_cache,
                    self._history_provider,
                    warm_targets,
                    lookback_days=60,
                )
                self._history_backfill_report = report
                # 只要有任一标的真正成功回填或缓存已足,视为进程内不再重试;
                # 全部失败(无 ok/skipped_cached)则保留 _history_warmed=False 允许下次重试,
                # 并置冷却时间戳避免每次 build 都打上游。
                effective = [
                    r for r in report if r["status"] in ("ok", "skipped_cached")
                ]
                if effective:
                    self._history_warmed = True
                    self._history_warm_last_failed_at = None
                    logger.info(f"History cache warm complete: {len(effective)}/{len(report)} usable")
                else:
                    self._history_warm_last_failed_at = datetime.now(timezone.utc)
                    logger.warning(
                        f"History cache warm all-failed for {len(report)} instruments; "
                        f"cooldown {self._history_warm_retry_cooldown} before retry"
                    )
            except Exception as e:
                logger.warning(f"History cache warm raised: {e}")
                self._history_warm_last_failed_at = datetime.now(timezone.utc)

        # 2. 获取新闻（或空列表）
        news: list[NewsItem] = []
        if include_news:
            news = await self.fetch_news()

        # 3. 加载历史快照
        recent_snapshots: list[dict] = []
        recent_advice: list[dict] = []
        execution_records: list[dict] = []
        forecast_records: list[dict] = []
        forecast_summary: dict = {}
        if include_history:
            recent_snapshots = self.persistence.load_recent(count=5)
            recent_advice = self.persistence.load_recent_advice(count=3)
            execution_records = self.persistence.list_executions()
            forecast_records = self.persistence.list_forecasts()
            forecast_records, settled_forecasts = await settle_due_forecasts(
                forecast_records,
                watchlist=list(self._watchlist) + list(self._sector_scan),
                history_cache=self.history_cache,
            )
            for record in settled_forecasts:
                self.persistence.save_forecast(record)
            forecast_summary = summarize_forecasts(forecast_records)

        # 5. 构建 AnalysisContext
        context = await self.context_builder.build(
            assets=self._assets,
            constraints=self._constraints,
            profile=self._profile,
            instruments=instruments,
            recent_snapshots=recent_snapshots,
            recent_advice=recent_advice,
            execution_records=execution_records,
            watchlist=self._watchlist,
            news=news,
            news_requested=include_news,
            news_provider_errors=dict(self.news_aggregator.last_errors),
            history_backfill_report=self._history_backfill_report,
            scan_instruments=scan_instruments,
            forecast_summary=forecast_summary,
            asset_schema_version=self._asset_schema_version,
            asset_load_warning=self._asset_load_warning,
            asset_base_currency=self._asset_base_currency,
            asset_accounts_v2=self._current_asset_accounts(),
            asset_positions_v2=self._current_asset_positions(),
            auto_included_holdings=auto_included_holdings,
            exposure_proxy=self._exposure_proxy,
        )

        # 保存最小快照，供下一次上下文进行前后对照。
        self.persistence.save_context(context)

        # 6. Flush history cache if present (ensure today's data persisted)
        if self.history_cache:
            try:
                await self.history_cache.flush()
                await self.history_cache.prune()
            except Exception as e:
                logger.warning(f"History cache maintenance failed: {e}")

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
            "event_calendar_enabled": self.event_calendar is not None,
            "sector_scan_loaded": len(self._sector_scan),
            "llm_analysis_enabled": self.llm_analysis.enabled,
        }

    def _get_scheduled_runner(self) -> ScheduledAnalysisRunner:
        if self._scheduled_runner is None:
            config = load_scheduled_config(self.config_dir)
            repo_root = Path(__file__).resolve().parents[2]
            self._scheduled_runner = ScheduledAnalysisRunner(
                self,
                config=config,
                artifact_dir=resolve_artifact_dir(config, repo_root=repo_root),
            )
        return self._scheduled_runner

    async def scheduled_run_due(
        self,
        *,
        now: Optional[str] = None,
        force: bool = False,
    ) -> dict:
        """Run any configured session that is currently due."""
        current = parse_datetime(now) if now else None
        return await self._get_scheduled_runner().run_due(now=current, force=force)

    async def scheduled_run_session(
        self,
        session_id: str,
        *,
        now: Optional[str] = None,
        force: bool = False,
    ) -> dict:
        """Run one configured scheduled analysis session."""
        current = parse_datetime(now) if now else None
        return await self._get_scheduled_runner().run_session(
            session_id,
            now=current,
            force=force,
        )

    def scheduled_run_latest(self, session_id: str) -> dict:
        """Read the latest scheduled analysis artifact for a session."""
        return self._get_scheduled_runner().latest(session_id)

    def preview_asset_migration_v2(self) -> dict:
        """生成 v1 -> v2 资产文件迁移预览，不写文件。"""
        source_path = self._asset_file_path()
        positions = [financial_asset_to_position_v2(asset) for asset in self._assets]
        accounts_by_id: dict[str, Account] = {}
        for asset in self._assets:
            account = account_from_v1_platform(asset.platform, asset.currency)
            accounts_by_id.setdefault(account.account_id, account)
        payload = {
            "schema_version": 2,
            "base_currency": "CNY",
            "accounts": [
                account.to_dict()
                for account in sorted(accounts_by_id.values(), key=lambda item: item.account_id)
            ],
            "positions": [position.to_storage_dict() for position in positions],
        }
        missing_summary: dict[str, int] = {}
        for position in positions:
            for field_name in position.data_completeness.get("missing_fields", []):
                missing_summary[field_name] = missing_summary.get(field_name, 0) + 1
        priority_order = [
            ("cost_basis", "上市持仓成本价/成本金额，用于未实现盈亏与 pnl 触发器"),
            ("instrument_key", "基金/证券代码，用于行情和净值接入"),
            ("quantity", "份额/股数/克数，用于市值计算"),
            ("valuation_as_of", "手工金额日期，用于判断时效"),
            ("classification", "资产分类，用于约束与暴露聚合"),
            ("supported_fx", "币种换算支持，用于 CNY 总值"),
        ]
        missing_priorities = [
            {
                "field": field_name,
                "count": missing_summary[field_name],
                "priority": index + 1,
                "reason": reason,
            }
            for index, (field_name, reason) in enumerate(priority_order)
            if missing_summary.get(field_name)
        ]
        return {
            "source_path": str(source_path),
            "write_path": str(self._local_data_dir / "financial_assets.json"),
            "backup_path": (
                str(source_path.with_name("financial_assets.v1.bak.json"))
                if source_path == self._local_data_dir / "financial_assets.json"
                else None
            ),
            "source_schema_version": self._asset_schema_version,
            "target_schema_version": 2,
            "base_currency": "CNY",
            "already_v2": self._asset_schema_version == 2,
            "position_count": len(positions),
            "account_count": len(accounts_by_id),
            "missing_fields_summary": missing_summary,
            "missing_fields_priority": missing_priorities,
            "positions": [position.to_dict() for position in positions],
            "accounts": [account.to_dict() for account in accounts_by_id.values()],
            "payload": payload,
            "will_write": False,
        }

    def migrate_assets_v2(self, *, confirmed: bool = False) -> dict:
        """确认式 v2 资产文件迁移。"""
        preview = self.preview_asset_migration_v2()
        if self._asset_schema_version == 2:
            return {
                **preview,
                "success": False,
                "error": "financial_assets.json is already schema_version=2",
            }
        if not confirmed:
            return {**preview, "success": True, "will_write": False}

        source_path = self._asset_file_path()
        write_path = self._local_data_dir / "financial_assets.json"
        self._local_data_dir.mkdir(parents=True, exist_ok=True)

        backup_path = None
        if source_path.exists() and source_path == write_path:
            backup_path = source_path.with_name("financial_assets.v1.bak.json")
            shutil.copy2(source_path, backup_path)

        with open(write_path, "w", encoding="utf-8") as f:
            json.dump(preview["payload"], f, ensure_ascii=False, indent=2)

        self._assets = self._load_assets_from_file()
        return {
            **preview,
            "success": True,
            "will_write": True,
            "written_path": str(write_path),
            "backup_path": str(backup_path) if backup_path else None,
            "action": "asset_migrated_v2",
        }

    # ------------------------------------------------------------------
    # 资产 CRUD 接口
    # ------------------------------------------------------------------

    def _ensure_legacy_asset_writable(self) -> None:
        """S2 过渡期保护：旧 FinancialAsset CRUD 不能覆盖 v2 文件。"""
        if self._asset_schema_version == 2:
            raise ValueError(
                "financial_assets.json is schema_version=2; legacy asset CRUD is disabled "
                "to avoid downgrading the file. Use the v2 migration/edit flow."
            )

    def add_asset(self, asset: FinancialAsset) -> None:
        """添加资产并持久化到本地文件。"""
        self._ensure_legacy_asset_writable()
        self._assets.append(self._with_cny_valuation(asset))
        self._save_assets()

    def remove_asset(self, name: str) -> bool:
        """按名称移除资产并持久化。"""
        self._ensure_legacy_asset_writable()
        original_len = len(self._assets)
        self._assets = [a for a in self._assets if a.name != name]
        if len(self._assets) < original_len:
            self._save_assets()
            return True
        return False

    def update_asset(self, name: str, **kwargs) -> bool:
        """按名称更新资产字段并持久化。"""
        self._ensure_legacy_asset_writable()
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
        self._ensure_legacy_asset_writable()
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
