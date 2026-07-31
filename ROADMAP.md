# ROADMAP.md — long-term direction

> Reference document. Describes stable long-term direction, not an
> executable checklist and not a source of current status — that's
> `STATUS.md`. Product north star is `stocks/VISION.md`; this document is
> the migration route toward it.

## Why this migration exists

Production today lets a deterministic rule engine (`QuantActionEngine` →
`factor_rules` → `portfolio_adjudicator`) own most final trading actions,
with the LLM restricted to news intelligence clustering and a constrained
Outlook that never enters the report as market-direction judgment. This
does not satisfy `stocks/VISION.md` §2.3, which requires the report to
answer seven questions per window — including market state, forward-looking
scenarios, and watch/setup candidates.

The 2026-07-22 product decision (see `PLAN.md`) reaffirmed the original
vision: a deterministic system assembles evidence; an LLM investment
analyst synthesizes it into judgment; a deterministic validator checks
evidence and feasibility; the user remains the sole decision-maker.

The 2026-07-31 direction reset (`docs/analysis/direction-2026-07-31.md`)
kept that shape but replaced the previous A0–A6 lettered phases with three
outcome-focused milestones. Reason: A0–A6 was written before an
adversarial review of a real report exposed where the gap actually lives
(report layer + outlook mainline + feedback), and the phase letters were
tracking implementation slices rather than user value.

## Target responsibility chain

```text
Financial Memory          user-confirmed facts
Unified Harvester         one collection pass, one as_of
Feature Layer             indicators, risk, constraints, candidate signals
LLM Investment Analyst    market outlook + actions + setup candidates
Advisory Validator        evidence + feasibility check
Presentation              seven-question report structure
Delivery                  freshness, versioning, channel delivery
User Feedback             accepted / partial / rejected / deferred
```

## Milestones

Whether each milestone has actually landed is a `STATUS.md` question, not a
roadmap question.

### M1 — Report structure upgrade

Bring the report layer up to VISION §2.3's seven-question shape. The
deterministic renderer already receives everything it needs to answer
questions 1, 2, 3, 5, 6, 7 — the current layer just doesn't project them
through.

Scope:
- Six-section structure: 本窗口变化 / 走势研判 / 可执行动作 / 提前布局 /
  禁止与延后 / 组合与检查点.
- "提前布局" becomes a first-class section: top 2-3 research candidates
  rendered with `display_label`, `action_hint`, `reassess_after`, and their
  composite score.
- Manual-review status no longer duplicates content between "可执行动作"
  and "禁止与延后"; the actions section explicitly lists the choice space
  the user needs to resolve, with reference ratios/amounts preserved from
  the pre-gate proposal.
- `data_notes` items that name real capital gaps (e.g. unsettled proceeds
  awaiting clearing rule) reach the push text.
- Cash six-bucket collapse rules: hide `confirmed_settling=0`, hide
  `strategic_exit` unless there is an executable sell action.
- `post_trade_projection` reaches the report as a compact "执行后" line
  when actions are executable.
- Truth-gate rejections carry an audit trail (original signal, instrument
  identity, proposed ratio) that flows into the report as a reference line.

Non-goals: outlook mainline (M2), feedback loop (M3), execution surface
(retired).

### M2 — Outlook mainline

Wire the LLM Investment Analyst path (`advisory_synthesizer.py` today,
shadow-only) into the production push, replacing the current constrained
`structured_outlook` as the source of forward-looking judgment.

Scope:
- Outlook consumes `UnifiedAnalysisSnapshot` (news, industry reports,
  sentiment, macro, technical features).
- Short-term (3-7 day) and medium-term (1-3 month) judgments each carry
  drivers, validation conditions, falsification conditions, and typed
  `source_refs`.
- `AdvisoryValidationReceipt` freshness / provenance / feasibility gates
  apply; failure downgrades the section to `研判待复核` — never fabricate.
- Rule signals remain, but as candidate evidence, not as the final
  authority for the outlook section.
- Shadow-parity replay against 5+ historical trading days before cutover;
  new Outlook must not silently contradict rule-driven risk state.

Non-goals: automated trading, LLM free-computing ratios/amounts, replacing
`AnalysisContext` v12 as the collection contract.

### M3 — Feedback loop

Turn user decisions into evidence that flows back into the next Outlook
run. Not an auto-tuner.

Scope:
- Per-recommendation feedback channel (Feishu inline card or CLI).
- Feedback statuses: `accepted` / `partial` / `rejected` / `deferred`.
- `AdviceRecord` becomes the ledger of feedback (renaming its original
  role from "user-confirmable draft" to "user-marked outcome").
- Weekly rollup: coverage of the seven questions, hit rate, top rejection
  reasons, data-boundary hits.
- The rollup feeds `recent_advice` in `AnalysisContext` and becomes an
  input to future Outlook synthesis. No parameter auto-tuning on small
  samples.

Non-goals: execution capture, broker connectivity, automated position
tracking (that remains manual per user preference).

### M4 — Constraint model upgrade (candidate)

