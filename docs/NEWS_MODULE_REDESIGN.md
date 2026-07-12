# 新闻情报模块重构方案

**状态**: 草案 v1.0 — 待多角度迭代评审
**日期**: 2026-07-13
**范围**: `IntelligenceHarvester` → `IntelligenceAnalyzer` → `intelligence_brief.py` → 飞书推送

---

## 1. 现状诊断

### 1.1 数据流

```
数据采集层 (IntelligenceHarvester)
  ├─ GNews API      (4 关键词, 96次/天, 免费限 100)
  ├─ Google RSS     (8 关键词, 无限)
  ├─ Finnhub News   (3 类目: general/commodity/crypto, 60次/分钟限)
  └─ 宏观/行情       (FRED/Yahoo/Binance/Finnhub)

分析层 (IntelligenceAnalyzer)
  ├─ 关键词主题聚类 → 6 个主题
  ├─ 字典式情感判断 → positive/negative/neutral
  ├─ 市场影响评估   → 6 个市场方向
  └─ 硬编码信号生成 → buy/sell/hold/watch

输出层 (intelligence_brief.py)
  ├─ LLM 翻译 (新增) → cluster titles + signal rationales
  ├─ LLM 快速总结     → 3-5 句中文综述
  └─ 格式化为飞书文本  → stdout → cron 推送
```

### 1.2 关键缺陷

| 层级 | 问题 | 严重度 |
|---|---|---|
| 采集 | 关键词 8 个，漏 CPI/ECB/tariffs/copper/credit 等大板块 | 高 |
| 采集 | GNews 只有前 4 个关键词，"Federal Reserve" 排在第 7 位没被 GNews 覆盖 | 高 |
| 分析 | 聚类单标签，一条新闻只能归一个主题 | 中 |
| 分析 | 主题只有 6 个，缺 inflation/credit/currencies/industrial_commodities 等 | 高 |
| 分析 | 情感判断查字典不看上下文，"fear of missing out" 被判 negative | 中 |
| 分析 | 信号生成硬编码阈值，不交叉验证，不做持续性判断 | 中 |
| 分析 | 同一主题内不做子聚类，多件不同的事被揉成一个 cluster | 低 |
| 输出 | 已解决：翻译 + 诊断日志 (2026-07-13) | — |

---

## 2. 市场覆盖面设计

### 2.1 目标覆盖矩阵

一个完整的宏观/跨资产情报系统应覆盖以下领域：

```
═══════════════════════════════════════════════════════════
大类                细项                        当前  目标
───────────────────────────────────────────────────────
央行/货币政策        Fed, ECB, BOJ, BOE, PBOC    △    ✓
通胀/就业            CPI, PPI, PCE, 非农, 失业    ✗    ✓
利率/固收            美债收益率, 信用利差, TIPS     △    ✓
外汇                DXY, EURUSD, USDJPY, USDCNY   △    ✓
权益                美股, 欧股, 日股, 中国, EM     △    ✓
波动率              VIX, VSTOXX, MOVE             ✓    ✓
商品/能源           原油, 天然气, 铜, 黄金          △    ✓
加密货币            BTC, ETH                      ✓    ✓
地缘政治            关税, 制裁, 冲突, 选举          ✗    ✓
市场结构            资金流向, 情绪, 期权定位         ✗    △
───────────────────────────────────────────────────────
✓=已覆盖  △=部分覆盖  ✗=缺失
```

### 2.2 建议关键词 (8 → 16)

```
GNews 层 (前 4 个, 96次/天):
  0. "Federal Reserve interest rate"    货币政策核心
  1. "CPI inflation PPI data"           通胀数据发布
  2. "crude oil price supply OPEC"      能源
  3. "gold price"                       黄金

Google RSS 层 (全部 16 个):
  4. "VIX volatility stock market"      波动率/风险
  5. "US Treasury yield bond market"    利率/固收
  6. "ECB BOJ central bank policy"      全球央行
  7. "US Dollar Index currency forex"   外汇
  8. "copper industrial metals"         工业金属 (经济晴雨表)
  9. "Bitcoin crypto"                   加密货币
 10. "China stock market economy"       中国权益
 11. "tariffs trade war sanctions"      地缘政治/贸易
 12. "stock market sell-off correction" 风险事件
 13. "NVIDIA AI semiconductor tech"     科技板块
 14. "defense aerospace spending"       国防板块
 15. "credit spread high yield corporate" 信用市场
```

