<div align="center">

# stocks-claw

**Agent-first personal finance context toolkit**

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-Supported-2496ED?logo=docker&logoColor=white)](NAS_DEPLOYMENT.md)
[![Deps](https://img.shields.io/badge/Deps-pandas%20%7C%20numpy%20%7C%20httpx%20%7C%20pytest-informational)](requirements.txt)

[English](README.md) · [中文](README.zh.md) · [Agent Guide](AGENT_GUIDE.md) · [NAS Deploy](NAS_DEPLOYMENT.md)

</div>

---

## Overview

`stocks-claw` is a local toolkit for AI agents. It reads personal asset data, watchlists, market quotes, news, portfolio constraints, and lightweight analysis scaffolds, then returns a structured `AnalysisContext` for the agent to analyze.

Current positioning:

- The engine provides data, normalization, degradation handling, portfolio mapping, drift checks, and prompt-ready context.
- The agent remains the primary reasoning layer for final investment analysis.
- Internal LLM enhancement/report generation exists as optional support, not the default product boundary.
- It is not an automated trading system and never places orders.

---

## Architecture

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

Key packages:

```text
stocks/
  adapters/     CLI, HTTP, MCP adapters
  domain/       dataclass models: Instrument, Quote, NewsItem, FinancialAsset, AnalysisContext
  engine/       StocksEngine, fetchers, scaffolds, context builder, persistence, optional LLM modules
  providers/    Tencent A-share, Eastmoney A-share, Finnhub, RSS news providers
  config/       watchlist, market/news config, portfolio constraints, engine.yaml
  data/         sample asset template
```

---

## Requirements

- Python 3.11+
- Runtime/test dependencies from `requirements.txt`:
  - pandas
  - numpy
  - httpx
  - pyyaml
  - pytest / pytest-asyncio
  - ruff

The project is no longer stdlib-only; it intentionally uses a small financial/data engineering stack.

---

## Quick Start

```bash
git clone https://github.com/BakaAkari/stocks-claw.git
cd stocks-claw

uv venv --python 3.11 .venv
uv pip install -r requirements.txt

uv run python -m pytest
uv run python -m stocks.adapters.cli --output json --no-news --no-quotes
```

Human-readable context:

```bash
uv run python -m stocks.adapters.cli --output text --no-news --no-quotes
```

Full context with quotes/news:

```bash
uv run python -m stocks.adapters.cli --output json
```

Optional LLM data enhancement:

```bash
uv run python -m stocks.adapters.cli --output json --llm-enhancer
```

Optional internal LLM report generation:

```bash
uv run python -m stocks.adapters.cli --output text --llm-analysis
```

---

## Configuration

Privacy-sensitive user data is local-only:

```text
.local/financial_assets.json    real holdings, git-ignored
.secret/                        API keys, git-ignored
```

Tracked config:

```text
stocks/config/watchlist.json
stocks/config/portfolio_constraints.json
stocks/config/news_sources.json
stocks/config/markets.json
stocks/config/engine.yaml
```

Asset file format:

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

Watchlist format:

```json
[
  {"code": "000300", "name": "沪深300", "market": "a", "exchange": "sz_index"},
  {"code": "QQQ", "name": "纳斯达克100ETF", "market": "us"}
]
```

---

## HTTP Mode

HTTP mode is available for local/NAS integration, but should be treated as an internal service until authentication and rate limiting are hardened.

```bash
uv run python -m stocks.adapters.http --host 127.0.0.1 --port 8687
curl http://127.0.0.1:8687/api/v1/health
```

Docker/NAS deployment notes are in `NAS_DEPLOYMENT.md`.

---

## Development Checks

```bash
uv run python -m pytest
uv run python -m compileall -q stocks tests
uv run ruff check .
```

Current default test root is `tests/`. Legacy `stocks/tests/test_v2.py` was removed because it was a stale broken test harness.

---

## Safety / Disclaimer

This project is for personal analysis, education, and reference only. It does not constitute investment advice. All investment decisions are the user's responsibility.

Do not commit `.local/`, `.secret/`, runtime snapshots, caches, or virtual environments.
