# stocks-claw 当前架构

本文只描述仓库当前实现，不包含未来设计。数据契约细节见
`stocks/DATA_MODEL.md`，操作规则见 `AGENT_GUIDE.md`。

## 1. 系统边界

`stocks-claw` 是 Agent 的个人金融上下文工具，不是自动交易系统。

- Engine 负责读取确认过的金融记忆、获取市场数据、计算轻量脚手架、记录质量与溯源，
  最终构建 `AnalysisContext v12`。
- Engine 也能按配置生成 `ScheduledAnalysisRun v1` 文件产物，供外部 Agent 定时读取。
- `ProfileInterpreter` 将用户自然语言偏好翻译为量化引擎参数(`computed_profile.json`),
  每次 session 自动合并进 `QuantActionEngine`。
- `QuantActionEngine.review_position()` 生成纯技术面 `QuantReview`;`factor_rules.collect_votes()` /
  `adjudicate()` 叠加约束、市场状态、事件、情报和数据新鲜度;`finalize_decision()` 输出
  `FinalDecision` 及 `drivers/dissent/confidence`。
- 外部 Agent 读取上下文或定时运行产物并完成最终判断。当前 Agent 报告不是自动执行单;
  交易有效性边界见 `docs/TRADING_SYSTEM_ADVERSARIAL_REVIEW_20260715.md`。
- 定时产物 `mandatory_blocks` 含四块确定事实:`risk_boundary`(风险等级)、
  `constraint_alerts`(大类约束偏离)、`capital_facts`(资金状况:约束/净可动用/冲突/回收/轮动参考)、
  `shadow_account`(建议信号分布)。`capital_facts` 只陈述事实不做解释,资金部署建议由 LLM 基于 persona 生成。
- `LLMAnalysis`（已废弃，默认关闭） 能生成兼容报告，但默认关闭，不改变主边界。
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
- `quant_action.py`：纯技术面 review、产品类型路由、确定性最终决策、驱动向量与置信度。
- `factor_rules.py`：约束、市场状态、事件、情报冲突和新鲜度因子投票及裁决。
- `intelligence_harvester.py` / `intelligence_analyzer.py` / `news_intelligence_store.py`：
  每小时多源情报采集、LLM 语义分析、规则降级、信号追踪与文件存储。
- `scheduled_analysis.py`：A 股/美股/情报 session 日历、运行产物存储、Action Card、
  Portfolio Risk、Capital Allocation、agent_task v4 和通知建议策略。
- `shadow_account.py` / `hypothesis_tracker.py` / `signal_tracker.py`：建议快照、研究论点和情报信号效果跟踪。
- `outlook_evidence.py`：构建 outlook 生成的白名单证据包，过滤过时/无来源的情报事件，计算置信度上限（cap）。
- `outlook_synthesizer.py`：基于 OpenAI 兼容端点的结构化 outlook 合成器，带 evidence hash 缓存、温度错误重试、围栏式 JSON 过滤，任何失败时退化到 sanitized-unavailable。
- `outlook_validation.py`：对 LLM 返回的 outlook 做字段、方面、证券来源、提示注入、内部 token 和数字授权校验；失败时退化到筛选后的 unavailable 状态。
- `outlook_delta.py`：计算主窗口 outlook 之间的语义差异，供观察窗口使用。
- `presentation.py`：将 outlook 和 outlook_delta 展开为用户可读的确定性文本，同时过滤掉任何内部 token。
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
action_cards(含 routing/drivers/dissent/confidence)、action_signals(含 rank/score)、
portfolio_risk、capital_allocation、risk_assessment、mandatory_blocks、data_quality、
agent_task v4(自包含指令集:persona/adaptability/data_reference/output_structure/飞书格式约束/情报要点)、
写入策略和通知建议。定时运行只写
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

