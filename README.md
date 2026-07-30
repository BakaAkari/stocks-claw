# stocks-claw

A single-user personal investment analyst system. It stores user-confirmed accounts, positions, and investment preferences; collects quotes, price history, news, filings, macro data, and events; and prepares traceable evidence for an LLM investment analyst to produce portfolio actions, market assessments, and opportunity candidates. The user remains the sole decision-maker and the system never places orders.

The current production path still uses deterministic rule actions plus a constrained Outlook. The long-term migration direction, described in `ROADMAP.md`, moves incrementally to:

```text
UnifiedAnalysisSnapshot
→ LLM InvestmentAdvisory
→ deterministic evidence/feasibility validation
→ presentation and delivery
```

Migration is shadow-first and does not replace production in one step. Whether
any given phase has actually landed is a `STATUS.md` question, not a claim
this README makes.

[中文](README.zh.md) · [Agents](AGENTS.md) · [Status](STATUS.md) · [Roadmap](ROADMAP.md) · [Vision](stocks/VISION.md) · [Architecture](ARCHITECTURE.md) · [Agent guide](AGENT_GUIDE.md) · [Plan](PLAN.md)

## Documentation map

- `AGENTS.md`: entry point for coding agents — document precedence order and
  session rules; start here for any development task.
- `STATUS.md`: the only source of current dynamic project state (what's
  landed, what's dirty, what's pending).
- `docs/tasks/`: the current bounded coding task.
- `stocks/VISION.md`: product north star (reference, rarely changes).
- `ROADMAP.md`: long-term migration direction (reference, not a checklist).
- `ARCHITECTURE.md`: current and target architecture, labeled `[PRODUCTION]` /
  `[SHADOW]` / `[PLANNED]` / `[DEPRECATED]` per component (reference).
- `docs/contracts/`: data contract lifecycle index (which contract is
  actually live, per contract).
- `stocks/DATA_MODEL.md`: schema field reference.
- `AGENT_GUIDE.md`: operational and financial-data safety rules.
- `PLAN.md`: decision history, not current status.
- `docs/archive/`: historical record only, no active authority.
- `EXECUTION_PLAN.md`: superseded, kept only so old links don't 404 — see
  `docs/tasks/` instead.

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

For current verification evidence (latest ruff/pytest/smoke results), see `STATUS.md` — this README does not track a point-in-time pass/fail baseline.

This software supports analysis and research. It does not promise returns or execute trades.