### 2.3 关键词设计原则

- **不做持仓映射** — 覆盖面由市场结构决定，不由当前持仓决定
- **互斥性优先** — 每个关键词覆盖一个独立的市场维度
- **优先级分层** — 前 4 个给 GNews（有 API 限额），剩余给无限源

---

## 3. 分析层改进

### 3.1 主题聚类 (6 → 12)

```python
THEME_KEYWORDS = {
    "monetary_policy":  ["fed", "federal reserve", "ecb", "boj", "boe", "pboc",
                         "interest rate", "rate hike", "rate cut", "fomc", "central bank"],
    "inflation":        ["cpi", "ppi", "pce", "inflation", "deflation", "disinflation",
                         "price index", "core inflation"],
    "employment":       ["nonfarm", "unemployment", "jobs report", "payroll",
                         "jobless claims", "wage growth", "labor market"],
    "fixed_income":     ["treasury", "yield", "bond", "credit spread", "high yield",
                         "investment grade", "tips", "yield curve"],
    "currencies":       ["dollar index", "dxy", "usdcny", "eurusd", "usdjpy",
                         "forex", "currency", "exchange rate"],
    "commodities_energy": ["oil", "crude", "opec", "natural gas", "energy", "petroleum"],
    "commodities_metals": ["gold", "silver", "copper", "industrial metal", "precious metal"],
    "equities":         ["stock market", "s&p 500", "nasdaq", "equity", "sell-off",
                         "rally", "correction", "bear market", "bull market"],
    "tech":             ["ai", "artificial intelligence", "semiconductor", "nvidia",
                         "chip", "big tech", "cloud computing"],
    "geopolitics":      ["war", "conflict", "sanction", "tariff", "trade war",
                         "military", "attack", "tension", "election"],
    "china_macro":      ["china", "chinese", "pboc", "csi 300", "a-share",
                         "shanghai", "shenzhen", "stimulus"],
    "crypto":           ["bitcoin", "btc", "ethereum", "crypto", "defi", "blockchain"],
}
```

### 3.2 多标签聚类

当前一条新闻只取第一个匹配的主题。改为所有匹配的主题都分配：

```
旧: "Fed cuts rates, oil rallies" → monetary_policy (第一个命中)
新: "Fed cuts rates, oil rallies" → monetary_policy AND commodities_energy
```

实现：`_cluster_articles` 中改为 `_detect_themes()` 返回 list 而非单个 str，按最高置信度主题归入 primary cluster，同时在 cluster metadata 中标记交叉主题。

### 3.3 情感判断 → LLM 驱动

当前字典匹配的问题：不知道主语、看不了否定、分不清 asset 和 risk。

方案：复用翻译管道模式，在聚类完成后批量调 LLM 判情感：

```
输入: [cluster theme] + 全部 article titles
输出: {sentiment: bullish/bearish/neutral/mixed, confidence: 0-1,
       rationale: "一句中文理由"}
```

保留字典匹配作为 LLM 不可用时的降级。低温度 (0.1)，批量调用。

### 3.4 信号生成改进

当前硬编码阈值 + 孤立信号。改进：

- **交叉验证** — VIX↑ 时检查 gold/oil/bond 方向是否一致
- **持续性** — 读最近 N 小时 snapshots，判断是趋势还是 spike
- **宏观叙事** — 组合多个 cluster 的情感方向，尝试归纳宏观主题
  - 例: inflation↑ + fed hawkish + yield↑ + gold↓ → "紧缩叙事"
  - 例: vix↓ + equity↑ + crypto↑ + gold↓ → "风险偏好"