Extend the constraint model beyond four-bucket min/max ratios to express
the constraint semantics real allocation decisions depend on:
irreversibility (sell = permanent exit, e.g. purchase-restricted funds),
segregated pools (e.g. overseas accounts isolated by capital controls —
no cross-pool funding, per-pool risk checks), and hard caps (breach ⇒
mandatory reduce candidate). Motivation and evidence:
`docs/analysis/kimi-report-constraint-comparison-2026-07-31.md`; bounded
scope in `docs/tasks/TASK-M4-constraint-model-upgrade.md`.

Status: backlog candidate. Sequencing against M2/M3 is re-evaluated when
M1 closes; it is independent of the outlook mainline and may be pulled
forward if constraint-driven advice errors show up in real reports.

Non-goals: multi-period glide-path planning (depends on M3 feedback
data), execution surface, auto-tuning.

### M5 — Advisory terminal (agent operating surface)

The product goal — 个人资产投资信息渠道汇总分析报告系统 + 专业的交易分析
研判终端 — is two layers, and only one of them is this repo's code:

- **Engine layer (this repo):** deterministic data, rules, ledgers,
  scheduled analysis, push reports, and confirmed-write financial memory.
  Everything the agent reads or writes goes through typed, audited
  surfaces (CLI/MCP/HTTP adapters, `_confirmed` gates).
- **Agent layer (not this repo's runtime):** the conversational analyst —
  ad-hoc multi-source research ("why did X drop"), personalized judgment,
  behavioral mirroring against the user's own rules, and driving the
  engine through its adapters. An LLM agent with the repo's MCP/CLI
  mounted plus an operating manual covers this; no new agent runtime is
  built here.

Engine-side enablers, sequenced as bounded tasks:

- **A1 — Natural-language asset intake entry.** Wire the SHADOW
  `AssetIntakeDraft` library (`asset_intake_parser.py`,
  `llm_asset_intake.py`, `asset_intake_writer.py`) into the CLI with the
  token-confirmation flow, so "我买入了 5 股 AAPL，成本 301.4" becomes a
  draft → user confirm → audited write. Direct hand-edits of
  `financial_assets.json` bypass the `_confirmed` gate and are not a
  supported path.
- **W1 — Watchlist productization.** User-designated instruments
  persisted as financial memory, pulled into daily context and
  action-signal scans, and surfaced in the push — so "把 AAPL 加入
  watchlist 每天跟踪" is a one-command operation.
- **D1 — US quotes freshness verification.** The M2 mainline freshness
  gate fails closed on stale/missing quotes (verified 2026-07-31 smoke).
  Finnhub/Polygon providers exist and a Finnhub key is present in
  `.secret/`; D1 verifies the US chain end-to-end in real scheduled runs
  and records freshness in the artifact.

Agent-layer deliverables (docs, not engine code): an operating manual
(system-prompt rules: user's style/discipline, confirm-before-write,
never bypass confirmed-write paths) and the user's investor profile kept
current as the personalization source.

Non-goals: building an agent runtime, broker connectivity, automated
order placement, moving conversational logic into the engine.

Sequencing decision (2026-08-01): **A1 → M3 → W1 → M4**, with D1 folded
into whichever task next touches the US quote path. A1 first because the
conversational asset-update pattern is the terminal's core write loop and
the library is already built; M3 next per the original plan; M4 stays
backlog until constraint-driven errors appear in real reports.

## Acceptance gates (apply per milestone)

1. **Engineering gate** — lint, tests, compile pass on the touched
   surface, and full-suite regression stays green.
2. **Data gate** — sources, freshness, gaps, and anomalies are visible in
   the artifact, not papered over.
3. **Consistency gate** — no duplicated authority across responsibility
   boundaries.
4. **Shadow gate** — for M2 specifically: new outlook path replayed
   against ≥5 consecutive trading days of historical data, with delta
   analysis logged.
5. **User-value gate** — for M2 and M3: user confirms the new capability
   reduces decision cost before the milestone is called done.

Gates 4 and 5 cannot be satisfied by a local run; see `STATUS.md` for what
has actually been run.

## Explicitly out of scope

- Automated trading or broker order placement (permanent invariant).
- Execution adapter, mock execution sink, end-to-end payload-to-receipt
  smoke (retired 2026-07-31 as separate tasks — user places orders).
- Letting the LLM freely compute amounts, position facts, or sources.
- Indefinitely keeping multiple production-grade contracts alive for
  "compatibility."
- Rewriting `AnalysisContext` v12 for its own sake; if a change is needed
  it belongs to a specific milestone.

## Known structural gaps (stable description, not a task list)

- Intelligence collection and portfolio-context collection are still two
  partially separate paths. M2 consumes `UnifiedAnalysisSnapshot`,
  which merges them for the advisory path, but the harvester itself may
  still land later.
- `DecisionEnvelope` is deprecated but its tests still run; scheduled for
  removal only when its removal is otherwise motivated.

For what milestone work has actually landed, see `STATUS.md`. For the next
concrete unit of work, see `docs/tasks/`.
