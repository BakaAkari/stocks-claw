# Agent 使用指南

**本文档供 AI Agent 和人类开发者共同使用。如果你是 AI Agent，本文是你操作
stocks-claw 的唯一入口文档。**

系统定位:个人投资分析师工作台。确定性引擎负责金融记忆、市场数据、定时产物；
Agent 负责读取产物并生成最终分析。系统不下单、不承诺收益。

**Agent 快速开始:**

- 读取定时产物:`uv run python -m stocks.adapters.cli --scheduled-run-latest cn_pre_close`
- JSON 中的 `agent_task` 字段是运行时自包含指令——严格按其执行即可，不需要外部 prompt
- 生成自定义上下文:`uv run python -m stocks.adapters.cli --output json`
- 所有路径基于项目根目录 `/mnt/user/code-project/stocks-claw`
- 飞书输出仅用 `**加粗**`、`` `行内代码` ``、`- 列表`、`[链接](url)`，禁用 `#` `|` ` ``` ` `>` `---` HTML

## 1. 必须遵守的边界

- 资产和投资者画像属于长期金融记忆，仅在用户明确确认后写入。
- 行情、新闻、宏观数据、技术指标和 LLM 推断不得写进长期金融记忆。
- 用户提到“买了”“卖了”或偏好变化时，先确认是否更新；未确认不得调用写接口。
- 同一资产冲突时，以用户最后一次明确确认的内容为准。
- 分析前读取 `stocks/prompts/personal_advice_prompt.txt`，它是统一分析约束。
  该契约是决策导向的：输出必须围绕 `upcoming_events`（未来催化剂日历）组织
  情景预案，引用 `rotation`（板块轮动排名）提名机会，并以"触发条件 → 动作 →
  幅度"三元组给出调仓清单；禁止不带触发条件的"观察/等待"。
- 每次报告先复盘上期建议：`recent_advice` 里的 `trigger_review` 是系统按
  收盘价核对的触发事实（fired / not_fired / no_data），必须逐条回应。
- `action_signals` 是引擎给出的规则化方向性候选动作（附 reasons），
  是分析的初始底稿：每条方向性信号必须采纳或给理由推翻，不许无视；
  它不是指令，最终动作仍需结合组合结构与用户偏好落定。
- 默认使用 `AnalysisContext.raw_prompt_input` 做建议输入；本地 Agent 上下文会包含真实
  金额、逐持仓市值、盈亏、暴露集中度、可动用资金和数据边界。对外 HTTP 接口默认
  隐藏精确金额是另一条远程安全边界。
- 所有结论必须结合 `data_quality`；stale、降级、换算失败和单源风险不可省略。

## 2. 环境与入口

要求 Python 3.11+ 和 `uv`：

```bash
uv venv --python 3.11 .venv
uv pip install -r requirements.txt
```

统一 CLI：

```bash
uv run python -m stocks.adapters.cli --output json --no-news --no-quotes
uv run python -m stocks.adapters.cli --output json
```

定时扫描与 Agent handoff：

```bash
uv run python -m stocks.adapters.cli --scheduled-run-due
uv run python -m stocks.adapters.cli --scheduled-run-due --now "2026-07-06T14:35:00+08:00"
uv run python -m stocks.adapters.cli --scheduled-run-session cn_pre_close --force
uv run python -m stocks.adapters.cli --scheduled-run-session us_pre_open --force
uv run python -m stocks.adapters.cli --scheduled-run-latest cn_pre_close
```

`--scheduled-run-due` 适合由 launchd/cron 高频调用；系统只在命中 session 时生成
`.local/scheduled_runs/` 产物。同一市场日内同一 session 默认只跑一次，`--force` 仅用于补跑
或调试。外部 Agent 应读取 `--scheduled-run-latest SESSION` 返回的 JSON，严格按
`agent_task` 字段的全部指令执行——`agent_task` 是自包含的任务说明书，包含
must_answer(必答问题)、must_not_do(硬性约束,含飞书格式)、persona(分析师人格)、
adaptability(自适应输出)、data_reference(数据字段定位)和 output_structure(分节模板)。
Agent 不需要任何外部 prompt。

可选内部 LLM 报告：

```bash
uv run python -m stocks.adapters.cli --output text --llm-analysis
```

项目不存在 `--llm-enhancer`，也不存在旧的子命令式 CLI。

## 3. 金融记忆操作

读取资产与画像：

```bash
uv run python -m stocks.adapters.cli --assets-list
uv run python -m stocks.adapters.cli --profile-get
```

所有写操作都必须带 `--confirmed`：

```bash
uv run python -m stocks.adapters.cli \
  --asset-add '{"name":"现金","platform":"银行","amount":10000,"currency":"CNY"}' \
  --confirmed

