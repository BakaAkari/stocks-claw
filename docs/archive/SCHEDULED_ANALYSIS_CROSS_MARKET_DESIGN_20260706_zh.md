> **状态:已被实现取代(SUPERSEDED)**
> 本文档为历史设计稿,其中描述的功能已实现并上线。
> 现行架构和契约以 `ARCHITECTURE.md`、`stocks/DATA_MODEL.md` 和代码为准。
> 保留本文仅作设计决策追溯。

# 跨市场定时持仓分析与 Agent 推送设计

> 日期:2026-07-06
> 状态:S3 设计归档；S2.5 扩扫支撑已于 2026-07-06 完成；S3-1~S3-5 工程实现已于 2026-07-06 完成,当前进入 S3-E 真实试运行
> 范围:A 股持仓 + IBKR 美股持仓对应的美股交易时段
> 约束:系统不自动交易,不擅自写长期金融记忆,最终自然语言判断仍由用户 Agent 完成

## 0. 总结结论

这个功能方向与现有愿景不冲突。它正好对应 `VISION.md` 的"主动节奏"能力域。2026-07-06
S3-1~S3-5 已按"定时扫描与触发推送(pull→push)"工程实现,后续状态以根目录
`PLAN.md` 与 `EXECUTION_PLAN.md` 为准。

真正需要避免的冲突有五个:

1. 不能只把系统做成自然语言报告生成器。系统应该生成结构化运行产物,自然语言摘要只是附属物；
   Agent 读取结构化证据后再做最终二次分析。
2. 不能把 Agent 当 cron。Agent 可以定时读取和推送,但定时、行情抓取、触发核对、
   数据质量与幂等状态应由 stocks-claw 负责。
3. 不能只按 A 股时间设计。用户资产里有 IBKR 美股持仓,美股会跨到中国夜间和次日凌晨,
   且必须按 `America/New_York` 交易所时区处理夏令时。
4. 不能绕过当前行动清单。S3 依赖 v2 资产、成本价、持仓数量、流动性和 pnl
   触发器；S2-E 已在 2026-07-06 由真实试用关闭,S2.5 扩扫支撑也已完成。
5. 不能做后台自动写入建议、执行或预测。任何长期记忆写入仍需要用户确认。

已实现的第一版不是常驻服务,而是"轻量调度 + 幂等运行产物 + Agent 读取最新产物":
用 macOS launchd / NAS cron 每 5 到 15 分钟唤起一次 CLI,CLI 判断当前是否有到期 session,
到期则构建上下文、核对触发、落 `.local/scheduled_runs/` 运行产物；Agent 再读取最新产物,
按 session 类型生成推送文本。

## 1. 产品目标

### 1.1 用户场景

用户不想每次手动问"现在要不要动"。系统应在关键时间点主动准备好一份可审查的持仓分析底稿,
让 Agent 可以进一步解释并推送:

- A 股盘前:今天重点盯什么,哪些触发条件接近。
- A 股开盘后:已有持仓有没有异常跳空、早盘破位或过热追涨风险。
- A 股收盘前:哪些持仓需要在收盘前做条件式处理。
- A 股盘后:今天的事实复盘、触发器核对、明天计划。
- 美股盘前:IBKR 美股持仓和美元资产的隔夜风险、盘前事件、开盘计划。
- 美股开盘后:早盘波动是否改变已有触发判断。
- 美股收盘前:是否有需要在美股收盘前处理的持仓风险。
- 美股盘后:收盘事实、触发核对、是否次日早上再推送给用户。

### 1.2 最小可用版本

第一版只做四件事:

1. 根据配置判断当前是否命中 A 股或美股 session。
2. 命中时运行 `build_context()` 获取 `AnalysisContext v12`。
3. 基于上下文生成结构化 `ScheduledAnalysisRun v1` 产物,包含数据质量、触发状态、
   持仓估值、Agent 任务说明和推荐推送策略。
4. CLI/MCP 提供"读取最新运行产物"入口,让外部 Agent 完成最终中文分析和推送。

第一版不做:

- 券商下单或 IBKR 交易接口。
- 自动同步 IBKR 账户真实持仓。
- 长驻 daemon、Redis、数据库、Web 管理台。
- 自动保存建议、执行或预测。
- 复杂回测和信号胜率统计。

