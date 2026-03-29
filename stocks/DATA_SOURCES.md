# 数据源配置文档

本文档记录 stocks 系统的所有外部数据源配置。

---

## 一、行情数据源（价格数据）

### 配置文件
`stocks/config/markets.json`

### A股数据源

| 提供商 | 类型 | 状态 | 配置 |
|--------|------|------|------|
| **腾讯财经** | 主要源 | 活跃 | `provider: tencent` |
| **东方财富** | 备用源 | 活跃 | `provider: eastmoney` |

**监控标的**（`watchlist.json` 中配置）：
- 个股：贵州茅台、平安银行、紫金矿业
- ETF：沪深300、华安黄金ETF、游戏ETF、稀有金属ETF

### 美股数据源

| 提供商 | 类型 | 状态 | 配置 | API Key |
|--------|------|------|------|---------|
| **Finnhub** | 主要源 | 活跃 | `provider: finnhub` | `.secret/finnhub-key.md` |

**监控标的**：
- QQQ、AAPL、UNH、NVDA、BABA、GS、MSTR

### 刷新机制
- 手动刷新：`python3 stocks/services/market_data_service.py`
- 定时任务：通过 OpenClaw cron 触发
- 健康检查：`python3 stocks/cli/health_check.py`

---

## 二、新闻数据源（资讯输入）

### 配置文件
`stocks/config/news_sources.json`

### 源列表

| 名称 | 类型 | 语言 | 内容来源 | API Key 位置 |
|------|------|------|----------|--------------|
| **Yahoo Finance RSS** | RSS | 英文 | Yahoo财经、大宗商品 | 无需 |
| **GNews Multi-Asset** | API | 英文 | 国际新闻聚合 | `.secret/gnews-key.md` |
| **Juhe 新闻头条 (ID 235)** | API | 中文 | 全网财经聚合 | `.secret/juhe-key.md` |
| **Juhe 财经新闻 (ID 743)** | API | 中文 | 澎湃新闻等专业媒体 | `.secret/juhe-caijing-key.md` |

### 各源详情

#### 1. Yahoo Finance RSS
- **URL**: `https://finance.yahoo.com/news/rssindex`
- **类型**: RSS Feed
- **覆盖**: 美股、大宗商品、国际财经
- **更新**: 实时
- **配额**: 无限制

#### 2. GNews API
- **URL**: `https://gnews.io/api/v4/search`
- **查询词**: `stock market gold nasdaq`
- **语言**: 英文
- **配额**: 免费额度（具体查看 GNews 官网）
- **Key**: `76fc86a55bacb7dbcabed5ef99d80b55`

#### 3. Juhe 新闻头条 (ID 235)
- **URL**: `http://v.juhe.cn/toutiao/index`
- **类别**: `caijing`（财经）
- **配额**: 100次/天
- **Key**: `09e3f75e8b0909c3982e4350a209ad05`

#### 4. Juhe 财经新闻 (ID 743)
- **URL**: `http://apis.juhe.cn/fapigx/caijing/query`
- **来源**: 澎湃新闻等深度财经媒体
- **配额**: 查看 Juhe 官网
- **Key**: `2f1f87b064b584e7e47af6d33560fd3c`

### 刷新机制
- 手动刷新：`python3 stocks/services/news_fetch_service.py`
- 定时任务：随报告生成时自动刷新
- 去重：60分钟内容指纹去重

---

## 三、API Key 管理

### 文件位置
所有 API Key 存放在 `.secret/` 目录：

```
.secret/
├── finnhub-key.md       # Finnhub API Key（美股行情）
├── gnews-key.md         # GNews API Key（英文新闻）
├── juhe-key.md          # 聚合数据新闻头条 Key
└── juhe-caijing-key.md  # 聚合数据财经新闻 Key
```

### Key 格式
每个文件只包含 Key 字符串，无其他内容：
```
d72j6j1r01qlfd9nf290d72j6j1r01qlfd9nf29g
```

---

## 四、故障排查

### 行情数据离线
1. 检查健康状态：`python3 stocks/cli/health_check.py`
2. 手动刷新：`python3 stocks/services/market_data_service.py`
3. 检查 Finnhub Key 是否过期

### 新闻数据离线
1. 检查 API Key 是否配置正确
2. 检查配额是否用完
3. 手动测试：`python3 -c "from stocks.services.news_fetch_service import NewsFetchService; print(NewsFetchService().fetch_juhe(limit=3))"`

---

## 五、新增数据源流程

1. 在 `stocks/config/markets.json` 或 `news_sources.json` 中添加配置
2. 如需 API Key，创建 `.secret/xxx-key.md` 文件
3. 在 `stocks/services/` 中添加对应的 fetch 方法
4. 在 `news_fetch_service.py` 或 `market_data_service.py` 中集成
5. 更新本文档

---

*最后更新：2026-03-29*
