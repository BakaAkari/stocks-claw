# 现行数据模型

本文只描述当前代码中的 schema。权威实现位于 `stocks/domain/models.py`，
`AnalysisContext.schema_version` 当前为 `12`。

## DecisionEnvelope v1

`DecisionEnvelope` 是面向用户建议的统一顶层协议；`AnalysisContext` 仍是证据层，
不再被规划为最终交付物。顶层字段固定为：

- `status ∈ {ok, degraded, setup_required, validation_failed, failed}`
- `mode_requested` 与
  `mode_used ∈ {internal_llm, agent_delegate, deterministic_only}`
- `decision_plan`、`agent_task`、`setup_required`（无值也必须显式为 `null`）
- `quality`、`errors`、`final_analysis_instructions`

三层职责严格分离：确定性引擎只产事实、候选和仓位边界；决策生成器只在该边界内
产生结构化 `DecisionPlan`；用户 Agent 审查数据质量并结合当前对话输出最终自然语言
分析。最终分析不是市场事实，不得反写行情、事件或资产数据。

唯一机器契约位于 `stocks/engine/decision_contract.py`：
`DECISION_ENVELOPE_SCHEMA` 是 JSON Schema 2020-12 描述，
`validate_decision_envelope()` 是不引入运行时依赖的等价本地校验器。prompt 文案不构成
协议，也不能绕过该校验器。

## FinancialAsset

用户确认的单条 v1 金融资产：

- `name`、`platform`、`amount`
- `asset_type`、`notes`、`confirmed`
- `currency`：用户输入的原始币种
- `instrument_key`：用户确认映射的证券标的，格式 `market:code`；未映射为 `null`
- `quantity`：用户确认的持有数量；未提供为 `null`
- `tradable`：用户确认的可交易状态；未知为 `null`
- `amount_cny`：运行时派生的人民币估值，不写回资产文件
- `conversion_status`：`ok` / `degraded` / `failed`
- `conversion_source`、`conversion_rate`

持久化只写原始资产字段与用户确认的映射字段；`amount_cny` 等运行时派生字段不写回。
换算失败的外币资产仍保留原金额和币种，但 `valuation_cny` 为 `null`，不能静默计入组合总值。

## Account / Position v2

`Position` / `Account` 是当前细粒度资产入口。`FinancialAsset` 保留为 v1
兼容层和旧 Adapter 的只读/受限写入形态。资产加载器兼容两种文件格式：

- v1：顶层 list，元素为 `FinancialAsset` 字段；加载时在内存中确定性映射到 v2 `Account` / `Position`，但不自动写回文件
- v2：顶层 dict，形如 `{schema_version: 2, base_currency: "CNY", accounts: [], positions: []}`

v1 文件不会自动写回；迁移必须通过 `asset_migrate_v2` / `--asset-migrate-v2`
预览后由用户确认完成。v2 文件加载后，旧 `asset_add/update/remove` 会拒绝写入，
避免把 v2 文件降级覆盖。

`Account` 表示账户层级：

- `account_id`、`display_name`
- `institution_type ∈ {brokerage, fund_platform, bank, insurance, manual}`
- `market_scope?`
- `base_currency`
- `default_liquidity_tier?`
- `notes?`

`Position` 统一表达持仓、现金、手工资产、保险等资产事实：

- `position_id`、`account_id`、`display_name`、`currency`
- `classification`：`Classification {asset_class, product_type, subtype?, exposure_tags[]}`
- `instrument?`：含 `instrument_key` 时仍复用 `market:code` 与当前支持市场 `a/us/crypto`
- `holding?`：`Holding {quantity, unit ∈ {share, gram, unit}, cost_basis?}`
- `valuation_input`：`ValuationInput {method, manual_amount?, as_of?}`，
  `method ∈ {market_quote, fund_nav, manual_amount, precious_metal_quote, insurance_value}`
- `liquidity`：`Liquidity {tradable?, rebalance_eligible?, tier, redemption_rule?, lockup_until?, maturity_date?}`，
  `tier ∈ {cash, t0, t1, t2_plus, periodic_open, locked, unknown}`
