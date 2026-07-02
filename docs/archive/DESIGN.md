> ARCHIVED 2026-07:结论已吸收或废弃,勿作为开发依据。

# stocks-claw v2 重构设计文档

> **版本**: v2.0
> **状态**: 设计完成，待开发执行
> **开发原则**: 干净重构，不做旧版本兼容，不考虑历史包袱，直接以最新最健壮最完整的状态实现
> **目标**: 让 OpenClaw / Hermes / 任何 LLM Agent 能够直接调用 stocks-claw 的金融分析能力

---

## 一、重构宣言

### 1.1 为什么重构

当前 stocks-claw v1 是一个**自主运行的个人投资顾问系统**，设计假设是：
- 系统自己理解用户输入（硬编码中文关键词匹配）
- 系统自己调用 LLM 生成建议
- 系统自己管理对话状态和多轮确认
- 系统通过 OpenClaw cron 定时推送报告到 Feishu

这个设计在 v1 阶段是合理的，但它与"Agent 能力扩展包"的定位存在根本冲突：
- Agent 自己就是 LLM，工具包内部再调 LLM = 双重调用
- Agent 自己做意图识别，工具包的硬编码匹配是多余的
- Agent 自己管理对话，工具包的对话状态管理是冲突的
- 20+ 个细分服务 + 大量中间状态文件，让 CLI 调用变得沉重而脆弱

### 1.2 重构原则

| 原则 | 说明 |
|------|------|
| **不做兼容** | v2 是全新实现，不保留 v1 的任何接口、文件结构或行为 |
| **不考虑历史** | 不迁移 v1 的测试、不保留 v1 的 CLI、不维护 v1 的文档 |
| **干净实现** | 每个模块职责单一，接口清晰，不堆砌功能 |
| **Agent 优先** | 所有设计决策以"Agent 如何调用"为第一优先级 |
| **默认无状态** | 每次调用独立，不依赖中间缓存文件 |
| **默认结构化输出** | 所有接口默认返回 JSON/结构化数据，人类可读是可选的 |

### 1.3 v1 与 v2 的根本区别

| 维度 | v1（当前） | v2（目标） |
|------|-----------|-----------|
| **角色定位** | 自主运行的投资顾问机器人 | Agent 的金融分析能力扩展包 |
| **用户交互** | 用户直接对 stocks-claw 说话 | 用户对 Agent 说话，Agent 调用 stocks-claw |
| **意图识别** | 硬编码中文关键词匹配 | **Agent 做**，stocks-claw 不做 |
| **LLM 调用** | 强制内嵌，系统自己调 LLM | **可选模块**，Agent 可以禁用后自己分析 |
| **状态管理** | 大量中间 JSON 缓存文件 | **默认无状态**，可选有状态 |
| **服务数量** | 20+ 个细分服务 | 5 个核心模块 |
| **输出格式** | 人类可读文本为主 | **默认 JSON 结构化** |
| **接入方式** | 只有 CLI | **CLI / Python API / MCP / HTTP** |
| **定时任务** | OpenClaw cron 驱动 | 系统 cron 调用 CLI，Agent 负责投递 |

---

## 二、总体架构

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Agent 交互适配层 (Adapters) │
│ │
│ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌─────────────┐ │
│ │ CLI 模式 │ │ Python API │ │ MCP 模式 │ │ HTTP API │ │
│ │ (命令行) │ │ (import) │ │ (协议标准) │ │ (本地服务) │ │
│ │ │ │ │ │ │ │ │ │
│ │ $ stocks │ │ import │ │ 工具注册 │ │ POST /api │ │
│ │ query │ │ stocks_claw │ │ 自动发现 │ │ /query │ │
│ │ report │ │ .engine │ │ 参数校验 │ │ /report │ │
│ │ assets │ │ .fetch │ │ 结果返回 │ │ /context │ │
│ └──────┬───────┘ └──────┬───────┘ └──────┬───────┘ └──────┬──────┘ │
│ └───────────────────┴───────────────────┴─────────────────┘ │
│ │ │
└──────────────────────────────┼──────────────────────────────────────────┘
│
统一接口契约 (AnalysisContext)
┌─────────────────────────────────────────────────────────────────────────┐
│ 核心引擎层 (Core Engine) │
│ │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ 数据获取模块 (Data Fetchers) │ │
│ │ fetch_quote(market, code) → Quote │ │
│ │ fetch_quotes(market?) → dict[market, list[Quote]] │ │
│ │ fetch_news(sources?, limit?) → list[NewsItem] │ │
│ │ load_assets() → list[FinancialAsset] │ │
│ │ load_watchlist() → list[Instrument] │ │
│ └─────────────────────────────────────────────────────────────────┘ │
│ │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ 分析脚手架模块 (Analysis Scaffolds) │ │
│ │ analyze_portfolio(assets) → PortfolioMapping │ │
│ │ analyze_market_state(quotes) → MarketState │ │
│ │ detect_drift(mapping, constraints) → list[DriftCheck] │ │
│ │ build_context(options?) → AnalysisContext │ │
│ └─────────────────────────────────────────────────────────────────┘ │
│ │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ LLM 数据增强模块 (LLM Enhancer) — 可选，默认禁用 │ │
│ │ enhance_news(news) → list[EnhancedNewsItem] │ │
│ │ generate_market_summary(quotes) → str (自然语言摘要) │ │
│ │ cross_source_deduplication(news) → list[NewsItem] │ │
│ │ grade_news_quality(news) → list[EnhancedNewsItem] │ │
│ └─────────────────────────────────────────────────────────────────┘ │
│ │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ LLM 驱动分析模块 (LLM Analysis) — 可选，可完全禁用 │ │
│ │ generate_report(context?, model?) → str (Markdown) │ │
│ │ extract_constraints(text) → dict │ │
│ └─────────────────────────────────────────────────────────────────┘ │
│ │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ 数据持久化模块 (Persistence) — 可选，显式调用 │ │
│ │ save_assets(assets) → 写入 financial_assets │ │
│ │ save_report(text) → 写入 reports/ │ │
│ │ save_constraints(constraints) → 写入 financial_assets │ │
│ └─────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
│