uv run python -m stocks.adapters.cli \
  --asset-update '{"name":"现金","changes":{"amount":12000}}' \
  --confirmed

uv run python -m stocks.adapters.cli --asset-remove '现金' --confirmed

uv run python -m stocks.adapters.cli --asset-migrate-v2
uv run python -m stocks.adapters.cli --asset-migrate-v2 --confirmed

uv run python -m stocks.adapters.cli \
  --profile-update '{"risk_tolerance":"moderate","investment_horizon":"long_term"}' \
  --confirmed

uv run python -m stocks.adapters.cli \
  --advice-save '{"instruments":[{"market":"a","code":"000001","name":"平安银行"}],"direction":{"a:000001":"watch"},"rationale_summary":"现金占比较高，等待放量站回20日线。","based_on":["quotes","portfolio"],"boundary":[{"type":"fact","text":"现金占比较高"},{"type":"inference","text":"等待放量站回20日线"}],"triggers":[{"instrument":"a:000001","type":"price_above","level":12.5,"action":"收盘站上12.5则用现金层一成建仓","invalidation":"跌破11.8本条作废"},{"instrument":"a:588000","type":"pnl_pct_above","level":20,"action":"浮盈达到20%后减半卫星仓"}],"actions":[{"target":"权益","action":"increase","size_hint":"现金层一成","trigger":"收盘站上20日线","invalidation":"跌破11.8本条作废","horizon":"short"}]}' \
  --confirmed

uv run python -m stocks.adapters.cli --advice-list

uv run python -m stocks.adapters.cli \
  --execution-save '{"advice_id":"2026-07-03T04:40:29.112458+00:00","target":"a:588000","action":"increase","extent":"partial","note":"按触发条件执行一部分","executed_at":"2026-07-03T12:00:00+08:00"}' \
  --confirmed

uv run python -m stocks.adapters.cli --execution-list

uv run python -m stocks.adapters.cli \
  --forecast-save '{"statement":"科创50ETF 一周内收盘高于 1.0","target":"a:588000","metric":"close","comparator":"above","level":1.0,"deadline":"2026-07-10","confidence":"medium"}' \
  --confirmed

