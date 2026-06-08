# stocks-claw v2 设计薄弱环节分析与强化建议

> 基于 DESIGN.md (1473 行) 的系统性审查
> 审查日期: 2026-06-05

---

## 一、总体评估

| 维度 | 当前覆盖度 | 风险等级 | 说明 |
|------|-----------|---------|------|
| **架构设计** | ⭐⭐⭐⭐⭐ 高 | 🟢 低 | 3层架构、接口契约、职责边界清晰 |
| **模块接口** | ⭐⭐⭐⭐ 较高 | 🟢 低 | 核心类和方法签名完整 |
| **多模式交互** | ⭐⭐⭐⭐ 较高 | 🟡 中 | CLI/Python/MCP/HTTP 都有示例 |
| **错误处理** | ⭐⭐ 较低 | 🔴 高 | 只有异常类，无降级策略 |
| **缓存策略** | ⭐ 低 | 🔴 高 | 只有配置项，无实现设计 |
| **安全性** | ⭐ 低 | 🔴 高 | 缺少输入验证、认证、速率限制 |
| **并发性能** | ⭐ 低 | 🟡 中 | 全同步设计，无异步考虑 |
| **可观测性** | ⭐⭐ 较低 | 🟡 中 | 日志有配置，无指标/监控/告警 |
| **测试策略** | ⭐⭐ 较低 | 🟡 中 | 只有文件列表，无方法论 |
| **部署运维** | ⭐ 低 | 🔴 高 | 完全缺失 |
| **数据一致性** | ⭐ 低 | 🟡 中 | 多源冲突未讨论 |
| **LLM Enhancer** | ⭐⭐⭐ 中 | 🟡 中 | 在独立文档，未纳入主设计 |

---

## 二、详细薄弱环节分析

### 2.1 错误处理与降级策略 —— 风险：🔴 高

**当前状态**：
- `errors.py` 保留异常类体系
- 设计提到"Provider 失败时自动降级"
- 但没有详细的降级策略设计

**具体问题**：

| 场景 | 当前设计 | 缺失 |
|------|---------|------|
| **Provider 全部失败** | 抛出 `ProviderExhaustedError` | 没有 fallback 到缓存数据或 mock 数据的策略 |
| **LLM API 超时** | 未讨论 | 超时后是否降级为规则分析？ |
| **LLM API 限流** | 未讨论 | 重试策略（指数退避？） |
| **网络中断** | 未讨论 | 部分数据可用时是否返回不完整结果？ |
| **配置错误** | `validators.py` 保留 | 启动时校验 vs 运行时校验的分工 |
| **数据解析失败** | 未讨论 | 腾讯返回 GBK 乱码时如何处理？ |

**强化建议**：

```python
# engine/fetchers.py — 增加降级策略
class DataFetchers:
    def fetch_quotes(self, market: str) -> dict[str, list[Quote]]:
        """获取行情，带多级降级"""
        try:
            # L1: 实时获取
            return self._fetch_live(market)
        except ProviderExhaustedError:
            # L2: 缓存降级（如果启用缓存）
            if self.cache_enabled:
                cached = self._fetch_from_cache(market)
                if cached:
                    logger.warning(f"Provider 全部失败，使用缓存数据: {market}")
                    return cached
            
            # L3: 返回空数据 + 标记（不抛异常，让 Agent 决定）
            logger.error(f"Provider 全部失败且无缓存: {market}")
            return {
                "status": "degraded",
                "market": market,
                "quotes": [],
                "error": "所有数据源不可用",
                "last_successful": self._get_last_success_time(market),
            }
```

**需要新增设计章节**：`## 十一、错误处理与降级策略`

---

### 2.2 缓存策略 —— 风险：🔴 高

**当前状态**：
- `engine.yaml` 有 `cache.enabled` 和 `quote_ttl`/`news_ttl`
- 但**无状态模式是默认**，缓存默认禁用

**具体问题**：

| 问题 | 说明 |
|------|------|
| **缓存与无状态的冲突** | 默认无状态，但缓存启用时就是有状态。这个边界没有清晰定义 |
| **缓存实现方式** | 内存缓存？文件缓存？Redis？没有设计 |
| **缓存失效策略** | TTL 到期后如何刷新？ |
| **缓存一致性** | 用户资产更新后，缓存的组合映射是否失效？ |
| **缓存粒度** | 是按市场缓存？按标的缓存？按用户缓存？ |

