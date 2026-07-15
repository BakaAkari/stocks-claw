# 情报分析引擎全面重构

> **状态：部分实现，后续路线已由 2026-07-15 对抗性审查重排**
> LLM 语义分析、规则 fallback、SignalTracker 与 holdings-aware 分析已落地；文中 P3 闭环和部分 schema 仍是设计，不代表当前实现。
> 现行架构以 `ARCHITECTURE.md`、`stocks/DATA_MODEL.md` 和代码为准；交易质量与后续优先级见 `docs/TRADING_SYSTEM_ADVERSARIAL_REVIEW_20260715.md`。

**版本**: v2.0
**日期**: 2026-07-13
**状态**: 部分实现快照；Phase A 核心和 SignalTracker 已落地，完整效果反馈闭环未完成
**覆盖**: P0 聚类质量 → P1 数据源优化 → P2 跨集群合成+信号验证 → P3 回测闭环

---

## 0. 统一架构视角

P0-P2 本质上是**同一件事**：把文本分析从规则引擎移到 LLM。一条 LLM 调用同时解决聚类、去重、合成、信号生成。P3 是独立的回测管道。

```
                       ┌─ IntelligenceHarvester (不变) ─┐
                       │  GNews 4kw + RSS 16kw + Finnhub │
                       │  P1: GNews降频, RSS限流         │
                       └──────────────┬──────────────────┘
                                      ↓ 80-160 篇文章
              ┌─ LLM 批量分析 (一次调用, temperature=0.1) ─┐
              │                                             │
              │  P0: 语义去重 + 多标签子聚类 + 摘要生成      │
              │  P1: holdings 感知 (传入持仓列表)            │
              │  P2: 跨集群传导链 + 信号交叉验证            │
              │                                             │
              │  输入: articles[] + holdings[] + macro{}     │
              │  输出: 结构化 JSON (见 §3)                   │
              │                                             │
              └──────────────────┬──────────────────────────┘
                                 ↓
              ┌─ 规则后处理 (保留) ─────────────────────────┐
              │  · VIX/10Y 阈值追加信号                      │
              │  · risk_warning.assess_risk()                │
              │  · source_credibility() 加权                 │
              │  · 数据质量标注                              │
              └──────────────────┬──────────────────────────┘
                                 ↓
              build_intelligence_run → 产物不变 → 下游兼容     │
                                                             │
  ┌──────────────────────────────────────────────────────────┘
  │
  │  P3: 信号回测闭环 (独立管道)
  │
  ├─ 每次信号生成 → 记录 (symbol, direction, generated_at, generation_price, rationale)
  ├─ 24h/1w 后 → 价格验证 → correct/wrong
  └─ 汇总 → 胜率统计 → 反馈到 LLM prompt 的 few-shot examples
```

---

## 1. P0+P1+P2: LLM 驱动的语义分析 (合并为一个变更)

### 1.1 为什么合并

- **P0 聚类去重** → LLM 做语义去重 + 子聚类 + 摘要
- **P1 holdings 接入** → 把持仓列表传入 LLM context，让它标注 portfolio_impact
- **P2 跨集群合成** → LLM 同时看到所有 cluster，自然生成传导链
- **P2 信号交叉验证** → LLM 看到 VIX↑ + gold↓ 时自己会判断矛盾

一次 LLM 调用，四个产出全部拿到。分开做是重复劳动。

### 1.2 LLM 输入结构

```json
{
  "task": "analyze_intelligence",
  "context": {
    "collected_at": "2026-07-13T02:53:00Z",
    "macro": {
      "vix": 15.84,
      "us_10y_yield": 4.54,
      "dxy": 120.69,
      "gold": 4077,
      "crude_oil": 69.6,
      "usd_cny": 6.79
    },
    "holdings": [
      {"symbol": "NVDA", "exposure": "tech", "weight": "high"},
      {"symbol": "XLE", "exposure": "energy", "weight": "medium"},
      {"symbol": "GLD", "exposure": "gold", "weight": "high"}
    ],
    "articles": [
      {
        "id": 0,
        "title": "Oil prices spike on fresh US-Iran attacks...",
        "source": "MarketWatch",
        "source_credibility": 0.75,
        "published_at": "2026-07-13T02:32:00Z",
        "summary": "Oil prices surged 4% after US military strikes on Iran..."
      }
    ]
  }
}
```

