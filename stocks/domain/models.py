from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional
from uuid import uuid4

_SUPPORTED_INSTRUMENT_MARKETS = {"a", "us", "crypto"}


def _normalize_instrument_key(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("instrument_key 必须是 market:code 字符串")
    raw = value.strip()
    if not raw:
        return None
    market, sep, code = raw.partition(":")
    market = market.strip().lower()
    code = code.strip()
    if not sep or not market or not code:
        raise ValueError("instrument_key 格式必须为 market:code")
    if market not in _SUPPORTED_INSTRUMENT_MARKETS:
        supported = ", ".join(sorted(_SUPPORTED_INSTRUMENT_MARKETS))
        raise ValueError(f"instrument_key market 必须是 {supported}")
    return f"{market}:{code}"


@dataclass(frozen=True)
class Instrument:
    """金融标的"""
    code: str
    name: str
    market: str                    # "a" / "us"
    exchange: Optional[str] = None  # "sh" / "sz" / "us"
    category: Optional[str] = None  # equity_cn / equity_us / tech / gold / bond / crypto
    pool: Optional[str] = None      # 候选池分层: core/sector/defensive/rates/ai_chain/broad 等

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "market": self.market,
            "exchange": self.exchange,
            "category": self.category,
            "pool": self.pool,
        }


@dataclass(frozen=True)
class Quote:
    """行情数据"""
    instrument: Instrument
    price: Optional[float] = None
    change: Optional[float] = None
    pct_change: Optional[float] = None
    volume_lot: Optional[float] = None
    amount_10k: Optional[float] = None
    open_price: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    prev_close: Optional[float] = None
    source: Optional[str] = None
    stale: bool = False
    as_of: Optional[str] = None

    indicators: Optional[dict] = None  # 技术指标计算结果

    def to_dict(self) -> dict:
        return {
            "instrument": {
                "code": self.instrument.code,
                "name": self.instrument.name,
                "market": self.instrument.market,
                "exchange": self.instrument.exchange,
                "category": self.instrument.category,
            },
            "price": self.price,
            "change": self.change,
            "pct_change": self.pct_change,
            "volume_lot": self.volume_lot,
            "amount_10k": self.amount_10k,
            "open_price": self.open_price,
            "high": self.high,
            "low": self.low,
            "prev_close": self.prev_close,
            "source": self.source,
            "stale": self.stale,
            "as_of": self.as_of,
            "indicators": self.indicators,
        }


@dataclass(frozen=True)
class NewsItem:
    """新闻条目 — 原始数据模型（适配后）

    字段说明：
    - summary: 可能为 None（如 Juhe 源不返回摘要）
    - published_at: 可能为 None（时间解析失败时）
    - source_type: 标识数据来源，用于 Agent 区分数据完整度
    - raw_metadata: 保留原始字段，供调试和 Agent 深度使用
    """
    title: str
    url: str
    source_name: str                    # 统一后的来源名称
    source_type: str                    # "rss" | "gnews" | "juhe_235" | "juhe_743"
    published_at: Optional[datetime]    # 标准化后的时间，解析失败为 None
    summary: Optional[str]             # 摘要，缺失为 None（不是空字符串）
    language: str = "unknown"            # "en" | "zh" | "unknown"
    tags: list[str] = field(default_factory=list)
    scope: str = "general"               # holding / general
    raw_metadata: dict = field(default_factory=dict)  # 原始字段保留

    def __post_init__(self) -> None:
        if self.scope not in {"holding", "general"}:
            raise ValueError("NewsItem.scope 必须是 holding 或 general")

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "source_name": self.source_name,
            "source_type": self.source_type,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "summary": self.summary,
            "language": self.language,
            "tags": self.tags,
            "scope": self.scope,
            # raw_metadata 不序列化，避免输出过大
        }


@dataclass(frozen=True)
class MarketEvent:
    """由新闻提取出的结构化市场事件。"""

    title: str
    url: str
    source_name: str
    source_type: str
    published_at: Optional[datetime]
    summary: Optional[str]
    event_type: str
    themes: list[str] = field(default_factory=list)
    affected_markets: list[str] = field(default_factory=list)
    affected_symbols: list[str] = field(default_factory=list)
    matched_holdings: list[str] = field(default_factory=list)
    sentiment: str = "unknown"
    urgency: str = "medium"
    impact_horizon: str = "short_term"
    confidence: float = 0.0
    rationale: str = ""
    raw_news_index: int = 0

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "source_name": self.source_name,
            "source_type": self.source_type,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "summary": self.summary,
            "event_type": self.event_type,
            "themes": self.themes,
            "affected_markets": self.affected_markets,
            "affected_symbols": self.affected_symbols,
            "matched_holdings": self.matched_holdings,
            "sentiment": self.sentiment,
            "urgency": self.urgency,
            "impact_horizon": self.impact_horizon,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "raw_news_index": self.raw_news_index,
        }


@dataclass(frozen=True)
class UpcomingEvent:
    """未来市场催化剂 — 只收录已官方公布的日程事实，不做预测。

    来源：
    - static_config: `stocks/config/event_calendar.json` 中人工维护的官方日程
      （FOMC、CPI、非农等，日期以官方发布为准）
    - finnhub_earnings: Finnhub 财报日历返回的 watchlist 标的财报日
    """

    date: str                                # ISO 日期，如 "2026-07-14"
    name: str                                # 事件名，如 "美国 6 月 CPI"
    event_type: str                          # macro_release / central_bank / earnings / other
    market: str                              # us / a / global / crypto
    time_utc: Optional[str] = None           # 已知的官方发布时间（UTC "HH:MM"），未知为 None
    scheduled_at: Optional[str] = None       # 完整 ISO 时点（含时区）；date-only 时为 None
    time_precision: str = "date"             # datetime / date
    status: str = "scheduled"                # scheduled / imminent / released_or_expired
    source: str = "static_config"            # static_config / finnhub_earnings
    affected_categories: list[str] = field(default_factory=list)  # 敏感的 watchlist 类别
    affected_symbols: list[str] = field(default_factory=list)     # 命中的 "market:code"
    days_until: Optional[int] = None         # 距 generated_at 的自然日数，构建时计算
    note: str = ""                           # 事实性备注（影响路径描述，非预测）

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "name": self.name,
            "event_type": self.event_type,
            "market": self.market,
            "time_utc": self.time_utc,
            "scheduled_at": self.scheduled_at,
            "time_precision": self.time_precision,
            "status": self.status,
            "source": self.source,
            "affected_categories": self.affected_categories,
            "affected_symbols": self.affected_symbols,
            "days_until": self.days_until,
            "note": self.note,
        }