- `role?`、`reported_performance?`、`data_completeness`、`confirmed`、`notes?`

受控词表：

- `asset_class ∈ {cash, cash_equivalent, fixed_income, equity, commodity, insurance, alternative, unknown}`
- `product_type ∈ {cash, money_market_fund, bank_wealth_management, fixed_income_plus_fund, mixed_fund, qdii_fund, feeder_fund, exchange_traded_fund, stock, short_treasury_etf, precious_metal_account, insurance_policy, manual_asset}`

当前 v2 校验规则：

- `market_quote` 必须有 `instrument.instrument_key` 与 `holding.quantity`
- `manual_amount` / `insurance_value` 必须有 `valuation_input.manual_amount`
- `insurance_policy` 默认 `liquidity.tradable=false`、`rebalance_eligible=false`、`tier=locked`
- `data_completeness.missing_fields` 机器可读记录：上市持仓缺成本、手工估值缺 `as_of`、分类未知等
- `to_storage_dict()` 不含任何运行时派生估值字段

`AnalysisContext.position_valuations` 是运行时派生估值快照，不写回资产文件：

- `market_quote`：最新行情价 × `holding.quantity`；行情缺失时显式降级到
  `valuation_input.manual_amount` 或成本兜底，并在 `flags` 标记
- `manual_amount` / `insurance_value`：直接使用用户确认金额；`as_of` 超过 30 天
  标记 `stale_manual`
- 每项包含 `market_value`、`market_value_cny`、`fx_rate`、`fx_source`、
  `price_source`、`as_of`、`flags`
- 有 `cost_basis` 时计算 `unrealized_pnl`、`unrealized_pnl_cny`、`pnl_pct`

组合级派生字段：

- `exposure_summary`：按 `classification.exposure_tags` 聚合 CNY 暴露
- `liquidity_summary`：按 `cash_or_t0`、`t1_t2`、`locked_or_ineligible`、
  `unknown` 四档聚合可动用性
- `asset_data_boundaries`：列出缺成本、缺估值日期、手工估值过期、缺行情、
  FX 不支持等会降级或阻断分析能力的问题
- `advice_granularity`：逐持仓标记 `detailed`、`sector`、`fixed` 或 `manual`；
  `sector` 可通过 `stocks/config/exposure_proxy.json` 注入代理标的信号，代理仅作
  板块参考，不等同于该持仓价格或净值

## Investor profile

画像保存在 `.local/investor_profile.json`，是可扩展 JSON 对象。当前示例字段：

- `risk_tolerance`
- `investment_horizon`
- `preferences`
- `constraints`
- `updated_at`：写入时由系统更新

## ScheduledAnalysisRun v1

`ScheduledAnalysisRun` 是 S3 定时扫描与 Agent handoff 的结构化运行产物。它不是
长期金融记忆，不写入 advice / executions / forecasts，也不构成最终建议。默认存储位置：

```text
.local/scheduled_runs/YYYY-MM-DD/{market}/{session}/{run_id}.json
.local/scheduled_runs/latest/{session}.json
```

顶层字段：

- `schema_version == 1`
- `run_id`：`{scheduled_utc}_{session}`，例如 `20260706T063500Z_cn_pre_close`
- `generated_at`：实际生成时间，UTC ISO
- `market ∈ {cn, us}`，第一版只覆盖 A 股与 IBKR 美股持仓相关 session
- `session`：`scheduled_sessions.json` 中的稳定 id
- `market_date`：交易所本地日期
- `exchange_timezone`、`user_timezone`
- `scheduled_for`：交易所本地时区下的计划触发时间
- `status ∈ {ok, degraded, skipped_market_closed, skipped_duplicate, failed}`
- `source_context`：来源 `AnalysisContext` 的 schema 与生成时间
- `portfolio_scope`：本次报告涉及的账户、持仓、证券 key 和主市场
- `session_summary`：面向 Agent 的 headline、priority 和 push_policy
- `position_reviews`：逐持仓事实核对，来自 `position_valuations`
- `trigger_reviews`：最近建议中的触发器核对结果
- `action_signal_reviews`：本次 session 相关的非 neutral 动作信号
- `data_quality`：原样保留 `AnalysisContext.data_quality`
- `action_cards`：逐持仓最终动作卡,含 `signal/action/ratio/facts/routing`、
  `drivers`(technical/intelligence/factor)、`dissent`、`confidence`、
  `intelligence_conflict`、`constraint_conflict`。
