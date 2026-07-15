# 新闻情报与交易分析统一推送系统 — 重构设计

> **状态：活跃设计稿，尚未完整实施**
> 当前代码已具备时间 session、每小时情报、事件触发、LLM 分析和持仓报告，但尚未实现本文完整 UnifiedHarvester、统一报告、Delta 推送与风险生命周期。
> 现行实现以 `ARCHITECTURE.md` 和代码为准；实施优先级已被 `docs/TRADING_SYSTEM_ADVERSARIAL_REVIEW_20260715.md` 的 T1 路线约束。

**版本**: v2.0（全新重写）
**日期**: 2026-07-13
**状态**: 活跃设计稿，部分底层能力已实现；统一报告与事件驱动架构待实施
**前置评审**: 四角色共识评审 (量化/个人投资/金融预测/资产规划) — 见 `docs/archive/NEWS_MODULE_REDESIGN_v1.1_20260713.md`

---

## 1. 设计目标

将设计时快照中的两套推送系统（24次/天情报巡逻 + 8次/天盘面会话）重构为统一的**事件驱动+时间驱动双轨**交易分析师报告系统。

**核心原则**：
- 推送频率 = 事件密度，不是时间均匀分布
- 情报和盘面是同一份报告的左右两栏，不是两份独立推送
- 无事件时段系统持续采存但不打扰用户
- 重大事件发生后 5 分钟内产出可执行的完整报告

---

## 2. 当前架构问题诊断

### 2.1 推送碎片化

```
设计时快照一天 32 次推送（典型交易日）:
  24 次情报巡逻 → 其中 18-20 次是"无重大事件"
   8 次盘面会话 → 情报引用的是 stale 快照（时差 30-90 分钟）

真实 actionable 信号: ~4-6 次/天
推送/信号比: 32:5 ≈ 6:1 噪声率
```

### 2.2 两类推送相互隔离

| 问题 | 表现 |
|---|---|
| 数据重复采集 | harvest() 和 build_context() 各自调 Finnhub/Yahoo，同一时刻两次 API 调用 |
| 情报时效断开 | 盘面 agent 读的 intelligence_digest 是上次整点的快照，CPI 发布后盘面分析要等到下个整点 |
| (已否决) 叙事层 | ~~原计划 NarrativeBuilder~~ → 方案A否决，cluster 直接供 Agent 自行归纳。见 2026-07-13 决策 |
| 分析各自为政 | 两个 LLM agent（情报 Agent + 盘面 Agent）各写各的，风格、深度、结论不保证一致 |

### 2.3 缺乏事件响应能力

当前所有触发都是固定时间：
- 情报：每小时整点
- 盘面：cron 每 5 分钟检查 session 窗口

CPI 20:30 发布 → 下次情报在 21:00 → 30 分钟窗口 = 价格发现已完成。FOMC 02:00 决议 → 下次情报在 03:00 → 1 小时滞后。

---

## 3. 目标架构

