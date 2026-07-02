# 现行数据模型

本文只描述当前代码中的 schema。权威实现位于 `stocks/domain/models.py`，
`AnalysisContext.schema_version` 当前为 `6`。

## FinancialAsset

用户确认的单条金融资产：

- `name`、`platform`、`amount`
- `asset_type`、`notes`、`confirmed`
- `currency`：用户输入的原始币种
- `amount_cny`：运行时派生的人民币估值，不写回资产文件
- `conversion_status`：`ok` / `degraded` / `failed`
- `conversion_source`、`conversion_rate`

持久化只写原始资产字段。换算失败的外币资产仍保留原金额和币种，但
`valuation_cny` 为 `null`，不能静默计入组合总值。

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

`AnalysisContext.recent_advice` 会在构建上下文时附加派生的 `performance` 字段，
用于并列展示建议日至最近历史收盘价的价格事实。该字段不写回 `.local/advice/`。

## Instrument 与 Quote

`Instrument`：

- `code`、`name`、`market`
- `exchange`、`category`

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
- `raw_metadata` 仅供内部调试，不进入序列化输出

`MarketEvent` 是从新闻中规则提取的事件，包含：

- 事件：`event_type`、`themes`、`rationale`
- 影响：`affected_markets`、`affected_symbols`、`matched_holdings`
- 判断：`sentiment`、`urgency`、`impact_horizon`、`confidence`
- 原新闻字段与 `raw_news_index`

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

## AnalysisContext v6

Agent 的统一入口：

- 元信息：`generated_at`、`schema_version`
- 金融记忆：`assets`、`asset_count`、`portfolio_constraints`、
  `portfolio_profile`
- 市场输入：`quotes`、`news`、`news_count`
- 结构化事件：`market_events`、`news_digest`
- 脚手架：`market_state`、`portfolio_mapping`、`drift_checks`
- 历史：`recent_snapshots`、`recent_advice`
- Agent 输入：`raw_prompt_input`
- 扩展数据：`macro_snapshot`、`technical_indicators`
- 质量与溯源：`data_quality`

`raw_prompt_input` 遵循
`stocks/prompts/personal_advice_prompt.txt`，只表达资产金额区间，不暴露逐笔精确金额。
精确值仍存在于结构化 `assets`，供受控调用方按需使用。

## data_quality v2

`data_quality` 包含：

- `currency_conversion`：每项外币换算状态、来源与失败统计
- `quotes`：真实行情 `as_of` 的最旧值、缺失时间数量、请求/返回数量、按市场
  `as_of` 与状态、Provider 与降级记录
- `news`：请求状态、来源分布和时效
- `macro`：来源、已填充/缺失字段和错误
- `technical_indicators`：覆盖与缺失标的
- `market_events`：提取数量、紧急度和持仓命中数

通用状态包括 `ok`、`partial`、`degraded`、`missing`、
`not_requested` 和 `not_configured`。美股单源失败额外标记
`single_source_failed`，历史价格回填标记为 stale。

## 最小历史快照

`.local/snapshots/` 中每份快照只保存：

- `generated_at`、`asset_count`
- 组合摘要
- `market_state`
- `drift_checks`

默认最多保留 30 份。构建新上下文时先加载近期快照，新快照在成功构建后写入，因此第二次
运行能够看到第一次的摘要。