### 1.3 LLM 输出 Schema (P0+P1+P2 合并)

```json
{
  "schema_version": 1,
  "deduped_articles": [
    {
      "article_ids": [0, 12, 45],
      "representative_title": "Oil prices spike...",
      "duplicate_count": 3
    }
  ],
  "clusters": [
    {
      "theme": "geopolitics",
      "sub_cluster": "us_iran_military_conflict",
      "summary_cn": "美军对伊朗发动军事打击，原油跳涨4%，全球风险资产承压",
      "sentiment": {
        "equity": "bearish",
        "oil": "bullish", 
        "gold": "bullish",
        "bond": "bearish",
        "dxy": "bullish"
      },
      "urgency": "critical",
      "confidence": 0.88,
      "portfolio_impact": {
        "NVDA": {"direction": "negative", "reason": "地缘风险压制科技估值"},
        "XLE": {"direction": "positive", "reason": "油价上涨直接利好能源板块"},
        "GLD": {"direction": "positive", "reason": "避险需求推高金价"}
      }
    }
  ],
  "cross_cluster_synthesis_cn": "美伊军事冲突→原油供应风险→油价跳涨→通胀预期上行→美债收益率走高→成长股估值承压。避险资产(黄金/美元)受益。与货币政策cluster的紧缩信号叠加，短期风险偏好恶化概率上升。",
  "signals": [
    {
      "symbol": "XLE",
      "direction": "accumulate",
      "rationale_cn": "地缘冲突推高能源价格，XLE直接受益。但需关注冲突外交解决窗口",
      "confidence": 0.72,
      "falsification_cn": "美伊在48小时内宣布停火",
      "horizon": "tactical",
      "cross_validated": true,
      "cross_validation_note": "与GLD避险信号同向，与NVDA科技承压信号反向——符合风险off模式"
    }
  ],
  "data_quality_notes": [
    "GNews今日配额耗尽，仅RSS+Finnhub源",
    "RSS源去重率: 73% (80篇→22篇独立)"
  ]
}
```

### 1.4 降级策略

```
LLM 调用成功 + JSON 解析成功 → 语义分析结果
LLM 超时/429/解析失败 → 降级到当前关键词匹配管道
   ↓
data_quality.analysis_mode = "fallback_rules"
data_quality.errors.append("LLM analysis failed: <reason>")
```

### 1.5 GNews 配额策略 (P1 运营层面)

问题：24 次/天 × 4 关键词 = 96 次，只剩 4 次余量。今天的实际数据确认：GNews 0 articles。

方案：

| 调整 | 效果 |
|---|---|
| 情报巡逻从每小时 → 每 2 小时 | 24→12 次/天, GNews 48 次 |
| **或** GNews 从 4 关键词 → 3 个 | 72 次/天, 28 次余量 |
| **或** 两者 | 36 次/天, 充足余量 |

推荐：**GNews 降到 3 关键词**（Fed + CPI + oil，去掉 gold——RSS 覆盖 gold 足够好）。频率保持每小时，因为事件触发需要高频轮询。

### 1.6 holdings 接入 (P1 一行改动)

```python
# scheduled_analysis.py _run_intelligence
- analyzer = IntelligenceAnalyzer(lookback_hours=6)
+ analyzer = IntelligenceAnalyzer(
+     lookback_hours=6,
+     holdings=self._get_user_holdings(),
+ )
```

持仓列表从 engine 的 watchlist + assets 提取，在 LLM context 中传给分析器。

---

## 2. P3: 信号回测闭环 (独立管道)

### 2.1 数据模型

```python
@dataclass
class TrackedSignal:
    signal_id: str
    generated_at: datetime
    symbol: str
    direction: str          # buy / sell / accumulate / reduce
    rationale: str
    generation_price: float
    confidence: float
    source: str             # "llm_analysis" / "rule_threshold"
    
    # 事后填充
    price_24h: Optional[float] = None
    price_1w: Optional[float] = None
    direction_correct_24h: Optional[bool] = None
    direction_correct_1w: Optional[bool] = None
    
    # 事后分析
    regime_at_generation: dict = field(default_factory=dict)  # {vix, cluster_themes}

@dataclass  
class SignalPerformance:
    total: int
    wins_24h: int
    wins_1w: int
    win_rate_24h: float
    win_rate_1w: float
    by_regime: dict   # {"high_vix": {"total": 12, "wins": 8, "rate": 0.67}}
    by_theme: dict    # {"geopolitics": {...}, "monetary_policy": {...}}
```