```
                            ┌──────────────────────┐
                            │   Financial Calendar │
                            │   CPI/FOMC/NFP/PPI/  │
                            │   GDP/PCE + 财报     │
                            └──────────┬───────────┘
                                       │
              ┌────────────────────────┼────────────────────────┐
              ▼                        ▼                        ▼
     ┌────────────────┐    ┌────────────────────┐    ┌────────────────┐
     │ 事件驱动触发器   │    │ 时间驱动触发器       │    │ 异常波动触发器   │
     │                │    │                    │    │                │
     │ CPI/FOMC/NFP   │    │ CN 盘前 08:50      │    │ VIX 急升 >20%  │
     │ 数据发布后5分钟  │    │ CN 盘后 15:20      │    │ 金/油 >2%异动  │
     │ 地缘重大事件    │    │ US 盘前 21:00      │    │ 信用利差异常    │
     │ 央行紧急声明    │    │ US 盘后 04:20      │    │ 多资产同步异动  │
     └───────┬────────┘    └─────────┬──────────┘    └───────┬────────┘
             │                       │                       │
             └───────────────────────┼───────────────────────┘
                                     ▼
                         ┌──────────────────────┐
                         │   Unified Harvester  │
                         │                      │
                         │ 1. 新闻采集 (一次)     │
                         │ 2. 行情+宏观 (一次)    │
                         │ 3. 组合估值+轮动 (一次) │
                         │ 4. 情报分析            │
                         │ 5. 跨资产交叉验证      │
                         │ 6. 组合防御评估        │
                         └──────────┬───────────┘
                                    │
                         ┌──────────▼───────────┐
                         │    Report Builder    │
                         │                      │
                         │ ┌──────────────────┐ │
                         │ │ 情报栏 (左)       │ │
                         │ │ · 事件聚类+情感   │ │
                         │ │ · 事件聚类+情感   │ │
                         │ │ · 来源可信度标注   │ │
                         │ │ · 跨资产传导评估   │ │
                         │ ├──────────────────┤ │
                         │ │ 盘面栏 (右)       │ │
                         │ │ · 组合状态+盈亏   │ │
                         │ │ · 动作信号+轮动   │ │
                         │ │ · 资金配置建议    │ │
                         │ │ · 风险预警级别    │ │
                         │ ├──────────────────┤ │
                         │ │ 决策栏 (底)       │ │
                         │ │ · 事件→调仓映射   │ │
                         │ │ · 时间视域+置信度  │ │
                         │ │ · 情景概率分布    │ │
                         │ │ · 防御模式建议    │ │
                         │ └──────────────────┘ │
                         └──────────┬───────────┘
                                    │
                         ┌──────────▼───────────┐
                         │    Report Router     │
                         │                      │
                         │ 🚨 红色 → 紧急推送    │
                         │ 📊 橙色 → 完整推送    │
                         │ 📋 蓝色 → 精简推送    │
                         │ 📁 灰色 → 仅存档      │
                         └──────────────────────┘
```

---

## 4. 推送等级与触发规则

### 4.1 四级推送

| 等级 | 触发条件 | 内容 | 目标延迟 | 日均次数 |
|---|---|---|---|---|
| 🚨 **紧急** | VIX>35 / 地缘危机 / 流动性危机 / -12%止损 | 完整报告 + 组合防御建议 | <5 min | 0-1 |
| 📊 **完整** | CPI/FOMC/非农 发布 / 盘前 / 盘后 | 完整报告（三栏） | <10 min | 4-6 |
| 📋 **精简** | 盘中检查 / 单一数据偏离 / 无事件盘前 | 关键变化 + 异常预警 | <5 min | 2-4 |
| 📁 **存档** | 无事件时段 | 完整采存，不推送 | N/A | 14-18 |

**日均推送从 32 次降到 8-12 次，每次都可执行。**

### 4.2 完整触发规则表

```
═══════════════════════════════════════════════════════════════════════
触发类型         触发条件                        等级     推送时机
───────────────────────────────────────────────────────────────────
事件驱动
  CPI 发布       发布日 08:30 ET                  📊 完整   发布+5min
  非农 发布      发布日 08:30 ET                  📊 完整   发布+5min
  FOMC 决议      决议日 14:00 ET                  📊 完整   发布+5min
  PPI/PCE/GDP    发布日 08:30 ET                  📋 精简   发布+5min
  央行紧急声明   非预定 Fed/ECB/BOJ 重大声明       🚨 紧急   检测+2min
时间驱动
  CN 盘前        工作日 08:50 CST                  📊 完整   定时
  CN 盘后        工作日 15:20 CST                  📊 完整   定时
  US 盘前        工作日 21:00 CST                  📊 完整   定时
  US 盘后        工作日 04:20 CST (次日)           📊 完整   定时
  CN 开盘观察    工作日 09:45 CST                  📋 精简   定时
  US 开盘观察    工作日 22:00 CST                  📋 精简   定时
  CN 收盘前检查  工作日 14:35 CST                  📋 精简   定时
  US 收盘前检查  工作日 03:35 CST (次日)           📋 精简   定时
异常驱动
  VIX 急升      15分钟内 VIX 升 >20%              🚨 紧急   检测+2min
  金/油异动      15分钟内 gold/oil >2%             📋 精简   检测+5min
  多资产同步     3+ 资产同向 >1.5% + 信用利差走阔  🚨 紧急   检测+2min
  流动性信号     信用利差 >200bp / VIX>35          🚨 紧急   检测+2min
无事件
  非以上任何时段 情报持续采存，不推送               📁 存档   —
───────────────────────────────────────────────────────────────────
```

---

## 5. 统一报告结构

### 5.1 完整报告 (📊 级别)

