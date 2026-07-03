# Agent 使用指南

本文是 Agent 操作 stocks-claw 的现行规则。系统负责金融记忆、市场数据和
AnalysisContext；最终分析由调用它的 Agent 完成。系统不下单，也不把任何输出包装成
确定收益承诺。

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
- 默认使用 `AnalysisContext.raw_prompt_input` 做建议输入；其中只有金额区间，不含逐笔
  精确金额。结构化 `assets` 仍含精确值，只在确有必要时使用。
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

uv run python -m stocks.adapters.cli \
  --profile-update '{"risk_tolerance":"moderate","investment_horizon":"long_term"}' \
  --confirmed

uv run python -m stocks.adapters.cli \
  --advice-save '{"instruments":[{"market":"a","code":"000001","name":"平安银行"}],"direction":{"a:000001":"watch"},"rationale_summary":"现金占比较高，等待放量站回20日线。","based_on":["quotes","portfolio"],"boundary":[{"type":"fact","text":"现金占比较高"},{"type":"inference","text":"等待放量站回20日线"}],"triggers":[{"instrument":"a:000001","type":"price_above","level":12.5,"action":"收盘站上12.5则用现金层一成建仓","invalidation":"跌破11.8本条作废"}]}' \
  --confirmed

uv run python -m stocks.adapters.cli --advice-list
```

资产保存在 `.local/financial_assets.json`，画像保存在
`.local/investor_profile.json`。没有本地资产文件时才读取
`stocks/data/financial_assets.json` 示例。画像结构示例见
`stocks/data/investor_profile.example.json`。确认保存的建议摘要位于 `.local/advice/`。

MCP 对应工具：

- `assets_list`
- `asset_add`、`asset_update`、`asset_remove`，参数必须含
  `"confirmed": true`
- `profile_get`、`profile_update`，写操作同样必须确认
- `advice_list`、`advice_save`，保存建议必须确认；`advice.triggers` 可选，
  保存"触发条件 → 动作"三元组供下次运行程序化核对
- `get_analysis_context`、`get_quotes`、`get_news`、
  `get_portfolio_summary`

启动 stdio MCP：

```bash
uv run python -m stocks.adapters.mcp
```

## 4. 配置与本地数据

受版本控制的配置位于 `stocks/config/`：

- `engine.yaml`：Provider、缓存、日历、LLM 与日志开关
- `watchlist.json`：标的、市场、交易所和类别
- `portfolio_constraints.json`：资产桶目标与约束
- `news_sources.json`：RSS/Atom 新闻源
- `markets.json`：市场元数据
- `event_calendar.json`：官方已公布的未来事件日程（FOMC/CPI/非农等，
  日期用完的条目被窗口自动过滤；新一批官方日程公布后人工增补）
- `sector_scan.json`：候选池扫描（26 标的，带 pool 分层:
  broad/sector/defensive/rates/ai_chain），只参与历史回填、轮动排名与
  动作信号，不请求实时行情

配置优先级为环境变量 > YAML > 代码默认值。嵌套环境变量使用双下划线，例如：

```bash
STOCKS_FETCHER__MAX_RETRIES=3
```

本地路径：

- `.local/history/`：行情历史缓存
- `.local/event_cache/`：Finnhub 财报日历 12 小时缓存
- `.local/snapshots/`：最多 30 份最小上下文快照
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

## 6. 每次修改的验收

```bash
uv run ruff check .
uv run python -m pytest -q
uv run python -m compileall -q stocks tests
uv run python -m stocks.adapters.cli --output json --no-news --no-quotes
```

不得提交 `.local/`、`.secret/`、缓存、快照、虚拟环境或用户资产。新增 Markdown
必须遵守 `PLAN.md` 的文档冻结规则。