**强化建议**：

明确区分两种模式：

```yaml
# engine.yaml
engine:
  mode: "stateless"  # stateless | cached | persistent
  
  # stateless: 每次调用独立，无缓存（默认，适合 Agent 调用）
  # cached: 启用内存缓存，适合高频调用场景
  # persistent: 启用文件缓存，适合定时任务场景
  
  cache:
    backend: "memory"  # memory | file | redis
    quote_ttl: 1800    # 30分钟（行情变化快）
    news_ttl: 7200     # 2小时（新闻变化慢）
    asset_ttl: 300     # 5分钟（用户资产可能随时更新）
    max_size: 100      # 最多缓存 100 条
```

**需要新增设计章节**：`## 十二、缓存与状态管理`

---

### 2.3 安全性 —— 风险：🔴 高

**当前状态**：
- API Key 存储在 `.secret/*-key.md` 中
- 没有讨论任何安全机制

**具体问题**：

| 安全问题 | 风险 | 当前状态 |
|---------|------|---------|
| **输入验证** | SQL 注入、路径遍历、命令注入 | ❌ 未设计 |
| **API Key 泄露** | 日志中可能打印 API Key | ❌ 未设计 |
| **HTTP API 认证** | 任何人可以调用本地 HTTP API | ❌ 未设计 |
| **速率限制** | 被恶意调用导致 API 配额耗尽 | ❌ 未设计 |
| **敏感数据** | 用户资产数据是隐私 | ❌ 未设计 |
| **CORS** | HTTP API 的跨域策略 | ❌ 未设计 |

**强化建议**：

```python
# adapters/http.py — 增加安全中间件
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

app = FastAPI()

# 1. 认证（HTTP API 模式）
security = HTTPBearer(auto_error=False)

async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """验证 Bearer Token"""
    if not credentials:
        # 本地开发模式允许无认证
        if not settings.allow_localhost_no_auth:
            raise HTTPException(status_code=401, detail="Missing authentication")
        return None
    
    token = credentials.credentials
    if not validate_token(token):
        raise HTTPException(status_code=403, detail="Invalid token")
    return token

# 2. 速率限制
@app.middleware("http")
async def rate_limit(request: Request, call_next):
    client_ip = request.client.host
    if not rate_limiter.allow(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    return await call_next(request)

# 3. 敏感数据脱敏
class SensitiveDataFilter(logging.Filter):
    """日志过滤器：脱敏 API Key"""
    def filter(self, record):
        if hasattr(record, 'msg') and isinstance(record.msg, str):
            record.msg = mask_api_keys(record.msg)
        return True
```

**需要新增设计章节**：`## 十三、安全设计`

---

### 2.4 并发与性能 —— 风险：🟡 中

**当前状态**：
- 所有数据获取是同步顺序执行
- `fetch_quotes()` → `fetch_news()` → `analyze_portfolio()` → `build_context()`

**具体问题**：

| 问题 | 影响 |
|------|------|
| **串行获取** | 行情 + 新闻 + 资产 串行获取，延迟叠加 |
| **无并发控制** | 多个 Agent 同时调用 HTTP API 时可能触发 API 限流 |
| **阻塞 I/O** | 网络请求阻塞 Python 线程 |
| **无连接池** | 每次请求新建 HTTP 连接 |

**强化建议**：

```python
# engine/fetchers.py — 增加并发获取
import asyncio
from concurrent.futures import ThreadPoolExecutor

class DataFetchers:
    def __init__(self, config):
        self.executor = ThreadPoolExecutor(max_workers=5)
        self.session = requests.Session()  # 连接池
    
    def build_context_async(self) -> AnalysisContext:
        """并发获取所有数据"""
        with ThreadPoolExecutor() as executor:
            # 并行获取行情、新闻、资产
            future_quotes = executor.submit(self.fetch_quotes)
            future_news = executor.submit(self.fetch_news)
            future_assets = executor.submit(self.load_assets)
            
            quotes = future_quotes.result()
            news = future_news.result()
            assets = future_assets.result()
        
        # 组合映射依赖资产数据，必须在资产获取后执行
        mapping = self.analyze_portfolio(assets)
        
        return AnalysisContext(
            quotes=quotes,
            news=news,
            assets=assets,
            portfolio_mapping=mapping,
        )
```