```markdown
**交易分析师报告 · 2026-07-15 08:35 CST**
触发: CPI 发布 (3.3% vs 预期 3.1%) | 来源: BLS (Tier 1)

**━━━ 宏观环境 ━━━**
VIX 24.2 ↑3.1 | 10Y 4.45% ↑8bp | DXY 105.1 ↑0.3%
Gold 2410 ↑0.8% | Crude 78.5 ↓1.2% | BTC 62.4k ↓2.1%

**━━━ 关键事件 ━━━**
🔴 [monetary_policy] CPI 连续第三个月超预期，市场重新定价 Fed 路径
   来源: Reuters(可信度0.95) + Bloomberg(0.95) + 3个聚合器 |
   影响: 美股↓ 美债↓ 美元↑ 黄金短期承压 | 持续: tactical(1-4周)

🟡 [geopolitics] 美国扩大对华芯片出口限制范围
   来源: WSJ(0.90) + Reuters(0.95) | 持续: structural(>1月)

**━━━ 组合影响 ━━━**
纳指 QDII  142,000 → 建议减仓 5-10%
  → 高利率环境下，高估值成长股首当其冲
  → 相似事件 (2024-04 CPI) 后 2 周纳指 -3.2%
A股 ETF   76,000 → 维持，等待中国刺激信号
黄金       161,000 → 维持，短期承压但结构性看多

**━━━ 风险预警 ━━━**
当前风险级别: 🟠 减仓 (Level 2)
触发: 连续 3 次 CPI 超预期 + VIX 25-35 + 交叉验证确认
防御模式: cautious
建议: 暂停权益加仓 | 现金目标 +5% | 关注黄金对冲机会
情景概率: 紧缩 45% | 软着陆 30% | 滞胀 15% | 危机 10%

**━━━ 数据边界 ━━━**
情报采集: 2026-07-15 08:30 CST, 14/16 关键词命中, 去重后 72 条
来源质量: Tier1 6条 Tier2 48条 Tier3 18条 | 可信度加权平均 0.68
API 用量: GNews 4次(累计 12/100) Finnhub 3次 Yahoo 3次
数据缺口: 信用利差数据未接入, 中国 A 股实时行情仅腾讯源
```

### 5.2 精简报告 (📋 级别)

只含：宏观快照 (1行) + 异常变化 (如有) + 风险级别 + 数据边界。~300字。

### 5.3 紧急报告 (🚨 级别)

完整报告 + 组合防御建议（具体对冲工具、建议现金比例、暂停信号清单）。立即推送，独立于正常周期。

---

## 6. 数据管道重构

### 6.1 UnifiedHarvester（统一采集层）

```python
class UnifiedHarvester:
    """一次调用完成所有数据采集，消除 API 重复请求。"""

    async def harvest(self, *, trigger_type: str) -> UnifiedSnapshot:
        # Phase 1: 并行采集 (所有 API 调用同时进行)
        news_task = self._fetch_news_all()       # 新闻 (GNews + RSS + Finnhub)
        quotes_task = self._fetch_quotes_all()    # 行情 (Finnhub + Binance)
        macro_task = self._fetch_macro_all()      # 宏观 (FRED + Yahoo)

        news, quotes, macro = await asyncio.gather(
            news_task, quotes_task, macro_task
        )

        # Phase 2: 分析层
        clusters = self._analyze_news(news)       # 聚类 + 情感 + 来源权重
        cross_validation = self._cross_validate(clusters, macro, quotes)
        defense_signal = self._assess_defense(clusters, quotes)

        # Phase 3: 组合层
        portfolio = await self._build_portfolio_context(quotes, macro)

        return UnifiedSnapshot(
            news=news, clusters=clusters,
            macro=macro, quotes=quotes, portfolio=portfolio,
            cross_validation=cross_validation, defense=defense_signal,
            trigger_type=trigger_type,
        )
```

### 6.2 消除的数据重复

| 重复项 | 当前 | 统一后 |
|---|---|---|
| Finnhub 行情 | harvest() 调 1 次 + build_context() 调 1 次 | 1 次 |
| Yahoo 宏观 | harvest() 调 1 次 + 不调 | 1 次 |
| FRED 数据 | harvest() 调 1 次 + 不调 | 1 次 |
| Binance BTC | harvest() 调 1 次 + 不调 | 1 次 |
| 新闻聚合 | harvest() 调 N 次 + build_context() 调 M 次 | N 次 (M=0) |