### 2.2 存储

`.local/signal_tracker/signals.jsonl` — 每行一条信号记录

`.local/signal_tracker/settlements.jsonl` — 结算记录

### 2.3 生命周期

```
生成信号 → 写入 signals.jsonl (generation_price 从 quotes 提取)
    ↓
cron: 每 6 小时扫描未结算信号
    ├─ 24h 到达 → 查询当前价格 → 计算方向正确性 → 写入 settlements
    └─ 1w 到达 → 同上
    ↓
每周汇总 → 生成 SignalPerformance → 反馈到 LLM prompt
```

### 2.4 反馈到 LLM (闭环)

```json
// 追加到 LLM 的 system prompt
{
  "performance_context": {
    "overall_win_rate_24h": 0.62,
    "best_regime": "high_vix (win_rate=0.78)",
    "worst_regime": "low_vix_complacent (win_rate=0.41)",
    "recent_trend": "improving (+0.05 vs last week)",
    "warning": "geopolitics集群信号准确率仅0.45，建议降低该类信号的confidence"
  }
}
```

---

## 3. 改动范围汇总

| 文件 | 改动 | 覆盖 |
|---|---|---|
| `intelligence_analyzer.py` | 新增 `LLMIntelligenceAnalyzer`，保留旧类为降级 | P0+P1+P2 |
| `intelligence_harvester.py` | GNews 关键词 4→3 | P1 |
| `scheduled_analysis.py` | 传入 holdings，切换到新 analyzer | P1 |
| `intelligence_brief.py` | 适配新 cluster 结构 | P0 |
| `signal_tracker.py` (新) | 信号记录 + 结算 + 统计 | P3 |
| `scripts/signal_settlement.py` (新) | cron 驱动的结算脚本 | P3 |
| `stocks/config/engine.yaml` | 新增 intelligence.llm_analysis 配置段 | P0 |
| 其他 | 不变 | — |

---

## 3.1 截至 2026-07-15 的实现映射

- 已实现:`LLMIntelligenceAnalyzer`、holdings 传入、GNews 关键词降为 3、JSON 解析降级、`SignalTracker`、结算脚本和基础测试。
- 已实现但需整改:category fallback 提高了 driver 字段覆盖,但多数为 synthesized `hold`,不能解释为独立资产级方向信号。
- 部分实现:跨集群合成、方向冲突和 holdings 影响已进入结构化产物,但 Driver/Conflict/Dissent 尚未共享同一匹配结果。
- 未完成:将版本化历史效果稳定反馈到生产 Prompt、足够样本的 Walk-forward、交易成本和策略版本归因。
- 当前默认测试为 536 passed / 2 failed;两项失败是固定日期 fixture 超出 Analyzer 六小时窗口。

## 4. 实施计划

### Phase A: LLM 分析核心 (P0+P1+P2 合并, 3-4 天)

- [ ] `LLMIntelligenceAnalyzer` — prompt + JSON schema + 解析 + 降级
- [ ] holdings 接入
- [ ] GNews 关键词 4→3
- [ ] intelligence_brief.py 适配
- [ ] 测试: mock LLM 响应验证输出结构
- [ ] E2E: 真实数据跑通全链路

### Phase B: 信号回测 (P3, 2-3 天)

- [ ] `signal_tracker.py` — 记录 + 结算逻辑
- [ ] `signal_settlement.py` — cron 结算脚本
- [ ] 性能统计 + LLM prompt 反馈
- [ ] 测试: 模拟信号生命周期

### Phase C: 调优 (持续)

- [ ] 24 小时真实数据验证去重率和 cluster 质量
- [ ] Prompt tuning
- [ ] 回测数据积累后的 few-shot 优化

---

## 5. 风险

| 风险 | 缓释 |
|---|---|
| LLM 分析质量不稳定 | 温度 0.1, 结构化输出, 降级管道 |
| Token 成本 | 一次调用 ~18K tokens, 24 次/天, 内部 API |
| 信号回测数据量小 | 前 2 周不反馈到 prompt, 仅统计 |
| 旧管道退化 | 保留完整 `IntelligenceAnalyzer` 为降级 |

---

*本文档覆盖 P0-P3 全部设计。实施从 Phase A 开始。*
