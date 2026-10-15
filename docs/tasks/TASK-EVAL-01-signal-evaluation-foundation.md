# TASK-EVAL-01：信号评价基础设施重构方案

> 当前状态只见 [STATUS](../../STATUS.md)。
> 日期：2026-10-15
> 本文保留原阶段计划，计划章节不代表实现。2026-10-15 用户指定先打通一条最小
> 闭环；该轮执行边界见 [最小评价契约](../contracts/signal-evaluation-v1.md)，
> 验收见 [纵向闭环记录](EVAL-01-MINIMAL-LOOP-ACCEPTANCE.md)。

## E0 历史验收记录（当时的阶段结论）

- 9 个相关 cron 暂停；旧账本只读快照及哈希封存，原件未清洗。
- 生产 scorecard 已断开旧胜率读取，返回 disabled_legacy_ledger；抛错探针验证通过。
- 生产者、结算者和报告消费者行号清单、账本重复/覆盖/缺价/延迟/来源报告已归档。
- 三项新闻源测试失败在改动前基线复现，测试配置隔离后引擎测试41通过；全量1456通过、7跳过、0失败。
- 证据：`/opt/data/workspace/stocks-claw-e0/20261015T114321/` 下 E0-RESULT.md、E0-test-resolution.md、code-inventory.txt、ledger-quality-full.json、pytest-after-test-isolation.txt。
- E0 关闭仅指冻结与盘点阶段验收，不代表新评价链实现；系统保持暂停，E1 未开始。

## 1. 目标

把现有 `SignalTracker`/`settlements.jsonl` 实验性闭环重建为可审计、幂等、按目标时间结算、可复现的评价子系统，且不改变当前生产报告的决策逻辑。

最终必须能回答：

- 哪一条生产信号在什么时间、基于什么输入和版本生成；
- 它的目标评价时间是什么；
- 实际取到了哪个时间点、哪个来源的价格；
- 结算是否唯一、是否完整、是否因数据问题不可评价；
- 该结果属于哪种信号语义，是否适合进入哪个指标。

## 2. 明确非目标

- 不自动下单；
- 不在本任务中重写规则引擎、LLM Advisory 或报告渲染；
- 不把历史污染账本伪清洗成“真实胜率”；
- 不在评价链稳定前用胜率自动调权、调阈值或改 prompt；
- 不默认引入 QuantConnect、MLflow、Redis 或外部数据库。

## 3. 目标责任链

```text
生产分析输出
  → canonical SignalEvent（不可变）
  → EvaluationTask（唯一 signal + policy + target session）
  → MarketObservation（目标时点、来源、as_of、质量）
  → EvaluationResult（唯一、可复现、带版本）
  → Metrics/Report（去重、分层、置信区间）
```

JSONL 可保留为原始 append-only 事件；规范化查询和状态真相优先采用本地事务存储（第一阶段可用 SQLite），不再依赖两个 JSONL 文件的跨文件状态同步。

## 4. 必须建立的数据契约

### 4.1 SignalEvent

至少包含：

- `signal_id`、`decision_id`、`strategy_id`、`strategy_version`；
- `signal_type` 与 `action_semantics`，禁止把研究候选自动当短线方向预测；
- canonical instrument identity；
- `generated_at`、`decision_as_of`；
- entry price、price as_of、source、freshness、price_type；
- input snapshot hash、config hash、prompt/model version；
- `scoring_status`：`scorable` / `unscorable` / `observation_only`。

### 4.2 EvaluationTask

唯一键：

```text
(signal_id, evaluation_policy_id, target_session)
```

状态：

```text
scheduled → due → claimed → settled
                         ├→ unavailable
                         ├→ expired
                         └→ cancelled
```

必须保存 `target_at` 或目标交易会话，不以 cron 当前时间代替目标时间。

### 4.3 MarketObservation

至少包含：

- instrument id；
- target_at、observed_at；
- price、price_type、source、source_as_of；
- exchange timezone、calendar/session；
- freshness、adjustment mode、quality status。

没有目标时点观测时，不得拿当前价格冒充目标价格。

### 4.4 EvaluationResult

至少包含：

- entry/exit observation 引用；
- gross/net return；
- direction outcome；
- neutral band；
- fees/slippage/FX（若适用）；
- result status 与 invalid/unavailable reason；
- evaluator version。

`below_min_change` 改为 `neutral_band`，与缺价、过期、provider 失败分离。

## 5. 信号语义分层

至少拆分：