## 2. 系统职责边界

### 2.1 stocks-claw 负责

- 读取本地确认过的 v2 资产和用户画像。
- 抓取当前可用行情、历史、新闻、事件、宏观数据。
- 计算持仓市值、浮动盈亏、暴露、流动性、建议粒度。
- 核对已保存建议的 price / pct_change / pnl 触发器。
- 判断当前 session 是否应运行,并保证同一市场日同一 session 默认只跑一次。
- 保存结构化运行产物和可选 Markdown 摘要到 `.local/`。
- 明确标记数据质量、stale 行情、单源失败、缺成本、缺数量、缺估值日期等边界。

### 2.2 Agent 负责

- 读取最新 `ScheduledAnalysisRun`。
- 审查系统给出的结构化事实与 `data_quality`。
- 根据当前对话、用户偏好和临时意图生成最终推送文本。
- 对每条引擎动作信号选择采纳或推翻并说明原因。
- 如需保存建议、执行、预测或修改资产,显式向用户确认后再调用写接口。

### 2.3 用户负责

- 决定是否采用建议。
- 决定哪些报告 session 要推送,哪些只生成不打扰。
- 在试运行后评价:哪些提醒有用、哪些太吵、哪些文本风格不对、哪些功能方向需要调整。

## 3. 跨市场 session 设计

### 3.1 统一规则

所有 session 都用交易所本地时区定义,再转换为用户时区显示。这样美股夏令时不会被硬编码错。

- 用户时区:`Asia/Shanghai`
- A 股交易所时区:`Asia/Shanghai`
- 美股交易所时区:`America/New_York`
- 当前日期 2026-07-06 处于美国夏令时,美股常规交易时段对应北京时间 21:30 到次日 04:00。
- 美国冬令时期间,美股常规交易时段对应北京时间 22:30 到次日 05:00。

### 3.2 A 股 session

| session | 建议触发时间 | 用户可见名称 | 主要用途 | 默认推送 |
|---|---:|---|---|---|
| `cn_pre_open` | 08:50 | A 股盘前 | 用昨日收盘、隔夜美股、事件日历和持仓触发器生成当天计划 | 推送 |
| `cn_open_watch` | 09:45 | A 股开盘观察 | 检查早盘跳空、破位、过热追涨和数据源是否正常 | 推送 |
| `cn_pre_close` | 14:35 | A 股收盘前 | 最适合做当天可执行条件判断,尤其是止损/减仓/不追涨 | 推送 |
| `cn_after_close` | 15:20 | A 股盘后 | 用收盘事实核对触发器、复盘动作、准备明日计划 | 推送或汇总 |

`cn_pre_close` 是 A 股最重要 session。它应该优先回答"收盘前是否需要动已有持仓",
而不是输出大而全日报。

### 3.3 美股 / IBKR session

美股 session 以 `America/New_York` 定义。以下北京时间仅作展示,实际实现必须由
`zoneinfo.ZoneInfo("America/New_York")` 换算。

| session | 美东时间 | 夏令时北京时间 | 冬令时北京时间 | 主要用途 | 默认推送 |
|---|---:|---:|---:|---|---|
| `us_pre_open` | 09:00 | 21:00 | 22:00 | 美股盘前计划,关注 IBKR 持仓、财报/宏观事件、盘前大幅波动 | 推送 |
| `us_open_watch` | 10:00 | 22:00 | 23:00 | 开盘后确认,避免只看盘前波动误判 | 推送 |
| `us_mid_session` | 12:30 | 00:30 次日 | 01:30 次日 | 可选 session,只在高波动或重要事件日启用 | 默认关闭 |
| `us_pre_close` | 15:30 | 03:30 次日 | 04:30 次日 | 收盘前风险处理,最打扰睡眠 | 默认只生成,critical 才推送 |
| `us_after_close` | 16:20 | 04:20 次日 | 05:20 次日 | 收盘事实、触发核对、盘后财报后续 | 默认只生成,次日早上汇总 |

IBKR 在本设计里指"用户在 IBKR 账户中录入的美股/美元持仓"。第一版不连接 IBKR API,
不自动同步 broker 账户余额和成交。IBKR 账户应以 v2 `Account` 表示,
持仓以 `Position` 表示,美股证券通过 `instrument_key: us:SYMBOL` 接入行情和 PnL 计算。

