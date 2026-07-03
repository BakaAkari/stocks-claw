# stocks-claw

Agent-first personal finance context toolkit. It combines confirmed holdings and investor
preferences with quotes, news, macro data, technical indicators, portfolio mapping, and data
quality metadata, then returns an `AnalysisContext` for an external Agent.

It does not place orders. The engine prepares facts and lightweight signals; the Agent owns the
final reasoning.

[中文](README.zh.md) · [Agent guide](AGENT_GUIDE.md) ·
[Architecture](ARCHITECTURE.md) · [Plan](PLAN.md)

## Requirements

- Python 3.11+
- `uv`
- dependencies from `requirements.txt`

```bash
uv venv --python 3.11 .venv
uv pip install -r requirements.txt
```

## Quick start

Build a local/offline context:

```bash
uv run python -m stocks.adapters.cli --output json --no-news --no-quotes
```

Fetch the configured quotes and news:

```bash
uv run python -m stocks.adapters.cli --output json
```

Generate the optional internal LLM report:

```bash
uv run python -m stocks.adapters.cli --output text --llm-analysis
```

The removed `--llm-enhancer` option is not supported.

## Confirmed memory writes

Reads never need confirmation:

```bash
uv run python -m stocks.adapters.cli --assets-list
uv run python -m stocks.adapters.cli --profile-get
```

Every holdings/profile write requires `--confirmed`:

```bash
uv run python -m stocks.adapters.cli \
  --asset-add '{"name":"Cash","platform":"Bank","amount":10000,"currency":"CNY"}' \
  --confirmed

uv run python -m stocks.adapters.cli \
  --profile-update '{"risk_tolerance":"moderate"}' \
  --confirmed
```

Update and remove are available through `--asset-update` and `--asset-remove`. Equivalent MCP
tools require `"confirmed": true`.

## Data and configuration

```text
.local/financial_assets.json          private holdings
.local/investor_profile.json          private preferences
.local/history/                       quote history cache
.local/snapshots/                     minimal rolling snapshots
.secret/                              local API keys and HTTP token
stocks/config/engine.yaml             runtime settings
stocks/config/watchlist.json          tracked instruments
stocks/config/news_sources.json       RSS/Atom sources
stocks/config/portfolio_constraints.json
```

When no private holdings file exists, `stocks/data/financial_assets.json` is used as sample
input. The profile example is `stocks/data/investor_profile.example.json`.

Nested environment overrides use double underscores:

```bash
STOCKS_FETCHER__MAX_RETRIES=3
```

Finnhub requires `FINNHUB_API_KEY`. Optional OpenAI-compatible reporting uses
`OPENAI_API_KEY` and `OPENAI_BASE_URL`.
SEC EDGAR filings require a contact-bearing user agent, for example
`SEC_USER_AGENT="stocks-claw/1.0 you@example.com"`; missing configuration is
reported in `data_quality.news.errors`.

## Interfaces

- CLI: `python -m stocks.adapters.cli`
- stdio MCP: `python -m stocks.adapters.mcp`
- local HTTP: `python -m stocks.adapters.http --host 127.0.0.1 --port 8687`

Remote HTTP binding requires `--allow-remote` plus `.secret/http-token`. The HTTP adapter has
no rate limiter or CORS policy and is not a public-internet service.

## Verification

```bash
uv run ruff check .
uv run python -m pytest -q
uv run python -m compileall -q stocks tests
uv run python -m stocks.adapters.cli --output json --no-news --no-quotes
```

See `stocks/DATA_MODEL.md` for the current schema. This software is for analysis and education,
not guaranteed investment advice.