**需要新增设计章节**：`## 十四、并发与性能优化`

---

### 2.5 可观测性 —— 风险：🟡 中

**当前状态**：
- `logging_utils.py` 保留 JSONL 日志
- `engine.yaml` 有日志级别配置
- 但没有指标、监控、告警设计

**具体问题**：

| 缺失 | 说明 |
|------|------|
| **指标收集** | API 调用成功率、延迟、Provider 可用性 |
| **健康检查细节** | `health_check()` 返回什么？只有 "ok/warning/error"？ |
| **告警机制** | Provider 连续失败时如何通知？ |
| **链路追踪** | Agent → CLI → Engine → Provider 的调用链 |
| **日志聚合** | 多进程/多实例时的日志聚合 |

**强化建议**：

```python
# engine/metrics.py — 新增指标模块
from dataclasses import dataclass
from datetime import datetime

@dataclass
class EngineMetrics:
    """引擎运行指标"""
    # Provider 指标
    provider_success: dict[str, int]      # 各 Provider 成功次数
    provider_failure: dict[str, int]    # 各 Provider 失败次数
    provider_latency: dict[str, float]  # 各 Provider 平均延迟
    
    # LLM 指标
    llm_calls: int = 0
    llm_errors: int = 0
    llm_latency: float = 0.0
    llm_tokens_in: int = 0
    llm_tokens_out: int = 0
    
    # 缓存指标
    cache_hits: int = 0
    cache_misses: int = 0
    
    # 系统指标
    uptime_seconds: float = 0.0
    last_health_check: datetime = None

class MetricsCollector:
    """指标收集器"""
    
    def record_provider_call(self, provider: str, success: bool, latency: float):
        if success:
            self.metrics.provider_success[provider] += 1
        else:
            self.metrics.provider_failure[provider] += 1
        self.metrics.provider_latency[provider] = latency
    
    def get_health_score(self) -> float:
        """计算健康分数 (0-1)"""
        total = sum(self.metrics.provider_success.values()) + sum(self.metrics.provider_failure.values())
        if total == 0:
            return 1.0
        return sum(self.metrics.provider_success.values()) / total
```

**需要新增设计章节**：`## 十五、可观测性设计`

---

### 2.6 测试策略 —— 风险：🟡 中

**当前状态**：
- 列出了测试文件列表
- 但没有测试方法论

**具体问题**：

| 缺失 | 说明 |
|------|------|
| **Mock 策略** | 如何 mock 外部 API（腾讯、Finnhub、GNews）？ |
| **LLM 测试** | 如何测试 LLM 模块？需要 mock LLM 调用 |
| **集成测试** | 端到端测试策略 |
| **性能测试** | 并发调用时的性能基准 |
| **契约测试** | AnalysisContext 的 schema 兼容性测试 |
| ** fixtures** | 测试数据如何准备？ |

**强化建议**：

```python
# tests/conftest.py — 测试基础设施
import pytest
from unittest.mock import Mock, patch

@pytest.fixture
def mock_tencent_provider():
    """Mock 腾讯 Provider"""
    provider = Mock()
    provider.get_quotes.return_value = [
        Quote(
            instrument=Instrument(code="000300", name="沪深300", market="a"),
            price=3542.33,
            change=12.45,
            pct_change=0.35,
        )
    ]
    return provider

@pytest.fixture
def mock_llm_client():
    """Mock LLM 客户端"""
    client = Mock()
    client.generate.return_value = "{"importance": "high", "summary": "测试摘要"}"
    return client

@pytest.fixture
def test_engine(mock_tencent_provider, mock_llm_client):
    """预配置的测试引擎"""
    engine = StocksEngine(config_path="tests/fixtures/test_engine.yaml")
    engine.fetchers.providers["a"]["tencent"] = mock_tencent_provider
    engine.llm_enhancer.client = mock_llm_client
    return engine
```