### 3.4 免打扰与 critical 推送

美股 `us_pre_close` 与 `us_after_close` 在中国时间是凌晨。第一版应默认:

- 仍生成运行产物,保证第二天可复盘。
- 不主动推送普通"不动/观察"类结论。
- 如果触发 critical 条件,允许即时推送。
- 非 critical 的美股盘后结论并入次日 `cn_pre_open` 或用户设定的晨报。

critical 条件的第一版建议:

- 已保存触发器状态从 `not_fired` 变成 `fired`。
- detailed 持仓实时或收盘 PnL 跌破用户保存的 `pnl_pct_below`。
- 单一持仓日内跌幅超过配置阈值,例如 -5%,且该持仓 `rebalance_eligible=true`。
- 行情大面积失败导致无法判断,但前一条建议本应今天复核。

critical 仍然不是自动交易信号,只改变推送优先级。

## 4. 结构化运行产物

### 4.1 为什么不能只生成 Markdown

如果系统只生成一篇 Markdown 报告,Agent 需要二次解析自然语言才能知道哪些是事实、
哪些是推断、哪些是数据缺口。这会违背当前项目的核心边界:系统产事实和候选,
Agent 产最终判断。

因此第一等产物必须是 JSON。Markdown 只用于人类快速查看。

### 4.2 文件位置

建议目录:

```text
.local/scheduled_runs/
  2026-07-06/
    cn/
      cn_pre_close/
        20260706T063500Z_cn_pre_close.json
        20260706T063500Z_cn_pre_close.md
    us/
      us_pre_open/
        20260706T130000Z_us_pre_open.json
        20260706T130000Z_us_pre_open.md
  latest/
    cn_pre_close.json
    us_pre_open.json
```

`latest/*.json` 建议是普通 pointer JSON 或复制后的最新产物,不要依赖 symlink。
`.local/` 已是本地隐私目录,不得提交。

### 4.3 `ScheduledAnalysisRun v1`

建议 JSON 顶层:

```json
{
  "schema_version": 1,
  "run_id": "20260706T063500Z_cn_pre_close",
  "generated_at": "2026-07-06T06:35:00Z",
  "market": "cn",
  "session": "cn_pre_close",
  "market_date": "2026-07-06",
  "exchange_timezone": "Asia/Shanghai",
  "user_timezone": "Asia/Shanghai",
  "scheduled_for": "2026-07-06T14:35:00+08:00",
  "status": "ok",
  "status_reason": null,
  "source_context": {
    "schema_version": 12,
    "generated_at": "2026-07-06T06:35:02Z"
  },
  "portfolio_scope": {
    "account_ids": [],
    "position_ids": [],
    "instrument_keys": [],
    "included_markets": ["a", "us"],
    "primary_market": "cn"
  },
  "session_summary": {
    "headline": "A 股收盘前只盯弱项和已保存触发器",
    "priority": "normal",
    "push_policy": "push_now"
  },
  "position_reviews": [],
  "trigger_reviews": [],
  "action_signal_reviews": [],
  "data_quality": {},
  "agent_task": {},
  "write_policy": {
    "may_write_financial_memory": false,
    "requires_user_confirmation": true
  },
  "notification": {
    "recommended": true,
    "urgency": "normal",
    "quiet_hours_blocked": false
  }
}
```

字段说明:

- `status ∈ {ok, degraded, skipped_market_closed, skipped_duplicate, failed}`。
- `market ∈ {cn, us, crypto, global}`。第一版只需要 `cn` 和 `us`。
- `session` 使用配置中定义的稳定 id。
- `portfolio_scope.primary_market` 表示本次报告重点,但不意味着忽略跨市场影响。
  例如 `cn_pre_open` 可以引用隔夜美股,`us_pre_open` 可以引用 A 股持仓的人民币现金约束。
- `position_reviews` 是系统层事实核对,不是最终建议。
- `agent_task` 是给 Agent 的自包含任务,不能只写"读取 prompt 文件"。
- `write_policy` 明确本次后台运行不得写长期金融记忆。

### 4.4 `position_reviews`

每个 detailed 持仓建议包含:

