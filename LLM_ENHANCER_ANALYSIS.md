# Engine 内部集成小型 LLM 的可行性分析

> 基于 stocks-claw v1 实际数据情况的深度分析
> 分析日期: 2026-06-05

---

## 一、问题定义

**核心问题**：在 engine 内部添加一个类似 GPT-4o mini 的小型低开销 LLM，是否能有效增强数据有效性和上游 Agent 的分析结果？

这个问题涉及三个层面：
1. **技术可行性**：小型 LLM 能否处理 engine 获取的异构数据？
2. **成本收益**：API 调用成本、延迟、可靠性是否值得？
3. **架构边界**：小型 LLM 的职责与上游 Agent 的 LLM 如何分工？

---

## 二、现有数据的问题清单（小型 LLM 可以解决什么）

### 2.1 新闻数据的具体问题

基于 `news_fetch_service.py` 的实际代码，现有 4 个新闻源的问题：

#### 问题 1：摘要缺失（Juhe 235 / 743）

```python
# 现有代码
'summary': '',  # 聚合数据API不返回摘要
```

**小型 LLM 可以**：基于标题生成简短摘要（1-2 句话），填补数据空白。

**示例**：
- 标题："美联储宣布维持利率不变"
- LLM 生成摘要："美联储在最新议息会议上决定维持基准利率在 5.25%-5.50% 区间不变，符合市场预期。"

#### 问题 2：时间格式混乱

```python
# 现有代码直接存储原始字符串
'published_at': (item.findtext('pubDate') or '').strip(),  # RSS: "Fri, 05 Jun 2026 14:30:00 GMT"
'published_at': (item.get('publishedAt') or '').strip(),   # GNews: "2026-06-05T14:30:00Z"
'published_at': (item.get('date') or '').strip(),           # Juhe 235: 格式未知
'published_at': (item.get('ctime') or '').strip(),         # Juhe 743: 格式未知
```

**小型 LLM 可以**：解析各种时间格式字符串，输出标准化 ISO 8601 格式。

**但更好的方案**：用 Python 的 `dateutil.parser` 做，不需要 LLM。这是结构化问题，不是语义问题。

#### 问题 3：跨源语义去重

**场景**：同一事件在不同源的报道
- RSS: "Fed Holds Rates Steady at 5.25%-5.50%"
- GNews: "Federal Reserve Maintains Interest Rates"
- Juhe 235: "美联储维持利率不变"
- Juhe 743: "美联储宣布维持基准利率"

**小型 LLM 可以**：判断这 4 条新闻是否描述同一事件，进行语义去重。

**这是 engine 用规则无法做到的**，因为涉及跨语言语义理解。

#### 问题 4：来源字段语义不统一

```python
# GNews
'source': (item.get('source') or {}).get('name') or 'GNews'  # "Reuters"

# Juhe 235
'source': item.get('author_name') or item.get('media_name') or '聚合数据'  # "张三" 或 "新浪财经"

# Juhe 743
'source': item.get('source') or '聚合数据财经'  # "澎湃新闻"

# RSS
# 无来源字段，只有 URL
```

**小型 LLM 可以**：
- 对 RSS 新闻，从 URL 推断来源（如 `finance.yahoo.com` → "Yahoo Finance"）
- 对 Juhe 235 的 `author_name`（个人作者），判断是否应该归类为"自媒体"或"官方媒体"
- 统一输出标准化的来源分类（如"国际主流媒体"、"国内财经媒体"、"自媒体"）

#### 问题 5：语言混合与翻译

- RSS + GNews = 英文
- Juhe 235 + 743 = 中文

**小型 LLM 可以**：
- 为英文新闻生成中文摘要（方便中文 Agent 处理）
- 为中文新闻生成英文摘要（方便英文 Agent 处理）
- 标记新闻的语言和翻译状态

**但成本较高**：每条新闻都要调用 LLM 做翻译，对于 20-30 条新闻的批量处理，成本不可忽视。

#### 问题 6：新闻质量分级

**小型 LLM 可以**：基于标题和内容，判断新闻的"重要性"和"紧急性"

```python
# LLM 输出示例
{
    "importance": "high",      # high / medium / low
    "urgency": "medium",       # immediate / high / medium / low
    "category": "宏观政策",     # 宏观政策 / 行业动态 / 个股新闻 / 国际市场 / 其他
    "sentiment": "neutral",    # positive / negative / neutral
    "relevance_tags": ["美联储", "利率", "美元"]  # 与用户可能相关的标签
}
```

**这对 Agent 非常有价值**：Agent 可以基于 importance/urgency 做筛选，而不是自己再判断一遍。