**需要新增设计章节**：`## 十六、测试策略`

---

### 2.7 部署与运维 —— 风险：🔴 高

**当前状态**：
- 完全缺失

**具体问题**：

| 缺失 | 说明 |
|------|------|
| **安装方式** | `pip install`？`git clone`？Docker？ |
| **依赖管理** | `requirements.txt` 有，但没有版本锁定 |
| **环境隔离** | 开发/测试/生产环境如何区分？ |
| **配置管理** | 不同环境的配置如何管理？ |
| **数据备份** | 用户资产数据如何备份？ |
| **更新策略** | 如何平滑升级？ |
| **监控部署** | 如何监控运行状态？ |

**强化建议**：

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY stocks/ ./stocks/
COPY .secret/ ./.secret/
COPY stocks/config/ ./stocks/config/
COPY stocks/data/ ./stocks/data/

EXPOSE 8787
CMD ["uvicorn", "stocks.adapters.http:app", "--host", "0.0.0.0", "--port", "8787"]
```

```yaml
# docker-compose.yml
version: '3.8'
services:
  stocks-claw:
    build: .
    ports:
      - "8787:8787"
    volumes:
      - ./stocks/data:/app/stocks/data
      - ./stocks/config:/app/stocks/config
      - ./.secret:/app/.secret
    environment:
      - STOCKS_CONFIG_PATH=/app/stocks/config/engine.yaml
      - STOCKS_LOG_LEVEL=info
    restart: unless-stopped
```

**需要新增设计章节**：`## 十七、部署与运维`

---

### 2.8 数据一致性 —— 风险：🟡 中

**当前状态**：
- 未讨论多源数据冲突

**具体问题**：

| 场景 | 问题 |
|------|------|
| **同一标的多源报价** | 腾讯和东方财富返回的价格略有差异，用哪个？ |
| **新闻时间冲突** | RSS 和 GNews 对同一事件的时间戳不同 |
| **资产数据更新** | 用户同时通过 CLI 和 HTTP API 修改资产 |

**强化建议**：

```python
# engine/fetchers.py — 多源冲突解决
class QuoteResolver:
    """行情数据冲突解决器"""
    
    def resolve(self, quotes_from_multiple_sources: list[Quote]) -> Quote:
        """多源数据冲突解决策略"""
        
        # 策略 1：优先使用最新时间戳的数据
        # 策略 2：如果差异 < 0.1%，取平均值
        # 策略 3：如果差异 > 1%，标记为异常，返回主源数据 + 警告
        
        if len(quotes_from_multiple_sources) == 1:
            return quotes_from_multiple_sources[0]
        
        # 检查价格差异
        prices = [q.price for q in quotes_from_multiple_sources if q.price]
        if not prices:
            return quotes_from_multiple_sources[0]
        
        max_price = max(prices)
        min_price = min(prices)
        diff_pct = (max_price - min_price) / min_price * 100
        
        if diff_pct > 1.0:
            # 差异过大，标记异常
            logger.warning(f"价格差异过大: {diff_pct:.2f}%")
            # 返回主源数据
            return quotes_from_multiple_sources[0]
        
        # 差异可接受，取平均值
        avg_price = sum(prices) / len(prices)
        return Quote(
            instrument=quotes_from_multiple_sources[0].instrument,
            price=avg_price,
            # ... 其他字段
        )
```

**需要新增设计章节**：`## 十八、数据一致性`

---

### 2.9 MCP 协议细节 —— 风险：🟡 中

**当前状态**：
- 展示了工具注册示例
- 但没有详细的 MCP 协议设计

**具体问题**：

| 缺失 | 说明 |
|------|------|
| **工具描述规范** | 每个工具的 description 如何写才能让 LLM 正确理解？ |
| **错误返回格式** | MCP 工具出错时返回什么？ |
| **参数校验** | MCP 层如何做参数校验？ |
| **生命周期** | MCP 服务器的启动、停止、重启策略 |
| **资源暴露** | 是否需要暴露 `resource://` 类型的资源？ |

**强化建议**：