@dataclass(frozen=True)
class FinancialAsset:
    """金融资产"""
    name: str
    platform: str
    amount: float
    asset_type: str = "unknown"
    notes: Optional[str] = None
    confirmed: bool = True
    currency: str = "CNY"  # 币种: CNY / USD / HKD 等
    instrument_key: Optional[str] = None  # 已确认映射的证券标的，格式 market:code
    quantity: Optional[float] = None      # 已确认持有数量，仅来自用户写入
    tradable: Optional[bool] = None       # 是否可交易；未知为 None
    amount_cny: Optional[float] = None  # 派生估值，不写回资产文件
    conversion_status: str = "ok"  # ok / degraded / failed
    conversion_source: str = "identity"
    conversion_rate: Optional[float] = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", (self.currency or "CNY").upper())
        object.__setattr__(
            self,
            "instrument_key",
            _normalize_instrument_key(self.instrument_key),
        )
        if self.quantity is not None:
            if isinstance(self.quantity, bool):
                raise ValueError("quantity 必须是数字")
            try:
                quantity = float(self.quantity)
            except (TypeError, ValueError) as exc:
                raise ValueError("quantity 必须是数字") from exc
            if quantity < 0:
                raise ValueError("quantity 不能为负数")
            object.__setattr__(self, "quantity", quantity)
        if self.tradable is not None and not isinstance(self.tradable, bool):
            raise ValueError("tradable 必须是 bool 或 null")

    @property
    def valuation_cny(self) -> Optional[float]:
        """返回可用于组合统计的 CNY 估值；未换算外币返回 None。"""
        if self.amount_cny is not None:
            return self.amount_cny
        if (self.currency or "CNY").upper() == "CNY":
            return self.amount
        return None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "platform": self.platform,
            "amount": self.amount,
            "asset_type": self.asset_type,
            "notes": self.notes,
            "confirmed": self.confirmed,
            "currency": self.currency,
            "instrument_key": self.instrument_key,
            "quantity": self.quantity,
            "tradable": self.tradable,
            "amount_cny": self.amount_cny,
            "conversion_status": self.conversion_status,
            "conversion_source": self.conversion_source,
            "conversion_rate": self.conversion_rate,
        }

    def to_storage_dict(self) -> dict:
        """返回持久化字段，排除运行时派生估值。"""
        data = {
            "name": self.name,
            "platform": self.platform,
            "amount": self.amount,
            "asset_type": self.asset_type,
            "notes": self.notes,
            "confirmed": self.confirmed,
            "currency": self.currency,
        }
        if self.instrument_key is not None:
            data["instrument_key"] = self.instrument_key
        if self.quantity is not None:
            data["quantity"] = self.quantity
        if self.tradable is not None:
            data["tradable"] = self.tradable
        return data


_ACCOUNT_INSTITUTION_TYPES = {"brokerage", "fund_platform", "bank", "insurance", "manual"}
_ASSET_CLASSES = {
    "cash",
    "cash_equivalent",
    "fixed_income",
    "equity",
    "commodity",
    "insurance",
    "alternative",
    "unknown",
}
_PRODUCT_TYPES = {
    "cash",
    "money_market_fund",
    "bank_wealth_management",
    "fixed_income_plus_fund",
    "mixed_fund",
    "qdii_fund",
    "feeder_fund",
    "exchange_traded_fund",
    "stock",
    "short_treasury_etf",
    "precious_metal_account",
    "insurance_policy",
    "manual_asset",
}
_HOLDING_UNITS = {"share", "gram", "unit"}
_VALUATION_METHODS = {
    "market_quote",
    "fund_nav",
    "manual_amount",
    "precious_metal_quote",
    "insurance_value",
}
_LIQUIDITY_TIERS = {"cash", "t0", "t1", "t2_plus", "periodic_open", "locked", "unknown"}
_COST_BASIS_METHODS = {"average"}


def _require_string(value, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _normalize_currency(value: Optional[str]) -> str:
    return (value or "CNY").upper().strip()


def _normalize_tag(value: str) -> str:
    tag = value.strip().lower().replace("-", " ").replace("/", " ")
    tag = re.sub(r"\s+", "_", tag)
    tag = re.sub(r"[^a-z0-9_]+", "", tag)
    return tag


def _validate_choice(value: str, allowed: set[str], field_name: str) -> str:
    if value not in allowed:
        raise ValueError(f"{field_name} must be one of {sorted(allowed)}")
    return value


def _require_mapping(value, field_name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")
    return value


@dataclass(frozen=True)
class Account:
    """v2 账户层级；只保存用户确认的本地账户事实。"""

    account_id: str
    display_name: str
    institution_type: str
    market_scope: Optional[list[str]] = None
    base_currency: str = "CNY"
    default_liquidity_tier: Optional[str] = None
    notes: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "account_id", _require_string(self.account_id, "account_id"))
        object.__setattr__(self, "display_name", _require_string(self.display_name, "display_name"))
        object.__setattr__(
            self,
            "institution_type",
            _validate_choice(self.institution_type, _ACCOUNT_INSTITUTION_TYPES, "institution_type"),
        )
        object.__setattr__(self, "base_currency", _normalize_currency(self.base_currency))
        if self.default_liquidity_tier is not None:
            object.__setattr__(
                self,
                "default_liquidity_tier",
                _validate_choice(
                    self.default_liquidity_tier,
                    _LIQUIDITY_TIERS,
                    "default_liquidity_tier",
                ),
            )
        if self.market_scope is not None:
            if not isinstance(self.market_scope, list):
                raise ValueError("market_scope must be a list or null")
            object.__setattr__(self, "market_scope", [str(item).strip() for item in self.market_scope])

    def to_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "display_name": self.display_name,
            "institution_type": self.institution_type,
            "market_scope": self.market_scope,
            "base_currency": self.base_currency,
            "default_liquidity_tier": self.default_liquidity_tier,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Account":
        data = _require_mapping(data, "account")
        return cls(**data)


@dataclass(frozen=True)
class CostBasis:
    """持仓成本事实；平均成本是 v2 首个支持方法。"""

    method: str = "average"
    unit_cost: Optional[float] = None
    cost_amount: Optional[float] = None
    currency: str = "CNY"

    def __post_init__(self) -> None:
        object.__setattr__(self, "method", _validate_choice(self.method, _COST_BASIS_METHODS, "cost_basis.method"))
        object.__setattr__(self, "currency", _normalize_currency(self.currency))
        unit_cost = _normalize_optional_float(self.unit_cost, "cost_basis.unit_cost")
        cost_amount = _normalize_optional_float(self.cost_amount, "cost_basis.cost_amount")
        if unit_cost is None and cost_amount is None:
            raise ValueError("cost_basis requires unit_cost or cost_amount")
        if unit_cost is not None and unit_cost < 0:
            raise ValueError("cost_basis.unit_cost cannot be negative")
        if cost_amount is not None and cost_amount < 0:
            raise ValueError("cost_basis.cost_amount cannot be negative")
        object.__setattr__(self, "unit_cost", unit_cost)
        object.__setattr__(self, "cost_amount", cost_amount)

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "unit_cost": self.unit_cost,
            "cost_amount": self.cost_amount,
            "currency": self.currency,
        }

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> Optional["CostBasis"]:
        if data is None:
            return None
        data = _require_mapping(data, "cost_basis")
        return cls(**data)