---

### 2.2 行情数据的问题

行情数据（Quote）已经通过 `Quote` dataclass 统一，问题较少。但小型 LLM 可以：

#### 问题 7：异常值检测

**场景**：某个 API 返回了明显错误的数据
- 腾讯返回某股票价格 = 0.01（实际应该是 100+）
- 东方财富返回涨跌幅 = 999%（明显异常）

**小型 LLM 可以**：基于历史数据和常识判断数据是否异常。

**但更好的方案**：用统计方法（如 3-sigma 法则）做异常检测，不需要 LLM。这是数学问题，不是语义问题。

#### 问题 8：行情数据的自然语言描述

**小型 LLM 可以**：将结构化行情数据转换为自然语言摘要

```python
# 输入：结构化 quotes
[
    {"name": "贵州茅台", "price": 1688.0, "change": -12.5, "pct_change": -0.74},
    {"name": "沪深300", "price": 3542.33, "change": 12.45, "pct_change": 0.35},
]

# LLM 输出
"今日 A 股整体震荡，大盘指数沪深300微涨 0.35%。个股方面，贵州茅台下跌 0.74%，
报 1688.0 元。黄金 ETF 表现强势，涨幅达 1.2%。"
```

**这对 Agent 的价值**：Agent 可以直接读取这段自然语言摘要，而不需要自己解析 JSON 再组织语言。

---

## 三、小型 LLM 的集成方案设计

### 3.1 架构位置

```
┌─────────────────────────────────────────────────────────────┐
│                         Agent (上游)                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │ 意图识别    │  │ 深度分析    │  │ 投资决策            │  │
│  │ (LLM)       │  │ (LLM)       │  │ (LLM)               │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              ↑
                              │ AnalysisContext (JSON)
                              │
┌─────────────────────────────────────────────────────────────┐
│                      Engine (核心)                            │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │ Fetchers    │  │ Scaffolds   │  │ LLM Enhancer        │  │
│  │ (数据获取)  │  │ (轻量归纳)  │  │ (小型 LLM 增强)     │  │
│  │             │  │             │  │                     │  │
│  │ - 各源独立  │  │ - 组合映射  │  │ - 摘要生成          │  │
│  │   fetcher   │  │ - 偏离检测  │  │ - 跨源去重          │  │
│  │ - 错误隔离  │  │ - 行情聚合  │  │ - 质量分级          │  │
│  │ - 原始存储  │  │             │  │ - 自然语言描述      │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
│                              │                                │
│  ┌─────────────────────────────────────────────────────────┐│
│  │              Context Builder (上下文组装)                ││
│  │  - 组合 fetchers + scaffolds + LLM enhancer 输出        ││
│  │  - 生成 AnalysisContext (JSON)                          ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
```

### 3.2 模块设计：`engine/llm_enhancer.py`

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class LLMEnhancerConfig:
    """小型 LLM 增强模块配置"""
    
    # 主开关
    enabled: bool = False
    
    # 各功能开关
    generate_missing_summaries: bool = True      # 为缺失摘要的新闻生成摘要
    cross_source_deduplication: bool = True      # 跨源语义去重
    quality_grading: bool = True                 # 新闻质量分级
    natural_language_summary: bool = True          # 行情数据自然语言描述
    
    # 成本控制
    max_llm_calls_per_request: int = 20          # 每次请求最多调用 LLM 次数
    max_news_items_to_process: int = 15          # 最多处理多少条新闻
    
    # 模型配置
    model: str = "gpt-4o-mini"                   # 默认使用低成本模型
    temperature: float = 0.3                     # 低温度，减少随机性
    
    # 缓存
    cache_ttl_seconds: int = 3600                # LLM 结果缓存 1 小时