```python
# adapters/mcp.py — 详细的 MCP 设计
from mcp.server import Server
from mcp.types import Tool, TextContent

server = Server("stocks-claw")

@server.tool()
def fetch_quote(market: str, code: str) -> list[TextContent]:
    """获取股票/ETF 实时行情
    
    使用示例：
    - 获取沪深300: market="sh", code="000300"
    - 获取贵州茅台: market="sh", code="600519"
    - 获取苹果美股: market="us", code="AAPL"
    
    返回格式：JSON 对象，包含 price, change, pct_change 等字段
    """
    try:
        quote = engine.fetch_quote(market, code)
        return [TextContent(type="text", text=json.dumps(quote.to_dict()))]
    except ProviderExhaustedError:
        return [TextContent(
            type="text",
            text=json.dumps({
                "error": "所有数据源不可用",
                "suggestion": "请稍后重试，或检查网络连接"
            })
        )]
    except ResolverError as e:
        return [TextContent(
            type="text",
            text=json.dumps({
                "error": f"无法解析标的: {e.message}",
                "suggestion": "请检查 market 和 code 是否正确"
            })
        )]
```

**需要新增设计章节**：`## 十九、MCP 协议详细设计`

---

### 2.10 HTTP API 细节 —— 风险：🟡 中

**当前状态**：
- 展示了端点示例
- 但没有详细的 API 设计

**具体问题**：

| 缺失 | 说明 |
|------|------|
| **认证机制** | 如何认证？Token？API Key？ |
| **错误响应格式** | 统一的错误响应结构 |
| **OpenAPI 文档** | 自动生成 Swagger 文档？ |
| **请求/响应模型** | Pydantic 模型定义 |
| **分页** | 新闻列表是否需要分页？ |
| **过滤** | 支持按时间、来源、语言过滤？ |

**强化建议**：

```python
# adapters/http.py — 详细的 API 设计
from fastapi import FastAPI, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional, Literal

app = FastAPI(
    title="stocks-claw API",
    version="2.0.0",
    docs_url="/docs",  # Swagger UI
    redoc_url="/redoc",  # ReDoc
)

# 统一的错误响应模型
class ErrorResponse(BaseModel):
    error: str = Field(..., description="错误类型")
    message: str = Field(..., description="错误描述")
    suggestion: Optional[str] = Field(None, description="解决建议")
    request_id: str = Field(..., description="请求 ID，用于排查")

# 新闻查询参数
class NewsQueryParams(BaseModel):
    limit: int = Field(10, ge=1, le=100, description="返回条数")
    sources: Optional[list[str]] = Field(None, description="指定新闻源")
    language: Optional[Literal["zh", "en"]] = Field(None, description="语言过滤")
    since: Optional[str] = Field(None, description="时间范围，如 2026-06-01")
    detail_level: Literal["compact", "standard", "full"] = Field("standard")

@app.get("/api/news", response_model=list[NewsItemResponse])
async def get_news(params: NewsQueryParams = Depends()):
    """获取财经新闻
    
    支持按来源、语言、时间过滤。
    默认返回最近 24 小时的新闻。
    """
    try:
        news = engine.fetch_news(
            sources=params.sources,
            limit=params.limit,
            language=params.language,
            since=params.since,
            detail_level=params.detail_level,
        )
        return [n.to_dict() for n in news]
    except FinancialMemoryError as e:
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error="news_fetch_failed",
                message=str(e),
                suggestion="请检查新闻源配置",
                request_id=get_request_id(),
            ).dict()
        )
```

**需要新增设计章节**：`## 二十、HTTP API 详细设计`

---

### 2.11 新闻 Provider 抽象 —— 风险：🟡 中

**当前状态**：
- 行情有 `QuoteProvider` 抽象基类
- 新闻没有对应的抽象基类

**问题**：
- 新闻源的异构性比行情大得多（RSS/XML、REST/JSON、字段差异大）
- 更需要抽象基类来统一接口

**强化建议**：