@dataclass(frozen=True)
class Holding:
    """持有数量与成本。"""

    quantity: float
    unit: str = "share"
    cost_basis: Optional[CostBasis] = None

    def __post_init__(self) -> None:
        quantity = _normalize_optional_float(self.quantity, "holding.quantity")
        if quantity is None or quantity < 0:
            raise ValueError("holding.quantity must be a non-negative number")
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "unit", _validate_choice(self.unit, _HOLDING_UNITS, "holding.unit"))
        if isinstance(self.cost_basis, dict):
            object.__setattr__(self, "cost_basis", CostBasis.from_dict(self.cost_basis))

    def to_dict(self) -> dict:
        return {
            "quantity": self.quantity,
            "unit": self.unit,
            "cost_basis": self.cost_basis.to_dict() if self.cost_basis else None,
        }

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> Optional["Holding"]:
        if data is None:
            return None
        data = _require_mapping(data, "holding")
        if "quantity" not in data:
            raise ValueError("holding.quantity must be a non-negative number")
        return cls(
            quantity=data["quantity"],
            unit=data.get("unit", "share"),
            cost_basis=CostBasis.from_dict(data.get("cost_basis")),
        )


@dataclass(frozen=True)
class Classification:
    """资产经济属性与暴露标签；替代 v1 自由文本 asset_type。"""

    asset_class: str
    product_type: str
    subtype: Optional[str] = None
    exposure_tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_class", _validate_choice(self.asset_class, _ASSET_CLASSES, "asset_class"))
        object.__setattr__(self, "product_type", _validate_choice(self.product_type, _PRODUCT_TYPES, "product_type"))
        if not isinstance(self.exposure_tags, list):
            raise ValueError("exposure_tags must be a list")
        tags = []
        for item in self.exposure_tags:
            tag = _normalize_tag(str(item))
            if tag and tag not in tags:
                tags.append(tag)
        object.__setattr__(self, "exposure_tags", tags)

    def to_dict(self) -> dict:
        return {
            "asset_class": self.asset_class,
            "product_type": self.product_type,
            "subtype": self.subtype,
            "exposure_tags": list(self.exposure_tags),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Classification":
        data = _require_mapping(data, "classification")
        return cls(
            asset_class=data.get("asset_class", "unknown"),
            product_type=data.get("product_type", "manual_asset"),
            subtype=data.get("subtype"),
            exposure_tags=data.get("exposure_tags", []),
        )


@dataclass(frozen=True)
class ValuationInput:
    """估值输入路由；只保存源事实，不保存派生市值。"""

    method: str
    manual_amount: Optional[float] = None
    as_of: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "method", _validate_choice(self.method, _VALUATION_METHODS, "valuation_input.method"))
        manual_amount = _normalize_optional_float(self.manual_amount, "valuation_input.manual_amount")
        if manual_amount is not None and manual_amount < 0:
            raise ValueError("valuation_input.manual_amount cannot be negative")
        object.__setattr__(self, "manual_amount", manual_amount)

    def to_dict(self) -> dict:
        return {"method": self.method, "manual_amount": self.manual_amount, "as_of": self.as_of}

    @classmethod
    def from_dict(cls, data: dict) -> "ValuationInput":
        data = _require_mapping(data, "valuation_input")
        return cls(
            method=data.get("method", "manual_amount"),
            manual_amount=data.get("manual_amount"),
            as_of=data.get("as_of"),
        )


@dataclass(frozen=True)
class Liquidity:
    """可交易性、可调仓性与流动性。"""

    tradable: Optional[bool] = None
    rebalance_eligible: Optional[bool] = None
    tier: str = "unknown"
    redemption_rule: Optional[str] = None
    lockup_until: Optional[str] = None
    maturity_date: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "tier", _validate_choice(self.tier, _LIQUIDITY_TIERS, "liquidity.tier"))
        if self.tradable is not None and not isinstance(self.tradable, bool):
            raise ValueError("liquidity.tradable must be bool or null")
        if self.rebalance_eligible is not None and not isinstance(self.rebalance_eligible, bool):
            raise ValueError("liquidity.rebalance_eligible must be bool or null")

    def to_dict(self) -> dict:
        return {
            "tradable": self.tradable,
            "rebalance_eligible": self.rebalance_eligible,
            "tier": self.tier,
            "redemption_rule": self.redemption_rule,
            "lockup_until": self.lockup_until,
            "maturity_date": self.maturity_date,
        }

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "Liquidity":
        if data is None:
            return cls()
        data = _require_mapping(data, "liquidity")
        return cls(**data)