class LLMEnhancer:
    """小型 LLM 增强模块
    
    职责：用低成本 LLM 做 engine 无法做的语义层面处理
    原则：只做结构化方法做不到的事情，不做 Agent 应该做的事情
    """
    
    def __init__(self, config: LLMEnhancerConfig):
        self.config = config
        # TODO: 初始化 LLM client
    
    def enhance_news(self, news: list[NewsItem]) -> list[EnhancedNewsItem]:
        """增强新闻数据
        
        处理流程：
        1. 为缺失摘要的新闻生成摘要
        2. 跨源语义去重
        3. 质量分级
        4. 限制处理数量（成本控制）
        """
        if not self.config.enabled:
            return [EnhancedNewsItem.from_raw(n) for n in news]
        
        # 1. 生成缺失摘要（只处理前 N 条）
        news_to_process = news[:self.config.max_news_items_to_process]
        
        # 2. 批量调用 LLM 生成摘要
        # 使用批量 API 减少调用次数
        items_without_summary = [n for n in news_to_process if n.summary is None]
        if items_without_summary and self.config.generate_missing_summaries:
            summaries = self._batch_generate_summaries(items_without_summary)
            for item, summary in zip(items_without_summary, summaries):
                item.summary = summary
        
        # 3. 跨源语义去重
        if self.config.cross_source_deduplication:
            news_to_process = self._semantic_deduplication(news_to_process)
        
        # 4. 质量分级
        if self.config.quality_grading:
            news_to_process = self._batch_grade_quality(news_to_process)
        
        return [EnhancedNewsItem.from_raw(n) for n in news_to_process]
    
    def generate_market_summary(self, quotes: list[Quote], market_state: dict) -> str:
        """生成行情自然语言摘要
        
        将结构化行情数据转换为人类可读的摘要。
        这对 Agent 很有价值：Agent 可以直接读取这段文字，
        而不需要自己解析 JSON。
        """
        if not self.config.enabled or not self.config.natural_language_summary:
            return ""
        
        # 构建 prompt
        prompt = self._build_market_summary_prompt(quotes, market_state)
        
        # 调用 LLM
        return self._call_llm(prompt, max_tokens=500)
    
    def _batch_generate_summaries(self, items: list[NewsItem]) -> list[str]:
        """批量生成摘要
        
        优化：将多条新闻合并为一个 prompt，减少 API 调用次数
        """
        # 每批处理 5 条新闻
        batch_size = 5
        results = []
        
        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]
            prompt = self._build_summary_prompt(batch)
            
            # 调用 LLM，返回 JSON 数组
            response = self._call_llm(prompt, max_tokens=200 * len(batch))
            
            # 解析结果
            try:
                summaries = json.loads(response)
                results.extend(summaries)
            except json.JSONDecodeError:
                # 如果解析失败，返回空摘要（降级处理）
                results.extend([""] * len(batch))
        
        return results
    
    def _semantic_deduplication(self, news: list[NewsItem]) -> list[NewsItem]:
        """跨源语义去重
        
        使用 LLM 判断新闻是否描述同一事件。
        优化：使用 embedding + 聚类，减少 LLM 调用次数。
        """
        # 方案 1：纯 LLM（准确但贵）
        # 对每对新闻调用 LLM 判断是否重复
        
        # 方案 2：Embedding + LLM（平衡）
        # 1. 用 embedding 模型计算新闻标题的相似度
        # 2. 对高相似度的新闻对，用 LLM 做最终确认
        
        # 方案 3：纯规则（便宜但不准）
        # 对中文和英文分别用关键词匹配
        
        # 推荐方案 2（如果 embedding 可用）或方案 1（如果新闻数量少）
        pass
    
    def _batch_grade_quality(self, news: list[NewsItem]) -> list[NewsItem]:
        """批量质量分级
        
        为每条新闻添加 importance / urgency / category / sentiment 标签
        """
        # 类似 _batch_generate_summaries，合并为一个 prompt
        pass
    
    def _call_llm(self, prompt: str, max_tokens: int = 500) -> str:
        """调用 LLM API
        
        包含：
        - 重试逻辑
        - 超时处理
        - 缓存检查
        - 错误降级（返回空字符串）
        """
        # TODO: 实现 LLM 调用
        pass