```json
{
  "position_id": "ibkr_nvda",
  "display_name": "NVDA",
  "instrument_key": "us:NVDA",
  "account_id": "ibkr",
  "advice_granularity": "detailed",
  "valuation": {
    "market_value_cny": 12345.67,
    "price": 172.3,
    "price_as_of": "2026-07-06T14:00:00Z",
    "price_source": "finnhub",
    "stale": false
  },
  "pnl": {
    "cost_basis_unit": 140.0,
    "pnl_pct": 23.07,
    "unrealized_pnl_cny": 2345.67
  },
  "liquidity": {
    "rebalance_eligible": true,
    "tier": "t1"
  },
  "flags": [],
  "session_facts": [
    "日内涨幅 1.2%",
    "浮盈超过 20%"
  ]
}
```

对于 `sector` / `fixed` / `manual` 粒度:

- `sector`: 可以带代理标的信号,但必须标明代理不是持仓价格。
- `fixed`: 只做事实展示和风险边界,不生成调仓动作。
- `manual`: 如果估值过期或缺 `as_of`,必须进入 `data_quality.asset_completeness`。

### 4.5 `agent_task`

`agent_task` 应包含:

```json
{
  "task_version": 1,
  "language": "zh-CN",
  "audience": "single_user",
  "session_intent": "pre_close_decision",
  "must_answer": [
    "已有持仓现在是否需要动",
    "哪些触发器已经触发或接近触发",
    "数据质量是否足以支持动作"
  ],
  "must_not_do": [
    "不得承诺收益",
    "不得忽略 data_quality",
    "不得建议动用 rebalance_eligible=false 的资产",
    "不得把代理 ETF 价格触发器套到场外基金"
  ],
  "output_style": {
    "max_words": 900,
    "prefer_actionable_bullets": true,
    "include_data_boundary": true
  },
  "final_analysis_instructions": "先给一句话执行结论,再按持仓列出动作,最后说明数据边界。"
}
```

这样 Agent 不需要猜当前 session 该怎么写。用户后续要微调文本风格,主要调这里和 session
prompt,不必改行情和持仓逻辑。

## 5. 调度架构

### 5.1 组件

```text
macOS launchd / NAS cron
        |
        v
stocks.adapters.cli --scheduled-run-due
        |
        v
MarketSessionCalendar
        |
        v
ScheduledAnalysisRunner
        |
        +--> StocksEngine.build_context()
        +--> TriggerReview / position valuation / data_quality
        +--> agent_task builder
        |
        v
RunArtifactStore (.local/scheduled_runs + duplicate guard)
        |
        v
Agent reads latest run -> final analysis -> notification
```

### 5.2 配置文件

建议新增 `stocks/config/scheduled_sessions.json`:

```json
{
  "schema_version": 1,
  "user_timezone": "Asia/Shanghai",
  "artifact_dir": ".local/scheduled_runs",
  "default_duplicate_window_minutes": 90,
  "quiet_hours": {
    "enabled": true,
    "start": "00:00",
    "end": "07:30",
    "timezone": "Asia/Shanghai",
    "allow_critical": true
  },
  "markets": {
    "cn": {
      "enabled": true,
      "exchange_timezone": "Asia/Shanghai",
      "sessions": [
        {"id": "cn_pre_open", "time": "08:50", "intent": "pre_open_plan", "push": "normal"},
        {"id": "cn_open_watch", "time": "09:45", "intent": "open_watch", "push": "normal"},
        {"id": "cn_pre_close", "time": "14:35", "intent": "pre_close_decision", "push": "normal"},
        {"id": "cn_after_close", "time": "15:20", "intent": "after_close_review", "push": "digest"}
      ]
    },
    "us": {
      "enabled": true,
      "exchange_timezone": "America/New_York",
      "sessions": [
        {"id": "us_pre_open", "time": "09:00", "intent": "pre_open_plan", "push": "normal"},
        {"id": "us_open_watch", "time": "10:00", "intent": "open_watch", "push": "normal"},
        {"id": "us_mid_session", "time": "12:30", "intent": "mid_session_check", "push": "disabled"},
        {"id": "us_pre_close", "time": "15:30", "intent": "pre_close_decision", "push": "critical_only"},
        {"id": "us_after_close", "time": "16:20", "intent": "after_close_review", "push": "digest"}
      ]
    }
  }
}
```