- `portfolio_risk`：集中度、止损风险与多因子压力情景。
- `capital_allocation`：约束告警、信号冲突、减仓回收、加仓候选、轮动参考与
  `net_deployable_cny`。`available_cash_cny` 仅包含 cash/T0；T1/T2 持仓单列为
  `strategic_exit_value_cny`，不得称为今日即时现金。
- `risk_assessment`：`level ∈ {hedge, reduce, watch, normal}`、`triggers`、`recommended_actions`、`suspend_accumulation`、`cash_target_pct`。
- `risk_state`：持久化风险状态（含 `level`、`transition`、`suspend_accumulation` 等），写入 `.local/risk_state/{market}_{market_date}.json`。
- `window_delta`：相对上一窗口的语义变化摘要（`material`、`changes`、`priority`、`notification`、`first_in_session`）。
- `portfolio_decision`：组合最终裁决，含 `status`、`approved_actions`、`suppressed_actions`、`replacement_chains`、`unresolved_conflicts`、`cash_schedule`。每个 `approved_action` 输出 `position_id`、`signal`、`ratio`、`action_description`、`cancel_condition`、`settlement_timing`、`next_checkpoint`。
- `data_boundaries`：结构化数据边界，`data_quality` + `source_context`。
- `research_candidates`：research_only 信号候选，最多 8 个，不进入 approved_actions。
- `execution_review`：对当前 run 中 `approved_actions` 的执行状态对照（executed/partial/rejected/deferred/not_executed/unknown）。
- `agent_task`：`task_version` 已升级为 5，只引用上述 5 个可信字段，输出固定五段：变化摘要 / 今日动作 / 禁止待确认 / 资金到账与边界 / 研究候选。
- `mandatory_blocks`：风险边界、约束偏离、资金事实、Shadow Account 和可选研究论点。
- `agent_task`(v4)：自包含的 Agent 任务说明书,含以下子字段:
  - `task_version`：4
  - `must_answer`：session 特定的必答问题(含前瞻展望指令)
  - `must_not_do`：数据忠实性、触发器完整性、资产路由、扫描池完整性、情报来源与飞书格式硬约束
  - `persona`：分析师人格定义(角色、原则)
  - `adaptability`：自适应输出规则(silent_when_nothing/loud_when_critical)
  - `data_reference`：逐字段数据定位指南
  - `output_structure`：分节输出模板(含飞书格式规则)
  - `final_analysis_instructions`：一行总结
- `write_policy`：固定声明后台运行不得写长期金融记忆，写入必须用户确认
- `notification`：推荐推送策略，通知层只发消息不写金融记忆
- `context_digest`：市场状态、组合结构、暴露、流动性、粒度、轮动 leader、情报摘要与事件日历

`agent_task.must_not_do` 固定包含：不得承诺收益、不得忽略 `data_quality`、不得建议动用
锁定资产、不得把代理 ETF 价格触发器套到场外基金、不得自动保存建议/执行/预测、
不得遗漏严重触发器、必须按 routing 报告资产操作约束、必须遵守飞书格式和情报来源边界。

### 当前已知一致性边界

- `data_quality.quotes.freshness` 仍保留全局最差值用于总览；Action Card 使用逐持仓
  `evidence.price_freshness`，run status 使用 primary market 的 `by_market` 状态，异市场 stale 不再污染动作。
- `capital_allocation.available_cash_cny` 与 `net_deployable_cny` 只从 cash/T0 和已批准回收计算；
  T1/T2/场外基金/股票价值单列为 `strategic_exit_value_cny`。
