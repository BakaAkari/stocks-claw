> ARCHIVED 2026-07:结论已吸收或废弃,勿作为开发依据。

# 架构边界分析：信息源管理与内容压缩的现实检验

> 基于 stocks-claw v1 实际代码的务实分析
> 分析日期: 2026-06-05

---

## 一、现有数据源的异构性分析

### 1.1 行情数据（Quote）— 已经统一，但代价明确

现有 3 个行情 provider，数据格式差异：

| Provider | 协议 | 原始格式 | 字段映射 | 编码 |
|----------|------|----------|----------|------|
| **Finnhub** | HTTPS/JSON | `{"c": 123.4, "pc": 120.0, "d": 3.4, "dp": 2.83}` | `c→price`, `pc→prev_close`, `d→change`, `dp→pct_change` | UTF-8 |
| **腾讯** | HTTPS/文本 | `v_s_sh000300="1~沪深300~000300~3542.33~12.45~0.35~..."` | 按 `~` 分隔取 parts[3], [4], [5] | **GBK** |
| **东方财富** | HTTPS/JSON | `{"f2": 123.4, "f3": 2.83, "f4": 3.4, ...}` | `f2→price`, `f3→pct_change`, `f4→change` | UTF-8 |

**统一方式**：每个 provider 内部做字段映射，输出统一的 `Quote` dataclass。

```python
@dataclass(frozen=True)
class Quote:
instrument: Instrument
price: Optional[float]
change: Optional[float]
pct_change: Optional[float]
volume_lot: Optional[float]
amount_10k: Optional[float]
open_price: Optional[float] = None
high: Optional[float] = None
low: Optional[float] = None
prev_close: Optional[float] = None
```

**这个统一是成功的**，因为：
- 所有行情数据本质上是同一类信息（价格、涨跌幅、成交量）
- 字段映射是**一一对应**的，没有语义损失
- 腾讯的 GBK 编码问题在 provider 内部解决，不暴露给上层

**但代价是**：每个新 provider 都需要写独立的解析逻辑（如腾讯的 `_parse_line`、东方财富的 `_row_to_quote`）。这不是"统一处理"，而是"独立适配 + 统一输出"。

---

### 1.2 新闻数据 — 异构性极大，无法简单统一

现有 4 个新闻源，差异远超行情数据：

#### 字段差异

| 字段 | Yahoo RSS | GNews | Juhe 235 | Juhe 743 |
|------|-----------|-------|----------|----------|
| **标题** | `title` | `articles[].title` | `result.data[].title` | `result.newslist[].title` |
| **摘要** | `description` | `articles[].description` | ** 无摘要** | ** 无摘要** |
| **URL** | `link` | `articles[].url` | `result.data[].url` | `result.newslist[].url` |
| **时间** | `pubDate` (RFC 822) | `publishedAt` (ISO 8601) | `date` (格式未知) | `ctime` (格式未知) |
| **来源** | 无 | `source.name` | `author_name`/`media_name` | `source` |
| **语言** | 英文 | 英文 | 中文 | 中文 |
| **协议** | RSS/XML | REST/JSON | REST/JSON | REST/JSON |

#### 关键问题

**问题 1：摘要缺失**

两个 Juhe 源（235 和 743）**完全不返回摘要**。现有代码的处理方式是硬编码空字符串：

```python
# news_fetch_service.py 第 149 行
'summary': '', # 聚合数据API不返回摘要

# 第 201 行
'summary': '', # API不返回摘要
```

这意味着：
- 如果 Agent 或 engine 的压缩策略依赖"摘要"字段做内容筛选，Juhe 源的数据会被错误地判定为"无价值"
- 如果 compact 级别只输出"标题+摘要"，Juhe 源的新闻在 compact 模式下几乎为空

**问题 2：时间格式不统一**

- RSS: `pubDate` = `"Fri, 05 Jun 2026 14:30:00 GMT"` (RFC 822)
- GNews: `publishedAt` = `"2026-06-05T14:30:00Z"` (ISO 8601)
- Juhe 235: `date` = 格式未知（可能是 `"2026-06-05 14:30:00"` 或其他）
- Juhe 743: `ctime` = 格式未知

现有代码**没有做任何时间标准化**，直接原样存储：

```python
'published_at': (item.findtext('pubDate') or '').strip(), # RSS
'published_at': (item.get('publishedAt') or '').strip(), # GNews
'published_at': (item.get('date') or '').strip(), # Juhe 235
'published_at': (item.get('ctime') or '').strip(), # Juhe 743
```