@dataclass(frozen=True)
class ReportedPerformance:
    """渠道报告的对账快照；不替代可计算 PnL。"""

    unrealized_pnl: Optional[float] = None
    cumulative_pnl: Optional[float] = None
    as_of: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "unrealized_pnl", _normalize_optional_float(self.unrealized_pnl, "reported_performance.unrealized_pnl"))
        object.__setattr__(self, "cumulative_pnl", _normalize_optional_float(self.cumulative_pnl, "reported_performance.cumulative_pnl"))
        object.__setattr__(self, "as_of", _require_string(self.as_of, "reported_performance.as_of"))
        object.__setattr__(self, "source", _require_string(self.source, "reported_performance.source"))

    def to_dict(self) -> dict:
        return {
            "unrealized_pnl": self.unrealized_pnl,
            "cumulative_pnl": self.cumulative_pnl,
            "as_of": self.as_of,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> Optional["ReportedPerformance"]:
        if data is None:
            return None
        data = _require_mapping(data, "reported_performance")
        return cls(**data)


@dataclass(frozen=True)
class Position:
    """v2 持仓模型；统一表达证券、基金、现金、手工资产和保险。"""

    position_id: str
    account_id: str
    display_name: str
    currency: str
    classification: Classification
    valuation_input: ValuationInput
    liquidity: Liquidity
    instrument: Optional[dict] = None
    holding: Optional[Holding] = None
    role: Optional[str] = None
    reported_performance: Optional[ReportedPerformance] = None
    data_completeness: dict = field(default_factory=dict)
    confirmed: bool = True
    notes: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "position_id", _require_string(self.position_id, "position_id"))
        object.__setattr__(self, "account_id", _require_string(self.account_id, "account_id"))
        object.__setattr__(self, "display_name", _require_string(self.display_name, "display_name"))
        object.__setattr__(self, "currency", _normalize_currency(self.currency))
        if isinstance(self.classification, dict):
            object.__setattr__(self, "classification", Classification.from_dict(self.classification))
        if isinstance(self.valuation_input, dict):
            object.__setattr__(self, "valuation_input", ValuationInput.from_dict(self.valuation_input))
        if isinstance(self.liquidity, dict):
            object.__setattr__(self, "liquidity", Liquidity.from_dict(self.liquidity))
        if isinstance(self.holding, dict):
            object.__setattr__(self, "holding", Holding.from_dict(self.holding))
        if isinstance(self.reported_performance, dict):
            object.__setattr__(
                self,
                "reported_performance",
                ReportedPerformance.from_dict(self.reported_performance),
            )
        instrument = dict(self.instrument or {})
        if instrument.get("instrument_key") is not None:
            instrument["instrument_key"] = _normalize_instrument_key(instrument.get("instrument_key"))
        object.__setattr__(self, "instrument", instrument or None)

        liquidity = self.liquidity
        if self.classification.product_type == "insurance_policy":
            liquidity = Liquidity(
                tradable=False if liquidity.tradable is None else liquidity.tradable,
                rebalance_eligible=False
                if liquidity.rebalance_eligible is None
                else liquidity.rebalance_eligible,
                tier="locked" if liquidity.tier == "unknown" else liquidity.tier,
                redemption_rule=liquidity.redemption_rule,
                lockup_until=liquidity.lockup_until,
                maturity_date=liquidity.maturity_date,
            )
            object.__setattr__(self, "liquidity", liquidity)

        self._validate_valuation_contract()
        object.__setattr__(self, "data_completeness", self._derive_completeness())

    @property
    def instrument_key(self) -> Optional[str]:
        return (self.instrument or {}).get("instrument_key")

    def _validate_valuation_contract(self) -> None:
        method = self.valuation_input.method
        if method in {"manual_amount", "insurance_value"}:
            if self.valuation_input.manual_amount is None:
                raise ValueError(f"{method} requires valuation_input.manual_amount")
        if method == "market_quote":
            if not self.instrument_key:
                raise ValueError("market_quote requires instrument_key")
            if self.holding is None or self.holding.quantity is None:
                raise ValueError("market_quote requires holding.quantity")

    def _derive_completeness(self) -> dict:
        missing: list[str] = []
        if self.classification.asset_class == "unknown":
            missing.append("classification")
        if self.valuation_input.method in {"manual_amount", "insurance_value"} and not self.valuation_input.as_of:
            missing.append("valuation_as_of")
        if self.valuation_input.method == "market_quote":
            if not self.instrument_key:
                missing.append("instrument_key")
            if self.holding is None:
                missing.append("quantity")
                missing.append("cost_basis")
            elif self.holding.cost_basis is None:
                missing.append("cost_basis")
        return {"missing_fields": missing}

    def to_dict(self) -> dict:
        return {
            "position_id": self.position_id,
            "account_id": self.account_id,
            "display_name": self.display_name,
            "currency": self.currency,
            "classification": self.classification.to_dict(),
            "instrument": dict(self.instrument) if self.instrument else None,
            "holding": self.holding.to_dict() if self.holding else None,
            "valuation_input": self.valuation_input.to_dict(),
            "liquidity": self.liquidity.to_dict(),
            "role": self.role,
            "reported_performance": (
                self.reported_performance.to_dict() if self.reported_performance else None
            ),
            "data_completeness": dict(self.data_completeness),
            "confirmed": self.confirmed,
            "notes": self.notes,
        }

    def to_storage_dict(self) -> dict:
        return self.to_dict()

    @classmethod
    def from_dict(cls, data: dict) -> "Position":
        data = _require_mapping(data, "position")
        return cls(
            position_id=data.get("position_id", ""),
            account_id=data.get("account_id", ""),
            display_name=data.get("display_name", ""),
            currency=data.get("currency", "CNY"),
            classification=Classification.from_dict(data.get("classification", {})),
            instrument=data.get("instrument"),
            holding=Holding.from_dict(data.get("holding")),
            valuation_input=ValuationInput.from_dict(data.get("valuation_input", {})),
            liquidity=Liquidity.from_dict(data.get("liquidity")),
            role=data.get("role"),
            reported_performance=ReportedPerformance.from_dict(data.get("reported_performance")),
            confirmed=data.get("confirmed", True),
            notes=data.get("notes"),
        )


_V1_ASSET_TYPE_CLASSIFICATION = {
    "equity": ("equity", "stock", []),
    "stock": ("equity", "stock", []),
    "股票": ("equity", "stock", []),
    "fund": ("equity", "mixed_fund", []),
    "基金": ("equity", "mixed_fund", []),
    "股票ETF": ("equity", "exchange_traded_fund", []),
    "股票etf": ("equity", "exchange_traded_fund", []),
    "etf": ("equity", "exchange_traded_fund", []),
    "指数基金": ("equity", "mixed_fund", []),
    "指数": ("equity", "mixed_fund", []),
    "qdii": ("equity", "qdii_fund", ["qdii_delayed_nav"]),
    "QDII": ("equity", "qdii_fund", ["qdii_delayed_nav"]),
    "bond": ("fixed_income", "manual_asset", ["fixed_income"]),
    "fixed_income": ("fixed_income", "manual_asset", ["fixed_income"]),
    "理财": ("fixed_income", "bank_wealth_management", ["bank_wmp"]),
    "固收": ("fixed_income", "manual_asset", ["fixed_income"]),
    "固收+": ("fixed_income", "fixed_income_plus_fund", ["fixed_income"]),
    "债券": ("fixed_income", "manual_asset", ["fixed_income"]),
    "cash": ("cash", "cash", ["cash_like"]),
    "现金": ("cash", "cash", ["cash_like"]),
    "deposit": ("cash", "cash", ["cash_like"]),
    "money_market": ("cash_equivalent", "money_market_fund", ["money_market", "cash_like"]),
    "现金管理": ("cash_equivalent", "money_market_fund", ["cash_like"]),
    "货币基金": ("cash_equivalent", "money_market_fund", ["money_market", "cash_like"]),
    "货基": ("cash_equivalent", "money_market_fund", ["money_market", "cash_like"]),
    "活期": ("cash", "cash", ["cash_like"]),
    "gold": ("commodity", "precious_metal_account", ["gold"]),
    "黄金ETF": ("commodity", "exchange_traded_fund", ["gold"]),
    "黄金etf": ("commodity", "exchange_traded_fund", ["gold"]),
    "贵金属": ("commodity", "precious_metal_account", ["precious_metals"]),
    "commodity": ("commodity", "manual_asset", []),
    "insurance": ("insurance", "insurance_policy", ["locked"]),
    "保险": ("insurance", "insurance_policy", ["locked"]),
    "locked": ("alternative", "manual_asset", ["locked"]),
    "crypto": ("alternative", "manual_asset", []),
    "reits": ("alternative", "manual_asset", []),
    "alternative": ("alternative", "manual_asset", []),
    "unknown": ("unknown", "manual_asset", []),
}


def _slugify_account_id(value: str) -> str:
    raw = (value or "manual").strip().lower().replace("-", " ").replace("/", " ")
    slug = re.sub(r"\s+", "_", raw)
    slug = re.sub(r"[^\w_]+", "", slug, flags=re.UNICODE)
    return slug or "manual"