- `window_delta` 仅比较语义键，忽略 `decision_id` 中的 run ID 和 `generated_at` 等运行时变化。
- `portfolio_decision.cash_schedule` 区分 `immediate_cash_cny`、`settling_cash_cny`、`strategic_exit_value_cny`、`locked_value_cny`，不得把持仓总值直接称为"今天可动用"。
- category fallback `hold` 可提高 intelligence driver 字段覆盖率,但不等于独立方向情报。
- `confidence` 表示当前证据与新鲜度,不表示历史胜率或未来收益概率。
- 完整交易质量边界见 `../docs/TRADING_SYSTEM_ADVERSARIAL_REVIEW_20260715.md`。

## AdviceRecord

用户确认保存的建议摘要，位于 `.local/advice/`，最多保留 30 条。它只保存摘要，
不保存 LLM 长文：

- `created_at`：系统生成的 UTC ISO 时间
- `instruments`：`[{market, code, name}]`
- `direction`：`{"market:code": "buy|sell|watch|hold"}`
- `rationale_summary`：500 字以内摘要
- `based_on`：`quotes`、`news`、`indicators`、`macro`、`portfolio`、`profile`
- `boundary`：`[{type: "fact"|"inference", text}]`
- `triggers`（可选，默认 `[]`）：可核对的触发条件，每条为
  `{instrument: "market:code", type, level, action, invalidation?}`，
  `type ∈ {price_above, price_below, pct_change_above, pct_change_below,
  pnl_pct_above, pnl_pct_below}`，
  `level` 为数字（价位或百分数），`action` 为非空动作描述。
  `pnl_pct_*` 的基准是用户 `holding.cost_basis`，保存时若目标持仓不存在或缺成本
  会结构化拒绝。
  旧记录缺失该字段时按 `[]` 加载。
- `actions`（可选，默认 `[]`）：结构化调仓动作，每条为
  `{target, action, size_hint, trigger?, invalidation?, horizon}`。
  `target` 必须命中已映射持仓、watchlist、扫描池或约束 bucket；
  `action ∈ {add, increase, reduce, exit, hold, watch}`；
  `horizon ∈ {short, medium, long}`；`size_hint` 允许比例、区间或自然语言，
  禁止保存具体货币金额。旧记录缺失该字段时按 `[]` 加载。

`AnalysisContext.recent_advice` 会在构建上下文时附加两个派生字段（均不写回
`.local/advice/`）：

- `performance`：并列展示建议日至最近历史收盘价的价格事实
- `trigger_review`：对每条 trigger 按建议日之后的收盘价序列核对，
  `status ∈ {fired, not_fired, no_data}`；有数据时附
  `observed = {basis: "close", start_at, latest_at, start_price, latest_price,
  max_price, min_price, pct_change}`，no_data 时附 `reason`。
  核对只陈述价格事实，不判断建议对错。
- `execution_review`：对每条 action 按 `advice_id + target` 精确匹配
  `ExecutionRecord`，`status ∈ {executed, partial, not_executed, unknown}`。
  匹配不到一律 `unknown`，不按名称、方向或金额猜测。

## ExecutionRecord

用户确认记录的建议执行或明确未执行，位于 `.local/executions/`：

- `id`：系统生成的记录 id
- `advice_id`：可选；建议记录的 `created_at`，用于与 action 精确匹配
- `target`：对应 action 的 target
- `action ∈ {add, increase, reduce, exit, hold, watch, none}`；`none` 表示明确未执行
- `extent ∈ {full, partial}`；当 `action = none` 时省略
- `note`
- `executed_at`：实际执行或确认未执行的时间
- `recorded_at`：系统记录时间
- `run_id`：来源 scheduled run id
- `decision_id`：必须关联的 portfolio action decision id
- `status ∈ {executed, rejected, deferred, planned, not_executed}`
- `planned_ratio`：建议动作比例
- `executed_ratio`：实际执行比例（0-1）
- `price`：执行或确认价格；status=executed 必填
- `rejection_reason`：status=rejected 必填
- `next_review_at`：status=deferred 可选的下次复核时间

