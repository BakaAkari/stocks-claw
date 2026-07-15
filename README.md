# stocks-claw

Personal investment analyst workbench for a single user. It combines confirmed accounts,
positions, investor preferences, quotes, news, macro data, technical indicators, portfolio
mapping, PnL/valuation scaffolds, and data-quality metadata, then returns an
`AnalysisContext` evidence package for an external Agent.

It does not place orders. The engine prepares facts, trigger checks, deterministic action cards,
and portfolio context; the Agent owns the final reasoning and the user remains the only decision-maker.
Current review status: risk monitoring and research assistance are usable, but automated actions,
capital deployment, and reports intended for direct execution have not passed trader-level acceptance.

[中文](README.zh.md) · [Agent guide](AGENT_GUIDE.md) ·
[Architecture](ARCHITECTURE.md) · [Plan](PLAN.md) ·
[Trading-system adversarial review](docs/TRADING_SYSTEM_ADVERSARIAL_REVIEW_20260715.md)

## Documentation map

- `PLAN.md`: direction, decisions, and current status
- `EXECUTION_PLAN.md`: the only active task and acceptance list
- `ARCHITECTURE.md`: current implementation architecture
- `stocks/DATA_MODEL.md`: current schemas and field semantics
- `AGENT_GUIDE.md`: operating rules for Agents and developers
- `stocks/VISION.md`: product north star
- `docs/TRADING_SYSTEM_ADVERSARIAL_REVIEW_20260715.md`: current trader-level quality boundary and remediation priorities
- `docs/INTELLIGENCE_FULL_REDESIGN.md`: partially implemented intelligence design
- `docs/NEWS_MODULE_REDESIGN.md`: active but not fully implemented unified-push design
- `docs/archive/`: historical evidence only; no current authority

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

Generate or read scheduled cross-market run artifacts for an external Agent:

```bash
uv run python -m stocks.adapters.cli --scheduled-run-due
uv run python -m stocks.adapters.cli --scheduled-run-session cn_pre_close --force
uv run python -m stocks.adapters.cli --scheduled-run-latest cn_pre_close
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

Private holdings now support v2 `Account` / `Position` files. Preview migration from the old
v1 asset list before writing it:

```bash
uv run python -m stocks.adapters.cli --asset-migrate-v2
uv run python -m stocks.adapters.cli --asset-migrate-v2 --confirmed
```

## Data and configuration

```text
.local/financial_assets.json          private holdings
.local/investor_profile.json          private preferences
.local/computed_profile.json          interpreted quant parameters
.local/history/                       quote history cache
.local/event_cache/                   Finnhub earnings-calendar cache
.local/snapshots/                     minimal rolling snapshots
.local/advice/                        confirmed advice summaries
.local/executions/                    confirmed execution records
.local/forecasts/                     confirmed forecast ledger
.local/scheduled_runs/                scheduled Agent handoff artifacts
.secret/                              local API keys and HTTP token
stocks/config/engine.yaml             runtime settings
stocks/config/watchlist.json          tracked instruments
stocks/config/news_sources.json       RSS/Atom sources
stocks/config/portfolio_constraints.json
stocks/config/event_calendar.json     static official event calendar
stocks/config/sector_scan.json        scan universe for rotation/signals
stocks/config/exposure_proxy.json     exposure-tag proxy mapping
stocks/config/scheduled_sessions.json scheduled A-share/US session config
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

See `stocks/DATA_MODEL.md` for the current schema and
`docs/TRADING_SYSTEM_ADVERSARIAL_REVIEW_20260715.md` for the current trading-quality boundary.
Fresh verification at the review baseline: ruff passes; pytest reports 536 passed and 2 failed due
to time-dependent intelligence fixtures. This software is for analysis and education, not guaranteed
investment advice.
