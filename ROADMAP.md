# ROADMAP.md — long-term direction

> Reference document. Describes stable long-term direction, not an
> executable checklist and not a source of current status — that's
> `STATUS.md`. Product north star is `stocks/VISION.md`; this document is
> the migration route toward it.

## Why this migration exists

Production today still lets a deterministic rule engine
(`QuantActionEngine` → `factor_rules` → `portfolio_adjudicator`) own most
final trading actions, with the LLM restricted to news intelligence and a
constrained medium-term Outlook. The 2026-07-22 product decision (see
`PLAN.md` decision log) reaffirmed the original vision instead: a
deterministic system assembles portfolio, news, price, history, macro, and
data-quality evidence; an LLM investment analyst synthesizes that evidence
into judgment; a deterministic validator checks evidence and feasibility;
the user remains the sole decision-maker. Rule signals become candidate
evidence, not the final authority.

This supersedes, as ongoing direction: continuing to thicken the rule engine
to cover every investment conflict; treating the constrained
`structured_outlook` as a full LLM analysis path; letting
`portfolio_adjudicator` own final recommendations; and re-validating Outlook
semantics in the push layer via bare-number matching.

## Target responsibility chain

```text
Financial Memory          user-confirmed facts
Unified Harvester         one collection pass, one as_of
Feature Layer             indicators, risk, constraints, candidate signals
LLM Investment Analyst    synthesis and recommendation
Advisory Validator        evidence and feasibility check
Presentation              pure projection and formatting
Delivery                  freshness, versioning, channel delivery
User Feedback             execution and review
```

## Phase direction (A0–A6)

Whether each phase has actually landed is a `STATUS.md` question, not a
roadmap question. This section describes what each phase means, not whether
it's done.

- **A0 — Contracts and shadow baseline.** Define `UnifiedAnalysisSnapshot`,
  `InvestmentAdvisory`, and `AdvisoryValidationReceipt`; build a repeatable
  baseline and failure taxonomy for the current production path. Shadow-only;
  production push is untouched.
- **A1 — Natural-language financial memory intake.** Extract structured
  drafts from natural-language asset/preference descriptions, diff against
  existing records, surface ambiguities, and write atomically only after
  user confirmation.
- **A2 — Unified evidence snapshot.** Merge the trading-session and
  intelligence collection paths into one `as_of`, one source registry, one
  data-quality view; stop feeding stale intelligence into trading windows.
- **A3 — Full LLM Advisory, shadow-only.** The LLM reads one unified
  snapshot and produces market judgment, position actions, portfolio
  impact, scenarios, forecast candidates, and sector/asset opportunities.
  The rule engine becomes input evidence. Output is shadow-only — never
  pushed, never written to `user_view`.
- **A4 — Semantic and feasibility validation.** Typed evidence refs, a
  validation receipt, content hashing, and a single corrective retry;
  checks span instrument legitimacy, position relationships, ratios,
  liquidity, settlement, lockups, and data anomalies. Removes cross-semantic
  bare-number authorization.
- **A5 — Production migration.** After shadow and validation acceptance,
  switch the four main-window reports to Advisory-driven rendering, first
  for A-share pre/post-close, then US sessions; observation windows report
  deltas against validated Advisory. Keep one stable current-path fallback
  until acceptance, then remove redundant compatibility layers.
- **A6 — Execution, forecasting, and feedback calibration.** Turn approved
  Advisory actions into user-confirmable `AdviceRecord` drafts; tie
  execution/rejection/deferral to `action_id`; settle confirmed forecast
  candidates on schedule; feed versioned outcome summaries back into later
  Advisory runs without auto-tuning on small samples.

## Acceptance gates (apply per phase)

1. **Engineering gate** — lint, tests, compile, and schema/offline smoke
   pass.
2. **Data gate** — sources, freshness, gaps, and anomalies are visible, not
   papered over.
3. **Consistency gate** — no duplicated authority across responsibility
   boundaries.
4. **Shadow gate** — new and old outputs are comparable and replayable
   (A3's shadow gate specifically requires at least 5 consecutive trading
   days of main-window shadow runs before A4 sign-off).
5. **User-value gate** — the user confirms the new capability reduces
   decision cost before the next phase or production switch (A5's gate
   specifically requires at least 10 consecutive trading days of side-by-side
   comparison before retiring the current rule-driven report).

Gates 4 and 5's day-counts cannot be satisfied by a local run; see
`STATUS.md` for what's actually been run.

## Explicitly out of scope for this migration

- Automated trading or broker order placement.
- Replacing structured contracts and validators with prompt text.
- A one-shot rewrite of all providers, positions, history, or cron
  scheduling.
- Disabling the current production report before the A3 shadow gate passes.
- Letting the LLM freely compute amounts, position facts, or sources.
- Indefinitely keeping multiple production-grade contracts alive "for
  compatibility."

## Current known structural gaps (stable description, not a task list)

- Intelligence collection and portfolio-context collection are still two
  partially separate paths (target: A2).
- Push validation still mixes freshness, format, integrity, and (residually)
  semantic concerns; the semantic re-validation via bare numbers is being
  removed incrementally (see `docs/tasks/`).
- Natural-language asset intake exists as library code
  (`asset_intake_parser.py`, `llm_asset_intake.py`, `asset_intake_writer.py`)
  but has no adapter-level, user-facing entry point yet (target: A1 exit).

For what phase work has actually landed, see `STATUS.md`. For the next
concrete unit of work, see `docs/tasks/`.