def classification_from_v1_asset_type(asset_type: str) -> Classification:
    key = asset_type or "unknown"
    asset_class, product_type, tags = _V1_ASSET_TYPE_CLASSIFICATION.get(
        key,
        _V1_ASSET_TYPE_CLASSIFICATION.get(key.lower(), ("unknown", "manual_asset", [])),
    )
    return Classification(
        asset_class=asset_class,
        product_type=product_type,
        exposure_tags=tags,
    )


def account_from_v1_platform(platform: str, base_currency: str = "CNY") -> Account:
    return Account(
        account_id=_slugify_account_id(platform),
        display_name=platform or "未知账户",
        institution_type="manual",
        base_currency=base_currency,
    )


def position_id_from_v1_asset(asset: FinancialAsset) -> str:
    """为 v1 资产生成稳定、不碰撞、可读的 v2 position_id。"""
    account_id = _slugify_account_id(asset.platform)
    name_slug = _slugify_account_id(asset.name)
    fingerprint = "|".join(
        [
            asset.platform or "",
            asset.name or "",
            asset.currency or "",
            asset.asset_type or "",
            asset.instrument_key or "",
        ]
    )
    digest = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:8]
    return f"{account_id}_{name_slug}_{digest}"


def financial_asset_to_position_v2(asset: FinancialAsset) -> Position:
    classification = classification_from_v1_asset_type(asset.asset_type)
    account_id = _slugify_account_id(asset.platform)
    if asset.instrument_key and asset.quantity is not None:
        valuation_input = ValuationInput(method="market_quote")
        holding = Holding(quantity=asset.quantity, unit="share")
        instrument = {"instrument_key": asset.instrument_key}
    else:
        valuation_input = ValuationInput(method="manual_amount", manual_amount=asset.amount)
        holding = None
        instrument = {"instrument_key": asset.instrument_key} if asset.instrument_key else None
    return Position(
        position_id=position_id_from_v1_asset(asset),
        account_id=account_id,
        display_name=asset.name,
        currency=asset.currency,
        classification=classification,
        instrument=instrument,
        holding=holding,
        valuation_input=valuation_input,
        liquidity=Liquidity(tradable=asset.tradable),
        confirmed=asset.confirmed,
        notes=asset.notes,
    )


def position_v2_to_financial_asset(position: Position) -> FinancialAsset:
    amount = position.valuation_input.manual_amount
    if amount is None:
        amount = 0.0
    return FinancialAsset(
        name=position.display_name,
        platform=position.account_id,
        amount=amount,
        asset_type=position.classification.asset_class,
        notes=position.notes,
        confirmed=position.confirmed,
        currency=position.currency,
        instrument_key=position.instrument_key,
        quantity=position.holding.quantity if position.holding else None,
        tradable=position.liquidity.tradable,
    )


@dataclass(frozen=True)
class PortfolioMapping:
    """组合映射脚手架 — 轻量规则输出，供 LLM 参考"""
    buckets: dict[str, list[FinancialAsset]] = field(default_factory=dict)
    ratios: dict[str, float] = field(default_factory=dict)
    dominant_layers: list[str] = field(default_factory=list)
    growth_exposure: str = "none"           # high / moderate / light / none
    buffer_strength: str = "none"           # strong / moderate / light / none
    liquidity_status: str = "thin"        # ample / adequate / thin
    locked_assets_present: bool = False

    def to_dict(self) -> dict:
        return {
            "buckets": {k: [a.to_dict() for a in v] for k, v in self.buckets.items()},
            "ratios": self.ratios,
            "dominant_layers": self.dominant_layers,
            "growth_exposure": self.growth_exposure,
            "buffer_strength": self.buffer_strength,
            "liquidity_status": self.liquidity_status,
            "locked_assets_present": self.locked_assets_present,
        }


@dataclass(frozen=True)
class MarketState:
    """市场状态脚手架 — 轻量规则输出，供 LLM 参考"""
    risk_appetite: str = "no_data"         # risk_on / cooling / broad_risk_off / mixed / no_data
    tech_state: str = "no_data"           # expanding / under_pressure / soft / mixed / no_data
    safe_haven_state: str = "no_data"      # strengthening / supported / weakening / no_data
    china_state: str = "no_data"           # stable_positive / stable / mixed_pressure / under_pressure / no_data
    rates_state: str = "no_data"           # bonds_bid / rates_pressure / neutral / no_data
    crypto_state: str = "no_data"          # strong / positive / mixed / soft / under_pressure / no_data
    cross_asset_summary: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "risk_appetite": self.risk_appetite,
            "tech_state": self.tech_state,
            "safe_haven_state": self.safe_haven_state,
            "china_state": self.china_state,
            "rates_state": self.rates_state,
            "crypto_state": self.crypto_state,
            "cross_asset_summary": self.cross_asset_summary,
        }


@dataclass(frozen=True)
class DriftCheck:
    """约束偏离检查"""
    bucket: str
    current_ratio: float
    target_min: Optional[float]
    target_max: Optional[float]
    status: str                          # within_range / below_min / above_max
    gap: float

    def to_dict(self) -> dict:
        return {
            "bucket": self.bucket,
            "current_ratio": self.current_ratio,
            "target_min": self.target_min,
            "target_max": self.target_max,
            "status": self.status,
            "gap": self.gap,
        }


_ADVICE_DIRECTIONS = {"buy", "sell", "watch", "hold"}
_ADVICE_BASED_ON = {"quotes", "news", "indicators", "macro", "portfolio", "profile"}
_ADVICE_BOUNDARY_TYPES = {"fact", "inference"}
_ADVICE_TRIGGER_TYPES = {
    "price_above",       # 收盘价上穿 level
    "price_below",       # 收盘价下穿 level
    "pct_change_above",  # 建议日以来累计涨跌幅 >= level（百分数）
    "pct_change_below",  # 建议日以来累计涨跌幅 <= level（百分数）
    "pnl_pct_above",     # 最新/期间收盘相对用户成本浮盈 >= level（百分数）
    "pnl_pct_below",     # 最新/期间收盘相对用户成本浮盈 <= level（百分数）
}
_ADVICE_ACTIONS = {"add", "increase", "reduce", "exit", "hold", "watch"}
_ADVICE_HORIZONS = {"short", "medium", "long"}
_EXECUTION_ACTIONS = _ADVICE_ACTIONS | {"none"}
_EXECUTION_EXTENTS = {"full", "partial"}
_EXECUTION_STATUSES = {"executed", "rejected", "deferred", "planned", "not_executed"}
_FORECAST_METRICS = {"close"}
_FORECAST_COMPARATORS = {"above", "below"}
_FORECAST_CONFIDENCES = {"low", "medium", "high"}
_FORECAST_STATUSES = {"open", "hit", "miss", "unresolved", "manual"}
_EXACT_MONEY_RE = re.compile(
    r"(?:[$¥￥]\s*\d[\d,]*(?:\.\d+)?)|"
    r"(?:\d[\d,]*(?:\.\d+)?\s*(?:元|人民币|美元|美金|港元|USD|CNY|HKD))",
    re.IGNORECASE,
)


def _normalize_optional_float(value, field_name: str) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number") from exc
    return number