`stocks/prompts/personal_advice_prompt.txt` 是（已废弃的自由文本路径保留的）分析约束。
`raw_prompt_input` 是本地 Agent 证据包，当前包含真实资产金额、逐持仓市值、盈亏、
暴露集中度、可动用资金和数据边界；仓位动作仍要求用比例、区间或自然语言表达，
不得保存具体下单金额。

HTTP 是远程接口边界，默认仍递归移除 `amount`、`amount_cny` 和 `total_value`；
调用方只有显式使用 `?include_amounts=true` 才能请求精确金额。

## 8. 本地持久化

- `.local/history/`：按标的 JSON 历史缓存，默认保留 90 天；按市场交易日去重。
- `.local/snapshots/`：最多 30 份最小快照。
- `.local/scheduled_runs/`：定时扫描 JSON/Markdown 产物与 latest 入口。
- `.local/news_intelligence/`：每小时情报采集快照、事件聚类和信号(7天在线/30天归档)。
- `.local/advice_snapshots/`：Shadow Account 建议快照。
- `.local/hypotheses/`：研究论点与 run 关联索引。
- `.local/signal_tracker/`：情报方向信号及后续价格结算。
- `.local/outlook_cache/`：结构化 `outlook` 的 LLM 生成缓存，24 小时 TTL，按 evidence hash 去重；不得提交。
- `.local/outlook_delta_state.json`：跨窗口 outlook 差异状态，用于观察窗口只报告变化。
- `.local/forecasts/`：用户确认保存的预测台账，系统按历史收盘价自动结算。
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

## 10. 中期 outlook 与展望合成

系统为 A 股/美股主窗口（`cn_pre_open`、`cn_after_close`、`us_pre_open`、`us_after_close`）
生成结构化展望 `structured_outlook`：

1. `outlook_evidence.py` 从 `AnalysisContext` 中提取白名单证据：前 5 持仓/冲突持仓、资产类别和 sector 快照、技术/轮动信号、情报事件、宏观、即将发生事件、风险和数据边界。
2. `OutlookSynthesizer` 调用 OpenAI 兼容端点，将证据包转换为约束后的 JSON outlook：near_term / medium_term、sector_views、asset_views、scenarios、source_refs、confidence。
3. `outlook_validation.py` 对 outlook 做多层校验：必填字段、方向词表、不含交易指令/内部 token/概率字段/数字授权、source_refs 必须来自证据包中的可验证新闻、instrument/symbol 必须在证据包的授权列表中。
4. 校验失败时，`sanitize_unavailable_outlook()` 代替生成一个筛选后的 unavailable 状态，不中断 session。
5. 观察窗口（`cn_open_watch`、`cn_pre_close`、`us_open_watch`、`us_pre_close`）
   不生成独立 outlook，而是计算与上一个主窗口 outlook 的语义差异，并展示为 `outlook_delta`。

`outlook` 和 `outlook_delta` 在渲入 `assistant_brief` 之前，经 `presentation.py` 的
`project_outlook_for_display()` / `project_outlook_delta_for_display()` 进一步白名单过滤，
确保用户可见正文不泄露内部 token、position_id 或 decision_id。

## 11. 推送边界与信任谷仓

定时产物并不直接发送到飞书。推送层由 Hermes cron/no-agent 脚本执行：

- `scripts/cron/stocks-claw-push-*.sh` 以 `--session` 调用 `scripts/run_push_report.py`。
- `run_push_report.py` 调用 `build_push_payload()` 构建用户可见报告，并调用
  `validate_payload_text()` 做崩溃闭锁（fail-closed）。
- 支持的 session 只包含 A 股/美股交易窗口：`cn_pre_open`、`cn_open_watch`、`cn_pre_close`、
  `cn_after_close`、`us_pre_open`、`us_open_watch`、`us_pre_close`、`us_after_close`。
  `global_intelligence_watch` 等情报产物不走这个推送门。