这意味着：
- 按时间排序可能出错（字符串排序 vs 时间排序）
- 去重逻辑（如"60分钟内容指纹去重"）可能失效
- Agent 无法判断新闻的时效性

**问题 3：来源字段语义不同**

- GNews: `source.name` = `"Reuters"`（媒体机构）
- Juhe 235: `author_name` = `"张三"`（个人作者）或 `media_name` = `"新浪财经"`（媒体）
- Juhe 743: `source` = `"澎湃新闻"`（媒体）
- RSS: 无来源字段，只有 URL

**问题 4：语言混合**

- RSS + GNews = 英文
- Juhe 235 + 743 = 中文

Agent 需要同时处理中英文新闻，或者 engine 需要按语言分组。

---

## 二、我的设计哪里过于理想化了？

### 2.1 错误假设 1："统一处理筛选清洗"

我设计的 `build_context()` 中假设 engine 可以统一压缩新闻数据：

```python
# 我之前的理想化设计
def _compress_news(news: list[NewsItem], level: str) -> list[NewsItem]:
if level == "compact":
return [{"title": n.title, "source": n.source} for n in news[:5]]
elif level == "standard":
return [{"title": n.title, "summary": n.summary, "source": n.source}
for n in news[:10]]
```

**现实问题**：
- Juhe 源的新闻 `summary` 永远是空字符串，standard 级别的输出对 Juhe 数据是浪费的
- 如果 engine 按"有摘要的新闻优先"排序，Juhe 数据会被系统性降级
- 如果 engine 不做这种排序，standard 级别对 Juhe 数据输出的是"标题+空摘要"，对 Agent 无价值

**正确做法**：压缩策略必须**按数据源分别处理**，或者 engine 需要标记每个新闻的"信息完整度"。

### 2.2 错误假设 2："三级输出粒度可以统一应用"

我假设 `compact`/`standard`/`full` 三级可以统一应用到所有数据：

| 级别 | 假设的内容 |
|------|-----------|
| compact | 标题 + 来源 |
| standard | 标题 + 摘要 + 来源 |
| full | 完整内容 |

**现实问题**：
- 对于 Juhe 源，standard = compact（因为没有摘要）
- 对于 RSS/GNews，standard 确实比 compact 多摘要
- 对于 full 级别，RSS 的 `description` 本身就是摘要（不是全文），GNews 的 `description` 也是摘要
- **没有任何一个源返回"全文"**，所以 full 级别实际上和 standard 差不多

**正确做法**：三级粒度设计需要**按数据源类型分别定义**，或者放弃"统一三级"，改为"数据源自适应输出"。

### 2.3 错误假设 3："engine 了解数据重要性"

我假设 engine 可以基于数据结构判断"哪些新闻更重要"：

```python
# 理想化设计
# 只保留：用户持仓中涨跌幅最大的前 5 个
return sorted(quotes, key=lambda q: abs(q.change_pct), reverse=True)[:5]
```

**现实问题**：
- 新闻的"重要性"不是结构化的，是语义化的
- engine 无法判断"美联储降息"和"某公司季度财报"哪个对用户更重要
- 只有 Agent（LLM）才能做这种语义判断
- engine 能做的最多是"去重"和"时间过滤"，不能做"重要性排序"

**正确做法**：engine 只做**结构化过滤**（去重、时间范围、语言筛选），不做**语义筛选**。语义筛选交给 Agent。

---

## 三、修正后的务实设计

### 3.1 信息源管理 — 由 engine 负责（修正后）

engine 负责的不是"统一处理数据"，而是：

1. **配置管理**：哪些源启用、API Key 存储、配额监控
2. **调度执行**：按配置调用各个 fetcher
3. **错误隔离**：某个源失败不影响其他源（现有代码已经做到了）
4. **原始数据存储**：每个源的原始响应独立存储（用于调试和审计）

**engine 不负责**：
- 跨源数据格式统一（每个源保留自己的原始格式）
- 跨源语义去重（不同源对同一事件的报道，标题可能完全不同）
- 跨源重要性排序

### 3.2 数据适配 — 每个源独立适配，输出到标准模型（带缺失标记）