`status=executed` 时要求 `price` 与 `executed_ratio`；`status=rejected` 要求 `rejection_reason`。
执行记录通过 `decision_id` 与后续 `execution_review` 精确关联，不再只依赖 `target` 字符串。

## DecisionSnapshot / OutcomeAttribution

`DecisionSnapshot` 保存每个 approved/suppressed action 的决策版本与计划，用于后续 Shadow Trial 效果归因：

- `decision_id`：关联 portfolio_decision / action
- `rule_version`：生成该决策的规则版本
- `params_hash`：当时个性化参数哈希
- `data_as_of`：决策所基于数据的时间戳
- `position_id`、`signal`、`ratio`、`horizon_days`
- `executed`：是否实际执行（基于 ExecutionRecord.status=executed）
- `entry_price`、`execution_price`、`settlement_timing`、`commission_rate`
- `settled`、`outcome`：结算状态与结果（hold/return 等）

快照持久化于 `.local/decisions/{decision_id}_{horizon}d.json`。`settle_decisions(as_of, price_history, snapshots, executions)` 对到期 horizon 结算，使用确定性价格序列，扣除往返交易成本（2×commission_rate），区分 executed 与 shadow（未执行）反事实；样本<10 只输出 count/raw outcomes，≥10 才输出 Wilson 95% 置信区间。

## ForecastRecord

用户确认保存的预测台账记录，位于 `.local/forecasts/`：

- `id`：系统生成的记录 id
- `created_at`：系统生成的 UTC ISO 时间
- `statement`：500 字以内的预测陈述
- `target`：可选；可程序化结算时为 `market:code`
- `metric`：当前仅支持 `close`
- `comparator ∈ {above, below}`
- `level`：可选；可程序化结算时为收盘价比较阈值
- `deadline`：`YYYY-MM-DD` 日期
- `confidence ∈ {low, medium, high}`
- `status ∈ {open, hit, miss, unresolved, manual}`
- `resolved_at`：自动结算或转入未决时的 UTC ISO 时间
- `resolution_note`：结算依据或无法结算原因

保存时缺 `target` 或 `level` 的记录直接标记为 `manual`，不进入自动结算。
`build_context(include_history=True)` 会对已到 `deadline` 的 `open` 记录按历史收盘价
结算：命中比较条件为 `hit`，未命中为 `miss`；目标不在 watchlist/扫描池、历史缓存不可用或
缺少可用收盘价时标记为 `unresolved` 并写明原因。结算结果会写回本地台账。

## Instrument 与 Quote

`Instrument`：

- `code`、`name`、`market`
- `exchange`、`category`
- `pool`：候选池分层（`core`/`broad`/`sector`/`defensive`/`rates`/`ai_chain` 等，
  watchlist 标的默认 `null` 按 core 处理）

`Quote`：

- 价格：`price`、`change`、`pct_change`、`prev_close`
- 日内：`open_price`、`high`、`low`
- 成交：`volume_lot`、`amount_10k`
- 溯源：`source`、`stale`、`as_of`
- `indicators`：可选技术指标

美股实时源失败时可用最近历史收盘价生成 `stale: true` 的 Quote；它不是实时价格。

## NewsItem 与 MarketEvent

`NewsItem` 保存标准化新闻：

- `title`、`url`
- `source_name`、`source_type`
- `published_at`、`summary`、`language`、`tags`
- `scope ∈ {holding, general}`：watchlist 定向 RSS/一手公告为 holding，
  通用新闻为 general
- `raw_metadata` 仅供内部调试，不进入序列化输出

`MarketEvent` 是从新闻中规则提取的事件，包含：

- 事件：`event_type`、`themes`、`rationale`
- 影响：`affected_markets`、`affected_symbols`、`matched_holdings`
- 判断：`sentiment`、`urgency`、`impact_horizon`、`confidence`
- 原新闻字段与 `raw_news_index`