def _normalize_date_string(value, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a YYYY-MM-DD date")
    raw = value.strip()
    try:
        date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a YYYY-MM-DD date") from exc
    return raw


@dataclass(frozen=True)
class AdviceRecord:
    """用户确认保存的建议摘要，不保存 LLM 长文。"""

    created_at: str
    instruments: list[dict]
    direction: dict[str, str]
    rationale_summary: str
    based_on: list[str]
    boundary: list[dict]
    triggers: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        *,
        instruments: list[dict],
        direction: dict[str, str],
        rationale_summary: str,
        based_on: list[str],
        boundary: list[dict],
        triggers: Optional[list[dict]] = None,
        actions: Optional[list[dict]] = None,
    ) -> "AdviceRecord":
        return cls(
            created_at=datetime.now(timezone.utc).isoformat(),
            instruments=instruments,
            direction=direction,
            rationale_summary=rationale_summary,
            based_on=based_on,
            boundary=boundary,
            triggers=list(triggers or []),
            actions=list(actions or []),
        )

    @classmethod
    def from_dict(cls, data: dict) -> "AdviceRecord":
        return cls(
            created_at=str(data.get("created_at", "")),
            instruments=list(data.get("instruments", [])),
            direction=dict(data.get("direction", {})),
            rationale_summary=str(data.get("rationale_summary", "")),
            based_on=list(data.get("based_on", [])),
            boundary=list(data.get("boundary", [])),
            triggers=list(data.get("triggers", [])),
            actions=list(data.get("actions", [])),
        )

    def __post_init__(self) -> None:
        if not self.created_at:
            raise ValueError("created_at is required")
        if len(self.rationale_summary) > 500:
            raise ValueError("rationale_summary must be 500 characters or fewer")
        if not isinstance(self.instruments, list) or not all(
            isinstance(item, dict)
            and isinstance(item.get("market"), str)
            and isinstance(item.get("code"), str)
            and isinstance(item.get("name"), str)
            for item in self.instruments
        ):
            raise ValueError("instruments must be a list of {market, code, name}")
        invalid_directions = set(self.direction.values()) - _ADVICE_DIRECTIONS
        if invalid_directions:
            raise ValueError(f"Unsupported advice directions: {sorted(invalid_directions)}")
        invalid_sources = set(self.based_on) - _ADVICE_BASED_ON
        if invalid_sources:
            raise ValueError(f"Unsupported based_on values: {sorted(invalid_sources)}")
        if not isinstance(self.boundary, list) or not all(
            isinstance(item, dict)
            and item.get("type") in _ADVICE_BOUNDARY_TYPES
            and isinstance(item.get("text"), str)
            and item.get("text")
            for item in self.boundary
        ):
            raise ValueError("boundary must contain {type: fact|inference, text}")
        if not isinstance(self.triggers, list):
            raise ValueError("triggers must be a list")
        for item in self.triggers:
            if not isinstance(item, dict):
                raise ValueError("each trigger must be an object")
            instrument = item.get("instrument")
            if not isinstance(instrument, str) or ":" not in instrument:
                raise ValueError("trigger.instrument must be 'market:code'")
            if item.get("type") not in _ADVICE_TRIGGER_TYPES:
                raise ValueError(
                    f"trigger.type must be one of {sorted(_ADVICE_TRIGGER_TYPES)}"
                )
            level = item.get("level")
            if not isinstance(level, (int, float)) or isinstance(level, bool):
                raise ValueError("trigger.level must be a number")
            action = item.get("action")
            if not isinstance(action, str) or not action.strip():
                raise ValueError("trigger.action must be a non-empty string")
            invalidation = item.get("invalidation")
            if invalidation is not None and not isinstance(invalidation, str):
                raise ValueError("trigger.invalidation must be a string when present")
            unknown = set(item) - {"instrument", "type", "level", "action", "invalidation"}
            if unknown:
                raise ValueError(f"Unsupported trigger fields: {sorted(unknown)}")
        if not isinstance(self.actions, list):
            raise ValueError("actions must be a list")
        for item in self.actions:
            if not isinstance(item, dict):
                raise ValueError("each action must be an object")
            target = item.get("target")
            if not isinstance(target, str) or not target.strip():
                raise ValueError("action.target must be a non-empty string")
            if item.get("action") not in _ADVICE_ACTIONS:
                raise ValueError(f"action.action must be one of {sorted(_ADVICE_ACTIONS)}")
            size_hint = item.get("size_hint")
            if not isinstance(size_hint, str) or not size_hint.strip():
                raise ValueError("action.size_hint must be a non-empty string")
            if _EXACT_MONEY_RE.search(size_hint):
                raise ValueError("action.size_hint must not contain exact currency amounts")
            if item.get("horizon") not in _ADVICE_HORIZONS:
                raise ValueError(f"action.horizon must be one of {sorted(_ADVICE_HORIZONS)}")
            trigger = item.get("trigger")
            if trigger is not None and not isinstance(trigger, str):
                raise ValueError("action.trigger must be a string when present")
            invalidation = item.get("invalidation")
            if invalidation is not None and not isinstance(invalidation, str):
                raise ValueError("action.invalidation must be a string when present")
            unknown = set(item) - {
                "target",
                "action",
                "size_hint",
                "trigger",
                "invalidation",
                "horizon",
            }
            if unknown:
                raise ValueError(f"Unsupported action fields: {sorted(unknown)}")

    def to_dict(self) -> dict:
        return {
            "created_at": self.created_at,
            "instruments": self.instruments,
            "direction": self.direction,
            "rationale_summary": self.rationale_summary,
            "based_on": self.based_on,
            "boundary": self.boundary,
            "triggers": self.triggers,
            "actions": self.actions,
        }