```python
@dataclass
class NewsItem:
title: str
url: str
source_name: str # 来源名称（各源统一后的）
source_type: str # "rss" | "gnews" | "juhe_235" | "juhe_743"
published_at: Optional[datetime] # 标准化后的时间（可能解析失败为 None）
summary: Optional[str] # 摘要（可能为 None，如 Juhe 源）
language: str # "en" | "zh" | "unknown"
raw_metadata: dict # 原始字段（保留用于调试）
```

**关键修正**：
- `summary` 是 `Optional[str]`，不是 `str`。Juhe 源返回 `None`，不是空字符串
- `published_at` 是 `Optional[datetime]`，解析失败时标记为 `None`
- 保留 `raw_metadata` 让 Agent 可以访问原始数据（如果需要）
- 保留 `source_type` 让 Agent 知道数据来源，可以按源做不同处理

### 3.3 内容压缩 — 分层处理，engine 只做结构化过滤

| 层级 | 职责 | 归属 | 说明 |
|------|------|------|------|
| **L1 原始数据获取** | 调用各源 API，获取原始响应 | **engine** | 每个源独立 fetcher |
| **L2 数据适配** | 解析原始响应，输出标准模型 | **engine** | 每个源独立 adapter，处理字段映射和编码 |
| **L3 结构化过滤** | 去重、时间范围过滤、语言筛选、空值过滤 | **engine** | 只基于结构化字段，不做语义判断 |
| **L4 语义压缩** | 判断重要性、提取关键信息、生成摘要 | **Agent** | 需要 LLM 能力 |
| **L5 深度分析** | 投资建议、主题判断 | **Agent** | 默认由 Agent 做 |

**engine 的 L3 结构化过滤具体做什么**：

```python
def _structural_filter(news: list[NewsItem]) -> list[NewsItem]:
"""engine 只做结构化过滤，不做语义判断"""

# 1. 去重：基于 URL + 标题相似度（简单字符串匹配）
seen_urls = set()
unique = []
for item in news:
if item.url in seen_urls:
continue
seen_urls.add(item.url)
unique.append(item)

# 2. 时间过滤：只保留最近 24 小时（可配置）
cutoff = datetime.now() - timedelta(hours=24)
recent = [n for n in unique if n.published_at and n.published_at > cutoff]

# 3. 空值过滤：去掉 title 为空的新闻
valid = [n for n in recent if n.title.strip()]

# 4. 按时间排序（最新的在前）
# 注意：published_at 为 None 的新闻排在最后
return sorted(valid, key=lambda n: n.published_at or datetime.min, reverse=True)
```

**engine 不做的事情**：
- 不按"摘要长度"判断重要性（Juhe 没摘要不代表不重要）
- 不做跨源语义去重（"Fed Raises Rates" 和 "美联储加息" 是同一事件，但 engine 无法判断）
- 不生成摘要（Juhe 没摘要，engine 不能凭空生成）

### 3.4 输出粒度 — 放弃"统一三级"，改为"数据源自适应"

**修正前的理想化设计**：

```python
detail_level: Literal["compact", "standard", "full"] = "standard"
```

**修正后的务实设计**：

```python
class NewsOutputConfig:
"""新闻输出配置 — 按数据源分别控制"""

# 全局限制
max_total_items: int = 20 # 总新闻条数上限
max_age_hours: int = 24 # 时间范围

# 按源分别控制（因为各源数据完整度不同）
per_source_limits: dict[str, int] = {
"rss": 5, # RSS 有摘要，可以多取几条
"gnews": 5, # GNews 有摘要，可以多取几条
"juhe_235": 3, # Juhe 没摘要，少取几条
"juhe_743": 3, # Juhe 没摘要，少取几条
}

# 字段选择（按源分别控制）
per_source_fields: dict[str, list[str]] = {
"rss": ["title", "summary", "url", "published_at", "source_name"],
"gnews": ["title", "summary", "url", "published_at", "source_name"],
"juhe_235": ["title", "url", "published_at", "source_name"], # 没 summary
"juhe_743": ["title", "url", "published_at", "source_name"], # 没 summary
}
```

**为什么这样设计**：
- 承认不同源的数据完整度不同
- 不强迫所有源输出相同的字段
- Agent 看到输出时，能清楚知道每个新闻有哪些字段可用
- 避免 Juhe 源输出"标题 + 空摘要"的尴尬

### 3.5 Agent 的职责 — 处理 engine 无法做的事情

Agent 需要处理 engine 无法处理的异构性：

