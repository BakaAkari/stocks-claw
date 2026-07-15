# stocks-claw 开发方向建议书

> **状态更新:历史参考,后续路线已被 2026-07-15 对抗性审查重排**
> 本文中的历史完成证据保留有效;未完成方向不再自动进入现行计划。
> 当前任务和交易质量边界以 `EXECUTION_PLAN.md` 与 `docs/TRADING_SYSTEM_ADVERSARIAL_REVIEW_20260715.md` 为准。

> 生成: 2026-07-09
> 来源: 多角度专业审查（量化研究 / 风险管理 / 组合管理 / 数据架构 / 行为金融 / 合规治理）
> 状态: 方向建议，非执行清单；后续派生 EXECUTION_PLAN 具体任务卡
>
> 本文基于对 stocks-claw v4.3 (AnalysisContext v12, 509 tests) 的完整审查，
> 从六个专业视角分析系统现状、结构性缺陷与优化方向。
> 审查当日已完成四项 P0 修复（见 §0）。

---

## §0 审查当日修复（已完成）

以下四项在 2026-07-09 审查中直接实施并验证通过：

| # | 修复项 | 文件 | 验证 |
|---|---|---|---|
| 1 | 硬编码汇率 `* 7.2` → `get_usd_cny_rate()` 实时汇率+缓存 | `quant_action.py:221` | CLI smoke 显示 `USD/CNY 6.81 (cache)` |
| 2 | -8% 到 -12% 止损空隙：新增 -10% 中间档，减仓 30% | `quant_action.py:108-118` | -10.5% 触发 `reduce, ratio=0.3` |
| 3 | 信号横截面排序：`accumulate_candidate` 按综合得分排名 | `action_signals.py:_rank_signals()` | 科创50 rank=1 (0.345), 沪深300 rank=2 (0.314) |
| 4 | 多因子压力测试：3 情景替代完美相关 `±5%/±10%` | `quant_action.py:_build_scenarios()` | Global risk off -7.4%, China shock -9.6%, Commodity +3.6% |

**全局验收**: ruff All checks passed, pytest 509 passed, compileall 0, CLI smoke 通过。

---

## §1 系统现状诊断

### 优势（不可动摇的基石）

- **数据质量体系 v10**：逐字段可追溯的来源、时点、降级状态 —— 同类系统中未见过
- **工程纪律**：509 tests, ruff 零警告, schema 版本管理, 49 条决策日志
- **投产比极高**：纯 Python + 文件存储 + CLI/MCP，零重型依赖，维护成本极低
- **行为金融学设计**：`personal_advice_prompt.txt` 的 pre-mortem 设计、触发核对机制、禁止无触发条件的"观察"——专业水准
- **情报管道**：GNews→RSS→Finnhub 三层降级，每小时 99 条新闻，13 标的行情，9 宏观字段

### 核心瓶颈

系统当前像一个**绩优的 Junior Analyst**——数据收集完整、指标计算正确、规则执行无偏差，但缺少 Senior Analyst 的判断力：

1. **只有单标的规则，没有组合级判断** —— 知道"该不该动 A"，不知道"A 和 B 之间该选谁"、"止损释放的钱该去哪"
2. **风险模型停留在零售级** —— `total_value * -0.10`（已修复为多因子），但仍缺 VaR、最大回撤预估、流动性压力测试
3. **信号无区分度** —— 5 个 `accumulate_candidate` 没有内部排序（已修复），但仍缺信号有效期管理和 alpha 衰减
4. **缺少绩效闭环** —— 三套台账分立但无归因分析，无法回答"我的建议中哪种类型胜率最高"

---

## §2 开发方向（按优先级分层）

### 第一层：系统健壮性（不改架构，局部增强）

这些是"让现有系统不再有已知硬伤"的补齐项。每一项改动范围小、风险低、收益明确。

#### 2.1 美股第二行情源（P0）

**现状**: `engine.yaml` 中 `fallback.us: []` —— Finnhub 是唯一美股实时源。
**风险**: Finnhub 宕机/限流/改定价 = 整个美股分析链路断裂。
**方案**: 接入 Polygon.io 免费档（5 req/min，覆盖实时美股），作为 Finnhub 降级链第二源。
**改动范围**: `fetchers.py` 新增 `PolygonQuoteProvider`，`engine.yaml` 的 `fallback.us` 加 `polygon`。
**依赖**: Polygon API key（用户提供）。

#### 2.2 GNews 零返回诊断

**现状**: 最近 10 次 `global_intelligence_watch` 中 GNews 返回 `status=ok, count=0`，降级到 Google RSS 正常。
**风险**: 主源长期静默退化，可能错过 GNews 独有的优质新闻源。
**方案**: 在 `IntelligenceHarvester` 中增加逐源 `quota_remaining` 追踪和连续零返回告警。
**改动范围**: `intelligence_harvester.py`，`news_intelligence_store.py` 的 `data_quality` 增加 `source_health`。

#### 2.3 跨 session 数据质量趋势

