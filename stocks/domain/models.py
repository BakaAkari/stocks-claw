from __future__ import annotations

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
}
_ADVICE_ACTIONS = {"add", "increase", "reduce", "exit", "hold", "watch"}
_ADVICE_HORIZONS = {"short", "medium", "long"}
_EXECUTION_ACTIONS = _ADVICE_ACTIONS | {"none"}
_EXECUTION_EXTENTS = {"full", "partial"}
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
    """用户确认记录的一次建议执行或明确未执行。"""

    id: str
    target: str
    action: str
    note: str
    executed_at: str
    recorded_at: str
    advice_id: Optional[str] = None
    extent: Optional[str] = None

    @classmethod
    def create(
        cls,
        *,
        target: str,
        action: str,
        note: str = "",
        executed_at: Optional[str] = None,
        advice_id: Optional[str] = None,
        extent: Optional[str] = None,
        id: Optional[str] = None,
    ) -> "ExecutionRecord":
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            id=id or uuid4().hex,
            advice_id=advice_id,
            target=target,
            action=action,
            extent=extent,
            note=note,
            executed_at=executed_at or now,
            recorded_at=now,
        )

    @classmethod
    def from_dict(cls, data: dict) -> "ExecutionRecord":
        return cls(
            id=str(data.get("id", "")),
            advice_id=data.get("advice_id"),
            target=str(data.get("target", "")),
            action=str(data.get("action", "")),
            extent=data.get("extent"),
            note=str(data.get("note", "")),
            executed_at=str(data.get("executed_at", "")),
            recorded_at=str(data.get("recorded_at", "")),
        )

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("execution.id is required")
        if self.advice_id is not None and not isinstance(self.advice_id, str):
            raise ValueError("execution.advice_id must be a string when present")
        if not isinstance(self.target, str) or not self.target.strip():
            raise ValueError("execution.target must be a non-empty string")
        if self.action not in _EXECUTION_ACTIONS:
            raise ValueError(f"execution.action must be one of {sorted(_EXECUTION_ACTIONS)}")
        if self.action == "none":
            if self.extent is not None:
                raise ValueError("execution.extent must be omitted when action is none")
        elif self.extent not in _EXECUTION_EXTENTS:
            raise ValueError("execution.extent must be full or partial")
        if not isinstance(self.note, str):
            raise ValueError("execution.note must be a string")
        if not self.executed_at:
            raise ValueError("execution.executed_at is required")
        if not self.recorded_at:
            raise ValueError("execution.recorded_at is required")

    def to_dict(self) -> dict:
        data = {
            "id": self.id,
            "advice_id": self.advice_id,
            "target": self.target,
            "action": self.action,
            "note": self.note,
            "executed_at": self.executed_at,
            "recorded_at": self.recorded_at,
        }
        if self.extent is not None:
            data["extent"] = self.extent
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

    # 预测台账摘要（S1-4）
    forecast_summary: dict = field(default_factory=dict)

    # 元信息（带默认值）
    schema_version: int = 11

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
            "forecast_summary": self.forecast_summary,
        }


@dataclass(frozen=True)
class DecisionEnvelope:
    """决策层统一传输协议；所有字段始终存在，缺失值显式为 None。"""

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
