# stocks-claw

服务单一用户的个人投资分析师工作台。系统把用户已确认的账户、持仓、投资偏好，与行情、
新闻、宏观数据、技术指标、组合映射、持仓估值/PnL 脚手架及数据质量信息组装成
`AnalysisContext` 证据包，交给外部 Agent 完成最终分析。

系统不下单。Engine 负责事实、触发核对、降级处理、确定性 Action Card 和组合上下文，
最终判断归 Agent，唯一决策人仍是用户。当前验收结论:风险监控和研究辅助可用,自动动作、
组合资金部署与可直接照单执行的报告尚未通过真实交易者验收。

[English](README.md) · [Agent 指南](AGENT_GUIDE.md) ·
[架构](ARCHITECTURE.md) · [计划](PLAN.md) ·
[交易系统对抗性审查](docs/TRADING_SYSTEM_ADVERSARIAL_REVIEW_20260715.md)

## 文档地图

- `PLAN.md`:方向、裁决和当前状态
- `EXECUTION_PLAN.md`:唯一现行任务与验收清单
- `ARCHITECTURE.md`:当前实现架构
- `stocks/DATA_MODEL.md`:当前 schema 与字段语义
- `AGENT_GUIDE.md`:Agent 与开发者操作规则
- `stocks/VISION.md`:产品北极星
- `docs/TRADING_SYSTEM_ADVERSARIAL_REVIEW_20260715.md`:现行真实交易质量边界与整改优先级
- `docs/INTELLIGENCE_FULL_REDESIGN.md`:部分实现的情报设计
- `docs/NEWS_MODULE_REDESIGN.md`:尚未完整实施的统一推送设计
- `docs/archive/`:仅保留历史证据,无现行效力

## 环境要求

- Python 3.11+
- `uv`
- `requirements.txt` 中的依赖

```bash
uv venv --python 3.11 .venv
uv pip install -r requirements.txt
```

## 快速开始

构建不访问行情和新闻的本地上下文：

```bash
uv run python -m stocks.adapters.cli --output json --no-news --no-quotes
```

获取配置中的行情与新闻：

```bash
uv run python -m stocks.adapters.cli --output json
```

可选内部 LLM 报告：

```bash
uv run python -m stocks.adapters.cli --output text --llm-analysis
```

生成或读取给外部 Agent 的跨市场定时运行产物：

```bash
uv run python -m stocks.adapters.cli --scheduled-run-due
uv run python -m stocks.adapters.cli --scheduled-run-session cn_pre_close --force
uv run python -m stocks.adapters.cli --scheduled-run-latest cn_pre_close
```

已删除的 `--llm-enhancer` 参数不再支持。

## 需确认的金融记忆写入

读取不需要确认：

```bash
uv run python -m stocks.adapters.cli --assets-list
uv run python -m stocks.adapters.cli --profile-get
```

任何持仓或画像写入都必须带 `--confirmed`：

```bash
uv run python -m stocks.adapters.cli \
  --asset-add '{"name":"现金","platform":"银行","amount":10000,"currency":"CNY"}' \
  --confirmed

uv run python -m stocks.adapters.cli \
  --profile-update '{"risk_tolerance":"moderate"}' \
  --confirmed
```

更新和删除分别使用 `--asset-update`、`--asset-remove`。对应 MCP 写工具必须传
`"confirmed": true`。

私有持仓当前支持 v2 `Account` / `Position` 文件。旧 v1 资产列表迁移前应先预览：

```bash
uv run python -m stocks.adapters.cli --asset-migrate-v2
uv run python -m stocks.adapters.cli --asset-migrate-v2 --confirmed
```

## 数据与配置

```text
.local/financial_assets.json          私有持仓
.local/investor_profile.json          私有投资偏好
.local/history/                       行情历史缓存
.local/event_cache/                   Finnhub 财报日历缓存
.local/snapshots/                     滚动最小快照
.local/advice/                        已确认建议摘要
.local/executions/                    已确认执行记录
.local/forecasts/                     已确认预测台账
.local/scheduled_runs/                定时扫描给 Agent 的 JSON/Markdown 产物
.secret/                              本地 API key 与 HTTP token
stocks/config/engine.yaml             运行配置
stocks/config/watchlist.json          关注标的
stocks/config/news_sources.json       RSS/Atom 新闻源
stocks/config/portfolio_constraints.json
stocks/config/event_calendar.json     静态官方事件日历
stocks/config/sector_scan.json        轮动/信号扫描池
stocks/config/scheduled_sessions.json A 股/美股定时 session 配置
stocks/config/exposure_proxy.json     暴露标签代理映射
```

没有私有持仓文件时，系统使用 `stocks/data/financial_assets.json` 作为示例输入。画像
示例位于 `stocks/data/investor_profile.example.json`。

嵌套环境变量使用双下划线：

```bash
STOCKS_FETCHER__MAX_RETRIES=3
```

Finnhub 使用 `FINNHUB_API_KEY`。可选 OpenAI-compatible 报告使用
`OPENAI_API_KEY` 和 `OPENAI_BASE_URL`。
SEC EDGAR 公告请求必须配置带联系邮箱的 UA，例如
`SEC_USER_AGENT="stocks-claw/1.0 you@example.com"`；缺失时会在
`data_quality.news.errors` 中显式报告。

## 接口

- CLI：`python -m stocks.adapters.cli`
- stdio MCP：`python -m stocks.adapters.mcp`
- 本地 HTTP：`python -m stocks.adapters.http --host 127.0.0.1 --port 8687`

HTTP 非回环监听必须同时提供 `--allow-remote` 和 `.secret/http-token`。当前 HTTP
适配器没有限速与 CORS 策略，不应作为公网服务。

## 验证

```bash
uv run ruff check .
uv run python -m pytest -q
uv run python -m compileall -q stocks tests
uv run python -m stocks.adapters.cli --output json --no-news --no-quotes
```

现行 schema 见 `stocks/DATA_MODEL.md`,交易质量边界见
`docs/TRADING_SYSTEM_ADVERSARIAL_REVIEW_20260715.md`。当前审查基线:ruff 通过,
pytest 536 passed / 2 failed(情报测试 fixture 固定日期超出 6 小时窗口)。本项目只用于分析、学习与参考,
不构成确定性投资建议。