@dataclass(frozen=True)
class ExecutionRecord:
    """决策执行记录 — 记录一次 planned / executed / rejected / deferred 的决策执行结果。"""

    id: str
    decision_id: str  # 必填，关联到 PortfolioDecision.decision_id
    status: str  # executed | rejected | deferred | planned
    target: str
    action: str
    note: str
    executed_at: str
    recorded_at: str
    advice_id: Optional[str] = None
    extent: Optional[str] = None
    price: Optional[float] = None  # status=executed 时必填
    executed_ratio: Optional[float] = None  # status=executed 时必填
    rejection_reason: Optional[str] = None  # status=rejected 时必填
    next_review_at: Optional[str] = None  # status=deferred 时可填

    @classmethod
    def create(
        cls,
        *,
        decision_id: str,
        status: str = "planned",
        target: str = "",
        action: str = "",
        note: str = "",
        executed_at: Optional[str] = None,
        advice_id: Optional[str] = None,
        extent: Optional[str] = None,
        price: Optional[float] = None,
        executed_ratio: Optional[float] = None,
        rejection_reason: Optional[str] = None,
        next_review_at: Optional[str] = None,
        id: Optional[str] = None,
        recorded_at: Optional[str] = None,
    ) -> "ExecutionRecord":
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            id=id or uuid4().hex,
            decision_id=decision_id,
            status=status,
            target=target,
            action=action,
            extent=extent,
            note=note,
            recorded_at=recorded_at or now,
            executed_at=executed_at or now,
            advice_id=advice_id,
            price=price,
            executed_ratio=executed_ratio,
            rejection_reason=rejection_reason,
            next_review_at=next_review_at,
        )

    @classmethod
    def from_dict(cls, data: dict) -> "ExecutionRecord":
        return cls(
            id=str(data.get("id", "")),
            decision_id=str(data.get("decision_id", "")),
            status=str(data.get("status", "planned")),
            target=str(data.get("target", "")),
            action=str(data.get("action", "")),
            extent=data.get("extent"),
            note=str(data.get("note", "")),
            recorded_at=str(data.get("recorded_at", "")),
            executed_at=str(data.get("executed_at", "")),
            advice_id=data.get("advice_id"),
            price=_normalize_optional_float(data.get("price"), "execution.price"),
            executed_ratio=_normalize_optional_float(data.get("executed_ratio"), "execution.executed_ratio"),
            rejection_reason=data.get("rejection_reason"),
            next_review_at=data.get("next_review_at"),
        )

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("execution.id is required")
        if not self.decision_id:
            raise ValueError("execution.decision_id is required")
        if self.status not in _EXECUTION_STATUSES:
            raise ValueError(
                f"execution.status must be one of {sorted(_EXECUTION_STATUSES)}"
            )
        if self.status == "executed":
            if self.price is None:
                raise ValueError("execution.price is required when status=executed")
            if self.executed_ratio is None:
                raise ValueError("execution.executed_ratio is required when status=executed")
            if self.executed_ratio < 0 or self.executed_ratio > 1:
                raise ValueError("execution.executed_ratio must be between 0 and 1")
        if self.status == "rejected" and not self.rejection_reason:
            raise ValueError("execution.rejection_reason is required when status=rejected")
        # Legacy backward-compatible validation
        if self.advice_id is not None and not isinstance(self.advice_id, str):
            raise ValueError("execution.advice_id must be a string when present")
        if not isinstance(self.target, str):
            raise ValueError("execution.target must be a string")
        if self.action and self.action not in _EXECUTION_ACTIONS:
            raise ValueError(f"execution.action must be one of {sorted(_EXECUTION_ACTIONS)}")
        if self.action == "none" and self.extent is not None:
            raise ValueError("execution.extent must be omitted when action is none")
        if self.action and self.action != "none" and self.extent not in (None, *_EXECUTION_EXTENTS):
            raise ValueError("execution.extent must be full or partial when action is set")
        if not isinstance(self.note, str):
            raise ValueError("execution.note must be a string")

    def to_dict(self) -> dict:
        data = {
            "id": self.id,
            "decision_id": self.decision_id,
            "status": self.status,
            "advice_id": self.advice_id,
            "target": self.target,
            "action": self.action,
            "note": self.note,
            "executed_at": self.executed_at,
            "recorded_at": self.recorded_at,
        }
        if self.extent is not None:
            data["extent"] = self.extent
        if self.price is not None:
            data["price"] = self.price
        if self.executed_ratio is not None:
            data["executed_ratio"] = self.executed_ratio
        if self.rejection_reason is not None:
            data["rejection_reason"] = self.rejection_reason
        if self.next_review_at is not None:
            data["next_review_at"] = self.next_review_at
        return data


@dataclass(frozen=True)
class ForecastRecord:
    """用户确认保存的一条可问责预测或人工预测记录。"""

    id: str
    created_at: str
    statement: str
    metric: str
    comparator: str
    deadline: str
    confidence: str
    status: str
    target: Optional[str] = None
    level: Optional[float] = None
    resolved_at: Optional[str] = None
    resolution_note: Optional[str] = None

    @classmethod
    def create(
        cls,
        *,
        statement: str,
        comparator: str,
        deadline: str,
        confidence: str,
        target: Optional[str] = None,
        level=None,
        metric: str = "close",
        id: Optional[str] = None,
    ) -> "ForecastRecord":
        normalized_target = _normalize_instrument_key(target)
        normalized_level = _normalize_optional_float(level, "forecast.level")
        status = "open"
        resolution_note = None
        if normalized_target is None or normalized_level is None:
            status = "manual"
            missing = []
            if normalized_target is None:
                missing.append("target")
            if normalized_level is None:
                missing.append("level")
            resolution_note = f"manual_review_required: missing {', '.join(missing)}"
        return cls(
            id=id or uuid4().hex,
            created_at=datetime.now(timezone.utc).isoformat(),
            statement=statement,
            target=normalized_target,
            metric=metric,
            comparator=comparator,
            level=normalized_level,
            deadline=deadline,
            confidence=confidence,
            status=status,
            resolution_note=resolution_note,
        )

    @classmethod
    def from_dict(cls, data: dict) -> "ForecastRecord":
        return cls(
            id=str(data.get("id", "")),
            created_at=str(data.get("created_at", "")),
            statement=str(data.get("statement", "")),
            target=data.get("target"),
            metric=str(data.get("metric", "")),
            comparator=str(data.get("comparator", "")),
            level=data.get("level"),
            deadline=str(data.get("deadline", "")),
            confidence=str(data.get("confidence", "")),
            status=str(data.get("status", "")),
            resolved_at=data.get("resolved_at"),
            resolution_note=data.get("resolution_note"),
        )

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("forecast.id is required")
        if not self.created_at:
            raise ValueError("forecast.created_at is required")
        if not isinstance(self.statement, str) or not self.statement.strip():
            raise ValueError("forecast.statement must be a non-empty string")
        if len(self.statement) > 500:
            raise ValueError("forecast.statement must be 500 characters or fewer")
        object.__setattr__(self, "target", _normalize_instrument_key(self.target))
        object.__setattr__(
            self,
            "level",
            _normalize_optional_float(self.level, "forecast.level"),
        )
        object.__setattr__(
            self,
            "deadline",
            _normalize_date_string(self.deadline, "forecast.deadline"),
        )
        if self.metric not in _FORECAST_METRICS:
            raise ValueError(f"forecast.metric must be one of {sorted(_FORECAST_METRICS)}")
        if self.comparator not in _FORECAST_COMPARATORS:
            raise ValueError(
                f"forecast.comparator must be one of {sorted(_FORECAST_COMPARATORS)}"
            )
        if self.confidence not in _FORECAST_CONFIDENCES:
            raise ValueError(
                f"forecast.confidence must be one of {sorted(_FORECAST_CONFIDENCES)}"
            )
        if self.status not in _FORECAST_STATUSES:
            raise ValueError(f"forecast.status must be one of {sorted(_FORECAST_STATUSES)}")
        if self.status == "open" and (self.target is None or self.level is None):
            raise ValueError("open forecasts require target and level")
        if self.status == "manual" and not self.resolution_note:
            object.__setattr__(
                self,
                "resolution_note",
                "manual_review_required",
            )
        if self.resolved_at is not None and not isinstance(self.resolved_at, str):
            raise ValueError("forecast.resolved_at must be a string when present")
        if self.resolution_note is not None and not isinstance(self.resolution_note, str):
            raise ValueError("forecast.resolution_note must be a string when present")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "statement": self.statement,
            "target": self.target,
            "metric": self.metric,
            "comparator": self.comparator,
            "level": self.level,
            "deadline": self.deadline,
            "confidence": self.confidence,
            "status": self.status,
            "resolved_at": self.resolved_at,
            "resolution_note": self.resolution_note,
        }


