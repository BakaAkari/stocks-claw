# Agent 使用指南

本文档面向帮助用户使用和维护 stocks-claw 的 AI Agent。

## 系统定位

`stocks-claw` 是一个运行在 Agent workspace 中的个人金融数据与分析上下文工具包。

当前主线定位：

- 程序负责读取资产、关注列表、行情、新闻与组合脚手架。
- 程序输出结构化 `AnalysisContext`。
- 最终投资分析默认由 Agent 主脑完成。
- 内部 LLM enhancer / analysis 是可选能力，不是默认主链路。

它不是自动交易系统，不执行下单；资产变更必须由用户确认。

## 当前真实入口

统一 CLI 入口是：

```bash
python -m stocks.adapters.cli [options]
```

推荐在本地开发环境中使用：

```bash
uv run python -m stocks.adapters.cli [options]
```

当前 CLI 不是 `query/report/assets` 子命令式接口。旧文档里的 `python -m stocks.cli.stocks ...` 已废弃。

## 快速开始

```bash
cd /path/to/stocks-claw
uv venv --python 3.11 .venv
uv pip install -r requirements.txt
uv run python -m pytest
```

最小 smoke：

```bash
uv run python -m stocks.adapters.cli --output json --no-news --no-quotes
```

人类可读输出：

```bash
uv run python -m stocks.adapters.cli --output text --no-news --no-quotes
```

包含行情和新闻：

```bash
uv run python -m stocks.adapters.cli --output json
```

启用 LLM 数据增强：

```bash
uv run python -m stocks.adapters.cli --output json --llm-enhancer
```

启用内部 LLM 报告生成：

```bash
uv run python -m stocks.adapters.cli --output text --llm-analysis
```

如果需要显式传入 OpenAI-compatible 配置：

```bash
uv run python -m stocks.adapters.cli \
  --output json \
  --llm-enhancer \
  --openai-key "$OPENAI_API_KEY" \
  --openai-base-url "$OPENAI_BASE_URL"
```

## 配置与数据

### 资产数据

优先级：

1. `.local/financial_assets.json` — 用户真实资产，本地隐私数据，不提交 git。
2. `stocks/data/financial_assets.json` — 示例资产模板。

当前资产 JSON 是扁平数组：

```json
[
  {
    "name": "科创50ETF华夏",
    "platform": "券商A股",
    "amount": 3071.0,
    "asset_type": "股票ETF",
    "notes": "588000，1800股",
    "confirmed": true,
    "currency": "CNY"
  }
]
```

### 关注列表

文件：

```text
stocks/config/watchlist.json
```

格式是扁平数组：

```json
[
  {"code": "000300", "name": "沪深300", "market": "a", "exchange": "sz_index"},
  {"code": "QQQ", "name": "纳斯达克100ETF", "market": "us"}
]
```

### Engine 配置

文件：

```text
stocks/config/engine.yaml
```

优先级：

```text
环境变量 > engine.yaml > 代码默认值
```

常用环境变量示例：

```bash
OPENAI_API_KEY=...
OPENAI_BASE_URL=...
FINNHUB_API_KEY=...
STOCKS_FETCHER_MAX_RETRIES=3
```

## Agent 常用工作流

### 查看当前组合上下文

```bash
uv run python -m stocks.adapters.cli --output json --no-news --no-quotes
```

Agent 读取 `context.assets`、`context.portfolio_mapping`、`context.drift_checks` 后回复用户。

### 获取完整上下文并由 Agent 分析

```bash
uv run python -m stocks.adapters.cli --output json
```

Agent 将返回的 `context` 作为事实输入，自行完成投资分析。不要把程序输出包装成确定投资建议；需要标注数据来源和不确定性。

### 保存上下文到文件

```bash
uv run python -m stocks.adapters.cli --output json --save /tmp/stocks-context.json
```

## HTTP / MCP

HTTP 与 MCP 适配器存在，但当前仍应视为本地/内网适配层。公开部署前必须补齐认证、速率限制、CORS 和接口安全审计。

本地 HTTP 启动：

```bash
uv run python -m stocks.adapters.http --host 127.0.0.1 --port 8687
```

健康检查：

```bash
curl http://127.0.0.1:8687/api/v1/health
```

## 测试与验证

默认测试：

```bash
uv run python -m pytest
```

编译检查：

```bash
uv run python -m compileall -q stocks tests
```

Ruff：

```bash
uv run ruff check .
```

## 隐私与安全规则

- 不提交 `.local/`。
- 不提交 `.secret/`。
- 不提交快照数据、缓存、虚拟环境。
- 任何资产写入都需要用户确认。
- 不把 LLM 输出当成投资事实或确定建议。
- HTTP 服务不要直接暴露公网。
