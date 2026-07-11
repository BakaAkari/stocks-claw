# stocks-claw 当前架构

本文只描述仓库当前实现，不包含未来设计。数据契约细节见
`stocks/DATA_MODEL.md`，操作规则见 `AGENT_GUIDE.md`。

## 1. 系统边界

`stocks-claw` 是 Agent 的个人金融上下文工具，不是自动交易系统。

- Engine 负责读取确认过的金融记忆、获取市场数据、计算轻量脚手架、记录质量与溯源，
  最终构建 `AnalysisContext v12`。
- Engine 也能按配置生成 `ScheduledAnalysisRun v1` 文件产物，供外部 Agent 定时读取。
- 外部 Agent 读取上下文或定时运行产物并完成最终判断。
- 可选 `LLMAnalysis` 能生成兼容报告，但默认关闭，不改变主边界。
- 系统没有券商连接和下单能力。

```text
CLI / stdio MCP / local HTTP
              |
              v
          StocksEngine
              |
  +-----------+------------+----------------+
  |                        |                |
financial memory       market inputs    local history
assets/profile         quotes/news      snapshots/cache
  |                        |                |
  +-----------> ContextBuilder <------------+
                  |
                  v
          AnalysisContext v12
                  |
                  v
       ScheduledAnalysisRun v1
                  |
                  v
             external Agent
```

## 2. 代码分层

### adapters

`stocks/adapters/` 只负责协议转换：

- `cli.py`：JSON/text 输出、资产/画像 CRUD、可选内部 LLM 报告、
  定时运行产物生成与读取。
- `mcp.py`：轻量 JSON-RPC 风格 stdio 工具，包括上下文、行情、新闻、组合和金融记忆。
- `http.py`：标准库 `http.server` JSON API；默认回环监听。

Adapter 不实现组合算法或 Provider 逻辑。

### engine

`StocksEngine` 是门面和编排入口：

1. 加载 `engine.yaml`、资产、画像、约束和 watchlist。
2. 注册启用的 Provider。
3. 创建 fetcher、历史缓存、新闻聚合器、宏观 Provider、脚手架与持久化组件。
4. 构建上下文或暴露读取/写入方法。

主要组件：

- `config_loader.py`：默认值、YAML 与 `STOCKS_*` 环境变量合并。
- `fetchers.py`：行情 Provider 选择、重试、降级和 `DegradationRecord`。
- `context_builder.py`：组装 context、质量信息、事件和 prompt 输入。
- `scaffolds.py`：组合分桶、偏离检查、跨资产市场状态。
- `history_cache.py` / `history_provider.py`：历史 K 线预热、按交易日去重与缓存。
- `indicators.py`：基于历史序列计算技术指标和年化波动率。
- `news_sources.py` / `market_events.py`：新闻聚合、去重和规则事件提取。
- `event_calendar.py`：未来催化剂日历（官方已公布日程静态配置 + Finnhub
  财报日历），产出 `upcoming_events` 与其质量节点。
- `rotation.py`：板块轮动脚手架，基于历史收盘计算 watchlist + 扫描池的
  5/20 日相对强弱排名。
- `action_signals.py`：规则化方向性候选动作（附 reasons 指标事实，
  2026-07-02 用户裁决启用，约束见 PLAN §4）。
- `macro_data.py`：FRED、Yahoo Finance 与 static_config 逐字段合并的宏观数据组合。
- `exchange_rate.py`：外币估值与显式换算质量。
- `persistence.py`：滚动最小上下文快照与确认建议摘要。
- `advice_review.py`：建议表现回看与触发器核对（按收盘价，只列事实）。
- `scheduled_analysis.py`：A 股/美股定时 session 日历、运行产物存储、
  产品类型路由(`_PRODUCT_TYPE_RULES` 四档分流:full/config_only/info_only/skip)、
  组合资金分配(`_build_capital_allocation` 约束检测+冲突标注+优先级排序)、
  自包含 agent_task v5 指令集(persona/adaptability/data_reference/飞书格式/情报要点)和通知建议策略。
- `llm_analysis.py`：默认关闭的兼容报告模块。

### domain

`stocks/domain/models.py` 定义不可变 dataclass：

- `Instrument`、`Quote`、`NewsItem`、`MarketEvent`、`UpcomingEvent`
- `FinancialAsset`、`Account`、`Position` 及 v1/v2 资产兼容映射
- `PortfolioMapping`、`MarketState`、`DriftCheck`
- `AdviceRecord`（含可选 `triggers` / `actions`）、`ExecutionRecord`、`ForecastRecord`
- `DecisionEnvelope`
- `AnalysisContext`