```

### 3.3 Prompt 设计示例

#### 摘要生成 Prompt

```python
SUMMARY_GENERATION_PROMPT = """你是一位金融新闻编辑。请为以下新闻标题生成简短摘要（1-2 句话）。

要求：
1. 摘要必须基于标题内容，不要编造信息
2. 如果标题信息不足，生成最合理的推测性摘要，并标记为[推测]
3. 摘要长度不超过 50 个字
4. 使用与标题相同的语言

新闻列表：
{news_list}

请按以下 JSON 格式返回：
[
  "摘要 1",
  "摘要 2",
  ...
]
"""
```

#### 跨源去重 Prompt

```python
DEDUPLICATION_PROMPT = """你是一位金融新闻编辑。请判断以下两条新闻是否描述同一事件。

新闻 A：{title_a}（来源：{source_a}，语言：{lang_a}）
新闻 B：{title_b}（来源：{source_b}，语言：{lang_b}）

请只回答 "是" 或 "否"，不要解释。
"""
```

#### 质量分级 Prompt

```python
QUALITY_GRADING_PROMPT = """你是一位金融分析师。请为以下新闻评估其重要性和紧急性。

新闻：{title}
摘要：{summary}
来源：{source}

请按以下 JSON 格式返回：
{{
  "importance": "high|medium|low",
  "urgency": "immediate|high|medium|low",
  "category": "宏观政策|行业动态|个股新闻|国际市场|其他",
  "sentiment": "positive|negative|neutral",
  "relevance_tags": ["标签1", "标签2"]
}}
"""
```

#### 行情摘要 Prompt

```python
MARKET_SUMMARY_PROMPT = """你是一位金融评论员。请根据以下行情数据，生成一段简短的市场综述（100-200 字）。

数据：
{quotes_json}

要求：
1. 提及主要指数和重点个股的表现
2. 指出涨跌幅最大的几个标的
3. 使用中文
4. 客观描述，不要预测未来
"""
```

---

## 四、成本分析

### 4.1 GPT-4o mini 定价（参考）

| 操作 | 输入 token | 输出 token | 成本（美元） |
|------|-----------|-----------|-------------|
| 生成 1 条摘要 | ~200 | ~50 | ~$0.0001 |
| 质量分级 1 条新闻 | ~300 | ~100 | ~$0.0002 |
| 行情摘要（批量） | ~1000 | ~200 | ~$0.0005 |
| 跨源去重（1 对） | ~400 | ~10 | ~$0.0001 |

### 4.2 单次请求成本估算

假设一次分析请求处理：
- 15 条新闻（5 条 RSS + 5 条 GNews + 5 条 Juhe）
- 10 个行情标的

| 功能 | 调用次数 | 单次成本 | 总成本 |
|------|---------|---------|--------|
| 生成缺失摘要（Juhe 5 条） | 1 次批量调用 | $0.0005 | $0.0005 |
| 质量分级（15 条） | 3 次批量调用（每批 5 条） | $0.001 | $0.003 |
| 跨源去重 | 5 次（高相似度对） | $0.0001 | $0.0005 |
| 行情摘要 | 1 次 | $0.0005 | $0.0005 |
| **总计** | | | **~$0.005** |

**结论**：单次请求成本约 0.5 美分，非常低廉。

### 4.3 日成本估算

假设用户每天使用 10 次：
- 日成本：10 × $0.005 = $0.05
- 月成本：30 × $0.05 = $1.5

**结论**：成本完全可接受。

---

## 五、与上游 Agent 的分工边界

### 5.1 小型 LLM（engine 内部）的职责

| 职责 | 说明 | 为什么放在 engine |
|------|------|------------------|
| **摘要生成** | 为缺失摘要的新闻生成简短摘要 | 数据补全，属于数据层 |
| **跨源去重** | 判断不同源的新闻是否同一事件 | 减少 Agent 的重复处理 |
| **质量分级** | importance / urgency / category / sentiment | 为 Agent 提供预筛选依据 |
| **行情摘要** | 结构化数据 → 自然语言 | 减少 Agent 的解析工作 |
| **格式标准化** | 时间格式、来源名称统一 | 数据清洗，属于数据层 |

### 5.2 上游 Agent 的职责

| 职责 | 说明 | 为什么放在 Agent |
|------|------|-----------------|
| **深度分析** | 结合用户持仓做个性化分析 | 需要用户上下文 |
| **投资决策** | 买入/卖出/持有建议 | 需要用户风险偏好 |
| **多轮对话** | 追问、澄清、确认 | 需要对话状态 |
| **跨领域整合** | 结合宏观经济、行业趋势 | 需要更广泛的知识 |
| **最终输出** | 生成人类可读的投资报告 | 需要用户偏好格式 |

### 5.3 明确的分工原则

```
Engine 的小型 LLM 做："数据层面的语义处理"
Agent 的 LLM 做："决策层面的深度分析"

分界线：是否涉及用户上下文
- 不涉及用户上下文 → engine 做
- 涉及用户上下文 → Agent 做
```

---

## 六、实现建议

### 6.1 作为可选模块，默认禁用

```python
# engine/core.py
class StocksEngine:
    def __init__(self, config: EngineConfig):
        self.fetchers = DataFetchers(config)
        self.scaffolds = Scaffolds(config)
        self.llm_enhancer = LLMEnhancer(config.llm_enhancer)  # 可选模块
        self.persistence = Persistence(config)
    
    def build_context(self, detail_level: str = "standard") -> AnalysisContext:
        # 1. 获取原始数据
        quotes = self.fetchers.get_quotes()
        news = self.fetchers.get_news()
        
        # 2. 轻量归纳
        portfolio = self.scaffolds.analyze_portfolio(quotes)
        drift = self.scaffolds.detect_drift(portfolio)
        
        # 3. LLM 增强（可选）
        if self.llm_enhancer.config.enabled:
            news = self.llm_enhancer.enhance_news(news)
            market_summary = self.llm_enhancer.generate_market_summary(quotes, portfolio)
        else:
            market_summary = ""
        
        # 4. 组装上下文
        return AnalysisContext(
            quotes=quotes,
            news=news,
            portfolio_mapping=portfolio,
            drift_checks=drift,
            market_summary=market_summary,  # 新增字段
        )