## UpcomingEvent

未来催化剂事件，只收录"已官方公布的日程事实"，不做预测：

- `date`（ISO 日期）、`name`、`event_type ∈ {macro_release, central_bank, earnings, other}`
- `market`、`time_utc`（未知为 `null`）
- `scheduled_at`：可比较的 UTC ISO 时点；只有日期时为 `null`，禁止伪造午夜
- `time_precision ∈ {datetime, date}`：明确事件时间精度
- `status ∈ {scheduled, imminent, released_or_expired}`；上下文只返回仍为
  `scheduled`/`imminent` 的事件，已发生事件被过滤并计入质量节点
- `source ∈ {static_config, finnhub_earnings}`
- `affected_categories`：对该事件敏感的 watchlist 类别（路径事实，非方向判断）
- `affected_symbols`：按类别匹配命中的 `market:code` 列表
- `days_until`：构建时相对 `generated_at` 计算的自然日数
- `note`：影响路径的事实性备注

静态日程维护在 `stocks/config/event_calendar.json`；财报日来自 Finnhub
财报日历（只查询 watchlist 美股标的）。窗口由 `calendar.lookahead_days`
控制（默认 14 天）。

## portfolio_risk 多因子情景

`portfolio_risk.scenario` 在 v4 中扩展为多因子压力测试(2026-07-09):

- 保留简单情景:`market_down_5_pct`、`market_down_10_pct`、`market_up_5_pct`(CNY)
- 新增多因子情景(每个含 `description`、`impact_cny`、`impact_pct`、`details`):
  - `global_risk_off`：VIX>30 全球避险,按 exposure_tags 分配冲击系数
  - `china_shock`：中国特定政策冲击
  - `inflation_commodity`：通胀/大宗商品冲击
- 计算方法:`exposure_tag_weighted`——每笔持仓取第一个匹配标签的冲击系数,不重复计算

## rotation（板块轮动脚手架）

`AnalysisContext.rotation` 是纯事实的相对强弱排名（`schema_version: 1`）：

- `status ∈ {ok, partial, no_data}`；`as_of` 为参与标的最近一根 K 线时间
- `window`：`{short_bars: 5, long_bars: 20}`
- `items`：按 r20 降序，含 `symbol`、`name`、`category`、`pool`、
  `universe ∈ {watchlist, scan}`、`r5`、`r20`（百分数，历史不足为 `null`）、
  `above_ma20`、`bars`、`as_of`、`rank`
- `category_momentum`：按类别聚合的 r5/r20 均值
- `leaders` / `laggards`：r20 前三 / 后三（样本 ≤3 时 laggards 为空）
- `missing`：历史不足未参与排名的标的（显式列出，不伪造）

计算基于 HistoryCache 的日 K 收盘序列；扫描池标的来自
`stocks/config/sector_scan.json`，只参与历史回填与轮动，不请求实时行情、
不进入 MarketState 判断。

## action_signals（引擎动作信号）

`AnalysisContext.action_signals` 是规则化的方向性候选动作
（`schema_version: 1`，2026-07-02 用户裁决启用，约束见 PLAN §4）：

- `status ∈ {ok, partial, no_data}`；`counts` 按信号计数
- `items`：每标的一条，含 `symbol`、`name`、`category`、`pool`、`universe`、
  `signal`、`reasons`（触发该规则的指标事实，逐条可复核）、
  `action_hint`（信号对应的动作模板）、`as_of`、
  可选 `event_watch`（T+3 内已公布催化剂叠加）
- `signal ∈ {accumulate_candidate, wait_for_pullback, reduce_risk,
  avoid_catching_falling_knife, rotation_candidate, neutral_hold, no_data}`
- 规则阈值集中定义于 `stocks/engine/action_signals.py` 头部；
  历史 <15 bars 一律 `no_data`；`accumulate_candidate` 要求 20 日涨幅至少
  2%，排除仅略大于 0 的横盘噪声
- 信号是"候选动作"而非指令，最终判断归用户与 Agent