@dataclass(frozen=True)
class AnalysisContext:
    """统一分析上下文 — 核心接口契约

    这是 stocks-claw 向 Agent 提供的"完整分析原料包"。
    Agent 可以：
    1. 直接读取其中的结构化数据做展示
    2. 把 context 喂给自己的 LLM 做分析
    3. 让 stocks-claw 内部 LLM 基于 context 生成报告
    """
    # 元信息
    generated_at: str

    # 用户金融记忆（权威输入）
    assets: list[FinancialAsset]
    asset_count: int
    portfolio_constraints: dict
    portfolio_profile: dict

    # 市场输入
    quotes: dict[str, list[Quote]]       # 按市场分组的所有行情
    news: list[NewsItem]
    news_count: int

    # 轻量脚手架（辅助信号）
    market_state: MarketState
    portfolio_mapping: PortfolioMapping
    drift_checks: list[DriftCheck]

    # 历史上下文
    recent_snapshots: list[dict]         # 最近 N 次报告摘要

    # 原始输入（供 LLM 阅读）
    raw_prompt_input: str                # 人类可读格式的完整上下文文本

    # 新闻事件层
    market_events: list[MarketEvent] = field(default_factory=list)
    news_digest: dict = field(default_factory=dict)

    # 全局情报巡逻聚合结果（事实源，来自 global_intelligence_watch）
    intelligence_digest: dict = field(default_factory=dict)

    # 宏观数据快照
    macro_snapshot: Optional[dict] = None

    # 技术指标汇总，key 格式为 "{market}:{code}"
    technical_indicators: dict[str, dict] = field(default_factory=dict)

    # 数据质量与溯源摘要
    data_quality: dict[str, dict] = field(default_factory=dict)

    # 最近确认保存的建议摘要
    recent_advice: list[dict] = field(default_factory=list)

    # 未来催化剂日历（官方已公布日程 + 财报日历）
    upcoming_events: list[UpcomingEvent] = field(default_factory=list)

    # 板块轮动脚手架（历史收盘相对强弱，纯事实排名）
    rotation: dict = field(default_factory=dict)

    # 引擎动作信号（规则化方向性候选动作，2026-07-02 用户裁决启用）
    action_signals: dict = field(default_factory=dict)

    # 规则回测记分卡
    rule_scorecard: dict = field(default_factory=dict)

    # 预测台账摘要（S1-4）
    forecast_summary: dict = field(default_factory=dict)

    # v2 资产事实与运行时派生估值（S2）
    asset_accounts: list[dict] = field(default_factory=list)
    asset_positions: list[dict] = field(default_factory=list)
    position_valuations: list[dict] = field(default_factory=list)
    exposure_summary: dict = field(default_factory=dict)
    liquidity_summary: dict = field(default_factory=dict)
    asset_data_boundaries: dict = field(default_factory=dict)
    advice_granularity: dict = field(default_factory=dict)

    # 引擎运行时配置（从 engine.yaml 加载，阈值/风控参数等）
    engine_config: dict = field(default_factory=dict)

    # 元信息（带默认值）
    schema_version: int = 12

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "schema_version": self.schema_version,
            "assets": [a.to_dict() for a in self.assets],
            "asset_count": self.asset_count,
            "portfolio_constraints": self.portfolio_constraints,
            "portfolio_profile": self.portfolio_profile,
            "quotes": {k: [q.to_dict() for q in v] for k, v in self.quotes.items()},
            "news": [n.to_dict() for n in self.news],
            "news_count": self.news_count,
            "market_events": [e.to_dict() for e in self.market_events],
            "news_digest": self.news_digest,
            "intelligence_digest": self.intelligence_digest,
            "market_state": self.market_state.to_dict(),
            "portfolio_mapping": self.portfolio_mapping.to_dict(),
            "drift_checks": [d.to_dict() for d in self.drift_checks],
            "recent_snapshots": self.recent_snapshots,
            "raw_prompt_input": self.raw_prompt_input,
            "macro_snapshot": self.macro_snapshot,
            "technical_indicators": self.technical_indicators,
            "data_quality": self.data_quality,
            "recent_advice": self.recent_advice,
            "upcoming_events": [e.to_dict() for e in self.upcoming_events],
            "rotation": self.rotation,
            "action_signals": self.action_signals,
            "rule_scorecard": self.rule_scorecard,
            "forecast_summary": self.forecast_summary,
            "asset_accounts": self.asset_accounts,
            "asset_positions": self.asset_positions,
            "position_valuations": self.position_valuations,
            "exposure_summary": self.exposure_summary,
            "liquidity_summary": self.liquidity_summary,
            "asset_data_boundaries": self.asset_data_boundaries,
            "advice_granularity": self.advice_granularity,
            "engine_config": self.engine_config,
        }


@dataclass(frozen=True)
class DecisionEnvelope:
    """（已冻结，未在运行时接线）决策层统一传输协议。

    此 dataclass 及其契约校验器已冻结为设计参考，不作为当前
    运行路径的一部分。系统当前通过 portfolio_decision.user_view
    交付确定性结果，通过 structured_outlook 交付受限 LLM 研判。

    G0 设计：Status ∈ {ok,degraded,setup_required,validation_failed,failed}
    Internal modes: internal_llm / agent_delegate / deterministic_only
    """

    status: str
    mode_requested: str
    mode_used: str
    decision_plan: Optional[dict] = None
    agent_task: Optional[dict] = None
    setup_required: Optional[dict] = None
    quality: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    final_analysis_instructions: str = ""

    def __post_init__(self) -> None:
        if self.status not in {
            "ok",
            "degraded",
            "setup_required",
            "validation_failed",
            "failed",
        }:
            raise ValueError("DecisionEnvelope.status 非法")
        if self.mode_used not in {
            "internal_llm",
            "agent_delegate",
            "deterministic_only",
        }:
            raise ValueError("DecisionEnvelope.mode_used 非法")
        if not self.mode_requested:
            raise ValueError("DecisionEnvelope.mode_requested 不能为空")
        if not self.final_analysis_instructions.strip():
            raise ValueError("final_analysis_instructions 不能为空")

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "mode_requested": self.mode_requested,
            "mode_used": self.mode_used,
            "decision_plan": self.decision_plan,
            "agent_task": self.agent_task,
            "setup_required": self.setup_required,
            "quality": self.quality,
            "errors": list(self.errors),
            "final_analysis_instructions": self.final_analysis_instructions,
        }
