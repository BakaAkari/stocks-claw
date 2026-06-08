# stocks-claw v2 架构设计 — Agent 可驱动的金融分析引擎

> 设计目标：让 OpenClaw / Hermes / 任何 LLM Agent 能够直接调用 stocks-claw 的能力，或驱动其内部 LLM 完成金融资产分析。

---

## 一、设计原则

### 1.1 核心原则

| 原则 | 说明 |
|------|------|
| **Agent 是主脑** | stocks-claw 是 Agent 的"金融分析能力扩展包"，不是独立智能体 |
| **能力分层暴露** | Agent 可以选择"只拿数据"、"拿脚手架"或"拿完整报告" |
| **LLM 调用可选** | 内部 LLM 分析是可选能力，Agent 可以禁用后自己分析 |
| **多模式接入** | 同一套核心能力，通过 CLI / Python API / MCP / HTTP 多种模式暴露 |
| **状态可控** | 支持完全无状态（Agent 管理状态）和有状态（本地缓存）两种模式 |
| **渐进迁移** | 不推翻重写，逐步重构，保持现有功能可用 |

### 1.2 关键决策

**决策 1：谁来做意图识别？**
- **Agent 做**。stocks-claw 不再包含任何自然语言意图识别层。
- Agent 把用户自然语言翻译成精确的功能调用。

**决策 2：谁来做最终分析？**
- **默认 Agent 做**。stocks-claw 提供"数据 + 脚手架"，Agent 用自己的 LLM 分析。
- **可选 stocks-claw 做**。Agent 可以要求 stocks-claw 内部调用 LLM 生成报告（兼容现有能力）。

**决策 3：状态存在哪里？**
- **默认无状态**。每次调用独立，不依赖中间缓存文件。
- **可选有状态**。启用本地缓存加速重复查询。

**决策 4：输出格式？**
- **默认结构化 JSON**。Agent 直接解析。
- **可选人类可读**。用户直接查看时使用。

---

## 二、总体架构

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Agent 交互适配层 (Adapters)                       │
│                                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐ │
│  │   CLI 模式   │  │ Python API   │  │   MCP 模式   │  │  HTTP API   │ │
│  │  (命令行)    │  │  (import)    │  │  (协议标准)  │  │  (本地服务)  │ │
│  │              │  │              │  │              │  │             │ │
│  │  $ stocks    │  │  import      │  │  工具注册    │  │  POST /api  │ │
│  │    query     │  │  stocks_claw │  │  自动发现    │  │    /query   │ │
│  │    report    │  │    .engine   │  │  参数校验    │  │    /report  │ │
│  │    assets    │  │    .fetch    │  │  结果返回    │  │    /context │ │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬──────┘ │
│         └───────────────────┴───────────────────┴─────────────────┘     │
│                              │                                          │
└──────────────────────────────┼──────────────────────────────────────────┘
                               │
                               ▼ 统一接口契约 (AnalysisContext / CommandResult)