第一版可以不引入新的市场日历依赖。可用标准库 `zoneinfo` 处理时区和夏令时,
用轻量 JSON 维护 A 股和美股休市日。后续如果休市日维护成本变高,再考虑引入专门日历库。

### 5.3 CLI

建议新增命令:

```bash
uv run python -m stocks.adapters.cli --scheduled-run-due
uv run python -m stocks.adapters.cli --scheduled-run-due --now "2026-07-06T14:35:00+08:00"
uv run python -m stocks.adapters.cli --scheduled-run-session cn_pre_close
uv run python -m stocks.adapters.cli --scheduled-run-session us_pre_open --force
uv run python -m stocks.adapters.cli --scheduled-run-latest cn_pre_close
```

语义:

- `--scheduled-run-due`: 由 cron/launchd 高频调用,内部判断是否有到期 session。
- `--now`: 测试专用,模拟时间,默认测试不得访问真实网络。
- `--scheduled-run-session`: 手动跑指定 session,用于调试和补跑。
- `--force`: 跳过重复运行保护。
- `--scheduled-run-latest`: 给 Agent 读取最新 JSON。

### 5.4 MCP

后续可以给 Agent 暴露:

- `scheduled_run_due`
- `scheduled_run_session`
- `scheduled_run_latest`
- `scheduled_run_list`

第一版最小路径可以先只做 CLI。如果当前 Agent 能读本地文件或调用 CLI,可以晚一点再接 MCP。

## 6. 幂等、失败和质量语义

### 6.1 幂等

同一个 `market_date + market + session` 默认只成功运行一次。再次命中时返回:

```json
{
  "status": "skipped_duplicate",
  "existing_run_id": "20260706T063500Z_cn_pre_close"
}
```

如果上次是 `failed`,允许在同一 session 窗口内自动重试一次。超过窗口后需要 `--force`。

### 6.2 休市

休市日返回 `skipped_market_closed`,仍可选择生成一份极简产物,说明没有交易 session。
不应在休市日伪造盘前/盘中信号。

### 6.3 数据失败

如果行情、历史、新闻或宏观数据失败:

- 产物仍应生成,但 `status=degraded`。
- `data_quality` 必须保留原始失败原因。
- Agent 推送必须把该失败对结论的影响说清楚。
- 对 `stale: true` 的美股历史收盘价,不得写成实时价格。

### 6.4 写入边界

后台定时任务只允许写运行产物和最小上下文快照。以下写入禁止自动发生:

- 资产、账户、持仓。
- 用户画像。
- 建议台账。
- 执行记录。
- 预测台账。

如果 Agent 看完运行产物认为应该保存建议,必须在推送里请用户确认,再走现有
`advice_save --confirmed` 或 MCP `confirmed: true`。

## 7. 输出文本分层

### 7.1 系统 Markdown 摘要

系统可以同时生成 `.md`,但它只服务调试和人工快速阅读。建议结构:

- 运行信息:session、时间、状态、数据质量。
- 一句话机器结论:例如"未发现必须立即动作的触发器"。
- 持仓事实:按 account / position 列出价格、Pnl、flags。
- 触发器事实:fired / approaching / not_fired / no_data。
- Agent 任务:本次最终分析必须回答的问题。

系统 Markdown 不应写成完整投资建议长文,否则会和 Agent 职责重叠。

### 7.2 Agent 最终推送模板

不同 session 的文本重点不同:

- `pre_open_plan`: 今天盯什么,什么条件触发动作,哪些资产不能动。
- `open_watch`: 早盘有没有打破原计划,不要追涨或恐慌。
- `pre_close_decision`: 是否在收盘前处理已有持仓,列出"动/不动/条件动"。
- `after_close_review`: 触发器核对、今日结论、下一交易日计划。

美股默认还需要标注:

- "这是 IBKR 美股持仓视角,不是 IBKR 实时账户同步。"
- "如果是凌晨生成,普通结论已延迟到晨间汇总。"

## 8. 与现有文档和愿景的冲突审计

### 8.1 不冲突的部分