这些对象是 Engine、Adapter 和测试之间的接口契约。

### providers

`stocks/providers/` 中的当前 Provider：

- 腾讯、东方财富：A 股行情，互为降级来源。
- Finnhub：美股行情（主源）；认证、限流、超时、网络和数据错误使用不同异常类型。
- Polygon.io：美股行情（备用源）；使用 `/v2/aggs/ticker/{symbol}/prev`，免费档约 5 次/分钟。
- 天天基金：公募基金净值（FundNavProvider）；使用 JSONP 接口获取 T-1 确认净值，支持 5 只场外公募基金自动估值。
- Binance：加密货币实时备用源与历史日 K 主源。
- 历史 K 线：A 股为东方财富→腾讯，美股为 Nasdaq→Yahoo，crypto 为
  Binance→Yahoo；每次回填保留逐源降级记录。
- RSS：读取 RSS 2.0 或 Atom 新闻。
- SEC EDGAR / 巨潮：仅查询 watchlist 的一手公告；Google News 模板按
  watchlist 动态生成 holding-scope RSS。

Provider Registry 按市场查找可用实现。美股当前主源 Finnhub + 备用 Polygon；双源均失败时质量信息标记
`single_source_failed`，并在历史存在时返回 `stale: true` 的最近收盘价。

## 3. build_context 数据流

`StocksEngine.build_context()` 的当前顺序：

1. 读取本地资产、约束、画像、watchlist 和 `sector_scan.json` 扫描池。
2. 将资产原币种金额转换为运行时 `amount_cny`；原始金额与币种不被改写。
3. 在需要时预热历史行情缓存（watchlist + 扫描池）。
4. 从 `news_sources.json` 中启用的 RSS/Atom 源聚合新闻。
5. 先加载近期最小快照。
6. `ContextBuilder` 获取行情并执行降级；美股可回填 stale 历史收盘价。
   扫描池不请求实时行情，只经历史缓存参与轮动。
7. 记录行情、计算技术指标与宏观快照。
8. 提取市场事件、拉取未来催化剂日历、计算轮动排名与动作信号、
   构建组合映射、偏离检查和市场状态；对最近建议做表现回看与触发器核对。
9. 生成 `raw_prompt_input` 与 `data_quality`。
10. 返回 `AnalysisContext v12`，随后保存本次最小快照。

由于“先读后写”，同一次运行不会把自身当作历史；第二次运行可以引用第一次快照。

## 4. 定时运行产物

`stocks/config/scheduled_sessions.json` 定义 A 股与美股 session。A 股使用
`Asia/Shanghai`，美股使用 `America/New_York`，由 `zoneinfo` 处理夏令时换算。
第一版跳过周末，并支持静态 holiday 列表；不会连接券商日历服务。

CLI 入口：

```bash
uv run python -m stocks.adapters.cli --scheduled-run-due
uv run python -m stocks.adapters.cli --scheduled-run-session cn_pre_close --force
uv run python -m stocks.adapters.cli --scheduled-run-latest cn_pre_close
```

`--scheduled-run-due` 适合由 launchd/cron 高频调用，系统只在命中 session
窗口时生成产物。同一 session 同一市场日默认只跑一次；`--force` 用于补跑和调试。

产物写入：

```text
.local/scheduled_runs/YYYY-MM-DD/{market}/{session}/{run_id}.json
.local/scheduled_runs/YYYY-MM-DD/{market}/{session}/{run_id}.md
.local/scheduled_runs/latest/{session}.json
```

`ScheduledAnalysisRun v1` 包含 session 元数据、持仓估值/PnL、触发器核对、
action_signals(含 rank/score)、portfolio_risk(含多因子情景)、capital_allocation(约束+冲突+资金分配)、data_quality、
agent_task v5(自包含指令集:persona/adaptability/data_reference/output_structure/
飞书格式约束/情报要点)、写入策略和通知建议。定时运行只写
`.local/scheduled_runs/`，不会自动保存 advice/execution/forecast，也不会修改资产或画像。

## 5. 金融记忆

### 资产

读取优先级：

1. `.local/financial_assets.json`
2. `stocks/data/financial_assets.json` 示例