## PortfolioMapping、MarketState 与 DriftCheck

`PortfolioMapping`：

- `buckets`、`ratios`、`dominant_layers`
- `growth_exposure`、`buffer_strength`、`liquidity_status`
- `locked_assets_present`

`MarketState`：

- `risk_appetite`、`tech_state`、`safe_haven_state`
- `china_state`、`rates_state`、`crypto_state`
- `cross_asset_summary`

没有对应行情时状态必须为 `no_data`，不能生成中性或乐观默认值。

`DriftCheck`：

- `bucket`、`current_ratio`
- `target_min`、`target_max`
- `status`：`within_range` / `below_min` / `above_max`
- `gap`

目标资产桶缺失时，当前占比按 0% 检查，不能跳过。

## MacroSnapshot

`macro_snapshot` 将市场定价代理与滞后官方统计分开：

- 顶层市场字段：`usd_cny`、`vix`、`us_10y_yield`、`dxy`（FRED
  广义美元指数代理）、`gold`、`crude_oil`
- `official_stats`：`cpi_yoy`（由 CPIAUCSL 同比换算）、
  `us_unemployment`、`fed_funds_rate`
- `field_sources`：每个已填字段对应 `{source, as_of}`；日期为原始观测日，
  不使用抓取时刻冒充
- `source`、`errors`、`timestamp`；`timestamp` 只表示本次组装时刻

Composite 按字段合并 FRED → Yahoo → static_config，上游部分成功不会阻止下游
补齐缺失字段。官方月度统计使用 24 小时本地缓存。

## AnalysisContext v12

Agent 的统一入口：

- 元信息：`generated_at`、`schema_version`
- 金融记忆：`assets`、`asset_count`、`portfolio_constraints`、
  `portfolio_profile`、`asset_accounts`、`asset_positions`
- 市场输入：`quotes`、`news`、`news_count`
- 结构化事件：`market_events`、`news_digest`
- 脚手架：`market_state`、`portfolio_mapping`、`drift_checks`、`rotation`
- 决策语义：`action_signals`
- 前瞻输入：`upcoming_events`
- 历史：`recent_snapshots`、`recent_advice`
- 预测台账：`forecast_summary`
- v2 资产派生：`position_valuations`、`exposure_summary`、`liquidity_summary`、
  `asset_data_boundaries`、`advice_granularity`
- Agent 输入：`raw_prompt_input`
- 扩展数据：`macro_snapshot`、`technical_indicators`
- 质量与溯源：`data_quality`

v12 相对 v11 新增 v2 资产运行时估值、暴露、流动性、数据边界与建议粒度字段；
v11 相对 v10 新增 `forecast_summary`，包含预测台账 open 条数、最近结算结果、
hit/miss 样本统计；样本少于 10 条时只标记“样本不足”，不输出命中率。
v10 相对 v9 为 `NewsItem` 增加 `scope`，用于持仓定向来源优先匹配；
v9 相对 v8 扩展 `macro_snapshot` 的 `official_stats` 与逐字段来源/观测日；
v8 相对 v7 扩展 `UpcomingEvent` 的完整时点、时间精度与生命周期状态；
v7 相对 v6 新增 `upcoming_events`、`rotation`、`action_signals` 三个顶层字段，
`recent_advice` 附加派生 `trigger_review`；其余字段不变。

`raw_prompt_input` 遵循
`stocks/prompts/personal_advice_prompt.txt`，在本地 Agent 上下文中输出真实资产金额、
逐持仓市值、盈亏、暴露和流动性边界。仓位动作仍用比例、区间或自然语言表达，
不得保存具体货币金额。HTTP 适配器默认隐藏精确金额是远程接口安全策略，不改变
本地 `raw_prompt_input` 的真实金额语义。

## data_quality v10

`data_quality` 包含：

- `currency_conversion`：每项外币换算状态、来源与失败统计
- `asset_format`：当前资产文件 schema、base_currency、加载条数、position 数量与
  迁移/加载告警