┌─────────────────────────────────────────────────────────────────────────┐
│                      核心引擎层 (Core Engine)                            │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ 数据获取模块 (Data Fetchers) — 纯函数，无状态，直接返回            │   │
│  │                                                                  │   │
│  │  fetch_quote(market, code)        → Quote                      │   │
│  │  fetch_news(sources, limit)         → list[NewsItem]             │   │
│  │  load_assets()                      → FinancialMemory              │   │
│  │  load_watchlist()                   → list[Instrument]           │   │
│  │  refresh_all_quotes()               → dict[market, list[Quote]] │   │
│  │                                                                  │   │
│  │ 特点：不写入任何中间文件，直接计算返回                              │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ 分析脚手架模块 (Analysis Scaffolds) — 轻量规则，给 LLM 的辅助信号   │   │
│  │                                                                  │   │
│  │  analyze_portfolio(assets)          → PortfolioMapping           │   │
│  │  analyze_market_state(quotes)       → MarketState                │   │
│  │  detect_drift(mapping, constraints) → list[DriftCheck]           │   │
│  │  build_context(...)                 → AnalysisContext            │   │
│  │                                                                  │   │
│  │ 特点：只做轻量归纳，不做最终判断，输出结构化数据                    │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ LLM 数据增强模块 (LLM Enhancer) — 可选，默认禁用                │   │
│  │                                                                  │   │
│  │  enhance_news(news)                 → list[EnhancedNewsItem]   │   │
│  │  generate_market_summary(quotes)      → str (自然语言摘要)       │   │
│  │                                                                  │   │
│  │ 特点：用低成本 LLM 做 engine 规则做不到的数据层语义处理          │   │
│  │       摘要生成、跨源去重、质量分级、行情摘要                      │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ LLM 驱动分析模块 (LLM Analysis) — 可选，可禁用                     │   │
│  │                                                                  │   │
│  │  generate_report(context, model?) → str (Markdown)             │   │
│  │  extract_constraints(text)          → dict (约束提取)             │   │
│  │                                                                  │   │
│  │ 特点：内部调用外部 LLM API，Agent 可选择自己替代                    │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ 数据持久化模块 (Persistence) — 可选，可禁用                         │   │
│  │                                                                  │   │
│  │  save_assets(assets)                → 写入 financial_assets.json│   │
│  │  save_report(text)                  → 写入 reports/             │   │
│  │  load_cache(key) / save_cache(...)  → 可选本地缓存                │   │
│  │                                                                  │   │
│  │ 特点：所有写入操作显式调用，不隐式触发                               │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      Provider / Data 层                                 │
│                                                                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌───────────────┐  │
│  │ 腾讯财经    │  │ 东方财富    │  │  Finnhub    │  │  RSS/GNews/   │  │
│  │ (A股行情)   │  │ (A股备用)   │  │  (美股行情) │  │  Juhe (新闻)  │  │
│  └─────────────┘  └─────────────┘  └─────────────┘  └───────────────┘  │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ 配置文件                                                          │   │
│  │  watchlist.json / markets.json / news_sources.json                │   │
│  │  financial_assets.json (用户资产)                                │   │
│  │  .secret/*-key.md (API Key)                                       │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 三、统一接口契约

### 3.1 核心数据对象

```python
# 所有模块共享的核心数据结构

@dataclass
class Quote:
    """行情数据"""
    instrument: Instrument
    price: float | None
    change: float | None
    pct_change: float | None
    # ... 其他字段

@dataclass
class NewsItem:
    """新闻条目 — 原始数据模型（适配后）"""
    title: str
    url: str
    source_name: str                    # 统一后的来源名称
    source_type: str                    # "rss" | "gnews" | "juhe_235" | "juhe_743"
    published_at: datetime | None      # 标准化后的时间，解析失败为 None
    summary: str | None               # 摘要，缺失为 None（不是空字符串）
    language: str = "unknown"         # "en" | "zh" | "unknown"
    tags: list[str] = field(default_factory=list)
    raw_metadata: dict = field(default_factory=dict)  # 原始字段保留

@dataclass
class EnhancedNewsItem(NewsItem):
    """增强后的新闻条目 — 包含 LLM Enhancer 生成的附加字段"""
    importance: str = "unknown"         # high / medium / low
    urgency: str = "unknown"            # immediate / high / medium / low
    category: str = "unknown"           # 宏观政策 / 行业动态 / 个股新闻 / 国际市场 / 其他
    sentiment: str = "unknown"          # positive / negative / neutral
    relevance_tags: list[str] = field(default_factory=list)
    llm_generated_summary: str | None = None  # LLM 生成的摘要（原始缺失时）
    enhanced_by_llm: bool = False      # 标记是否经过 LLM 增强

@dataclass
class FinancialAsset:
    """金融资产"""
    name: str
    platform: str
    amount: float
    asset_type: str
    notes: str | None
    confirmed: bool

@dataclass
class PortfolioMapping:
    """组合映射脚手架"""
    buckets: dict[str, list[FinancialAsset]]  # 资产桶归类
    ratios: dict[str, float]                  # 各桶占比
    dominant_layers: list[str]                # 主导层
    growth_exposure: str                      # high/moderate/light/none
    # ... 其他轻量信号

@dataclass
class MarketState:
    """市场状态脚手架"""
    risk_appetite: str      # risk_on / cooling / broad_risk_off / mixed / unknown
    tech_state: str         # expanding / under_pressure / soft / mixed / unknown
    safe_haven_state: str   # strengthening / supported / weakening / unknown
    china_state: str        # stable_positive / stable / mixed_pressure / under_pressure / unknown
    cross_asset_summary: list[str]

@dataclass
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
    quotes: dict[str, list[Quote]]      # 按市场分组的所有行情
    news: list[NewsItem]                   # 原始新闻（或 EnhancedNewsItem）
    news_count: int
    
    # LLM 增强输出（当 llm_enhancer.enabled = true 时填充）
    market_summary_nl: str = ""            # 行情自然语言摘要（LLM 生成）
    enhanced_news_count: int = 0           # 增强后的新闻数量
    
    # 轻量脚手架（辅助信号）
    market_state: MarketState
    portfolio_mapping: PortfolioMapping
    drift_checks: list[dict]           # 约束偏离检查
    
    # 历史上下文
    recent_snapshots: list[dict]       # 最近 N 次报告摘要
    
    # 原始输入（供 LLM 阅读）
    raw_prompt_input: str               # 人类可读格式的完整上下文文本
    
    # 元信息
    llm_enhancer_enabled: bool = False   # 本次上下文是否经过 LLM 增强
    llm_enhancer_model: str = ""         # 使用的增强模型
```

### 3.2 核心引擎接口

```python
class StocksEngine:
    """核心引擎 — Agent 可直接调用的统一入口"""
    
    # ========== 数据获取 ==========
    
    def fetch_quote(self, market: str, code: str) -> Quote:
        """获取单只股票行情"""
        pass
    
    def fetch_quotes(self, market: str | None = None) -> dict[str, list[Quote]]:
        """获取监控列表全部行情（market=None 则获取所有市场）"""
        pass
    
    def fetch_news(self, sources: list[str] | None = None, limit: int = 10) -> list[NewsItem]:
        """获取新闻（sources=None 则按配置获取所有源）"""
        pass
    
    def load_assets(self) -> list[FinancialAsset]:
        """加载用户金融资产"""
        pass
    
    # ========== 分析脚手架 ==========
    
    def analyze_portfolio(self, assets: list[FinancialAsset] | None = None) -> PortfolioMapping:
        """分析组合结构（assets=None 则自动加载）"""
        pass
    
    def analyze_market_state(self, quotes: dict[str, list[Quote]] | None = None) -> MarketState:
        """分析市场状态（quotes=None 则自动获取）"""
        pass
    
    def detect_drift(self, 
        mapping: PortfolioMapping | None = None,
        constraints: dict | None = None
    ) -> list[dict]:
        """检测组合是否偏离约束目标"""
        pass
    
    def build_context(self,
        include_news: bool = True,
        include_quotes: bool = True,
        include_history: bool = True,
    ) -> AnalysisContext:
        """组装完整分析上下文 — 核心方法"""
        pass
    
    # ========== LLM 数据增强（可选） ==========
    
    def enhance_news(
        self,
        news: list[NewsItem] | None = None,
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
        """
        pass
    
    def generate_market_summary(
        self,
        quotes: dict[str, list[Quote]] | None = None,
        market_state: MarketState | None = None,
    ) -> str:
        """生成行情自然语言摘要 — LLM 数据增强模块
        
        将结构化行情数据转换为人类可读的市场综述。
        如果 llm_enhancer.enabled = False，返回空字符串。
        """
        pass
    
    # ========== LLM 分析（可选） ==========
    
    def generate_report(self, 
        context: AnalysisContext | None = None,
        model: str | None = None,
        format: str = "markdown"
    ) -> str:
        """基于上下文生成 LLM 投资报告（context=None 则自动构建）"""
        pass
    
    def extract_constraints(self, text: str) -> dict:
        """从自然语言提取结构化约束"""
        pass
    
    # ========== 数据更新 ==========
    
    def add_asset(self, asset: FinancialAsset) -> FinancialAsset:
        """添加/更新资产"""
        pass
    
    def update_constraints(self, constraints: dict) -> dict:
        """更新投资约束"""
        pass
    
    # ========== 健康检查 ==========
    
    def health_check(self) -> dict:
        """系统健康检查"""
        pass
```

---

## 四、多模式交互适配层

### 4.1 模式总览

| 模式 | 适用场景 | 启动方式 | Agent 调用方式 |
|------|---------|---------|--------------|
| **CLI** | 快速调试、定时任务、cron | 无需启动 | `python3 -m stocks.cli query 000300` |
| **Python API** | Agent 与 stocks-claw 同进程 | 无需启动 | `from stocks.engine import StocksEngine; engine.fetch_quote(...)` |
| **MCP** | 支持 MCP 的 Agent（Claude Desktop 等）| `python3 -m stocks.mcp` | Agent 自动发现工具 |
| **HTTP API** | 跨进程、跨机器、Web UI | `python3 -m stocks.server` | `curl http://localhost:8787/api/query` |

### 4.2 CLI 模式

```bash
# 数据查询（返回 JSON，Agent 解析）
stocks query 000300 --market sh --format json
stocks quotes --market a --format json
stocks news --limit 5 --format json
stocks assets --format json

# 分析脚手架（返回结构化数据）
stocks analyze portfolio --format json
stocks analyze market --format json
stocks analyze drift --format json

# 组装完整上下文（核心）
stocks context --format json
stocks context --format json --no-news    # 不包含新闻
stocks context --format json --no-cache   # 禁用缓存，完全实时

# LLM 分析（可选，内部调用 LLM）
stocks report --format markdown           # 生成人类可读报告
stocks report --format json               # 返回报告 + 元信息
stocks report --use-llm gpt-4             # 指定 LLM 模型
stocks report --no-llm                    # 只返回 AnalysisContext，不调用 LLM

# 数据更新
stocks assets add --name "黄金ETF" --platform "支付宝" --amount 50000
stocks constraints update --from-text "成长仓位不超过30%"

# 健康检查
stocks health --format json
```

### 4.3 Python API 模式

```python
from stocks.engine import StocksEngine

engine = StocksEngine()

# 模式 A：Agent 自己分析（推荐）
context = engine.build_context()
# Agent 把 context 喂给自己的 LLM
# context.assets, context.market_state, context.portfolio_mapping...

# 模式 B：Agent 让 stocks-claw 内部生成报告
report = engine.generate_report(context, model="gpt-4")
# Agent 直接把 report 展示给用户

# 模式 C：混合 — Agent 获取数据，自己加工后让 stocks-claw 生成报告
quotes = engine.fetch_quotes()
my_analysis = f"我观察到 {quotes['a'][0].name} 涨了 {quotes['a'][0].pct_change}%"
context = engine.build_context()
report = engine.generate_report(context, custom_prompt_prefix=my_analysis)
```

### 4.4 MCP 模式

```python
# stocks/mcp_server.py
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
    quote = engine.fetch_quote(market, code)
    return quote.to_dict()

@server.tool()
def build_analysis_context() -> dict:
    """组装完整的金融分析上下文，包含用户资产、市场行情、新闻和组合分析脚手架
    
    返回的上下文可以直接用于 LLM 分析。
    """
    context = engine.build_context()
    return context.to_dict()

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
def add_asset(name: str, platform: str, amount: float, asset_type: str = "unknown") -> dict:
    """添加或更新金融资产"""
    asset = engine.add_asset(FinancialAsset(...))
    return asset.to_dict()

@server.tool()
def health_check() -> dict:
    """系统健康检查"""
    return engine.health_check()
```

Agent（如 Claude Desktop）配置：
```json
{
  "mcpServers": {
    "stocks-claw": {
      "command": "python3",
      "args": ["-m", "stocks.mcp"]
    }
  }
}
```

### 4.5 HTTP API 模式

```python
# stocks/http_server.py
from fastapi import FastAPI
from stocks.engine import StocksEngine

app = FastAPI()
engine = StocksEngine()

@app.get("/api/quote/{market}/{code}")
def get_quote(market: str, code: str):
    return engine.fetch_quote(market, code).to_dict()

@app.get("/api/quotes")
def get_quotes(market: str | None = None):
    return {k: [q.to_dict() for q in v] for k, v in engine.fetch_quotes(market).items()}

@app.get("/api/news")
def get_news(limit: int = 10):
    return [n.to_dict() for n in engine.fetch_news(limit=limit)]

@app.get("/api/assets")
def get_assets():
    return [a.to_dict() for a in engine.load_assets()]

@app.post("/api/assets")
def post_asset(asset: FinancialAssetInput):
    return engine.add_asset(asset.to_model()).to_dict()

@app.get("/api/context")
def get_context(
    include_news: bool = True,
    include_quotes: bool = True,
    include_history: bool = True,
):
    """获取完整分析上下文"""
    return engine.build_context(
        include_news=include_news,
        include_quotes=include_quotes,
        include_history=include_history,
    ).to_dict()

@app.post("/api/report")
def post_report(
    model: str | None = None,
    custom_context: dict | None = None,
):
    """生成投资报告"""
    context = AnalysisContext.from_dict(custom_context) if custom_context else None
    return {"report": engine.generate_report(context, model=model)}

@app.get("/api/health")
def get_health():
    return engine.health_check()
```

启动：
```bash
python3 -m stocks.server --port 8787
```

---

## 五、状态管理策略

### 5.1 两种模式

| 模式 | 说明 | 适用场景 |
|------|------|---------|
| **无状态模式** (`--no-cache`) | 每次调用实时获取数据，不读写任何缓存文件 | Agent 每次调用都是独立的，不需要历史 |
| **有状态模式** (默认) | 使用本地 JSON 文件缓存，支持历史快照 | 定时任务、需要历史对比 |

### 5.2 无状态模式下的数据流

```
Agent 调用
    ↓
StocksEngine.build_context(no_cache=True)
    ├── fetch_quotes()          → 实时查询所有 Provider
    ├── fetch_news()            → 实时抓取所有新闻源
    ├── load_assets()           → 读取 financial_assets.json
    ├── analyze_portfolio()     → 内存计算
    ├── analyze_market_state()  → 内存计算
    └── build_raw_prompt()     → 组装文本
    ↓
返回 AnalysisContext（纯内存对象，不写入任何文件）
    ↓
Agent 决定：
    a) 自己分析 → 把 context 喂给 Agent LLM
    b) 让 stocks-claw 分析 → engine.generate_report(context)
```

### 5.3 有状态模式下的数据流

```
Agent 调用
    ↓
StocksEngine.build_context()
    ├── fetch_quotes()          → 查询 Provider
    │   └── save_cache()        → 可选：写入 market_quotes.json
    ├── fetch_news()            → 抓取新闻
    │   └── save_cache()        → 可选：写入 news_feed.json
    ├── load_assets()           → 读取 financial_assets.json
    ├── analyze_portfolio()     → 计算
    │   └── save_cache()        → 可选：写入 portfolio_mapping.json
    ├── analyze_market_state()  → 计算
    │   └── save_cache()        → 可选：写入 market_state.json
    └── build_raw_prompt()
    ↓
返回 AnalysisContext
    ↓
engine.generate_report(context)
    └── save_report()           → 写入 reports/personal-latest.md
    └── save_snapshot()         → 写入 snapshots/
```

### 5.4 配置控制

```python
# stocks/config/engine.yaml
engine:
  # 缓存策略
  cache:
    enabled: true                    # 是否启用缓存
    quote_ttl: 1800                  # 行情缓存有效期（秒）
    news_ttl: 7200                   # 新闻缓存有效期（秒）
    state_ttl: 3600                  # 分析状态缓存有效期（秒）
  
  # LLM 配置
  llm:
    enabled: true                    # 是否启用内部 LLM 分析
    default_model: "gpt-5.4"
    fallback_model: "kimi-k2.5"
    url: "http://localhost:11434/v1/chat/completions"
    api_key: ""
  
  # 报告配置
  report:
    save_to_file: true               # 是否保存报告到文件
    history_limit: 10                # 保留历史快照数量
    dedup_cooldown_minutes: 60       # 重复报告冷却时间
```

---

## 六、与现有架构的对比和迁移

### 6.1 模块映射

| 现有模块 | v2 归属 | 处理方式 |
|---------|--------|---------|
| `financial_memory_service.py` | 数据持久化 | 保留核心逻辑，简化为 `AssetStore` |
| `news_fetch_service.py` | 数据获取 | 保留，简化为纯函数 `fetch_news()` |
| `market_data_service.py` | 数据获取 | 保留，简化为 `fetch_quotes()` |
| `query_service.py` | 数据获取 | 保留，作为 `fetch_quote()` 实现 |
| `provider_service.py` | Provider 层 | 保留，fallback 机制有用 |
| `resolver_service.py` | Provider 层 | 保留 |
| `watchlist_service.py` | 数据获取 | 保留，简化 |
| `market_state_service.py` | 分析脚手架 | **重构**：只做轻量归纳，不写入文件 |
| `portfolio_mapping_service.py` | 分析脚手架 | **重构**：只做轻量映射，不写入文件 |
| `advisory_service.py` | 分析脚手架 | **移除**：规则建议由 Agent LLM 或内部 LLM 完成 |
| `theme_analysis_service.py` | 分析脚手架 | **移除**：主题分析由 LLM 完成 |
| `market_signal_service.py` | 分析脚手架 | **移除**：合并到 `MarketStateAnalyzer` |
| `report_assembly_service.py` | 上下文组装 | **重构**：简化为 `build_context()` |
| `personal_insight_service.py` | 上下文组装 | **重构**：合并到 `build_context()` |
| `personal_llm_report_service.py` | LLM 分析 | **重构**：变为可选模块 `generate_report()` |
| `constraint_chat_service.py` | LLM 分析 | **重构**：变为可选模块 `extract_constraints()` |
| `chat_router_service.py` | — | **移除**：Agent 自己做路由 |
| `command_service.py` | — | **移除**：Agent 自己翻译命令 |
| `asset_memory_chat_service.py` | — | **移除**：Agent 自己管理对话 |
| `asset_update_service.py` | 数据更新 | 保留核心逻辑，简化为 `add_asset()` |
| `event_log_service.py` | 日志 | 保留，简化为 `log_event()` |
| `health_check_service.py` | 健康检查 | 保留，简化为 `health_check()` |
| `quote_guard.py` | Provider 层 | 保留 |
| `config_loader.py` | 配置 | 保留 |
| `validators.py` | 配置 | 保留 |

### 6.2 文件结构变化

```
stocks-claw/
├── stocks/
│   ├── __init__.py
│   ├── __main__.py                    # python3 -m stocks 入口
│   │
│   ├── engine/                        # 核心引擎层（新增）
│   │   ├── __init__.py
│   │   ├── core.py                    # StocksEngine 主类
│   │   ├── fetchers.py                # 数据获取（quote/news/asset）
│   │   ├── scaffolds.py               # 分析脚手架（portfolio/market/drift）
│   │   ├── context_builder.py         # AnalysisContext 组装
│   │   ├── llm_analysis.py            # 可选 LLM 分析（report/constraints）
│   │   └── persistence.py             # 数据持久化（可选）
│   │
│   ├── adapters/                      # 交互适配层（新增）
│   │   ├── __init__.py
│   │   ├── cli.py                     # CLI 统一入口
│   │   ├── mcp.py                     # MCP 服务器
│   │   └── http.py                    # HTTP API 服务器
│   │
│   ├── providers/                     # Provider 层（保留）
│   │   ├── base.py
│   │   ├── tencent_a.py
│   │   ├── eastmoney_a.py
│   │   ├── finnhub_quote.py
│   │   └── registry.py
│   │
│   ├── domain/                        # Domain 层（保留）
│   │   └── models.py
│   │
│   ├── services/                      # 现有服务层（逐步迁移后移除）
│   │   └── ...                        # 过渡期保留，标记 deprecated
│   │
│   ├── cli/                           # 现有 CLI（过渡期保留）
│   │   └── ...
│   │
│   ├── config/                        # 配置（保留）
│   │   ├── markets.json
│   │   ├── news_sources.json
│   │   └── watchlist.json
│   │
│   ├── data/                          # 数据（保留）
│   │   └── financial_assets.json
│   │
│   ├── prompts/                       # Prompts（保留）
│   │   └── personal_advice_prompt.txt
│   │
│   ├── errors.py                      # 异常（保留）
│   ├── logging_utils.py               # 日志（保留）
│   ├── llm_config.py                  # LLM 配置（保留）
│   ├── config_loader.py               # 配置加载（保留）
│   └── validators.py                  # 校验（保留）
│
├── .secret/                           # API Key（保留）
├── reports/                           # 报告输出（保留）
├── tests/                             # 测试（迁移）
├── AGENT_GUIDE.md                     # Agent 使用指南（重写）
├── ARCHITECTURE_V2.md                # 本文件
└── README.md
```

---

## 七、Agent 使用指南（示例）

### 7.1 OpenClaw Agent 使用示例

Agent 读取 `AGENT_GUIDE.md` 后，知道 stocks-claw 提供以下能力：

```
# 能力清单（Agent 内部记忆）

stocks-claw 提供以下工具函数：

1. fetch_quote(market, code) → Quote
   获取单只股票/ETF 实时行情

2. fetch_quotes(market?) → dict[market, list[Quote]]
   获取监控列表全部行情

3. fetch_news(limit?) → list[NewsItem]
   获取最新财经新闻

4. load_assets() → list[FinancialAsset]
   获取用户金融资产

5. build_context(options?) → AnalysisContext
   组装完整分析上下文（核心）
   包含：资产、行情、新闻、组合映射、市场状态、偏离检查、历史快照

6. generate_report(context?, model?) → str
   基于上下文生成 LLM 投资报告（可选）

7. add_asset(name, platform, amount, type?) → FinancialAsset
   添加/更新资产

8. health_check() → dict
   系统健康检查
```

**场景 1：用户查询股票**

用户："紫金矿业今天怎么样？"

Agent 思考：用户想查询股票行情 → 调用 `fetch_quote`

Agent 执行：
```bash
python3 -m stocks.engine fetch_quote --market sh --code 601899 --format json
```

Agent 解析 JSON，回复用户：
> 紫金矿业 (601899) 最新价 12.50 元，涨 2.46%，成交活跃。

**场景 2：用户查看资产**

用户："我现在的资产情况？"

Agent 思考：用户想看资产 → 调用 `load_assets`

Agent 执行：
```bash
python3 -m stocks.engine load_assets --format json
```

Agent 解析 JSON，整理后回复：
> 你当前有 5 项资产，总计约 38 万元。其中理财占比最高（52%），黄金 ETF 约 8%...

**场景 3：用户要投资建议**

用户："帮我看看今天的投资建议"

Agent 思考：用户需要综合分析 → 调用 `build_context` 获取完整上下文，然后 Agent 自己的 LLM 分析

Agent 执行：
```bash
python3 -m stocks.engine build_context --format json --no-cache
```

Agent 拿到 `AnalysisContext`，喂给自己的 LLM：
```
系统提示：你是一位投资顾问，基于以下数据给出建议...

用户资产：{context.assets}
市场行情：{context.quotes}
最新新闻：{context.news}
组合分析：{context.portfolio_mapping}
市场状态：{context.market_state}
偏离检查：{context.drift_checks}
历史对比：{context.recent_snapshots}

请给出今日投资建议...
```

Agent 把 LLM 生成的建议回复用户。

**场景 4：用户更新资产**

用户："我最近在支付宝买了 5 万块华安黄金 ETF"

Agent 思考：用户要更新资产 → 调用 `add_asset`

Agent 执行：
```bash
python3 -m stocks.engine add_asset \
  --name "华安黄金ETF" \
  --platform "支付宝" \
  --amount 50000 \
  --type "黄金ETF"
```

Agent 确认后回复：
> 已添加资产：华安黄金ETF / 支付宝 / 50000元。当前黄金类资产占比约 13%。

**场景 5：定时报告（cron）**

```bash
# crontab
0 9,11,14,16 * * 1-5 cd /path/to/stocks-claw && \
  python3 -m stocks.engine generate_report --format markdown --save
```

Agent 在对话中告知用户报告已生成，或主动推送摘要。

### 7.2 Hermes Agent 使用示例

Hermes Agent 通过 Python API 直接调用：

```python
from stocks.engine import StocksEngine

engine = StocksEngine()

# 用户说："帮我看看今天的投资建议"
context = engine.build_context()

# Hermes 自己的 LLM 分析
hermes_llm_prompt = f"""
基于以下金融数据给出投资建议：

资产概况：{context.portfolio_mapping.dominant_layers}
市场状态：风险{context.market_state.risk_appetite}，科技{context.market_state.tech_state}
偏离检查：{context.drift_checks}

请给出今日投资建议...
"""

report = hermes_llm.generate(hermes_llm_prompt)
# 回复用户
```

### 7.3 MCP Agent 使用示例

Claude Desktop 配置 MCP 后，Claude 自动发现工具：

用户："我的投资组合健康吗？"

Claude 自动调用 `build_analysis_context()` 工具，获取完整上下文，然后自己分析回复用户。

---

## 八、实现路线图

### Phase 1：核心引擎（2-3 天）

1. 创建 `stocks/engine/` 目录
2. 实现 `StocksEngine` 核心类
3. 迁移 `fetch_quote`、`fetch_news`、`load_assets` 为纯函数
4. 实现 `build_context()` 核心方法
5. 所有方法支持 `no_cache` 模式

### Phase 2：CLI 适配（1-2 天）

1. 重写 `stocks/cli.py` 为真正的统一入口
2. 所有子命令在同一个进程内执行
3. 默认 `--format json`，支持 `--format markdown/text`
4. 支持 `--no-cache`、`--no-llm` 等全局选项

### Phase 3：LLM 可选化（1-2 天）

1. 重构 `personal_llm_report_service.py` 为 `engine/llm_analysis.py`
2. `generate_report()` 变为可选方法
3. `extract_constraints()` 变为可选方法
4. 支持 `--no-llm` 完全禁用内部 LLM

### Phase 4：MCP 适配（1 天）

1. 创建 `stocks/adapters/mcp.py`
2. 暴露核心工具函数
3. 编写 MCP 配置文档

### Phase 5：HTTP 适配（可选，1-2 天）

1. 创建 `stocks/adapters/http.py`
2. FastAPI 服务
3. 支持 CORS、健康检查端点

### Phase 6：清理遗留（1 天）

1. 标记 `services/` 下 deprecated 模块
2. 更新 `AGENT_GUIDE.md`
3. 迁移测试到 `engine/` 层
4. 更新 `README.md`

---

## 九、关键设计决策总结

| 决策 | 选择 | 理由 |
|------|------|------|
| 意图识别 | **Agent 做** | Agent 本身就是 LLM，意图识别能力更强 |
| 最终分析 | **默认 Agent 做，可选 stocks-claw 做** | 避免双重 LLM 调用，Agent 更了解用户上下文 |
| 状态管理 | **默认无状态，可选有状态** | CLI 调用应该是独立的，不依赖历史文件 |
| 输出格式 | **默认 JSON** | Agent 需要结构化数据 |
| 中间文件 | **默认不写入，显式调用才写入** | 无状态模式下不污染文件系统 |
| LLM 调用 | **可选模块，可完全禁用** | Agent 可以自己替代 |
| 服务层 | **大幅简化，20+ → 5 个核心模块** | 去除过度设计，聚焦核心能力 |
| 接入模式 | **CLI + Python API + MCP + HTTP** | 覆盖所有 Agent 使用场景 |

---

*设计版本：v2.0*
*设计日期：2026-06-05*
*状态：设计完成，待实现*