┌─────────────────────────────────────────────────────────────────────────┐
│ Provider / Data 层 │
│ │
│ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌───────────────┐ │
│ │ 腾讯财经 │ │ 东方财富 │ │ Finnhub │ │ RSS/GNews/ │ │
│ │ (A股行情) │ │ (A股备用) │ │ (美股行情) │ │ Juhe (新闻) │ │
│ └─────────────┘ └─────────────┘ └─────────────┘ └───────────────┘ │
│ │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ 配置文件 / 数据文件 │ │
│ │ watchlist.json / markets.json / news_sources.json │ │
│ │ financial_assets.json (用户资产) │ │
│ │ .secret/*-key.md (API Key) │ │
│ └─────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 三、核心设计决策

### 3.1 决策 1：谁做意图识别？

**答案：Agent 做。stocks-claw 不做任何意图识别。**

v1 的 `ChatRouterService`、`CommandService`、`AssetMemoryChatService` 全部移除。

Agent 把用户自然语言翻译成精确的功能调用：
- 用户说"紫金矿业今天怎么样" → Agent 调用 `fetch_quote("sh", "601899")`
- 用户说"我买了5万黄金" → Agent 调用 `add_asset(name="黄金ETF", platform="支付宝", amount=50000)`
- 用户说"帮我看看投资建议" → Agent 调用 `build_context()` 然后自己分析

### 3.2 决策 2：谁做最终分析？

**答案：默认 Agent 做。stocks-claw 只提供"数据 + 脚手架"。**

v1 的 `PersonalLLMReportService`、`ThemeAnalysisService`、`AdvisoryService` 等分析生成层全部重构为"可选模块"。

Agent 的三条分析路径：

**路径 A：Agent 自己分析（推荐）**
```python
context = engine.build_context()
# Agent 把 context 喂给自己的 LLM，生成投资建议
```

**路径 B：让 stocks-claw 内部生成（兼容）**
```python
context = engine.build_context()
report = engine.generate_report(context, model="gpt-4")
# Agent 直接把 report 展示给用户
```

**路径 C：混合 — Agent 加工后让 stocks-claw 生成**
```python
context = engine.build_context()
custom_prefix = "用户特别关注黄金板块..."
report = engine.generate_report(context, custom_prompt_prefix=custom_prefix)
```

### 3.3 决策 3：状态管理策略

**答案：默认无状态。每次调用独立，不读写任何中间缓存文件。**

v1 的中间状态文件全部移除：
- `market_quotes.json` — 移除
- `market_state.json` — 移除
- `portfolio_mapping.json` — 移除
- `advisory_plan.json` — 移除
- `news_feed.json` — 移除（新闻直接返回，不缓存）

保留的文件（用户数据 + 配置）：
- `financial_assets.json` — 用户资产（唯一持久化数据）
- `watchlist.json` — 监控标的配置
- `markets.json` — 市场配置
- `news_sources.json` — 新闻源配置
- `.secret/*-key.md` — API Key

可选的持久化（显式调用）：
- `reports/personal-latest.md` — 保存报告（`--save` 显式触发）
- `reports/snapshots/` — 历史快照（`--save` 显式触发）
- `logs/stocks.jsonl` — 运行日志

### 3.4 决策 4：输出格式

**答案：默认 JSON 结构化。人类可读是可选的。**

所有接口默认返回 `dict` / `list` / `dataclass`，通过 `.to_dict()` 序列化为 JSON。

CLI 默认 `--format json`，支持 `--format markdown` 和 `--format text`。

### 3.5 决策 5：LLM 调用策略

**答案：内部 LLM 分为两个独立可选模块，均可完全禁用。**

| 模块 | 职责 | 默认状态 | 说明 |
|------|------|---------|------|
| **LLM Enhancer** | 数据层语义增强（摘要生成、跨源去重、质量分级、行情摘要） | **禁用** | 解决 engine 规则无法处理的语义问题 |
| **LLM Analysis** | 决策层深度分析（生成报告、提取约束） | **禁用** | 替代 Agent 做最终分析 |

v1 的 `personal_llm_report_service.py` 和 `constraint_chat_service.py` 重构为 `engine/llm_analysis.py`。

配置控制：
```yaml
engine:
llm_enhancer:
enabled: false # 默认禁用数据增强
model: "gpt-4o-mini" # 低成本模型，专用于数据增强

llm:
enabled: false # 默认禁用内部 LLM 分析（Agent 自己分析）
# enabled: true # 启用内部 LLM 分析（兼容模式）
```

当 `llm_enhancer.enabled: false` 时：
- `enhance_news()` 直接返回原始新闻数据（无增强）
- `generate_market_summary()` 返回空字符串
- 所有数据以原始格式返回给 Agent

当 `llm.enabled: false` 时：
- `generate_report()` 返回错误提示，建议 Agent 自己分析
- `extract_constraints()` 返回错误提示，建议 Agent 自己提取
- 所有 CLI 的 `--no-llm` 是默认行为

**LLM Enhancer 与 LLM Analysis 的分工边界：**

```
LLM Enhancer（数据层） LLM Analysis（决策层）
↓ ↓
摘要生成、跨源去重、质量分级 投资建议、约束提取、报告生成
↓ ↓
不涉及用户上下文 需要用户上下文
↓ ↓
可被 Agent 替代 可被 Agent 替代
↓ ↓
成本：~$0.005/次 成本：~$0.05/次
```

---

## 四、统一接口契约

### 4.1 核心数据对象

```python
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass(frozen=True)
class Instrument:
"""金融标的"""
code: str
name: str
market: str # "a" / "us"
exchange: Optional[str] = None # "sh" / "sz" / "us"


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
source_name: str # 统一后的来源名称
source_type: str # "rss" | "gnews" | "juhe_235" | "juhe_743"
published_at: Optional[datetime] # 标准化后的时间，解析失败为 None
summary: Optional[str] # 摘要，缺失为 None（不是空字符串）
language: str = "unknown" # "en" | "zh" | "unknown"
tags: list[str] = field(default_factory=list)
raw_metadata: dict = field(default_factory=dict) # 原始字段保留

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
importance: str = "unknown" # high / medium / low
urgency: str = "unknown" # immediate / high / medium / low
category: str = "unknown" # 宏观政策 / 行业动态 / 个股新闻 / 国际市场 / 其他
sentiment: str = "unknown" # positive / negative / neutral
relevance_tags: list[str] = field(default_factory=list)
llm_generated_summary: Optional[str] = None # LLM 生成的摘要（原始缺失时）
enhanced_by_llm: bool = False # 标记是否经过 LLM 增强

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
growth_exposure: str = "none" # high / moderate / light / none
buffer_strength: str = "none" # strong / moderate / light / none
liquidity_status: str = "thin" # ample / adequate / thin
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
risk_appetite: str = "unknown" # risk_on / cooling / broad_risk_off / mixed / unknown
tech_state: str = "unknown" # expanding / under_pressure / soft / mixed / unknown
safe_haven_state: str = "unknown" # strengthening / supported / weakening / unknown
china_state: str = "unknown" # stable_positive / stable / mixed_pressure / under_pressure / unknown
rates_state: str = "unknown" # bonds_bid / rates_pressure / neutral / unknown
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
status: str # within_range / below_min / above_max
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
schema_version: int = 2

# 用户金融记忆（权威输入）
assets: list[FinancialAsset]
asset_count: int
portfolio_constraints: dict
portfolio_profile: dict

# 市场输入
quotes: dict[str, list[Quote]] # 按市场分组的所有行情
news: list[NewsItem] # 原始新闻（或 EnhancedNewsItem）
news_count: int

# LLM 增强输出（当 llm_enhancer.enabled = true 时填充）
market_summary_nl: str = "" # 行情自然语言摘要（LLM 生成）
enhanced_news_count: int = 0 # 增强后的新闻数量

# 轻量脚手架（辅助信号）
market_state: MarketState
portfolio_mapping: PortfolioMapping
drift_checks: list[DriftCheck]

# 历史上下文
recent_snapshots: list[dict] # 最近 N 次报告摘要

# 原始输入（供 LLM 阅读）
raw_prompt_input: str # 人类可读格式的完整上下文文本

# 元信息
llm_enhancer_enabled: bool = False # 本次上下文是否经过 LLM 增强
llm_enhancer_model: str = "" # 使用的增强模型

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
```

### 4.2 核心引擎接口

```python
class StocksEngine:
"""核心引擎 — Agent 可直接调用的统一入口

设计原则：
- 所有方法默认无状态，不读写任何中间缓存文件
- 所有方法返回结构化数据（dataclass），支持 .to_dict() 序列化
- LLM 分析是可选模块，可通过配置完全禁用
"""

def __init__(self, config_path: Optional[str] = None):
"""初始化引擎

Args:
config_path: 配置文件路径，默认使用 stocks/config/engine.yaml
"""
pass

# ========== 数据获取 ==========

def fetch_quote(self, market: str, code: str) -> Quote:
"""获取单只股票/ETF 实时行情

Args:
market: 市场代码，"sh" / "sz" / "us"
code: 证券代码，如 "000300", "AAPL", "QQQ"

Returns:
Quote 对象

Raises:
ResolverError: 标的无法解析
ProviderExhaustedError: 所有 Provider 均失败
"""
pass

def fetch_quotes(self, market: Optional[str] = None) -> dict[str, list[Quote]]:
"""获取监控列表全部行情

Args:
market: 市场代码，None 则获取所有市场

Returns:
{market_key: [Quote, ...], ...}
"""
pass

def fetch_news(self, sources: Optional[list[str]] = None, limit: int = 10) -> list[NewsItem]:
"""获取最新财经新闻

Args:
sources: 新闻源列表，None 则按配置获取所有源
limit: 每源获取条数

Returns:
NewsItem 列表，按时间倒序
"""
pass

def load_assets(self) -> list[FinancialAsset]:
"""加载用户金融资产

Returns:
FinancialAsset 列表
"""
pass

def load_watchlist(self, market: Optional[str] = None) -> list[Instrument]:
"""加载监控标的列表

Args:
market: 市场代码，None 则获取所有市场

Returns:
Instrument 列表
"""
pass

# ========== 分析脚手架 ==========

def analyze_portfolio(self, assets: Optional[list[FinancialAsset]] = None) -> PortfolioMapping:
"""分析组合结构

将资产按桶归类（防守、黄金、成长、A股主题、流动性、长期锁定等），
计算各桶占比，识别主导层。

Args:
assets: 资产列表，None 则自动加载

Returns:
PortfolioMapping 脚手架对象
"""
pass

def analyze_market_state(self, quotes: Optional[dict[str, list[Quote]]] = None) -> MarketState:
"""分析市场状态

基于行情数据计算轻量市场状态信号：
- 风险偏好（risk_on / cooling / broad_risk_off / mixed）
- 科技板块状态
- 避险资产状态
- 中国资产状态

Args:
quotes: 行情数据，None 则自动获取

Returns:
MarketState 脚手架对象
"""
pass

def detect_drift(
self,
mapping: Optional[PortfolioMapping] = None,
constraints: Optional[dict] = None,
) -> list[DriftCheck]:
"""检测组合是否偏离约束目标

对比当前组合占比与用户定义的目标区间，
识别超出范围的资产桶。

Args:
mapping: 组合映射，None 则自动分析
constraints: 约束配置，None 则自动加载

Returns:
DriftCheck 列表
"""
pass

def build_context(
self,
include_news: bool = True,
include_quotes: bool = True,
include_history: bool = True,
) -> AnalysisContext:
"""组装完整分析上下文 — 核心方法

这是 stocks-claw 最重要的方法。
它收集所有数据（资产、行情、新闻），
计算所有脚手架（组合映射、市场状态、偏离检查），
组装成 AnalysisContext 返回给 Agent。

Args:
include_news: 是否包含新闻
include_quotes: 是否包含行情
include_history: 是否包含历史快照

Returns:
AnalysisContext 对象
"""
pass

# ========== LLM 数据增强（可选） ==========

def enhance_news(
self,
news: Optional[list[NewsItem]] = None,
generate_summaries: bool = True,
deduplicate: bool = True,
grade_quality: bool = True,
) -> list[EnhancedNewsItem]:
"""增强新闻数据 — LLM 数据增强模块

使用低成本 LLM（默认 gpt-4o-mini）对新闻数据进行语义层面处理：
1. 为缺失摘要的新闻生成摘要
2. 跨源语义去重
3. 质量分级（importance/urgency/category/sentiment）

如果 llm_enhancer.enabled = False，直接返回原始数据（无增强）。

Args:
news: 新闻列表，None 则自动获取
generate_summaries: 是否生成缺失摘要
deduplicate: 是否跨源去重
grade_quality: 是否质量分级

Returns:
EnhancedNewsItem 列表（或原始 NewsItem 的增强版本）
"""
pass

def generate_market_summary(
self,
quotes: Optional[dict[str, list[Quote]]] = None,
market_state: Optional[MarketState] = None,
) -> str:
"""生成行情自然语言摘要 — LLM 数据增强模块

将结构化行情数据转换为人类可读的市场综述。
这对 Agent 很有价值：Agent 可以直接读取这段文字，
而不需要自己解析 JSON 再组织语言。

如果 llm_enhancer.enabled = False，返回空字符串。

Args:
quotes: 行情数据，None 则自动获取
market_state: 市场状态，None 则自动分析

Returns:
市场综述文本（100-200 字），或空字符串
"""
pass

# ========== LLM 分析（可选） ==========

def generate_report(
self,
context: Optional[AnalysisContext] = None,
model: Optional[str] = None,
custom_prompt_prefix: Optional[str] = None,
) -> str:
"""基于上下文生成 LLM 投资报告

内部调用外部 LLM API，基于 AnalysisContext 生成投资建议。
如果 engine.llm_enabled = False，抛出异常。

Args:
context: 分析上下文，None 则自动构建
model: LLM 模型名，None 则使用默认
custom_prompt_prefix: 自定义 prompt 前缀（Agent 可注入额外上下文）

Returns:
Markdown 格式报告文本

Raises:
RuntimeError: LLM 模块未启用
"""
pass

def extract_constraints(self, text: str) -> dict:
"""从自然语言提取结构化约束

内部调用 LLM 从用户自然语言描述中提取投资约束。
如果 engine.llm_enabled = False，抛出异常。

Args:
text: 自然语言描述，如"成长仓位不超过30%，黄金控制在20%以内"

Returns:
结构化约束字典

Raises:
RuntimeError: LLM 模块未启用
"""
pass

# ========== 数据更新 ==========

def add_asset(self, asset: FinancialAsset) -> FinancialAsset:
"""添加/更新资产

如果同名同平台资产已存在，则更新；否则新增。

Args:
asset: 金融资产对象

Returns:
更新后的 FinancialAsset
"""
pass

def update_constraints(self, constraints: dict) -> dict:
"""更新投资约束

增量更新约束字段，保留未更新的字段。

Args:
constraints: 约束字典

Returns:
更新后的完整约束
"""
pass

# ========== 健康检查 ==========

def health_check(self) -> dict:
"""系统健康检查

检查：
- API Key 是否配置
- 行情 Provider 是否可用
- 新闻源是否可用
- 用户资产是否配置

Returns:
{"status": "ok" | "warning" | "error", "checks": [...]}
"""
pass
```

---

## 五、模块设计

### 5.1 文件结构

```
stocks-claw/
├── stocks/
│ ├── __init__.py
│ ├── __main__.py # python3 -m stocks 入口
│ │
│ ├── engine/ # 核心引擎层
│ │ ├── __init__.py # 导出 StocksEngine
│ │ ├── core.py # StocksEngine 主类
│ │ ├── fetchers.py # 数据获取（quote/news/asset/watchlist）
│ │ ├── scaffolds.py # 分析脚手架（portfolio/market/drift）
│ │ ├── context_builder.py # AnalysisContext 组装
│ │ ├── llm_enhancer.py # 可选 LLM 数据增强（摘要/去重/分级）
│ │ ├── llm_analysis.py # 可选 LLM 分析（report/constraints）
│ │ └── persistence.py # 数据持久化（asset/constraints/report）
│ │
│ ├── adapters/ # 交互适配层
│ │ ├── __init__.py
│ │ ├── cli.py # CLI 统一入口
│ │ ├── mcp.py # MCP 服务器
│ │ └── http.py # HTTP API 服务器（FastAPI）
│ │
│ ├── providers/ # Provider 层
│ │ ├── __init__.py
│ │ ├── base.py # QuoteProvider 抽象基类
│ │ ├── tencent_a.py # 腾讯财经 A股
│ │ ├── eastmoney_a.py # 东方财富 A股（备用）
│ │ ├── finnhub_quote.py # Finnhub 美股
│ │ └── registry.py # ProviderRegistry
│ │
│ ├── domain/ # Domain 层
│ │ ├── __init__.py
│ │ └── models.py # Instrument, Quote, NewsItem, FinancialAsset, ...
│ │
│ ├── config/ # 配置
│ │ ├── __init__.py
│ │ ├── engine.yaml # 引擎配置
│ │ ├── markets.json # 市场配置
│ │ ├── news_sources.json # 新闻源配置
│ │ └── watchlist.json # 监控标的
│ │
│ ├── data/ # 用户数据
│ │ └── financial_assets.json # 用户资产（唯一持久化数据）
│ │
│ ├── prompts/ # LLM Prompts
│ │ └── personal_advice_prompt.txt
│ │
│ ├── errors.py # 异常类
│ ├── logging_utils.py # 日志工具
│ ├── llm_config.py # LLM 配置
│ └── validators.py # 配置校验
│
├── .secret/ # API Key
│ ├── finnhub-key.md
│ ├── gnews-key.md
│ ├── juhe-key.md
│ └── juhe-caijing-key.md
│
├── reports/ # 报告输出（可选持久化）
│ └── snapshots/ # 历史快照
│
├── logs/ # 运行日志
│ └── stocks.jsonl
│
├── tests/ # 测试
│ ├── __init__.py
│ ├── conftest.py # pytest 配置
│ ├── test_engine/ # 引擎层测试
│ │ ├── test_fetchers.py
│ │ ├── test_scaffolds.py
│ │ ├── test_context_builder.py
│ │ └── test_llm_analysis.py
│ ├── test_providers/ # Provider 层测试
│ │ ├── test_tencent_a.py
│ │ ├── test_finnhub_quote.py
│ │ └── test_registry.py
│ └── test_adapters/ # 适配层测试
│ ├── test_cli.py
│ └── test_mcp.py
│
├── AGENT_GUIDE.md # Agent 使用指南
├── ARCHITECTURE_V2.md # 架构设计文档（本文件）
├── DESIGN.md # 重构设计文档（本文件）
├── README.md # 项目说明
└── requirements.txt # Python 依赖
```

### 5.2 模块职责

| 模块 | 文件 | 职责 | 状态 |
|------|------|------|------|
| **核心引擎** | `engine/core.py` | `StocksEngine` 主类，统一入口 | 新建 |
| **数据获取** | `engine/fetchers.py` | `fetch_quote`, `fetch_quotes`, `fetch_news`, `load_assets`, `load_watchlist` | 新建 |
| **分析脚手架** | `engine/scaffolds.py` | `analyze_portfolio`, `analyze_market_state`, `detect_drift` | 新建 |
| **上下文组装** | `engine/context_builder.py` | `build_context` — 核心方法 | 新建 |
| **LLM 数据增强** | `engine/llm_enhancer.py` | `enhance_news`, `generate_market_summary` — 可选 | 新建 |
| **LLM 分析** | `engine/llm_analysis.py` | `generate_report`, `extract_constraints` — 可选 | 新建 |
| **持久化** | `engine/persistence.py` | `save_assets`, `save_constraints`, `save_report` — 显式调用 | 新建 |
| **CLI 适配** | `adapters/cli.py` | 统一 CLI 入口，单进程执行 | 新建 |
| **MCP 适配** | `adapters/mcp.py` | MCP 服务器，工具注册 | 新建 |
| **HTTP 适配** | `adapters/http.py` | FastAPI 服务 | 新建 |
| **Provider 基类** | `providers/base.py` | `QuoteProvider` 抽象基类 | 保留 |
| **腾讯财经** | `providers/tencent_a.py` | A股行情获取 | 保留 |
| **东方财富** | `providers/eastmoney_a.py` | A股行情备用 | 保留 |
| **Finnhub** | `providers/finnhub_quote.py` | 美股行情 | 保留 |
| **Provider 注册** | `providers/registry.py` | `ProviderRegistry` | 保留 |
| **Domain 模型** | `domain/models.py` | 所有 dataclass | 保留 |
| **异常** | `errors.py` | 异常类体系 | 保留 |
| **日志** | `logging_utils.py` | JSONL 日志 | 保留 |
| **LLM 配置** | `llm_config.py` | LLM 连接配置 | 保留 |
| **配置校验** | `validators.py` | 配置校验 | 保留 |

### 5.3 移除的模块（v1 → v2）

| v1 模块 | 移除原因 |
|---------|---------|
| `services/chat_router_service.py` | Agent 自己做路由 |
| `services/command_service.py` | Agent 自己翻译命令 |
| `services/asset_memory_chat_service.py` | Agent 自己管理对话 |
| `services/constraint_chat_service.py` | 重构为 `engine/llm_analysis.py` |
| `services/personal_llm_report_service.py` | 重构为 `engine/llm_analysis.py` |
| `services/report_assembly_service.py` | 重构为 `engine/context_builder.py` |
| `services/personal_insight_service.py` | 重构为 `engine/context_builder.py` |
| `services/market_state_service.py` | 重构为 `engine/scaffolds.py` |
| `services/portfolio_mapping_service.py` | 重构为 `engine/scaffolds.py` |
| `services/advisory_service.py` | 移除，分析由 Agent LLM 或 `engine/llm_analysis.py` 完成 |
| `services/theme_analysis_service.py` | 移除，主题分析由 LLM 完成 |
| `services/market_signal_service.py` | 移除，合并到 `engine/scaffolds.py` |
| `services/market_data_service.py` | 重构为 `engine/fetchers.py` |
| `services/news_fetch_service.py` | 重构为 `engine/fetchers.py` |
| `services/news_input_service.py` | 移除，新闻直接返回不缓存 |
| `services/financial_memory_service.py` | 重构为 `engine/persistence.py` |
| `services/asset_update_service.py` | 重构为 `engine/core.py::add_asset` |
| `services/query_service.py` | 重构为 `engine/fetchers.py` |
| `services/provider_service.py` | 重构为 `providers/registry.py` |
| `services/resolver_service.py` | 重构为 `engine/fetchers.py` |
| `services/watchlist_service.py` | 重构为 `engine/fetchers.py` |
| `services/watchlist_generator.py` | 移除 |
| `services/health_check_service.py` | 重构为 `engine/core.py::health_check` |
| `services/event_log_service.py` | 移除，日志直接走 `logging_utils.py` |
| `services/quote_guard.py` | 保留，合并到 Provider 层 |
| `cli/chat_route.py` | 移除，Agent 自己做路由 |
| `cli/handle_command.py` | 移除，Agent 自己翻译命令 |
| `cli/send_llm_report.py` | 重构为 `adapters/cli.py` |
| `cli/build_personal_report.py` | 移除，调试工具 |
| `cli/build_personal_llm_report.py` | 移除，调试工具 |
| `cli/financial_memory.py` | 重构为 `adapters/cli.py` |
| `cli/health_check.py` | 重构为 `adapters/cli.py` |
| `cli/refresh_news.py` | 重构为 `adapters/cli.py` |
| `cli/validate_config.py` | 重构为 `adapters/cli.py` |
| `cli/tail_logs.py` | 重构为 `adapters/cli.py` |
| `cli/personal_insight_context.py` | 移除，调试工具 |
| `scripts/personal-report-delivery.sh` | 重构为系统 cron 调用 CLI |
| `scripts/query_stock.py` | 移除，功能合并到 `engine/fetchers.py` |
| `config_loader.py` | 重构为 `config/__init__.py` |

---

## 六、多模式交互设计

### 6.1 CLI 模式

```bash
# 安装后可用
pip install -e .
stocks --help

# 或直接从源码运行
python3 -m stocks --help
```

**子命令：**

```bash
# 数据查询
stocks query <code> [--market <market>] [--format json|text]
stocks quotes [--market <market>] [--format json]
stocks news [--limit <n>] [--sources <source1,source2>] [--format json]
stocks assets [--format json]
stocks watchlist [--market <market>] [--format json]

# 分析脚手架
stocks analyze portfolio [--format json]
stocks analyze market [--format json]
stocks analyze drift [--format json]

# 核心：组装完整上下文
stocks context [--format json] [--no-news] [--no-quotes] [--no-history]

# LLM 分析（可选，需启用 LLM 模块）
stocks report [--format markdown|json] [--model <model>] [--save]
stocks report --no-llm # 只返回 AnalysisContext，不调用 LLM
stocks constraints extract "<text>" # 从自然语言提取约束

# 数据更新
stocks assets add --name <name> --platform <platform> --amount <amount> [--type <type>] [--notes <notes>]
stocks constraints update --json '<json>'
stocks constraints update --from-text "<text>" # 需启用 LLM 模块

# 健康检查
stocks health [--format json]

# 配置
stocks config validate
stocks config show
```

**全局选项：**

```bash
--config <path> # 指定配置文件
--no-cache # 禁用所有缓存，完全实时获取
--llm-enabled # 临时启用 LLM 模块（覆盖配置）
--llm-disabled # 临时禁用 LLM 模块（覆盖配置）
--format json # 默认 JSON 输出
--format markdown # Markdown 输出（人类可读）
--format text # 纯文本输出
-v, --verbose # 详细输出
```

### 6.2 Python API 模式

```python
from stocks.engine import StocksEngine

# 初始化
engine = StocksEngine()
# 或指定配置
engine = StocksEngine(config_path="/path/to/engine.yaml")

# 模式 A：Agent 自己分析（推荐）
context = engine.build_context()

# Agent 把 context 喂给自己的 LLM
agent_prompt = f"""
你是一位投资顾问。基于以下数据给出建议：

资产：{context.assets}
行情：{context.quotes}
新闻：{context.news}
组合：{context.portfolio_mapping}
市场：{context.market_state}
偏离：{context.drift_checks}

请给出投资建议。
"""
report = agent_llm.generate(agent_prompt)

# 模式 B：让 stocks-claw 内部生成（兼容）
report = engine.generate_report(context, model="gpt-4")

# 模式 C：混合
context = engine.build_context()
custom_prefix = "用户特别关注黄金板块..."
report = engine.generate_report(context, custom_prompt_prefix=custom_prefix)

# 数据获取
quote = engine.fetch_quote("sh", "601899")
quotes = engine.fetch_quotes("a")
news = engine.fetch_news(limit=5)
assets = engine.load_assets()

# 分析脚手架
mapping = engine.analyze_portfolio(assets)
state = engine.analyze_market_state(quotes)
drift = engine.detect_drift(mapping)

# 数据更新
engine.add_asset(FinancialAsset(
name="华安黄金ETF",
platform="支付宝",
amount=50000,
asset_type="黄金ETF",
))

# 健康检查
health = engine.health_check()
```

### 6.3 MCP 模式

```python
# stocks/adapters/mcp.py
from mcp.server import Server
from stocks.engine import StocksEngine

server = Server("stocks-claw")
engine = StocksEngine()

@server.tool()
def fetch_quote(market: str, code: str) -> dict:
"""获取股票/ETF 实时行情

Args:
market: 市场代码，如 "sh"(上海A股), "sz"(深圳A股), "us"(美股)
code: 证券代码，如 "000300", "AAPL", "QQQ"
"""
return engine.fetch_quote(market, code).to_dict()

@server.tool()
def build_analysis_context() -> dict:
"""组装完整的金融分析上下文

包含用户资产、市场行情、新闻和组合分析脚手架。
返回的上下文可以直接用于 LLM 分析。
"""
return engine.build_context().to_dict()

@server.tool()
def generate_investment_report(model: str | None = None) -> str:
"""生成个人投资分析报告

基于用户资产、市场行情和新闻，调用 LLM 生成投资建议。
如果 model 未指定，使用默认模型。
"""
return engine.generate_report(model=model)

@server.tool()
def list_assets() -> list[dict]:
"""查看用户金融资产列表"""
return [a.to_dict() for a in engine.load_assets()]

@server.tool()
def add_asset(
name: str,
platform: str,
amount: float,
asset_type: str = "unknown",
notes: str | None = None,
) -> dict:
"""添加或更新金融资产"""
asset = FinancialAsset(
name=name,
platform=platform,
amount=amount,
asset_type=asset_type,
notes=notes,
)
return engine.add_asset(asset).to_dict()

@server.tool()
def health_check() -> dict:
"""系统健康检查"""
return engine.health_check()
```

Agent 配置（Claude Desktop）：
```json
{
"mcpServers": {
"stocks-claw": {
"command": "python3",
"args": ["-m", "stocks.adapters.mcp"]
}
}
}
```

### 6.4 HTTP API 模式

```python
# stocks/adapters/http.py
from fastapi import FastAPI
from stocks.engine import StocksEngine

app = FastAPI(title="stocks-claw API", version="2.0")
engine = StocksEngine()

@app.get("/api/quote/{market}/{code}")
def get_quote(market: str, code: str):
return engine.fetch_quote(market, code).to_dict()

@app.get("/api/quotes")
def get_quotes(market: str | None = None):
return {k: [q.to_dict() for q in v] for k, v in engine.fetch_quotes(market).items()}

@app.get("/api/news")
def get_news(limit: int = 10, sources: str | None = None):
source_list = sources.split(",") if sources else None
return [n.to_dict() for n in engine.fetch_news(source_list, limit)]

@app.get("/api/assets")
def get_assets():
return [a.to_dict() for a in engine.load_assets()]

@app.post("/api/assets")
def post_asset(asset: dict):
return engine.add_asset(FinancialAsset(**asset)).to_dict()

@app.get("/api/context")
def get_context(
include_news: bool = True,
include_quotes: bool = True,
include_history: bool = True,
):
return engine.build_context(
include_news=include_news,
include_quotes=include_quotes,
include_history=include_history,
).to_dict()

@app.post("/api/report")
def post_report(
model: str | None = None,
custom_context: dict | None = None,
custom_prompt_prefix: str | None = None,
):
context = AnalysisContext.from_dict(custom_context) if custom_context else None
return {
"report": engine.generate_report(
context=context,
model=model,
custom_prompt_prefix=custom_prompt_prefix,
)
}

@app.get("/api/health")
def get_health():
return engine.health_check()
```

启动：
```bash
python3 -m stocks.adapters.http --port 8787
# 或
uvicorn stocks.adapters.http:app --port 8787
```

---

## 七、配置设计

### 7.1 引擎配置

```yaml
# stocks/config/engine.yaml
engine:
# 版本
version: 2.0

# 缓存策略
cache:
enabled: false # 默认禁用缓存（无状态模式）
quote_ttl: 1800 # 行情缓存有效期（秒）
news_ttl: 7200 # 新闻缓存有效期（秒）

# LLM 数据增强配置（低成本模型，用于数据预处理）
llm_enhancer:
enabled: false # 默认禁用数据增强
model: "gpt-4o-mini" # 低成本模型，专用于数据增强
url: "http://localhost:11434/v1/chat/completions" # LLM API 端点
api_key: ""
timeout: 30 # 增强操作超时（秒）
temperature: 0.3 # 低温度，减少随机性

# 功能开关
features:
generate_missing_summaries: true # 为缺失摘要的新闻生成摘要
cross_source_deduplication: true # 跨源语义去重
quality_grading: true # 新闻质量分级
natural_language_summary: true # 行情自然语言摘要

# 成本控制
limits:
max_llm_calls_per_request: 20 # 每次请求最多调用 LLM 次数
max_news_items_to_process: 15 # 最多处理多少条新闻
cache_ttl_seconds: 3600 # LLM 结果缓存 1 小时

# LLM 分析配置（决策层，用于生成报告和提取约束）
llm:
enabled: false # 默认禁用内部 LLM 分析（Agent 自己分析）
default_model: "gpt-5.4"
fallback_model: "kimi-k2.5"
url: "http://localhost:11434/v1/chat/completions"
api_key: ""
timeout: 120
max_tokens: 1800
temperature: 0.6

# 报告配置
report:
save_to_file: false # 默认不保存报告到文件
history_limit: 10 # 保留历史快照数量
dedup_cooldown_minutes: 60 # 重复报告冷却时间
output_dir: "reports" # 报告输出目录

# 日志配置
logging:
enabled: true
level: "info" # debug / info / warning / error
file: "logs/stocks.jsonl"

# 数据配置
data:
assets_file: "stocks/data/financial_assets.json"
watchlist_file: "stocks/config/watchlist.json"
markets_file: "stocks/config/markets.json"
news_sources_file: "stocks/config/news_sources.json"
```

### 7.2 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `STOCKS_CONFIG_PATH` | `stocks/config/engine.yaml` | 配置文件路径 |
| `STOCKS_LLM_ENHANCER_ENABLED` | `false` | 是否启用 LLM 数据增强 |
| `STOCKS_LLM_ENHANCER_MODEL` | `gpt-4o-mini` | 数据增强模型 |
| `STOCKS_LLM_ENABLED` | `false` | 是否启用内部 LLM 分析 |
| `STOCKS_LLM_MODEL` | `gpt-5.4` | 默认 LLM 模型 |
| `STOCKS_LLM_FALLBACK` | `kimi-k2.5` | Fallback 模型 |
| `STOCKS_LLM_URL` | `http://localhost:11434/v1/chat/completions` | LLM API 端点 |
| `STOCKS_LLM_API_KEY` | `''` | LLM API Key |
| `STOCKS_CACHE_ENABLED` | `false` | 是否启用缓存 |
| `STOCKS_LOG_LEVEL` | `info` | 日志级别 |

---

## 八、开发执行计划

### Phase 1: 基础设施（1 天）

1. 创建 `stocks/engine/`, `stocks/adapters/`, `stocks/domain/` 目录
2. 重构 `domain/models.py` — 定义所有 dataclass + `.to_dict()`
3. 重构 `errors.py` — 精简异常类
4. 重构 `config/` — 新增 `engine.yaml` 配置
5. 重构 `logging_utils.py` — 支持配置化日志级别
6. 更新 `requirements.txt` — 新增 `fastapi`, `uvicorn`, `mcp` 等可选依赖

### Phase 2: Provider 层（1 天）

1. 保留 `providers/base.py` — `QuoteProvider` 抽象基类
2. 保留 `providers/tencent_a.py` — 腾讯财经
3. 保留 `providers/eastmoney_a.py` — 东方财富
4. 保留 `providers/finnhub_quote.py` — Finnhub
5. 重构 `providers/registry.py` — 简化注册表逻辑
6. 移除 `provider_service.py` 的复杂 fallback 逻辑，简化到 `registry.py`

### Phase 3: 核心引擎层（2-3 天）

1. `engine/fetchers.py` — 实现数据获取
- `fetch_quote()`, `fetch_quotes()`, `fetch_news()`, `load_assets()`, `load_watchlist()`
- 从 v1 的 `query_service.py`, `market_data_service.py`, `news_fetch_service.py` 提取核心逻辑
- 移除所有中间状态文件写入

2. `engine/scaffolds.py` — 实现分析脚手架
- `analyze_portfolio()` — 从 v1 `portfolio_mapping_service.py` 提取核心逻辑
- `analyze_market_state()` — 从 v1 `market_state_service.py` 提取核心逻辑
- `detect_drift()` — 从 v1 `advisory_service.py` 提取约束检查逻辑
- 只做轻量归纳，不写入任何文件

3. `engine/context_builder.py` — 实现上下文组装
- `build_context()` — 核心方法
- 组装 `AnalysisContext` 对象
- 集成 `llm_enhancer` 模块（如果启用）
- 生成 `raw_prompt_input` 人类可读文本

4. `engine/llm_enhancer.py` — 实现 LLM 数据增强模块
- `enhance_news()` — 摘要生成、跨源去重、质量分级
- `generate_market_summary()` — 行情自然语言摘要
- 使用低成本模型（gpt-4o-mini），批量调用减少 API 次数
- 失败时降级为原始数据（不抛异常）

5. `engine/llm_analysis.py` — 实现可选 LLM 分析
- `generate_report()` — 从 v1 `personal_llm_report_service.py` 提取
- `extract_constraints()` — 从 v1 `constraint_chat_service.py` 提取
- 检查 `llm_enabled` 配置，禁用时抛出异常

6. `engine/persistence.py` — 实现数据持久化
- `save_assets()`, `save_constraints()`, `save_report()`
- 所有写入操作显式调用，不隐式触发

7. `engine/core.py` — 实现 `StocksEngine` 主类
- 组合所有子模块（包括 `llm_enhancer`）
- 实现统一接口
- `health_check()`

### Phase 4: 适配层（2 天）

1. `adapters/cli.py` — CLI 统一入口
- 使用 `argparse` 实现所有子命令
- 单进程执行，不 subprocess
- 默认 `--format json`
- 支持全局选项 `--no-cache`, `--llm-enabled`, `--config`

2. `adapters/mcp.py` — MCP 服务器
- 暴露核心工具函数
- 工具描述文档完善

3. `adapters/http.py` — HTTP API 服务器
- FastAPI 实现
- 所有端点返回 JSON
- 健康检查端点

### Phase 5: 测试（1-2 天）

1. `tests/test_engine/test_fetchers.py` — 测试数据获取
2. `tests/test_engine/test_scaffolds.py` — 测试分析脚手架
3. `tests/test_engine/test_context_builder.py` — 测试上下文组装
4. `tests/test_engine/test_llm_analysis.py` — 测试 LLM 分析（mock）
5. `tests/test_providers/` — 保留现有 Provider 测试
6. `tests/test_adapters/test_cli.py` — 测试 CLI
7. `tests/test_adapters/test_mcp.py` — 测试 MCP

### Phase 6: 文档（1 天）

1. 重写 `AGENT_GUIDE.md` — Agent 使用指南
2. 重写 `README.md` — 项目说明
3. 保留 `ARCHITECTURE_V2.md` — 架构文档
4. 保留 `DESIGN.md` — 本设计文档

---

## 八、架构边界：信息源管理与内容压缩

### 8.1 问题定义

用户提出的核心问题是：**信息源管理**和**内容压缩精简提炼**这两个职责，应该由 engine 内部处理，还是交给上游 Agent？

这是一个关键的架构边界划分问题，直接影响 engine 的复杂度、Agent 的调用成本、以及系统的可维护性。

### 8.2 信息源管理 —— 由 engine 负责

**信息源管理**包括：
- 配置哪些数据源（Finnhub、腾讯、GNews、Juhe 等）
- API Key 管理与安全存储
- 数据源健康监控与可用性检测
- 数据源降级与 fallback 策略
- 配额管理与速率限制

**决策：信息源管理由 engine 负责，Agent 只通过抽象接口调用。**

**理由：**

| 维度 | engine 负责的优势 | 交给 Agent 的问题 |
|------|------------------|------------------|
| **专业知识** | engine 了解每个 API 的配额、格式、可靠性、fallback 逻辑 | Agent 需要知道 Finnhub 的 rate limit、腾讯 API 的字段映射，超出其能力范围 |
| **稳定性** | engine 可以自动降级（如 Finnhub 失败时切到腾讯），Agent 无感知 | Agent 需要处理每个 API 的失败场景，调用逻辑极其复杂 |
| **安全性** | API Key 由 engine 统一管理，Agent 不接触密钥 | Agent 需要传递或管理密钥，增加泄露风险 |
| **一致性** | 所有 Agent 共享同一套数据源配置和策略 | 每个 Agent 可能配置不同，导致行为不一致 |
| **演进性** | 新增数据源只需改 engine，Agent 无感知 | 新增数据源需要所有 Agent 更新 prompt 或代码 |

**engine 的抽象接口：**

```python
# engine/fetchers.py — Agent 不需要知道具体数据源
class DataFetcher:
def get_quotes(self, symbols: list[str]) -> list[Quote]:
"""获取行情，engine 内部决定用 Finnhub 还是腾讯"""
pass

def get_news(self, query: str, limit: int = 10) -> list[NewsItem]:
"""获取新闻，engine 内部决定用 GNews 还是 Juhe"""
pass
```

### 8.3 内容压缩 —— 分层处理，engine 主导

**内容压缩**不是单一职责，需要分层看：

| 层级 | 职责 | 归属 | 说明 |
|------|------|------|------|
| **L1 原始数据清洗** | 去重、格式标准化、错误过滤、空值处理 | **engine** | 数据质量是 engine 的专业领域 |
| **L2 轻量归纳** | 行情聚合、组合映射、约束偏离检测 | **engine** | scaffolds 模块的核心职责 |
| **L3 内容压缩策略** | 根据 token 预算裁剪数据、选择输出粒度 | **engine 提供策略，Agent 选择级别** | 见下方详细设计 |
| **L3.5 LLM 数据增强** | 摘要生成、跨源去重、质量分级、行情摘要 | **engine 可选模块（LLM Enhancer）** | 用低成本 LLM 做规则做不到的事 |
| **L4 深度分析** | 投资建议、主题判断、情感分析 | **Agent 或 engine 的 LLM 模块** | 默认 Agent 做，可选 engine 做 |

#### 8.3.1 为什么 L3 压缩策略需要 engine 主导？

**核心矛盾：**
- LLM 上下文窗口有限（如 4K/8K/128K），Agent 不能把 engine 返回的所有原始数据都喂给 LLM
- 但压缩策略需要了解**数据的结构和重要性**，这是 engine 的专业领域
- 同时，Agent 知道自己的 LLM 能力（上下文窗口大小）

**最佳设计：engine 提供"压缩策略"和"多粒度输出"，Agent 选择压缩级别或指定 token 预算。**

#### 8.3.2 三级输出粒度设计

engine 提供三级输出，Agent 根据场景选择：

| 级别 | 名称 | 内容 | 适用场景 | 预估 token |
|------|------|------|----------|-----------|
| **compact** | 极简 | 只含：组合总览（市值/盈亏/偏离）、关键约束偏离、3-5 条重要新闻标题 | Agent 快速判断、移动端、token 紧张 | ~500 |
| **standard** | 标准 | 含：完整 quotes（用户持仓）、market_state、portfolio_mapping、drift_checks、新闻摘要（10 条） | 日常分析、默认推荐 | ~2000 |
| **full** | 完整 | 含：所有原始数据、完整新闻内容、历史快照、raw_prompt_input | 深度分析、生成报告 | ~8000+ |

#### 8.3.3 build_context() 参数设计

```python
# engine/context_builder.py
class ContextBuilder:
def build_context(
self,
# Agent 只需选择级别，engine 自动处理压缩逻辑
detail_level: Literal["compact", "standard", "full"] = "standard",

# 高级：Agent 可精确控制（可选）
max_news_items: Optional[int] = None,
include_raw_quotes: bool = False,
include_historical_snapshots: bool = False,
max_token_budget: Optional[int] = None, # engine 尽量压缩到该预算内

# 始终包含的核心数据（不受压缩影响）
assets: list[Asset],
constraints: list[Constraint],
) -> AnalysisContext:
"""
构建分析上下文。

压缩逻辑由 engine 内部实现：
- compact: 只取 quotes 中涨跌幅最大的前 5 个，新闻只取标题
- standard: 完整 quotes，新闻取标题+摘要，market_state 完整
- full: 所有数据，包括原始 API 响应的完整内容

如果指定 max_token_budget，engine 会动态调整：
- 先按 detail_level 生成
- 如果超出预算，逐级降级（full → standard → compact）
- 如果 compact 仍超出，只保留核心约束和组合总览
"""
pass
```

#### 8.3.4 压缩逻辑示例

```python
# engine/context_builder.py 内部实现

def _compress_quotes(quotes: list[Quote], level: str) -> list[Quote]:
if level == "compact":
# 只保留：用户持仓中涨跌幅最大的前 5 个 + 大盘指数
return sorted(quotes, key=lambda q: abs(q.change_pct), reverse=True)[:5]
elif level == "standard":
# 保留所有用户持仓 + 大盘指数
return quotes
else: # full
# 保留所有，包括非持仓的关注股票
return quotes

def _compress_news(news: list[NewsItem], level: str) -> list[NewsItem]:
if level == "compact":
# 只保留标题，去掉正文
return [{"title": n.title, "source": n.source} for n in news[:5]]
elif level == "standard":
# 保留标题+摘要，前 10 条
return [{"title": n.title, "summary": n.summary, "source": n.source}
for n in news[:10]]
else: # full
# 保留完整内容
return news
```

### 8.4 职责边界总结

| 职责 | engine | Agent |
|------|--------|-------|
| **信息源配置** | 管理数据源、API Key、fallback | 不接触 |
| **数据获取** | 调用所有 API | 不直接调用 |
| **原始数据清洗** | 去重、格式化、错误过滤 | 不处理 |
| **轻量归纳** | scaffolds（组合映射、偏离检测） | 不处理 |
| **压缩策略** | 提供 compact/standard/full 三级 | 选择级别或指定 token 预算 |
| **深度分析** | 默认不做（LLM 模块可选） | 默认由 Agent 做 |
| **最终输出** | 结构化 JSON | 人类可读文本（如果需要） |

### 8.5 对现有设计的影响

1. **context_builder.py** 需要增加 `detail_level` 参数和压缩逻辑
2. **CLI 适配层** 需要增加 `--detail` 选项（compact/standard/full）
3. **MCP 工具描述** 需要说明三级输出的区别，帮助 Agent 选择
4. **AGENT_GUIDE.md** 需要增加"如何选择 detail_level"的指南

---

## 九、关键设计决策总结

| 决策 | 选择 | 理由 |
|------|------|------|
| 意图识别 | **Agent 做** | Agent 本身就是 LLM，比硬编码关键词强得多 |
| 最终分析 | **默认 Agent 做，可选 stocks-claw 做** | 避免双重 LLM 调用，Agent 更了解用户上下文 |
| 状态管理 | **默认无状态，可选有状态** | CLI 调用应该是独立的，不依赖历史文件 |
| 输出格式 | **默认 JSON 结构化** | Agent 直接解析，不需要字符串处理 |
| 中间文件 | **默认不写入，显式调用才写入** | 无状态模式下不污染文件系统 |
| LLM 调用 | **可选模块，默认禁用** | Agent 可以自己替代 |
| 服务层 | **20+ 个 → 5 个核心模块** | 去除过度设计，聚焦核心能力 |
| 接入模式 | **CLI + Python API + MCP + HTTP** | 覆盖所有 Agent 使用场景 |
| 旧版本兼容 | **不做** | 干净重构，v1 代码完全移除 |
| 历史迁移 | **不考虑** | 重新实现，不迁移 v1 的测试和 CLI |

---

## 十、验收标准

### 10.1 功能验收

- [ ] `python3 -m stocks query 000300 --market sh --format json` 返回正确 JSON
- [ ] `python3 -m stocks context --format json` 返回完整 AnalysisContext
- [ ] `python3 -m stocks context --llm-enhancer --format json` 返回带增强字段的 AnalysisContext
- [ ] `python3 -m stocks report --format markdown` 生成人类可读报告（LLM 启用时）
- [ ] `python3 -m stocks report --no-llm` 返回 AnalysisContext，不调用 LLM
- [ ] `python3 -m stocks assets add --name "黄金ETF" --platform "支付宝" --amount 50000` 成功添加资产
- [ ] `python3 -m stocks health --format json` 返回健康状态
- [ ] MCP 模式下，Claude Desktop 能自动发现所有工具
- [ ] HTTP API 模式下，`curl /api/context` 返回正确 JSON
- [ ] LLM Enhancer 启用时，Juhe 源新闻有 LLM 生成的摘要
- [ ] LLM Enhancer 启用时，跨源重复新闻被正确去重
- [ ] LLM Enhancer 启用时，新闻有 importance/urgency 质量标签

### 10.2 架构验收

- [ ] 无 `services/` 目录（v1 的服务层完全移除）
- [ ] 无中间状态文件（`market_quotes.json`, `market_state.json` 等不存在）
- [ ] 所有接口默认返回 JSON
- [ ] 所有方法支持无状态模式
- [ ] LLM 模块默认禁用
- [ ] 代码覆盖率 > 80%

### 10.3 Agent 验收

- [ ] OpenClaw Agent 能通过 CLI 调用所有功能
- [ ] Hermes Agent 能通过 Python API 调用所有功能
- [ ] Claude Desktop 能通过 MCP 调用所有功能
- [ ] 任何 Agent 都能通过 HTTP API 调用所有功能

---

*文档版本: v2.0*
*编写日期: 2026-06-05*
*状态: 设计完成，待开发执行*
*开发原则: 干净重构，不做旧版本兼容，不考虑历史问题，直接以最新最健壮最完整的状态实现*
> ARCHIVED 2026-07:结论已吸收或废弃,勿作为开发依据。
