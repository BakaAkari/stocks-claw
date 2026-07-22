# stocks-claw

A single-user personal investment analyst system. It stores user-confirmed accounts, positions, and investment preferences; collects quotes, price history, news, filings, macro data, and events; and prepares traceable evidence for an LLM investment analyst to produce portfolio actions, market assessments, and opportunity candidates. The user remains the sole decision-maker and the system never places orders.

The current production path still uses deterministic rule actions plus a constrained Outlook. The next implementation phase, defined in `EXECUTION_PLAN.md`, migrates incrementally to:

```text
UnifiedAnalysisSnapshot
→ LLM InvestmentAdvisory
→ deterministic evidence/feasibility validation
→ presentation and delivery
```

Migration is shadow-first and does not replace production in one step.

[中文](README.zh.md) · [Vision](stocks/VISION.md) · [Plan](PLAN.md) · [Execution](EXECUTION_PLAN.md) · [Architecture](ARCHITECTURE.md) · [Agent guide](AGENT_GUIDE.md)

## Authoritative documentation

- `stocks/VISION.md`: product north star
- `PLAN.md`: current decisions, state, and migration route
- `EXECUTION_PLAN.md`: only active implementation checklist
- `ARCHITECTURE.md`: current and approved target architecture
- `stocks/DATA_MODEL.md`: current and planned contracts
- `AGENT_GUIDE.md`: operational and development rules
- `docs/archive/`: historical evidence only

## Current capabilities

- Account/Position v2 financial memory, cost basis, currency, liquidity, classification, and investor profile
- A-share, US, fund, and crypto quotes and history with fallback/data-quality metadata
- News, RSS, GNews, SEC EDGAR, CNInfo, macro data, and event calendars
- Indicators, rotation, exposure, anomaly detection, and portfolio constraints
- LLM intelligence clustering and constrained medium-term Outlook
- Scheduled CN/US sessions, cron generation, and Feishu delivery
- Advice, execution, forecast, shadow-account, and signal-settlement foundations

## Target capabilities

- Natural-language asset intake with deterministic diff and user confirmation
- A unified, same-`as_of` market evidence snapshot
- Full LLM InvestmentAdvisory using portfolio, style, prices, history, news, macro, and constraints
- Rules as evidence; validators as evidence/feasibility guards rather than substitute decision-makers
- One report covering portfolio actions, market scenarios, and sector/asset opportunities
- Execution, rejection, and forecast feedback loops

## Quick start

```bash
.venv/bin/python -m stocks.adapters.cli --output json --no-news --no-quotes
.venv/bin/python -m stocks.adapters.cli --output json
.venv/bin/python -m stocks.adapters.cli --scheduled-run-due
.venv/bin/python -m stocks.adapters.cli --scheduled-run-latest cn_pre_close
```

The legacy `--llm-analysis` entrypoint is intentionally disabled and is not the target Advisory path.

## Financial memory

Reads do not require confirmation:

```bash
.venv/bin/python -m stocks.adapters.cli --assets-list
.venv/bin/python -m stocks.adapters.cli --profile-get
```

All asset, profile, advice, execution, and forecast writes require explicit user confirmation. Private state lives under `.local/`; credential symlinks under `.secret/` point to `/opt/data/.secret/`. Neither is committed.

SEC EDGAR requires a contact-bearing `SEC_USER_AGENT` or `.secret/sec-user-agent.md`.

## Verification

```bash
.venv/bin/ruff check .
.venv/bin/pytest -q -o 'addopts='
.venv/bin/python -m compileall -q stocks tests
.venv/bin/python -m stocks.adapters.cli --output json --no-news --no-quotes
```

Fresh baseline on 2026-07-22: ruff passes, pytest reports `1124 passed`, and live Finnhub, Polygon, and SEC EDGAR smoke checks pass.

This software supports analysis and research. It does not promise returns or execute trades.