uv run python -m stocks.adapters.cli --forecast-list
```

资产保存在 `.local/financial_assets.json`，画像保存在
`.local/investor_profile.json`。没有本地资产文件时才读取
`stocks/data/financial_assets.json` 示例。画像结构示例见
`stocks/data/investor_profile.example.json`。确认保存的建议摘要位于 `.local/advice/`，
执行记录位于 `.local/executions/`，预测台账位于 `.local/forecasts/`。

MCP 对应工具：

- `assets_list`
- `asset_add`、`asset_update`、`asset_remove`，参数必须含
  `"confirmed": true`
- `asset_migrate_v2`，`confirmed=false` 只返回完整迁移预览和缺字段摘要；
  `confirmed=true` 写入 `.local/financial_assets.json`，源文件若已是 `.local`
  私有文件则备份为 `financial_assets.v1.bak.json`
- `profile_get`、`profile_update`，写操作同样必须确认
- `advice_list`、`advice_save`，保存建议必须确认；`advice.triggers` 可选，
  保存"触发条件 → 动作"三元组供下次运行程序化核对；触发器支持
  `price_*`、`pct_change_*` 和基于用户成本的 `pnl_pct_above/pnl_pct_below`；
  pnl 型触发器要求对应 v2 持仓有 `holding.cost_basis`。`advice.actions`
  用于保存结构化调仓动作，`size_hint` 只能写比例或自然语言，不写具体金额
- `execution_list`、`execution_save`，保存建议执行记录必须确认；执行对照只按
  `advice_id + target` 精确匹配，匹配不到显示 `unknown`
- `forecast_list`、`forecast_save`，保存预测记录必须确认；有 `target + level`
  的预测到期后按历史收盘价自动结算，缺 `target` 或 `level` 的记录保存为
  `manual`，不自动判断
- `get_analysis_context`、`get_quotes`、`get_news`、
  `get_portfolio_summary`

启动 stdio MCP：

```bash
uv run python -m stocks.adapters.mcp
```

## 4. 资产分类与产品类型路由

`.local/financial_assets.json` (schema v2) 中每个持仓都有 `classification` 字段，
包含三个维度：

- `asset_class`: 大类（equity / fixed_income / commodity / cash_equivalent / insurance）
- `product_type`: 产品形态（exchange_traded_fund / stock / qdii_fund / mixed_fund / fixed_income_plus_fund / bank_wealth_management / precious_metal_account / money_market_fund / cash / insurance_policy / short_treasury_etf）
- `exposure_tags`: 暴露标签（nasdaq100 / gold / tech / us_equity / a_share 等）

### 4.1 产品类型路由规则

`_build_action_cards()` 在生成持仓动作卡时，按 `product_type` 分流：

| 模式 | product_type | 行为 |
|---|---|---|
| **full** (全规则) | `exchange_traded_fund`, `stock`, `short_treasury_etf` | 完整止损/止盈/MA20趋势/左侧加仓，ratio 非零 |
| **config_only** (配置型) | `qdii_fund`, `feeder_fund`, `mixed_fund`, `fixed_income_plus_fund`, `precious_metal_account` | 引擎仍计算信号，但 ratio=0，信号降级为建议提醒，附资产特性上下文 |
| **info_only** (信息型) | `bank_wealth_management` | 不跑引擎，输出持有状态 + 产品说明 |
| **skip** (跳过) | `money_market_fund`, `cash`, `cash_equivalent`, `insurance_policy` | 不生成任何信号 |

**配置型资产的上下文示例**：
- qdii_fund → "场外 QDII，申赎 T+2，适合长期配置。无明确替代方向时不宜频繁止盈"
- mixed_fund → "主动管理混合基金，高浮盈后需关注基金经理风格漂移风险"
- fixed_income_plus_fund → "固收+产品，波动率低，MA20/RSI 技术信号不适用"
- precious_metal_account → "积存金，买卖有价差，短线操作成本高"

### 4.2 估值方法

每笔持仓有 `valuation_input.method`：

- `market_quote`：通过 watchlist 获取实时/近实时行情 → 适用于场内 ETF 和美股
- `manual_amount`：手工录入金额，非实时 → 适用于场外基金、银行理财、积存金
- `insurance_value`：保险现金价值 → 仅用于组合统计，不参与交易信号

Agent 读取 `action_cards[].facts` 时，带有"手工估值"标注的持仓不可按 ratio 执行，
需登录对应平台确认实时净值后再手动操作。

### 4.3 新增持仓规范

新增手工估值持仓时，必须填写 `classification.product_type` 和 `exposure_tags`，
否则系统回退到 `full` 模式（会对场外基金发出自动止损/止盈比例）。

```json
{
  "position_id": "alipay_example",
  "account_id": "alipay",
  "classification": {
    "asset_class": "equity",
    "product_type": "qdii_fund",
    "exposure_tags": ["qdii", "nasdaq100", "tech", "us_equity"]
  },
  "valuation_input": {
    "method": "manual_amount",
    "manual_amount": 100000,
    "as_of": "2026-07-11"
  },
  "liquidity": {
    "tradable": true,
    "rebalance_eligible": true,
    "tier": "t2_plus"
  }
}
```

exposure_tags 决定情报事件匹配和压力测试冲击系数。标签需具体（如 `nasdaq100`、`gold`），
避免使用过于宽泛的标签（如 `us_equity` 会导致与不相关的事件主题误匹配）。

## 5. 配置与本地数据

受版本控制的配置位于 `stocks/config/`：

- `engine.yaml`：Provider、缓存、日历、LLM 与日志开关
- `watchlist.json`：标的、市场、交易所和类别
- `portfolio_constraints.json`：资产桶目标与约束
- `news_sources.json`：RSS/Atom 新闻源
- `markets.json`：市场元数据
- `event_calendar.json`：官方已公布的未来事件日程（FOMC/CPI/非农等，
  日期用完的条目被窗口自动过滤；新一批官方日程公布后人工增补）
- `sector_scan.json`：候选池扫描（50 标的，带 pool 分层:
  broad/sector/defensive/rates/ai_chain），只参与历史回填、轮动排名与
  动作信号，不请求实时行情
- `scheduled_sessions.json`：A 股与美股定时扫描 session、时区、重复运行窗口、
  免打扰和默认推送策略

配置优先级为环境变量 > YAML > 代码默认值。嵌套环境变量使用双下划线，例如：

```bash
STOCKS_FETCHER__MAX_RETRIES=3
```

本地路径：

- `.local/history/`：行情历史缓存
- `.local/event_cache/`：Finnhub 财报日历 12 小时缓存
- `.local/snapshots/`：最多 30 份最小上下文快照
- `.local/scheduled_runs/`：定时扫描 JSON/Markdown 产物与 latest 指针
- `data/cache/`：非隐私运行缓存，例如汇率
- `.secret/`：API key、HTTP token；禁止提交

Finnhub 行情/财报日历读取 `FINNHUB_API_KEY`（或
`.secret/finnhub-key.md`）。SEC EDGAR 要求请求 UA 带可联系邮箱，启用公告源时设置：

```bash
export SEC_USER_AGENT="stocks-claw/1.0 you@example.com"
```

缺少该变量不会伪装成功；`data_quality.news.errors` 会逐标的报告配置缺失。

## 5. HTTP 边界

HTTP 适配器默认只监听回环地址，并默认隐藏精确金额：

```bash
uv run python -m stocks.adapters.http --host 127.0.0.1 --port 8687
curl http://127.0.0.1:8687/api/v1/health
```

非回环监听必须显式加 `--allow-remote`，并提供
`.secret/http-token`；请求使用 `Authorization: Bearer <token>`。当前实现没有限速和
CORS，不应直接暴露公网。

## 6. 飞书格式约束

所有推送到飞书的输出必须仅使用以下格式:
- `**加粗**` 代替标题层级
- `` `行内代码` `` 代替代码块
- `- 列表`
- `[链接](url)`
- 空行分隔段落

**禁止**(会导致整条消息降级为纯文本):`#` 标题、`|` 表格、` ``` ` 代码块、`>` 引用、
`---` 分隔线、`- [ ]` 任务列表、HTML 标签。

此约束已编码在 `agent_task.must_not_do` 和 `output_structure.format_rules` 中。

## 7. 每次修改的验收

```bash
uv run ruff check .
uv run python -m pytest -q
uv run python -m compileall -q stocks tests
uv run python -m stocks.adapters.cli --output json --no-news --no-quotes
```

不得提交 `.local/`、`.secret/`、缓存、快照、虚拟环境或用户资产。新增 Markdown
必须遵守 `PLAN.md` 的文档冻结规则。