| 类型 | 评价方式 |
|---|---|
| `short_term_directional` | 24h/1w 方向与净收益 |
| `left_side_accumulation` | 20d/60d 收益、最大回撤、后续趋势 |
| `risk_reduction` | 回撤避免程度、组合风险变化 |
| `research_candidate` | 后续窗口表现，不作为立即交易命中率 |
| `medium_term_outlook` | 1m/3m 条件兑现率 |

`wait_for_pullback` 不得直接映射成 buy；`reduce_risk` 不得直接等价为 sell 预测。

## 6. 分阶段实施

### E0：冻结解释权与盘点

- 停止将现有胜率注入 prompt、调权或阈值；
- 原始 JSONL 只读保留；
- 盘点所有 signal producer、settlement caller、cron、报告消费者；
- 输出旧账本质量报告：重复、覆盖、缺价、时间延迟、来源分布。

验收：生产路径不再消费旧胜率做决策；盘点清单可由 grep 和运行探针复现。

### E1：Canonical identity 与 entry snapshot

- 统一 `market:code` 标识与 provider 映射；
- 生成信号时强制保存 entry observation；
- 缺 entry 进入 `unscorable`，不得进入正常评价任务；
- 增加 source/as_of/freshness/price_type。

验收：真实 A 股、美股、加密信号 entry coverage 达到 100% 或每个缺失都有明确不可评价状态；不存在裸字符串猜测路径。

### E2：EvaluationTask 与幂等状态机

- 实现唯一键和原子 upsert；
- 结算任务 claim/lease，防止并发重复；
- 失败可重试，但同一任务只能有一个最终结果；
- 原始事件与规范化状态分离。

验收：重复运行、并发运行、进程中断恢复均不增加重复最终结果；重复率为 0。

### E3：交易日历与目标观测

- A 股、美股、加密分别定义 calendar；
- 明确自然时间窗口和交易会话窗口的差异；
- 以 target_at/session 查询历史观测；
- 记录 target/observed 偏差和 provider 质量。

验收：周末、节假日、盘前盘后、跨时区样例均得到可解释状态；不得用 cron 当前价冒充目标价。

### E4：Result/Metrics 重建

- 由唯一 EvaluationResult 派生报告；
- 按 strategy/version/source/symbol/market regime/horizon 分层；
- 同时输出 win rate、neutral rate、gross/net return、expectancy、最大回撤和样本质量；
- 小样本输出原始结果与“不足以判断”，不输出伪精确结论。

验收：旧账本与新账本并行对比；差异可解释；报告不再按原始重复行统计。

### E5：策略语义评价与 shadow 反馈

- 为左侧、风险、研究候选和短线方向建立各自评价器；
- 与 `DecisionSnapshot`/Forecast 明确边界；
- 只生成 shadow feedback，不自动改策略；
- 连续多个市场状态和版本后，才讨论是否接入人工调参流程。

验收：每个指标都能追溯到 SignalEvent、EvaluationTask、Observation 和 Result；用户确认结果对决策有价值后才进入下一阶段。

## 7. 验收门

### 数据门

- entry coverage ≥ 99%，剩余均有明确 reason；
- target observation coverage ≥ 99%（按可交易日历）；
- 结算重复率 = 0；
- 任何失败不伪造结果。

### 语义门

- 行动建议、研究候选、短线方向和中长期判断不混算；
- `neutral_band` 不再计入胜负；
- 每个指标有明确 horizon 和策略版本。

### 可复现门

- 同一输入快照、代码版本、配置、模型/提示版本可重建同一信号与结果；
- 目标价格、观测时间和来源可回读；
- 旧账本保留，不覆盖原始数据。

### 反馈门

- 评价结果在用户确认前不得自动改变生产策略；
- 至少跨越多周、多标的、多市场状态；
- 通过 shadow 对照和人工审查后，才决定是否接入策略反馈。

## 8. 停止条件

出现以下任一情况，停止扩大范围，回到当前阶段修复：

- 仍需按字段名或字符串猜测 instrument；
- 同一任务仍可写出多个最终结果；
- 目标时间和观测时间无法区分；
- 报告只能给胜率，不能给样本质量和收益口径；
- 代码测试通过但真实 provider smoke 失败；
- 需要修改生产规则才能掩盖评价链缺陷。

## 9. 文档治理

- 本文是唯一的评价重构执行方案；
- `STATUS.md` 只记录真实当前状态，不提前宣称阶段完成；
- `ARCHITECTURE.md` 描述最终责任边界，待代码落地后更新；
- `DATA_MODEL.md` 只记录已实现契约；计划字段先不写成生产事实；
- 历史 `INTELLIGENCE_FULL_REDESIGN`、旧回测设计和历史审查继续留在 archive，不再作为当前执行依据。