| 问题 | engine 的处理 | Agent 需要做的 |
|------|--------------|---------------|
| 中英文混合 | engine 标记 `language` 字段 | Agent 按语言分组，或分别处理 |
| 摘要缺失 | engine 标记 `summary=None` | Agent 对 Juhe 新闻降低期望，或自己生成摘要 |
| 时间格式混乱 | engine 尽量解析，失败标记 `None` | Agent 对 `published_at=None` 的新闻做特殊处理 |
| 跨源语义去重 | engine 不做 | Agent 用 LLM 判断 "Fed Raises Rates" 和 "美联储加息" 是否同一事件 |
| 重要性排序 | engine 只按时间排序 | Agent 根据用户持仓和兴趣做语义重要性排序 |
| 全文缺失 | engine 只返回摘要/标题 | Agent 决定是否需要 fetch 全文（如果需要，调用 engine 的 fetch_full_text 工具） |

---

## 四、对现有代码的借鉴

### 4.1 行情数据的统一模式 — 可以复用

现有代码的 `QuoteProvider` + `ProviderRegistry` + `Quote` dataclass 模式是成功的：

```python
# base.py — 抽象接口
class QuoteProvider(ABC):
@abstractmethod
def get_quote(self, instrument: Instrument) -> Quote: ...

# tencent_a.py / eastmoney_a.py / finnhub_quote.py — 各自实现解析
# registry.py — 按市场注册 provider
```

这个模式可以复用到新闻数据：

```python
# 新增
class NewsProvider(ABC):
@abstractmethod
def fetch(self, limit: int = 10) -> list[NewsItem]: ...

# rss_provider.py / gnews_provider.py / juhe_235_provider.py / juhe_743_provider.py
# news_registry.py — 按类型注册
```

### 4.2 新闻数据的现有问题 — 需要修正

现有 `news_fetch_service.py` 的问题：

1. **所有 fetch 方法在一个类里**：`fetch_rss`, `fetch_gnews`, `fetch_juhe`, `fetch_juhe_caijing` 都写在 `NewsFetchService` 里，违反单一职责
2. **没有抽象接口**：没有 `NewsProvider` base class，新增源需要改 `NewsFetchService`
3. **硬编码空摘要**：Juhe 源的 `summary: ''` 应该改为 `summary: None`，让上层知道这是"缺失"不是"空"
4. **时间未标准化**：`published_at` 存储原始字符串，没有解析为 `datetime`
5. **没有语言标记**：Agent 不知道哪些是中文、哪些是英文

---

## 五、修正后的职责边界

| 职责 | engine | Agent |
|------|--------|-------|
| **信息源配置** | 管理数据源、API Key、fallback | 不接触 |
| **数据获取** | 调用各源 API，错误隔离 | 不直接调用 |
| **数据适配** | 每个源独立解析，输出标准模型（带缺失标记） | 不处理 |
| **结构化过滤** | 去重、时间过滤、空值过滤、按时间排序 | 不做 |
| **语义压缩** | 不做（无法判断重要性） | Agent 用 LLM 做 |
| **跨源语义去重** | 不做（不同语言/标题无法匹配） | Agent 用 LLM 做 |
| **摘要生成** | 不做（Juhe 没摘要，engine 不能凭空生成） | Agent 需要时自己生成 |
| **深度分析** | 默认不做 | 默认由 Agent 做 |
| **输出格式** | 结构化 JSON（按数据源分别控制字段） | 人类可读文本 |

---

## 六、关键修正总结

### 6.1 放弃"统一三级压缩"

原设计：`compact`/`standard`/`full` 统一应用到所有数据

修正后：按数据源分别控制输出字段和条数，因为各源数据完整度不同

### 6.2 放弃"engine 判断重要性"

原设计：engine 按涨跌幅排序、按摘要长度筛选

修正后：engine 只按时间排序，重要性判断交给 Agent

### 6.3 承认字段缺失

原设计：假设所有新闻都有 `summary`

修正后：`summary: Optional[str]`，Juhe 源返回 `None`，Agent 需要处理缺失

### 6.4 保留原始数据

原设计：engine 只返回处理后的数据

修正后：标准模型保留 `raw_metadata` 字段，Agent 可以访问原始数据

---

*分析结论：信息源管理由 engine 负责是正确的，但内容压缩不能由 engine 统一处理。engine 只做结构化过滤（去重、时间、空值），语义层面的压缩和筛选必须由 Agent 处理。新闻数据的异构性比行情数据大得多，需要按数据源分别适配、分别控制输出。*
> ARCHIVED 2026-07:结论已吸收或废弃,勿作为开发依据。