```python
# providers/news_base.py — 新增新闻 Provider 抽象基类
from abc import ABC, abstractmethod
from typing import Optional
from datetime import datetime

class NewsProvider(ABC):
    """新闻 Provider 抽象基类"""
    
    @property
    @abstractmethod
    def source_type(self) -> str:
        """源类型标识，如 "rss", "gnews", "juhe_235"""
        pass
    
    @property
    @abstractmethod
    def supported_languages(self) -> list[str]:
        """支持的语言列表"""
        pass
    
    @abstractmethod
    def fetch(self, limit: int = 10, since: Optional[datetime] = None) -> list[RawNewsItem]:
        """获取新闻
        
        返回原始格式的新闻数据，由 adapter 转换为标准 NewsItem
        """
        pass
    
    @abstractmethod
    def health_check(self) -> dict:
        """检查数据源健康状态"""
        pass

# providers/rss_provider.py
class RSSNewsProvider(NewsProvider):
    source_type = "rss"
    supported_languages = ["en", "zh"]
    
    def __init__(self, url: str, name: str):
        self.url = url
        self.name = name
    
    def fetch(self, limit: int = 10, since: Optional[datetime] = None) -> list[RawNewsItem]:
        # 实现 RSS 获取逻辑
        pass
    
    def health_check(self) -> dict:
        # 检查 RSS 可访问性
        pass

# providers/gnews_provider.py
class GNewsProvider(NewsProvider):
    source_type = "gnews"
    supported_languages = ["en", "zh"]
    
    def __init__(self, api_key: str, query: str):
        self.api_key = api_key
        self.query = query
    
    def fetch(self, limit: int = 10, since: Optional[datetime] = None) -> list[RawNewsItem]:
        # 实现 GNews API 调用
        pass
```

**需要修改**：`## 五、模块设计` 中增加新闻 Provider 抽象

---

### 2.12 LLM Enhancer 模块 —— 风险：🟡 中

**当前状态**：
- 在 `LLM_ENHANCER_ANALYSIS.md` 中详细讨论
- 但 `DESIGN.md` 中还没有正式纳入设计

**问题**：
- `DESIGN.md` 的 `engine/llm_analysis.py` 只包含 `generate_report` 和 `extract_constraints`
- 没有 `llm_enhancer.py` 的设计
- 第八章（架构边界）也没有提到 LLM Enhancer

**强化建议**：

将 `LLM_ENHANCER_ANALYSIS.md` 的核心设计合并到 `DESIGN.md`：

1. 在 `## 五、模块设计` 中增加 `engine/llm_enhancer.py`
2. 在 `## 七、配置设计` 中增加 `llm_enhancer` 配置节
3. 在 `## 四、统一接口契约` 中增加 `EnhancedNewsItem` 和 `market_summary` 字段

---

### 2.13 数据隐私 —— 风险：🔴 高

**当前状态**：
- 用户资产数据存储在 `financial_assets.json`
- 没有讨论数据隐私保护

**具体问题**：

| 问题 | 风险 |
|------|------|
| **明文存储** | 资产数据是明文 JSON，任何人可以读取 |
| **日志泄露** | 日志中可能记录用户资产信息 |
| **HTTP 传输** | HTTP API 传输资产数据时没有加密 |
| **备份安全** | 备份文件如何保护？ |

**强化建议**：

```python
# engine/persistence.py — 增加数据加密
from cryptography.fernet import Fernet
import os

class SecurePersistence:
    """加密持久化"""
    
    def __init__(self, key: Optional[str] = None):
        # 从环境变量或文件加载加密密钥
        key = key or os.environ.get("STOCKS_DATA_KEY")
        if not key:
            logger.warning("未配置数据加密密钥，资产数据将以明文存储")
            self.cipher = None
        else:
            self.cipher = Fernet(key.encode())
    
    def save_assets(self, assets: list[FinancialAsset]):
        data = json.dumps([a.to_dict() for a in assets])
        
        if self.cipher:
            data = self.cipher.encrypt(data.encode()).decode()
        
        with open(self.assets_path, 'w') as f:
            f.write(data)
    
    def load_assets(self) -> list[FinancialAsset]:
        with open(self.assets_path, 'r') as f:
            data = f.read()
        
        if self.cipher:
            data = self.cipher.decrypt(data.encode()).decode()
        
        return [FinancialAsset(**item) for item in json.loads(data)]
```

