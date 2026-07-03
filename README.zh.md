# stocks-claw

面向 Agent 的个人金融上下文工具包。系统把用户已确认的持仓和投资偏好，与行情、新闻、
宏观数据、技术指标、组合映射及数据质量信息组装成 `AnalysisContext`，交给外部 Agent
完成最终分析。

系统不下单。Engine 只负责事实、降级处理和轻量信号，最终判断归 Agent。

[English](README.md) · [Agent 指南](AGENT_GUIDE.md) ·
[架构](ARCHITECTURE.md) · [计划](PLAN.md)

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

## 数据与配置

```text
.local/financial_assets.json          私有持仓
.local/investor_profile.json          私有投资偏好
.local/history/                       行情历史缓存
.local/snapshots/                     滚动最小快照
.secret/                              本地 API key 与 HTTP token
stocks/config/engine.yaml             运行配置
stocks/config/watchlist.json          关注标的
stocks/config/news_sources.json       RSS/Atom 新闻源
stocks/config/portfolio_constraints.json
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

现行 schema 见 `stocks/DATA_MODEL.md`。本项目只用于分析、学习与参考，不构成确定性
投资建议。