- `asset_completeness`：逐 position 缺字段与降级/阻断问题，来源为
  `asset_data_boundaries`
- `quotes`：真实行情 `as_of` 的最旧值、缺失时间数量、请求/返回数量、按市场
  `as_of` 与状态、`single_source` 单源事实、Provider 与降级记录
- `news`：请求状态、来源分布、`scopes` 数量、Provider `errors` 和时效
- `macro`：按全部市场/官方统计字段给出来源、最旧真实 `as_of`、freshness、
  `missing_as_of`、`field_sources`、已填充/缺失字段和逐源错误
- `technical_indicators`：覆盖与缺失标的
- `market_events`：新闻提取与财报日历投影的数量、`calendar_event_count`、
  紧急度和持仓命中数
- `history_backfill`：启动时历史 K 线回填的结构化上报。字段包括
  `status`、`requested_count`、`ok_count`、`skipped_cached_count`、
  `failed_count` 与 `items`。每个 item 结构为
  `{symbol, market, source, rows, status, error}`，其中 `status ∈ {ok, skipped_cached, failed}`
  且 `source` 标注实际成功源（包括 `eastmoney_kline`、`tencent_kline`、
  `nasdaq_kline`、`binance_kline`、`yahoo_kline` 等）。每项同时给出
  `primary_source`、`fallback_source`、
  `degradation_result` 与逐源 `errors`，可区分主源成功、备用成功与全失败。
  失败标的记录 `error` 字符串；全部失败会触发 10 分钟冷却而非静默重试。

- `upcoming_events`：事件日历质量。`status ∈ {ok, partial, missing,
  not_configured}`、`lookahead_days`、`window_end`、`event_count`、
  `sources`（按来源计数）、`expired_count`（本次过滤的已发生事件数）与
  `cache`（Finnhub 财报日历 12h 缓存 hits/misses）、
  `errors`（按 Provider 记录失败原因，
  例如 Finnhub key 未配置时财报日历显式报错而非静默缺失）
- `rotation`：轮动脚手架覆盖。`status`、`as_of`、`item_count`、
  `missing_count` 与 `missing` 列表
- `action_signals`：动作信号覆盖。`status`、`item_count` 与按信号的 `counts`
- `auto_included_holdings`：本次 build_context 运行时自动加入行情/历史请求的
  detailed 持仓列表；只影响本次上下文，不写 watchlist
- `exposure_summary`、`liquidity_summary`、`advice_granularity`：对应顶层派生字段
  的质量摘要

通用状态包括 `ok`、`partial`、`degraded`、`missing`、
`not_requested` 和 `not_configured`。美股单源失败额外标记
`single_source_failed`，历史价格回填标记为 stale。

`schema_version` 语义：v10 相对 v9 新增 `asset_completeness`、
`auto_included_holdings`、`exposure_summary`、`liquidity_summary` 与
`advice_granularity`；v9 相对 v8 扩展 `upcoming_events.cache`、
`market_events.calendar_event_count` 以及 `news.scopes/errors`；
v8 相对 v7 扩展 `macro` 的逐字段来源、
真实最旧时点与官方统计质量；v7 相对 v6 扩展 `history_backfill.items` 的逐源降级字段；
v6 相对 v5 为 `quotes.by_market` 增加 `single_source`；
v5 相对 v4 为 `upcoming_events` 增加 `expired_count`；
v4 相对 v3 新增 `upcoming_events`、`rotation` 与
`action_signals` 三个节点；v3 相对 v2 新增 `history_backfill` 节点；
其余节点结构不变。
字段增减为破坏性变更，须同步更新本文件、
`_build_data_quality` 与 tests 断言(见 PLAN §4 红线)。

## 最小历史快照

`.local/snapshots/` 中每份快照只保存：

- `generated_at`、`asset_count`
- 组合摘要
- `market_state`
- `drift_checks`

默认最多保留 30 份。构建新上下文时先加载近期快照，新快照在成功构建后写入，因此第二次
运行能够看到第一次的摘要。