### 6.3 情报快照连续化

> **2026-07-13 决策**: 叙事模板方案（NarrativeBuilder）已否决。快照连续化简化为纯数据层追踪——追踪 cluster 的主题消长和 sentiment 方向变化，不做叙事归类。详见方案A决策记录。

---

## 7. 新增核心能力

### 7.1 金融日历集成

```python
@dataclass
class EconomicEvent:
    id: str
    name: str                    # "CPI", "FOMC", "Nonfarm Payrolls"
    scheduled_time: datetime     # 发布时间 (美东)
    importance: str              # "critical" / "high" / "medium"
    refresh_after_s: int         # 发布后强制采集窗口 (秒)
    affected_assets: list[str]   # ["equity", "bond", "dxy", "gold"]
    keywords_boost: list[str]    # 发布前后加权的关键词
```

`EconomicCalendarWatcher` 在每个 harvester 周期检查未来 1 小时内是否有预定事件，有则提前预加载关键词权重，发布时刻到期立即触发采集。

### 7.2 来源可信度

| Tier | 权重 | 典型来源 | 信号可行动性 |
|---|---|---|---|
| Tier 1 | 0.90-1.00 | Reuters, Bloomberg, BLS, Federal Reserve | fact → 可触发调仓 |
| Tier 2 | 0.60-0.85 | CNBC, MarketWatch, WSJ, FT | inference → 触发关注 |
| Tier 3 | 0.30-0.55 | Seeking Alpha, Benzinga, FXStreet | rumor → 仅记录 |

### 7.3 叙事层 — 已否决

> **2026-07-13 决策**: 方案A — 不做 NarrativeBuilder。理由：
> - 当前 cluster 的 theme/sentiment/summary 已包含足够上下文，Agent 可自行归纳宏观判断
> - 预定义的 6 个叙事模板（tightening_fear / risk_on / reflation / stagflation / geopolitical_crisis / china_reflation）会将复杂的宏观环境强行归类，导致输出固化
> - 跨 cluster 关联是真正有价值的方向，但用规则引擎做关联检测比叙事模板更合适（未来可选方案B）
> - 宏观叙事的合成是 LLM 的天然能力，不应由确定性规则代劳
### 7.4 三级风险预警

| 级别 | 触发条件 | 系统行为 |
|---|---|---|
| 🟡 **关注** | 单一数据偏离 / VIX 15-25 | 报告中标注，不独立推送 |
| 🟠 **减仓** | 连续 3+ 同向 / VIX 25-35 / 交叉验证确认 | 生成 reduce 建议，暂停 accumulate |
| 🔴 **对冲** | VIX>35 / 地缘危机 / 流动性危机 | 紧急推送 + 组合防御 + 暂停全部加仓 |

### 7.5 组合防御模式

```python
@dataclass
class PortfolioDefenseSignal:
    mode: str              # normal / cautious / defensive / panic
    triggers: list[str]
    hedges: list[dict]     # [{instrument, size_pct, rationale}]
    suspend_accumulation: bool
    cash_target: float

DEFENSE_RULES = [
    {"conditions": "vix>25 AND credit_widen>50bp", "mode": "cautious"},
    {"conditions": "vix>30 AND 3+ negative_clusters", "mode": "defensive"},
    {"conditions": "vix>35 OR geopolitical_crisis OR liquidity_crisis",
     "mode": "panic",
     "hedges": [
         {"instrument": "VIX calls", "size_pct": 2},
         {"instrument": "TLT", "size_pct": 5},
     ]},
]
```

### 7.6 信号量化跟踪

每条信号记录生成时价格 + 事后方向验证 + 胜率统计。

```python
@dataclass
class TrackedSignal:
    signal_id: str
    generated_at: datetime
    symbol: str
    direction: str
    generation_price: float
    regime: str
    confidence: float
    # 事后填充
    price_24h: Optional[float] = None
    price_1w: Optional[float] = None
    correct: Optional[bool] = None
```

---

## 8. 关键词与主题覆盖

### 8.1 关键词 (8 → 16)