```

### 6.2 渐进式启用

建议分阶段实现：

| 阶段 | 功能 | 优先级 | 原因 |
|------|------|--------|------|
| **Phase 1** | 摘要生成 | 高 | 解决 Juhe 源的核心痛点 |
| **Phase 2** | 行情摘要 | 高 | 对 Agent 价值大，成本低 |
| **Phase 3** | 质量分级 | 中 | 增强 Agent 的筛选能力 |
| **Phase 4** | 跨源去重 | 中 | 技术复杂，需要 embedding |
| **Phase 5** | 格式标准化 | 低 | 用规则即可，不需要 LLM |

### 6.3 配置示例

```json
{
  "llm_enhancer": {
    "enabled": false,
    "model": "gpt-4o-mini",
    "features": {
      "generate_missing_summaries": true,
      "cross_source_deduplication": false,
      "quality_grading": true,
      "natural_language_summary": true
    },
    "limits": {
      "max_llm_calls_per_request": 20,
      "max_news_items_to_process": 15
    },
    "api_key": "${OPENAI_API_KEY}"
  }
}
```

---

## 七、风险与缓解

### 7.1 LLM 幻觉风险

**风险**：LLM 生成摘要时编造信息。

**缓解**：
- Prompt 明确要求"基于标题内容，不要编造"
- 对生成摘要添加 `[LLM生成]` 标记，Agent 知道这是推测
- 设置温度 = 0.3，减少随机性

### 7.2 API 失败风险

**风险**：LLM API 调用失败，导致整个请求失败。

**缓解**：
- 所有 LLM 调用有 try-catch，失败时降级为原始数据
- 设置超时（如 10 秒），超时则跳过增强
- 缓存 LLM 结果，减少重复调用

### 7.3 成本失控风险

**风险**：新闻数量过多，LLM 调用次数超出预算。

**缓解**：
- `max_llm_calls_per_request` 硬限制
- `max_news_items_to_process` 硬限制
- 按 importance 排序，只处理高重要性新闻

### 7.4 与 Agent 重复工作

**风险**：engine 的 LLM 和 Agent 的 LLM 做重复工作。

**缓解**：
- 明确分工：engine 做"数据层语义处理"，Agent 做"决策层深度分析"
- engine 的 LLM 输出标记为 `enhanced_by_llm`，Agent 知道哪些已经处理过
- 如果 Agent 不信任 engine 的增强，可以忽略并自己重新处理

---

## 八、结论

### 8.1 是否值得添加？

**是的，值得添加，但必须是可选模块，默认禁用。**

理由：
1. **解决真实痛点**：Juhe 源无摘要、跨源去重、质量分级都是 engine 用规则无法解决的问题
2. **成本低廉**：单次请求约 0.5 美分，月成本约 $1.5
3. **显著提升 Agent 体验**：Agent 收到的是"预处理过的高质量数据"，而不是"原始异构数据"
4. **不替代 Agent**：只做数据层处理，不做决策层分析

### 8.2 核心设计原则

1. **可选性**：默认禁用，用户/Agent 显式启用
2. **降级性**：LLM 失败时不影响核心功能
3. **透明性**：所有 LLM 增强的数据标记来源
4. **成本可控**：硬限制调用次数和处理数量
5. **分工明确**：engine 做数据层，Agent 做决策层

### 8.3 对现有设计的影响

| 文件 | 变更 |
|------|------|
| `engine/llm_enhancer.py` | 新增：小型 LLM 增强模块 |
| `engine/context_builder.py` | 修改：集成 llm_enhancer，新增 `market_summary` 字段 |
| `engine/core.py` | 修改：初始化 llm_enhancer |
| `domain/models.py` | 修改：`NewsItem` 增加 `enhanced_by_llm` 标记 |
| `config/engine.json` | 新增：llm_enhancer 配置 |
| `adapters/cli.py` | 修改：增加 `--llm-enhance` 选项 |

---

*分析结论：在 engine 内部添加小型 LLM 增强模块是**有价值且可行**的。它能解决 engine 用规则无法处理的语义层面问题（摘要生成、跨源去重、质量分级），同时保持与上游 Agent 的清晰分工。建议作为可选模块实现，默认禁用，渐进式启用。*
