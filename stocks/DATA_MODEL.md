# 现行数据模型

本文只描述当前代码中的 schema。权威实现位于 `stocks/domain/models.py`，
`AnalysisContext.schema_version` 当前为 `11`。

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

用户确认的单条金融资产：

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

## Investor profile

画像保存在 `.local/investor_profile.json`，是可扩展 JSON 对象。当前示例字段：

- `risk_tolerance`
- `investment_horizon`
- `preferences`
- `constraints`
- `updated_at`：写入时由系统更新

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
  `type ∈ {price_above, price_below, pct_change_above, pct_change_below}`，
  `level` 为数字（价位或百分数），`action` 为非空动作描述。
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

## AnalysisContext v11

Agent 的统一入口：

- 元信息：`generated_at`、`schema_version`
- 金融记忆：`assets`、`asset_count`、`portfolio_constraints`、
  `portfolio_profile`
- 市场输入：`quotes`、`news`、`news_count`
- 结构化事件：`market_events`、`news_digest`
- 脚手架：`market_state`、`portfolio_mapping`、`drift_checks`、`rotation`
- 决策语义：`action_signals`
- 前瞻输入：`upcoming_events`
- 历史：`recent_snapshots`、`recent_advice`
- 预测台账：`forecast_summary`
- Agent 输入：`raw_prompt_input`
- 扩展数据：`macro_snapshot`、`technical_indicators`
- 质量与溯源：`data_quality`

v11 相对 v10 新增 `forecast_summary`，包含预测台账 open 条数、最近结算结果、
hit/miss 样本统计；样本少于 10 条时只标记“样本不足”，不输出命中率。
v10 相对 v9 为 `NewsItem` 增加 `scope`，用于持仓定向来源优先匹配；
v9 相对 v8 扩展 `macro_snapshot` 的 `official_stats` 与逐字段来源/观测日；
v8 相对 v7 扩展 `UpcomingEvent` 的完整时点、时间精度与生命周期状态；
v7 相对 v6 新增 `upcoming_events`、`rotation`、`action_signals` 三个顶层字段，
`recent_advice` 附加派生 `trigger_review`；其余字段不变。

`raw_prompt_input` 遵循
`stocks/prompts/personal_advice_prompt.txt`，只表达资产金额区间，不暴露逐笔精确金额。
精确值仍存在于结构化 `assets`，供受控调用方按需使用。

## data_quality v9

`data_quality` 包含：

- `currency_conversion`：每项外币换算状态、来源与失败统计
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

通用状态包括 `ok`、`partial`、`degraded`、`missing`、
`not_requested` 和 `not_configured`。美股单源失败额外标记
`single_source_failed`，历史价格回填标记为 stale。

`schema_version` 语义：v9 相对 v8 扩展 `upcoming_events.cache`、
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
