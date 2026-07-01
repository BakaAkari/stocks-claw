<div align="center">

# stocks-claw

**Agent 优先的个人金融上下文工具包**

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-支持-2496ED?logo=docker&logoColor=white)](NAS_DEPLOYMENT.md)
[![Deps](https://img.shields.io/badge/Deps-pandas%20%7C%20numpy%20%7C%20httpx%20%7C%20pytest-informational)](requirements.txt)

[English](README.md) · [中文](README.zh.md) · [Agent 指南](AGENT_GUIDE.md) · [NAS 部署](NAS_DEPLOYMENT.md)

</div>

---

## 项目概览

`stocks-claw` 是给 AI Agent 使用的本地金融数据工具包。它读取个人资产、关注标的、市场行情、新闻、组合约束和轻量分析脚手架，然后输出结构化 `AnalysisContext`，供 Agent 进一步分析。

当前定位：

- Engine 负责数据、清洗、降级、组合映射、偏离检查和上下文组装。
- Agent 主脑负责最终投资分析。
- 内部 LLM enhancer / analysis 是可选能力，不是默认主链路。
- 不是自动交易系统，不执行下单。

---

## 架构

```text
Agent / CLI / HTTP / MCP
        ↓
stocks.adapters.*
        ↓
StocksEngine
        ↓
DataFetcher + ProviderRegistry + PortfolioScaffold + MarketScaffold + ContextBuilder
        ↓
Tencent / Eastmoney / Finnhub / RSS + local config/data
        ↓
AnalysisContext JSON
```

主要目录：

```text
stocks/
  adapters/     CLI、HTTP、MCP 适配器
  domain/       dataclass 模型：Instrument、Quote、NewsItem、FinancialAsset、AnalysisContext
  engine/       StocksEngine、fetchers、scaffolds、context builder、persistence、可选 LLM 模块
  providers/    腾讯 A股、东方财富 A股、Finnhub、RSS 新闻 Provider
  config/       watchlist、市场/新闻配置、组合约束、engine.yaml
  data/         示例资产模板
```

---

## 环境要求

- Python 3.11+
- `requirements.txt` 中的小型数据工程依赖：
  - pandas
  - numpy
  - httpx
  - pyyaml
  - pytest / pytest-asyncio
  - ruff

项目已不再是纯标准库；当前明确引入小型金融/数据处理栈。

---

## 快速开始

```bash
git clone https://github.com/BakaAkari/stocks-claw.git
cd stocks-claw

uv venv --python 3.11 .venv
uv pip install -r requirements.txt

uv run python -m pytest
uv run python -m stocks.adapters.cli --output json --no-news --no-quotes
```

人类可读上下文：

```bash
uv run python -m stocks.adapters.cli --output text --no-news --no-quotes
```

包含行情和新闻的完整上下文：

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

---

## 配置

隐私数据只保存在本地：

```text
.local/financial_assets.json    真实持仓，git-ignored
.secret/                        API keys，git-ignored
```

纳入版本管理的配置：

```text
stocks/config/watchlist.json
stocks/config/portfolio_constraints.json
stocks/config/news_sources.json
stocks/config/markets.json
stocks/config/engine.yaml
```

资产文件格式：

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

关注列表格式：

```json
[
  {"code": "000300", "name": "沪深300", "market": "a", "exchange": "sz_index"},
  {"code": "QQQ", "name": "纳斯达克100ETF", "market": "us"}
]
```

---

## HTTP 模式

HTTP 模式可用于本地/NAS 集成，但在认证、限速等硬化完成前，应视为内网服务。

```bash
uv run python -m stocks.adapters.http --host 127.0.0.1 --port 8687
curl http://127.0.0.1:8687/api/v1/health
```

Docker/NAS 部署见 `NAS_DEPLOYMENT.md`。

---

## 开发验证

```bash
uv run python -m pytest
uv run python -m compileall -q stocks tests
uv run ruff check .
```

默认测试根目录是 `tests/`。旧的 `stocks/tests/test_v2.py` 已删除，因为它是过期且缩进损坏的测试入口。

---

## 安全与免责声明

本项目仅用于个人分析、学习和参考，不构成投资建议。所有投资决策由用户自行负责。

不要提交 `.local/`、`.secret/`、运行快照、缓存或虚拟环境。