- 与 `VISION.md` 一致:确定性系统负责工作台、记忆、触发核对、定时节奏；Agent 负责综合推理。
- 与 `PLAN.md` 一致:主动节奏的最薄版本就是 cron + 触发命中推送。
- 与 `AGENT_GUIDE.md` 一致:所有结论必须结合 `data_quality`,最终分析由 Agent 完成。
- 与 `DATA_MODEL.md` 一致:S3 复用 v2 `Account` / `Position`、`position_valuations`、
  `exposure_summary`、`liquidity_summary`、`advice_granularity`、pnl 触发器。
- 与本地持久化边界一致:运行产物进入 `.local/`,不提交、不污染示例数据。

### 8.2 需要显式规避的冲突

1. **S3-E 真实试运行仍未关闭。** S3-1~S3-5 工程实现已于 2026-07-06 完成,
   但跨多个 A 股/美股 session 的准时性、夜间打扰策略和 Agent 文风仍需真实运行验证。
2. **不能把"报告"当 source of truth。** 现有架构强调 `AnalysisContext` 和 data_quality；
   S3 必须输出 JSON 运行产物,不能只存一篇 Markdown。
3. **不能硬编码北京时间美股 session。** 美国夏令时会改变北京时间展示。实现必须按
   `America/New_York` 定义 session,再转换到 `Asia/Shanghai`。
4. **不能宣称 IBKR 实时同步。** 当前系统支持本地录入 IBKR 持仓并抓美股行情,
   但没有 broker API 同步、没有订单状态、没有成交回报。
5. **不能夜间无差别打扰。** 美股收盘前和盘后在中国凌晨,默认应生成产物但不推送普通结论。
6. **不能自动保存建议。** 即便触发器 fired,后台也只能生成提醒；保存建议和执行记录仍需确认。
7. **不能引入重型依赖作为第一版。** 根据成长规则,第一版应用标准库时区 + JSON 配置 +
   cron/launchd 完成,等真实试运行证明价值后再加厚。

### 8.3 本次检查发现的既有文档漂移

检查时发现 `ARCHITECTURE.md` 仍有 `AnalysisContext v10` 的旧描述,而
`stocks/DATA_MODEL.md` 与代码已是 `AnalysisContext.schema_version == 12`、
`data_quality v10`。这属于 S2 后文档漂移,不是 S3 设计冲突。2026-07-06 文档清理已将
`ARCHITECTURE.md` 现状描述同步到 v12。

检查时还发现 `stocks/DATA_MODEL.md` 小节标题仍写 `AnalysisContext v11`,但正文已经说明
v12 相对 v11 新增字段。2026-07-06 文档清理已将标题同步为 v12。

## 9. 试运行与反馈闭环

用户想"开发完以后运行一段时间,再微调输出文本和功能方向"。这与现有成长规则高度一致。
建议把 S3 出口定义成试运行,而不是一次性追求完美。

### 9.1 试运行周期

建议先运行 2 到 4 周:

- 第一周只生成产物和本地读取,减少误推送风险。
- 第二周开启 A 股四个 session 推送。
- 第三周开启美股 `us_pre_open` / `us_open_watch` 推送,凌晨 session 只生成。
- 第四周根据用户反馈调整 session、阈值和文本。

### 9.2 反馈记录

可以新增 `.local/scheduled_feedback/`:

```json
{
  "run_id": "20260706T063500Z_cn_pre_close",
  "useful": true,
  "too_noisy": false,
  "missed_issue": null,
  "text_feedback": "结论够直接,但美股凌晨不需要推送",
  "decision_impact": "helped_hold",
  "recorded_at": "2026-07-06T08:00:00Z"
}
```

反馈记录是本地试运行数据,不是金融记忆。它可以帮助后续微调输出文本和 session 策略。

## 10. 实施切片建议

> 2026-07-06 更新:S3-1~S3-5 已工程实现；S3-E 仍待真实试运行关闭。

### S3-0 立项前置

- 已完成 S2-E:真实资产迁移为 v2,关键上市持仓补齐 quantity + cost_basis。
- 已用真实 build_context 验证 A 股和 IBKR 美股持仓的估值、Pnl、暴露、流动性输出。
- 用户明确开工 S3 定时扫描与触发推送。

### S3-0.5 可选支撑:受控扫描池扩容

已完成。2026-07-06 S2.5 将 `stocks/config/sector_scan.json` 扩为 50 项,其中
A 股/港股代理 32 项、美股 18 项,并加配置守门测试。实现中剔除了受份额拆分污染
历史收益的 `515880`,改用 `159695` 通信 ETF 嘉实。

