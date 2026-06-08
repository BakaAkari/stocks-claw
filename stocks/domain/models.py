from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass(frozen=True)
class Instrument:
    """金融标的"""
    code: str
    name: str
    market: str                    # "a" / "us"
    exchange: Optional[str] = None  # "sh" / "sz" / "us"


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
    
    def to_dict(self) -> dict:
        return {
            "instrument": {
                "code": self.instrument.code,
                "name": self.instrument.name,
                "market": self.instrument.market,
                "exchange": self.instrument.exchange,
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
    raw_metadata: dict = field(default_factory=dict)  # 原始字段保留
    
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
            # raw_metadata 不序列化，避免输出过大
        }


@dataclass(frozen=True)
class EnhancedNewsItem(NewsItem):
    """增强后的新闻条目 — 包含 LLM Enhancer 生成的附加字段
    
    当 llm_enhancer.enabled = true 时，engine 返回此类型替代 NewsItem。
    """
    importance: str = "unknown"         # high / medium / low
    urgency: str = "unknown"            # immediate / high / medium / low
    category: str = "unknown"           # 宏观政策 / 行业动态 / 个股新闻 / 国际市场 / 其他
    sentiment: str = "unknown"          # positive / negative / neutral
    relevance_tags: list[str] = field(default_factory=list)
    llm_generated_summary: Optional[str] = None  # LLM 生成的摘要（原始缺失时）
    enhanced_by_llm: bool = False      # 标记是否经过 LLM 增强
    
    def to_dict(self) -> dict:
        base = super().to_dict()
        base.update({
            "importance": self.importance,
            "urgency": self.urgency,
            "category": self.category,
            "sentiment": self.sentiment,
            "relevance_tags": self.relevance_tags,
            "llm_generated_summary": self.llm_generated_summary,
            "enhanced_by_llm": self.enhanced_by_llm,
        })
        return base


@dataclass(frozen=True)
class FinancialAsset:
    """金融资产"""
    name: str
    platform: str
    amount: float
    asset_type: str = "unknown"
    notes: Optional[str] = None
    confirmed: bool = True
    
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "platform": self.platform,
            "amount": self.amount,
            "asset_type": self.asset_type,
            "notes": self.notes,
            "confirmed": self.confirmed,
        }


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
    risk_appetite: str = "unknown"         # risk_on / cooling / broad_risk_off / mixed / unknown
    tech_state: str = "unknown"           # expanding / under_pressure / soft / mixed / unknown
    safe_haven_state: str = "unknown"     # strengthening / supported / weakening / unknown
    china_state: str = "unknown"          # stable_positive / stable / mixed_pressure / under_pressure / unknown
    rates_state: str = "unknown"          # bonds_bid / rates_pressure / neutral / unknown
    cross_asset_summary: list[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "risk_appetite": self.risk_appetite,
            "tech_state": self.tech_state,
            "safe_haven_state": self.safe_haven_state,
            "china_state": self.china_state,
            "rates_state": self.rates_state,
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
    news: list[NewsItem]                   # 原始新闻（或 EnhancedNewsItem）
    news_count: int
    
    # 轻量脚手架（辅助信号）
    market_state: MarketState
    portfolio_mapping: PortfolioMapping
    drift_checks: list[DriftCheck]
    
    # 历史上下文
    recent_snapshots: list[dict]         # 最近 N 次报告摘要
    
    # 原始输入（供 LLM 阅读）
    raw_prompt_input: str                # 人类可读格式的完整上下文文本
    
    # LLM 增强输出（当 llm_enhancer.enabled = true 时填充）
    market_summary_nl: str = ""            # 行情自然语言摘要（LLM 生成）
    enhanced_news_count: int = 0           # 增强后的新闻数量
    
    # 元信息（带默认值）
    schema_version: int = 2
    llm_enhancer_enabled: bool = False   # 本次上下文是否经过 LLM 增强
    llm_enhancer_model: str = ""         # 使用的增强模型
    
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
            "market_summary_nl": self.market_summary_nl,
            "enhanced_news_count": self.enhanced_news_count,
            "market_state": self.market_state.to_dict(),
            "portfolio_mapping": self.portfolio_mapping.to_dict(),
            "drift_checks": [d.to_dict() for d in self.drift_checks],
            "recent_snapshots": self.recent_snapshots,
            "raw_prompt_input": self.raw_prompt_input,
            "llm_enhancer_enabled": self.llm_enhancer_enabled,
            "llm_enhancer_model": self.llm_enhancer_model,
        }