**需要新增设计章节**：`## 二十一、数据隐私与安全存储`

---

### 2.14 配置管理 —— 风险：🟡 中

**当前状态**：
- 有 `engine.yaml` 示例
- 有环境变量列表
- 但没有详细的配置管理设计

**具体问题**：

| 缺失 | 说明 |
|------|------|
| **配置校验规则** | 每个配置项的合法值范围？ |
| **配置热更新** | 运行时修改配置是否需要重启？ |
| **多环境配置** | 开发/测试/生产环境如何区分？ |
| **配置继承** | 基础配置 + 环境特定配置？ |
| **配置文档** | 每个配置项的详细说明？ |

**强化建议**：

```python
# config/validator.py — 配置校验
from pydantic import BaseModel, Field, validator
from typing import Optional, Literal

class EngineConfig(BaseModel):
    """引擎配置模型 —— 自动校验"""
    
    version: str = Field("2.0", regex=r"^\d+\.\d+$")
    
    class CacheConfig(BaseModel):
        enabled: bool = False
        backend: Literal["memory", "file", "redis"] = "memory"
        quote_ttl: int = Field(1800, ge=60, le=86400)
        news_ttl: int = Field(7200, ge=60, le=86400)
        
        @validator('backend')
        def validate_redis(cls, v, values):
            if v == 'redis' and not os.environ.get('REDIS_URL'):
                raise ValueError("使用 redis 缓存需要设置 REDIS_URL 环境变量")
            return v
    
    cache: CacheConfig = CacheConfig()
    
    class LLMConfig(BaseModel):
        enabled: bool = False
        model: str = "gpt-4o-mini"
        url: str = Field("http://localhost:11434/v1/chat/completions", regex=r"^https?://")
        timeout: int = Field(120, ge=10, le=300)
        max_tokens: int = Field(1800, ge=100, le=8000)
        temperature: float = Field(0.6, ge=0.0, le=2.0)
    
    llm: LLMConfig = LLMConfig()
```

**需要新增设计章节**：`## 二十二、配置管理`

---

### 2.15 版本管理 —— 风险：🟡 中

**当前状态**：
- `AnalysisContext` 有 `schema_version: int = 2`
- 但没有讨论版本演进策略

**具体问题**：

| 问题 | 说明 |
|------|------|
| **Schema 演进** | v2.1 增加字段时，如何保持向后兼容？ |
| **API 版本** | HTTP API 的 URL 版本控制？ |
| **MCP 版本** | MCP 工具的版本管理？ |
| **数据迁移** | 用户资产数据格式升级？ |

**强化建议**：

```python
# domain/models.py — 版本兼容
@dataclass(frozen=True)
class AnalysisContext:
    schema_version: int = 2
    
    @classmethod
    def from_dict(cls, data: dict) -> "AnalysisContext":
        """从字典创建，支持版本兼容"""
        version = data.get("schema_version", 1)
        
        if version == 1:
            # v1 → v2 迁移
            data = cls._migrate_v1_to_v2(data)
        elif version == 2:
            pass  # 当前版本
        else:
            raise ValueError(f"不支持的 schema 版本: {version}")
        
        return cls(**data)
    
    @staticmethod
    def _migrate_v1_to_v2(data: dict) -> dict:
        """v1 到 v2 的迁移逻辑"""
        # v1 的字段映射到 v2
        data["schema_version"] = 2
        # 新增字段的默认值
        data.setdefault("market_summary", "")
        data.setdefault("enhanced_news", [])
        return data
```

**需要新增设计章节**：`## 二十三、版本管理`

---

### 2.16 定时任务 —— 风险：🟡 中

**当前状态**：
- 提到"系统 cron 调用 CLI"
- 但没有详细设计

**具体问题**：

| 缺失 | 说明 |
|------|------|
| **任务配置** | 哪些任务需要定时执行？ |
| **调度策略** | 每天几点执行？ |
| **失败重试** | 任务失败时如何重试？ |
| **通知机制** | 任务失败时如何通知用户？ |
| **并发控制** | 防止任务重叠执行 |

**强化建议**：