**现状**: 每次 `build_context` 独立运行，Agent 看不到跨运行的数据质量变化。
**方案**: 在 `context_builder.py` 中加载最近一次 snapshot 的 `data_quality` 做 diff —— 最小形式：对比 `quotes.by_market.us.as_of`，未推进则标记 `stale_since_last_run`。
**改动范围**: `context_builder.py` 的 `_build_data_quality`（新增约 20 行）。

#### 2.4 反事实复盘提示

**现状**: `after_close` session 的 `agent_task.must_answer` 缺少"如果按盘前计划执行 vs 未执行"的对比。
**方案**: 在 `build_agent_task()` 的 `after_close_review` 中添加反事实提示。
**改动范围**: `scheduled_analysis.py` 的 `build_agent_task()`（新增 2 行 must_answer）。

#### 2.5 推送疲劳保护

**现状**: 用户每天最多 27 次推送（8 持仓 + 19 情报），决策疲劳风险显著。
**方案**: `global_intelligence_watch` 仅在 `urgency=critical` 时 `push_now`，普通情报归入每 4 小时 `digest`；同一信号类型 4 小时内不重复推送。
**改动范围**: `scheduled_analysis.py` 的 `_notification()`，`scheduled_sessions.json` 的 intelligence push 策略。

---

### 第二层：分析深度（需要新模块，但复用现有数据）

这些是"让系统从 Jr. Analyst 升级到 Sr. Analyst"的能力加厚项。

#### 2.6 组合级资金分配提示（PM 视角）

**目标**: 当止损/止盈触发释放资金时，系统能给出"这笔钱的第一候选去哪"的结构化上下文。
**输入**: `action_cards`（哪些要减/清）+ 已排名的 `action_signals`（哪些可买）+ `liquidity_summary`（可用资金）+ `exposure_summary`（组合缺口）。
**输出**: `capital_allocation_hints[]` —— 每项包含 `source`（从哪释放）、`amount`、`candidates`（按排名 Top 2）、`rationale`。
**改动范围**: `scheduled_analysis.py` 新增 `_build_capital_allocation_hints()` 函数，在 `pre_open` 和 `pre_close` session 输出中追加。
**依赖**: 需要信号排名（已具备，§0 #3）。

#### 2.7 绩效归因最小可行版（PM 视角）

**目标**: 季度回顾时能回答"我的建议命中率如何？按信号源/方向/标的类型分组统计"。
**输入**: `AdviceRecord`（建议台账）+ `ExecutionRecord`（执行台账）+ `HistoryCache`（历史价格）。
**输出**: `attribution_summary.json` —— `by_signal_source`、`by_direction`、`by_asset_class` 的命中率和平均收益。
**改动范围**: `advice_review.py` 新增 `compute_attribution()`，CLI 新增 `--attribution-report`。
**依赖**: 需要 ≥30 条 AdviceRecord 样本（当前可用）。不需新数据源，纯聚合计算。

#### 2.8 VaR / 最大回撤预估（风控视角）

**目标**: 在 `portfolio_risk` 中增加参数法 VaR (95%, 1-day) 和基于历史数据的最大回撤。
**方案**: 从 `HistoryCache` 取各持仓的日收益率序列 → 加权组合波动率 → `VaR = total_value * 1.65 * sigma_daily`。
**改动范围**: `compute_portfolio_risk()` 增加 `var_95_1d`、`var_95_5d`、`max_drawdown_est`。
**依赖**: 需要 ≥60 天历史收益数据（当前约 30 天，需积累）。

#### 2.9 信号有效期管理（量化视角）

**目标**: `action_signal` 增加 `signal_age_days` 和 `freshness` 字段，超过 5 天的信号自动降级。
**方案**: 在 `compute_action_signals()` 输出的每条 item 中计算 `signal_age_days = (now - as_of).days`，`freshness = fresh if ≤3 else stale if ≤5 else expired`。
**改动范围**: `action_signals.py` 增加 `_attach_signal_age()`，`_build_action_signal_reviews()` 过滤 `expired` 信号。

---

### 第三层：长期愿景（需用户价值裁决后立项）

这些是 Backlog 中已有或审查新建议的方向性提案，不排期、不预设串行依赖。

#### 3.1 论点笔记本（VISION 能力域 2）

**现状**: 设计稿未立项。
**目标**: 每个投资论点是活的文档——含前提、证据、反证、下一个验证节点。Agent 在分析时可以引用相关论点的当前状态。
**关键决策点**: 论点的数据结构、与现有 AdviceRecord 的关系、是否需要 LLM 辅助维护。

#### 3.2 组合归因（VISION 能力域 1）

**现状**: 已在 Backlog。
**目标**: Brinson 归因或类似框架，区分选股收益 vs 配置收益 vs 交互效应。
**关键决策点**: 基准选择（沪深300+标普500 混合基准？自定义基准？）、归因频率（季度/月度？）。

#### 3.3 估值数据层（VISION 能力域 4 长线）

**现状**: 已在 Backlog。
**目标**: A 股/美股主要指数的 PE/PB 历史百分位，解锁长线估值判断。
**关键决策点**: 数据源（免费源可用性、是否需要付费 Wind/Bloomberg 替代）、更新频率。

#### 3.4 波动率自适应仓位（量化视角）