```
GNews 层 (前 4 个, 96 次/天):
  0. "Federal Reserve interest rate policy"
  1. "CPI inflation PPI data"
  2. "crude oil price supply OPEC"
  3. "gold price safe haven"

Google RSS 层 (全部 16 个):
  4. "VIX volatility stock market fear"
  5. "US Treasury yield bond market"
  6. "ECB BOJ central bank monetary policy"
  7. "US Dollar Index DXY currency forex"
  8. "copper industrial metals commodity"
  9. "Bitcoin BTC cryptocurrency"
 10. "China stock market economy stimulus"
 11. "tariffs trade war sanctions geopolitics"
 12. "stock market sell-off correction crash"
 13. "NVIDIA AI semiconductor chip tech"
 14. "defense aerospace military spending"
 15. "credit spread high yield corporate bond"
```

### 8.2 主题聚类 (6 → 12)

```python
THEME_KEYWORDS = {
    "monetary_policy":       [fed, ecb, boj, boe, pboc, rate hike, rate cut, fomc, central bank],
    "inflation":             [cpi, ppi, pce, inflation, deflation, price index, core inflation],
    "employment":            [nonfarm, unemployment, jobs, payroll, jobless claims, wage growth],
    "fixed_income_credit":   [treasury, yield, bond, credit spread, high yield, tips, yield curve],
    "currencies_fx":         [dollar index, dxy, usdcny, eurusd, usdjpy, forex, exchange rate],
    "commodities_energy":    [oil, crude, opec, natural gas, energy, petroleum],
    "commodities_metals":    [gold, silver, copper, industrial metal, precious metal],
    "equities":              [stock market, s&p 500, nasdaq, equity, sell-off, rally, correction],
    "technology":            [ai, semiconductor, nvidia, chip, big tech, cloud],
    "geopolitics":           [war, conflict, sanction, tariff, trade war, military, tension, election],
    "china_macro":           [china, pboc, csi 300, a-share, shanghai, shenzhen, stimulus],
    "crypto":                [bitcoin, btc, ethereum, crypto, defi, blockchain],
}
```

---

## 9. 实施计划

### Phase 0 — 防御性补全 (一周内)

- [ ] 关键词 8→16，GNews 重排序
- [ ] 来源可信度权重表 + 信号可行动性标签
- [ ] 金融日历数据结构 + EconomicCalendarWatcher
- [ ] 三级风险预警触发框架
- [ ] API 用量追踪 (JSONL)
- [ ] 事件驱动触发器 (CPI/FOMC/非农 基础版)
- [ ] 取消无事件时段的"无重大事件 推送 → 仅存档

### Phase 1 — 统一采集层 (两周内)

- [ ] UnifiedHarvester 实现
- [ ] 消除 API 重复调用
- [ ] 主题 6→12 + 多标签聚类
- [ ] LLM 情感判断 + 字典降级
- [ ] 情报快照连续化 (纯数据层 — cluster 主题消长追踪，不做叙事归类)

### Phase 2 — 统一报告 (一月内)

- [ ] ReportBuilder 三栏结构
- [x] ~~叙事模板库 + NarrativeBuilder~~ (2026-07-13 方案A否决)
- [ ] 主题→配置动作映射表
- [ ] ReportRouter (四级推送)
- [ ] cron jobs 合并 (10→3: 事件触发器 + 时间触发器 + 异常监控)

### Phase 3 — 防御与预测 (两月内)

- [ ] 组合防御模式 + PortfolioDefenseSignal
- [ ] 新闻指纹→压力场景匹配
- [ ] 信号量化跟踪 + 回测管道
- [ ] 拐点检测 + 情感斜率变化
- [ ] 历史相似事件回溯
- [ ] 多因素预警评分框架

---

## 10. 风险与回退

| 风险 | 缓释措施 |
|---|---|
| 统一采集层成为单点 | 保持每个源的独立异常处理；统一失败时降级到旧管道 |
| ~~LLM 调用增加（叙事构建新增）~~ | (已否决，不需要缓释) |
| 金融日历数据维护成本 | 初期只覆盖 CPI/FOMC/非农 3 个事件，后续渐进 |
| 推送减少后用户感知信息缺失 | Phase 0 先做"安静模式"（采存不推送），保持用户可手动查询 |
| 旧 cron jobs 与新系统并存时的冲突 | 统一管道上线后，旧 cron jobs 先 pause 而非 remove，观察 2 周 |

---

*本文档为 v2.0 全新设计。实施从 Phase 0 开始。*