```yaml
# config/scheduler.yaml
scheduler:
  enabled: false  # 默认禁用，由外部 cron 管理
  
  tasks:
    - name: "daily_report"
      schedule: "0 9 * * *"  # 每天 9:00
      command: "stocks report --format markdown --save"
      timeout: 300
      retry: 3
      
    - name: "health_check"
      schedule: "0 */6 * * *"  # 每 6 小时
      command: "stocks health --format json"
      timeout: 60
      retry: 1
      
    - name: "news_refresh"
      schedule: "0 8,12,18 * * *"  # 每天 8/12/18 点
      command: "stocks news --limit 20 --save"
      timeout: 120
      retry: 2
```

**需要新增设计章节**：`## 二十四、定时任务`

---

## 三、强化优先级排序

### 🔴 P0 — 必须在开发前完成设计

| 序号 | 薄弱环节 | 原因 |
|------|---------|------|
| 1 | **错误处理与降级策略** | 直接影响系统稳定性 |
| 2 | **安全性（输入验证、认证）** | 直接影响系统安全 |
| 3 | **数据隐私** | 用户资产是敏感数据 |
| 4 | **LLM Enhancer 纳入主设计** | 已讨论但未纳入 DESIGN.md |

### 🟡 P1 — 应该在开发初期完成设计

| 序号 | 薄弱环节 | 原因 |
|------|---------|------|
| 5 | **缓存策略** | 影响性能和成本 |
| 6 | **并发与性能** | 影响用户体验 |
| 7 | **测试策略** | 影响代码质量 |
| 8 | **新闻 Provider 抽象** | 影响代码可维护性 |
| 9 | **配置管理** | 影响部署灵活性 |

### 🟢 P2 — 可以在开发中后期补充

| 序号 | 薄弱环节 | 原因 |
|------|---------|------|
| 10 | **可观测性** | 影响运维效率，但不阻塞开发 |
| 11 | **部署与运维** | 影响上线，但不阻塞开发 |
| 12 | **数据一致性** | 影响数据质量，但场景较少 |
| 13 | **MCP/HTTP 详细设计** | 可以在实现时细化 |
| 14 | **版本管理** | 影响长期维护，但 v2.0 不需要 |
| 15 | **定时任务** | 可以由外部 cron 替代 |

---

## 四、建议的下一步行动

### 4.1 立即行动（今天）

1. **将 LLM Enhancer 纳入 DESIGN.md**
   - 在 `## 五、模块设计` 中增加 `engine/llm_enhancer.py`
   - 在 `## 七、配置设计` 中增加 `llm_enhancer` 配置
   - 在 `AnalysisContext` 中增加 `market_summary` 和 `enhanced_news` 字段

2. **补充错误处理设计**
   - 新增 `## 十一、错误处理与降级策略`
   - 定义每个异常场景的降级行为

### 4.2 本周内完成

3. **补充安全设计**
   - 新增 `## 十三、安全设计`
   - 设计输入验证、认证、速率限制

4. **补充数据隐私设计**
   - 新增 `## 二十一、数据隐私与安全存储`
   - 设计数据加密和访问控制

5. **补充缓存策略**
   - 新增 `## 十二、缓存与状态管理`
   - 明确 stateless/cached/persistent 三种模式

### 4.3 开发过程中逐步补充

6. **测试策略** — 在写测试代码前完成设计
7. **并发与性能** — 在性能测试前完成设计
8. **部署与运维** — 在准备上线前完成设计
9. **可观测性** — 在系统稳定运行后补充

---

## 五、总结

当前 DESIGN.md 在**架构设计、接口契约、职责边界**三个核心维度是**扎实且完整的**。但在**工程落地、运维安全、性能优化**维度存在明显 gap，总计 **15 个薄弱环节**。

**最关键的 4 个 P0 问题**：
1. 错误处理与降级策略（直接影响稳定性）
2. 安全性设计（直接影响安全）
3. 数据隐私保护（用户资产敏感）
4. LLM Enhancer 纳入主设计（已讨论但未落地）

**建议**：在启动开发前，至少完成 P0 和 P1 的设计补充。P2 可以在开发过程中逐步完善。

---

*审查结论：设计骨架完整，但血肉需要补充。建议先补 P0/P1 设计，再启动开发。*