**目标**: `DEFAULT_POSITION_LIMIT_PCT` 从固定 5% 改为按标的波动率自动调整。高波缩仓、低波略放，但不超过硬顶 10%。
**关键决策点**: 基准波动率锚定（20% 年化？）、调整公式的激进程度。

#### 3.5 基金净值与贵金属报价 Provider

**现状**: 已在 Backlog，依赖用户补录基金代码/份额/克数。
**目标**: 覆盖场外基金、银行理财、黄金账户的净值/报价获取，消除手工估值盲区。

#### 3.6 `context_builder.py` 渐进式拆分

**现状**: 2439 行单体文件，认知负荷过高。
**目标**: 不重写，在下一次涉及该文件的变更时顺手拆分：`_build_data_quality` → 独立模块，`_value_position` 5 种估值分发 → `valuation.py`。
**原则**: 每次拆分不超过 200 行移动，保持测试全绿。

---

## §3 不做的事（明确排除）

以下方向在当前架构和目标下不予考虑：

- **实时推送升级为 WebSocket / Server-Sent Events** —— 文件 + cron 满足当前需求，增加推送复杂度不创造用户价值
- **多用户 / 商业化平台化** —— VISION 明确"非目标"
- **自动交易 / 券商 API 对接** —— PLAN §6 永久禁止
- **LLM 替代规则引擎** —— 规则可解释、可复核、可测试；LLM 只在 Agent 分析层使用
- **通用回测平台 / 因子库** —— 超出个人投资分析师定位
- **实时 tick 级行情** —— 日 K + 定时 session 满足当前交易频率

---

## §4 架构不变式（新增约束）

以下约束在本次审查中确认或新增，未来所有开发必须遵守：

1. **复杂度必须由已验证的价值拉动**（VISION 成长规则，不可动摇）
2. **数据不可信显式报缺，绝不静默装好**（VISION "先诚实再博学"，不可动摇）
3. **规则引擎输出必须附可复核的指标事实**（quant_action / action_signals 的 reasons 字段）
4. **风险管理输出必须标注假设和方法论**（新增：多因子 scenario 的 shock coefficient 来源和局限）
5. **新增信号/规则必须有对应的单元测试覆盖**（PLAN §6 扩展）
6. **跨 session 数据质量变化必须可见**（§2.3 落实后生效）

---

## §5 下一步

1. 用户审阅本文，对 §2 各层的优先级和范围给出裁决
2. 裁决后派生 `EXECUTION_PLAN.md` 的具体任务卡
3. 每完成一个任务卡：
   - 全局验收（ruff / pytest / compileall / CLI smoke）
   - 决策日志追加记录
   - 用户价值裁决（"有用 / 没用 / 需调整"）

**不预设串行依赖**。§2 的第一层项目（2.1~2.5）互相独立，可并行或按用户兴趣择一开工。

---

## 附录 A：审查方法论

本次审查采用六视角交叉验证：

| 视角 | 核心关注 | 关键发现 |
|---|---|---|
| 量化研究员 | 信号质量、因子构造、回测严谨性 | 信号缺少区分度（已修复）、缺有效期管理 |
| 风险经理 | 尾部风险、压力测试、相关性结构 | 完美相关假设严重失真（已修复为多因子）、缺 VaR |
| 对冲基金 PM | 仓位构建、想法漏斗、绩效归因 | 缺组合级资金分配、缺绩效归因 |
| 数据平台架构师 | 管道可靠性、可观测性、数据血缘 | GNews 空返回、context_builder 膨胀、缺跨 session diff |
| 行为金融/产品 | 决策质量、认知偏差、信息呈现 | pre-mortem 设计优秀、缺反事实复盘、推送疲劳 |
| 合规/治理 | 审计轨迹、决策问责 | 审计体系优秀、唯一硬伤是硬编码汇率（已修复） |

## 附录 B：修复证据（2026-07-09）

```
Fix 1: quant_action.py — get_usd_cny_rate() 替代 * 7.2
  证据: CLI smoke 日志 "使用缓存汇率 USD/CNY: 6.***"
  文件: stocks/engine/quant_action.py:220-222

Fix 2: quant_action.py — 新增 MID_STOP_PCT=-10.0, MID_STOP_RATIO=0.3
  证据: -10.5% pnl → signal=reduce, action="浮亏超出中间阈值 -10.0%，减仓 30%"
  文件: stocks/engine/quant_action.py:58-59, 108-118

Fix 3: action_signals.py — _rank_signals() 横截面排序
  证据: 科创50 rank=1 score=0.345, 沪深300 rank=2 score=0.314
  文件: stocks/engine/action_signals.py:_rank_signals()

Fix 4: quant_action.py — _build_scenarios() 多因子压力测试
  证据: global_risk_off -7.42%, china_shock -9.58%, inflation_commodity +3.58%
  文件: stocks/engine/quant_action.py:_build_scenarios()
  调用方: scheduled_analysis.py:958 传入 position_valuations=

全局验收:
  ruff: All checks passed
  pytest: 509 passed
  compileall: 0 errors
  CLI smoke: 通过
```