- 推送脚本会检查 artifact 新鲜度（默认 45 分钟窗口）；新鲜度不足时以 `INVALID` 退出，不发送。
- 推送时间应晚于 `scheduled_sessions.json` 中对应 session 的生成时间，
  以避免 `artifact age` 为负数（如 `cn_pre_open` 9:00 生成，推送 cron 9:05）。
- 推送调试可以使用 `--now` 将推送时间与 artifact 时间对齐，但不应用于生产环境。



## 12. 配置

主要配置：

- `stocks/config/engine.yaml`（含 `fallback.us: [polygon]` 降级链）
- `stocks/config/watchlist.json`
- `stocks/config/portfolio_constraints.json`
- `stocks/config/news_sources.json`
- `stocks/config/markets.json`
- `stocks/config/event_calendar.json`：官方已公布的未来事件日程
- `stocks/config/sector_scan.json`：候选池扫描，带 pool 分层（不进入 watchlist）
- `stocks/config/scheduled_sessions.json`：A 股/美股/情报定时运行 session、时区与推送策略
- `scripts/`：定时任务脚本(`intelligence_brief.py`、情报/到期结算/事件检查 shell 入口)

Engine 配置优先级：环境变量 > YAML > 代码默认值。嵌套键使用双下划线，例如
`STOCKS_FETCHER__MAX_RETRIES=3`。

## 13. 当前已知架构限制

- 全局 quote freshness 当前会传给全部持仓,跨市场 session 可能相互污染动作比例。
- `capital_allocation` 将 T1/T2 持仓计入 deployable,不能直接解释为今日现金。
- **P1 已知缺陷**:`_build_capital_allocation()` 对低于 800 元的加仓会原地修改 Action Card,存在顺序依赖和原始信号丢失风险。
- 情报 Driver 与 IntelConflictRule 使用不同匹配路径,可能出现结构化冲突漏报。
- 产品路由在 `quant_action.py` 与 `scheduled_analysis.py` 存在重复映射。
- Watch Window 仍以完整快照为主,尚未实现 Delta/SILENT 闭环。
- **outlook 合成与推送**：`outlook_validation.py` 对数字和 sector 名称的白名单较为严格，
  导致合法的 ETF 基金代码（如 `a:159110`）和合法的行业/风格标签（如 `防御/航空`）
  被报 `unauthorized`，尚需宽化。此问题影响 `us_pre_open` 等已生成报告的推送。
- 推送 cron 时间必须与 `scheduled_sessions.json` 的生成时间保持正向容差；
  原配置中 `cn_pre_open-push` 8:55 运行、报告 9:00 才生成，导致 `artifact age -4.3 minutes` 失败。
  当前 45 分钟窗口来自 `build_push_payload.py`。原 30 分钟在产物提前生成（如 9:35 生成 10:00 的
  `cn_open_watch`）、推送 10:05 时就被突破，因此扩大到 45 分钟并将推送 cron 调到 scheduled + 5–10 分钟。
- 详情与整改优先级见 `docs/TRADING_SYSTEM_ADVERSARIAL_REVIEW_20260715.md`。

## 14. 验证基线

当前实测基线（2026-07-21）：

- `ruff check .`：通过
- `pytest -q`：1106 passed / 2 failed
- 失败集合：`tests/providers/test_filings.py` 中 SEC EDGAR 公告测试
- 失败原因：测试对网络异常的 assertion 与当前 provider 的异常处理路径不一致；不影响生产推送逻辑
- 未解决问题：outlook 对数字和 sector 的误报（见 §11）

当前不建议将自动生成的交易动作作为直接执行单；系统定位为风险监控与研究工作台，详情见
`docs/TRADING_SYSTEM_ADVERSARIAL_REVIEW_20260715.md`。

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q stocks tests
.venv/bin/python -m stocks.adapters.cli --output json --no-news --no-quotes
```

测试覆盖 Engine、Provider、脚手架、持久化、CLI/MCP/HTTP、安全确认、降级状态、schema 与 outlook 合成/校验/推送边界。