完成后的约束仍保持:

- 只扩 `stocks/config/sector_scan.json`,不让系统自由扫描全市场。
- A 股每个方向先选 1 个高流动性代表 ETF,总量控制在 A 股扫描项约 30~40 个。
- 覆盖宽基、成长、周期、消费、防御和港股主题 ETF 代理。
- 港股只用 A 股上市 ETF/QDII 代理,例如恒生科技、恒生医疗、港股互联网;不引入
  `hk` 市场,不声称获得港股实时行情。
- 验收必须证明新增标的能完成历史回填,进入 rotation/action_signals,且缺数据会在
  `data_quality` 显式上报,不能拖垮现有扫描池。

### S3-1 session 配置与时区日历

实现状态:已完成。

- 新增 `scheduled_sessions.json`。
- 新增 `MarketSessionCalendar` 负责 session 解析与到期判断。
- 覆盖 A 股固定时段、美股 `America/New_York` 夏令时换算、休市跳过、重复运行跳过。

### S3-2 运行产物模型与存储

实现状态:已完成。

- 新增 `ScheduledAnalysisRun` dataclass 或等价 dict builder。
- 新增 `RunArtifactStore`。
- 产物写入 `.local/scheduled_runs/`,latest 指针可读取。

### S3-3 Runner 与 CLI

实现状态:已完成。

- 新增 `ScheduledAnalysisRunner`。
- CLI 支持 `--scheduled-run-due`、`--scheduled-run-session`、`--scheduled-run-latest`。
- 默认测试使用 fixture 时间和 mock engine,不访问真实网络。

### S3-4 Agent handoff

实现状态:已完成。

- 生成 `agent_task`。
- 根据 session intent 给出不同 `must_answer` 和输出风格。
- 确保 Agent 可以只读 JSON 就完成二次分析,无需读取仓库 prompt。

### S3-5 通知适配

实现状态:已完成第一版 recommended push policy,真实通知渠道未接入。

- 第一版可以只输出"recommended push policy"。
- 后续按用户选择接入 Lark/本地通知/邮件。
- 通知层只发消息,不写金融记忆。

### S3-E 试运行验收

实现状态:待真实试运行关闭。

- 至少 10 个 A 股 session 产物、6 个美股 session 产物。
- 至少 1 次美股夏令时换算验证。
- 至少 1 次 `skipped_market_closed` 或模拟休市测试。
- 至少 1 次 duplicate skip。
- 至少 1 次 degraded 数据质量仍生成产物。
- 用户对推送频率、文本风格和决策帮助做价值裁决。

## 11. 验收标准

工程验收:

- `uv run ruff check .`
- `uv run python -m pytest -q`
- `uv run python -m compileall -q stocks tests`
- `uv run python -m stocks.adapters.cli --output json --no-news --no-quotes`
- 模拟时间跑通 A 股 `cn_pre_close` 与美股 `us_pre_open`。
- 默认测试不得访问真实网络。

行为验收:

- A 股交易日 14:35 生成 `cn_pre_close` 产物。
- 2026-07-06 这种美国夏令时日期,`us_pre_open` 对应北京时间 21:00,
  `us_open_watch` 对应 22:00。
- 冬令时模拟日期,`us_pre_open` 自动变成北京时间 22:00,
  `us_open_watch` 自动变成 23:00。
- 美股凌晨 session 默认不推送普通结论,critical 才建议即时推送。
- IBKR 持仓报告明确来自本地 v2 持仓 + 行情,不宣称 broker 实时同步。
- 所有自动运行产物都带 `data_quality` 和 `write_policy.requires_user_confirmation=true`。

## 12. 开放决策

开发前建议用户明确五个偏好:

1. 美股凌晨 `us_pre_close` / `us_after_close` 是否允许 critical 即时推送。
2. `us_mid_session` 是否启用。默认建议关闭。
3. 首选通知渠道:Agent 对话、飞书、邮件、本地通知,还是先只生成文件。
4. 试运行期间每天最多接受几次推送。
5. A 股盘后和美股盘后是否合并成次日晨报。

这些偏好不影响底层数据结构,可以先通过 config 配置,不用进入长期金融记忆。
