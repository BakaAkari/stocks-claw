# stocks-claw

A single-user personal investment analyst system. It stores user-confirmed accounts, positions, and investment preferences; collects quotes, price history, news, filings, macro data, and events; and prepares traceable evidence for an LLM investment analyst to produce portfolio actions, market assessments, and opportunity candidates. The user remains the sole decision-maker and the system never places orders.

Development follows confirmed personal constraints → reconciled financial facts →
traceable LLM proposals → user decisions and actual execution → review. The local
main-window chain preserves original LLM proposals and deterministic validation;
deployment and real-world acceptance are separate states recorded in `STATUS.md`.
See [the financial truth contract](docs/contracts/financial-trust-v1.md) for the
current API, supported actions, limitations and explicitly simulated smoke commands.
The operating order and deployment gates are defined in
[Operating Model v1](docs/contracts/operating-model-v1.md).

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

Primary CN/US report commands require a frozen Advisory lineage and render its
validated view deterministically, including the existing `run_llm_report.py`
cron entrypoint. `--force-llm` cannot bypass primary-session freshness or lineage
checks. Intelligence-only narration retains its separate LLM path.
Context construction may persist derived caches/artifacts, but never automatically
settles or rewrites confirmed forecasts. See the
[hardening review](docs/analysis/system-hardening-2026-10-19.md) for remaining gaps.


## Financial memory

Reads do not require confirmation:

```bash
.venv/bin/python -m stocks.adapters.cli --assets-list
.venv/bin/python -m stocks.adapters.cli --profile-get
```

All asset, profile, advice, execution, and forecast writes require explicit user confirmation. Private state lives under `.local/`; credential symlinks under `.secret/` point to `/opt/data/.secret/`. Neither is committed.

The [decision fact-chain contract](docs/contracts/decision-review-v2.md) describes
source-linked advice, append-only user decisions, individual confirmed fills,
and read-only review through CLI/MCP. Recorded fills do not automatically update
balances or positions. Sale proceeds and subsequent price changes are not profit.

SEC EDGAR requires a contact-bearing `SEC_USER_AGENT` or `.secret/sec-user-agent.md`.

## Verification

```bash
.venv/bin/ruff check .
.venv/bin/pytest -q -o 'addopts='
.venv/bin/python -m compileall -q stocks tests
.venv/bin/python -m stocks.adapters.cli --output json --no-news --no-quotes
```

For current verification evidence (latest ruff/pytest/smoke results), see `STATUS.md` — this README does not track a point-in-time pass/fail baseline.

For the manual signal → frozen price-observation result pipeline, use the commands and
explicit window definitions in [the minimum evaluation contract](docs/contracts/signal-evaluation-v1.md).
Its real-price smoke replays existing production rule code into a new data directory;
it does not restart scheduled analysis or enable strategy feedback.

For the recommendation → decision → fills → review acceptance replay, run:

```bash
.venv/bin/python scripts/smoke_decision_review.py --output-dir /tmp/stocks-decision-review-example
```

Use a fresh output directory. This runs current project code with public historical
prices and explicitly simulated user decisions, portfolio and fills; it does not
use or modify the user's financial memory. Production rules must naturally produce
an executable recommendation; absent one, the smoke reports failure.

This software supports analysis and research. It does not promise returns or execute trades.

## Account evidence preview

See [account intake and readiness](docs/contracts/account-intake-readiness-v1.md)
for A-share/US templates, required broker evidence and the confirmation boundary.

```bash
.venv/bin/python -m stocks.adapters.cli --financial-intake-template a --output json
.venv/bin/python -m stocks.adapters.cli --financial-intake-preview-file /absolute/private/account.json --output json
.venv/bin/python -m stocks.adapters.cli --financial-readiness --output json
.venv/bin/python scripts/smoke_account_readiness.py --output-dir /tmp/stocks-account-readiness-new
```

These APIs do not import or confirm facts. The smoke uses synthetic accounts in
an isolated new directory; passing it does not establish real-account readiness.