---

## 4. 数据质量与健壮性

### 4.1 API 用量追踪

每次 harvest 写一行 JSONL 到 `.local/usage/`：

```jsonl
{"ts":"...","source":"gnews","calls":4,"errors":0,"quota_daily":100}
{"ts":"...","source":"google_rss","calls":16,"errors":0}
{"ts":"...","source":"finnhub","calls":3,"errors":0,"throttled":0}
{"ts":"...","source":"binance","calls":1,"errors":0}
{"ts":"...","source":"yahoo","calls":3,"errors":0}
{"ts":"...","source":"fred","calls":1,"errors":0}
```

聚合脚本读取 → 日报表 → 决策扩容/缩量。

### 4.2 降级链

| 场景 | 当前行为 | 目标行为 |
|---|---|---|
| GNews 429 | 抛异常，source_status=error | 退到 Google RSS 填补，不抛异常 |
| Finnhub 429 | ProviderRateLimitError | 等待 retry-after，超时则退 |
| LLM 翻译失败 | 静默返回原文 | 已有 stderr 日志 (2026-07-13) ✓ |
| LLM 情感判断失败 | N/A | 降级到字典匹配 |
| 全部新闻源失败 | data_quality=degraded | 推送中显式标注"无新闻数据" |

### 4.3 去重增强

当前仅 URL 去重。增加标题相似度去重（同一事件不同 URL）：

```python
# 在 URL 去重后，对剩余文章做标题相似度去重
# 使用 LLM 或简单 Jaccard 相似度
if title_similarity(a, b) > 0.7:
    keep the one with earlier published_at
```

---

## 5. 实施路线

### Phase 1 — 覆盖面 (低风险, 今天可做)

- [ ] 关键词 8→16 个
- [ ] GNews 关键词重排序（Fed/CPI/oil/gold 在前 4）
- [ ] Google RSS 每词条数 10→12
- [ ] API 用量追踪 (JSONL)

### Phase 2 — 分析质量 (中风险, 需测试)

- [ ] 主题 6→12 个
- [ ] 多标签聚类
- [ ] LLM 情感判断 + 字典降级
- [ ] 子聚类（同主题内按标题相似度分组）

### Phase 3 — 信号深化 (高风险, 需大量测试)

- [ ] 跨资产交叉验证
- [ ] 持续性判断 (读历史 snapshots)
- [ ] 宏观叙事归纳
- [ ] 标题相似度去重
- [ ] 降级链补全

---

## 6. 风险与权衡

| 决策 | 收益 | 风险 |
|---|---|---|
| 16 个关键词 | 覆盖面大幅提升 | 噪声增加，analyzer 截断 80 条可能漏信号 |
| 多标签聚类 | 不丢交叉主题 | 一条新闻出现在多个 cluster，可能重复推送 |
| LLM 情感判断 | 准确度大幅提升 | 增加 LLM 调用 (~12 次/小时)，依赖 API 可用性 |
| 子聚类 | 区分同主题不同事件 | 增加复杂度，边界情况多 |
| API 用量追踪 | 透明化，可决策 | 维护成本，JSONL 文件会增长 |

---

## 7. 未决问题（待讨论）

1. **LLM 调用的成本和延迟** — 翻译 + 总结 + 情感判断 = 每小时 3 次 LLM 调用，内部代理撑得住吗？
2. **新闻时效性窗口** — 目前 lookback 6 小时。非农/CPI 发布后 5 分钟内就应有反应，6 小时窗口是否太大？
3. **去重粒度** — URL 去重 + 标题相似度够吗？同一事件从不同角度报道是否应该保留多条？
4. **飞书输出长度** — 目前 ~900 字上限，16 个关键词 + 12 个主题后可能撑爆。是否分"摘要推送"和"完整报告"两个版本？

---

*本文档将在后续迭代中从 Agent 视角、用户视角、运维视角分别评审。*