资产文件兼容 v1 `FinancialAsset` 列表与 v2 `{schema_version, accounts, positions}`
格式。v1 只在内存中确定性映射到 `Account` / `Position`，不自动写回；迁移必须走
用户确认的 `asset_migrate_v2` / `--asset-migrate-v2 --confirmed`。持久化保留用户输入的
原始金额、币种、账户和持仓事实；人民币估值、逐持仓市值、浮动盈亏、暴露和流动性摘要均为
运行时派生字段，不写回资产文件。无法换算的资产保留事实，但不静默计入人民币组合总值。

### 投资者画像

`.local/investor_profile.json` 保存长期风险偏好、投资期限、偏好与约束。示例结构位于
`stocks/data/investor_profile.example.json`。

CLI 写操作要求 `--confirmed`；MCP 写操作要求 `confirmed: true`。确认缺失时 Adapter
返回失败，Engine 文件不会改变。

## 6. 数据质量与失败语义

`stocks/errors.py` 把 Provider 错误分为：

- 可重试：超时、网络、限流、数据格式错误。
- 不可重试：认证、配置、Provider 不存在。

`DataFetcher` 将失败、备用 Provider 与结果记录到 `DegradationRecord`。Context 将
以下信息统一暴露到 `data_quality`：

- 外币换算失败或硬编码/过期汇率降级
- 行情请求、返回、Provider、fallback 与 stale 状态
- 新闻来源、数量与时效
- 宏观字段缺失和异常
- 技术指标覆盖缺口
- 事件提取覆盖
- 资产文件格式、v2 持仓字段完备性、逐持仓估值降级、运行时自动纳入行情宇宙的持仓
- 暴露聚合、流动性分层与建议粒度推导质量

缺数据必须表示为 `missing`、`not_requested`、`not_configured` 或 `no_data`，不能
用空值伪装正常状态。

## 7. Prompt 与金额边界

`stocks/prompts/personal_advice_prompt.txt` 是内置 LLM 与外部 Agent 共用的分析约束。
`raw_prompt_input` 是本地 Agent 证据包，当前包含真实资产金额、逐持仓市值、盈亏、
暴露集中度、可动用资金和数据边界；仓位动作仍要求用比例、区间或自然语言表达，
不得保存具体下单金额。

HTTP 是远程接口边界，默认仍递归移除 `amount`、`amount_cny` 和 `total_value`；
调用方只有显式使用 `?include_amounts=true` 才能请求精确金额。

## 8. 本地持久化

- `.local/history/`：按标的 JSON 历史缓存，默认保留 90 天；按市场交易日去重。
- `.local/snapshots/`：最多 30 份最小快照。
- `.local/scheduled_runs/`：定时扫描 JSON 产物与 latest 入口。
- `.local/news_intelligence/`：每小时情报采集快照、事件聚类和信号(7天在线/30天归档)。
- `data/cache/`：非隐私缓存，例如汇率；不与密钥目录混用。
- `.secret/`：API key 与 HTTP token。

`.local/`、`.secret/`、`data/cache/` 和历史遗留的
`stocks/data/history/` 均不得提交。

## 9. HTTP 安全边界

HTTP 默认监听 `127.0.0.1`。非回环地址必须同时满足：

- 命令行显式传入 `--allow-remote`
- `.secret/http-token` 存在且非空
- 客户端发送匹配的 Bearer token

异常响应不回传内部堆栈。当前没有速率限制和 CORS 策略，因此不能视为公网 API。

## 10. 配置

主要配置：

- `stocks/config/engine.yaml`（含 `fallback.us: [polygon]` 降级链）
- `stocks/config/watchlist.json`
- `stocks/config/portfolio_constraints.json`
- `stocks/config/news_sources.json`
- `stocks/config/markets.json`
- `stocks/config/event_calendar.json`：官方已公布的未来事件日程
- `stocks/config/sector_scan.json`：候选池扫描，带 pool 分层（不进入 watchlist）
- `stocks/config/scheduled_sessions.json`：A 股/美股/情报定时运行 session、时区与推送策略
- `scripts/`：定时任务脚本(intelligence_report.py 直接格式化+受控LLM总结、cron 触发脚本)

Engine 配置优先级：环境变量 > YAML > 代码默认值。嵌套键使用双下划线，例如
`STOCKS_FETCHER__MAX_RETRIES=3`。

## 11. 验证基线

```bash
uv run ruff check .
uv run python -m pytest -q
uv run python -m compileall -q stocks tests
uv run python -m stocks.adapters.cli --output json --no-news --no-quotes
```

测试覆盖 Engine、Provider、脚手架、持久化、CLI/MCP/HTTP、安全确认、降级状态和 schema。
