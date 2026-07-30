# PLAN.md — decision history

This file is a decision log, not a status or task document. For current
state see `STATUS.md`; for long-term direction see `ROADMAP.md`; for the
next unit of work see `docs/tasks/`; for document precedence see
`AGENTS.md`.

## 2026-07-31 decision: direction reset to M1/M2/M3, retire TASK-002~005

Triggered by an adversarial review of a real `cn_after_close` report run
this day (`docs/analysis/adversarial-review-2026-07-31.md`). The review
measured the current push text against `stocks/VISION.md` §2.3's seven
required questions and found **2 full / 2 partial / 3 missing** coverage.

The user reaffirmed the product's north star: **combine news, industry
reports and market sentiment into market-direction analysis, produce
short/medium-term action recommendations, and surface setup candidates for
future positioning.** Removing the outlook layer (an option briefly
considered inside the review) was explicitly ruled out — outlook is core
value, not optional.

Direction reset:

- Replace the A0–A6 lettered phase table with three outcome-focused
  milestones (M1 report structure upgrade, M2 outlook mainline, M3
  feedback loop). Full description in `ROADMAP.md`; rationale in
  `docs/analysis/direction-2026-07-31.md`.
- Retire, as separate tasks, TASK-002 through TASK-005. TASK-002's
  legitimate scope (feedback ledger) is folded into M3; TASK-003/004 are
  removed entirely because the user places orders manually and does not
  need an execution surface; TASK-005 is subsumed by M2 (migration is the
  milestone itself, not a separate audit).
- Delete `EXECUTION_PLAN.md` (had already been reduced to a redirect stub;
  `docs/tasks/` is the sole active task list).
- Archive completed TASK-001 sub-tasks under
  `docs/archive/tasks-completed-2026-07/`.

Existing hard invariants remain: no automated trading, no writing to
financial memory without user confirmation, no LLM freely computing
amounts or facts, no fabricating outlook when validation fails.

## 2026-07-22 decision: LLM investment analyst, not a thicker rule engine

The user reaffirmed the product's original positioning: a deterministic
system should assemble the user's portfolio, news, prices, history, macro
data, and data-quality signals; an LLM acting as the on-duty personal
investment analyst should synthesize that evidence into judgment — position
actions, market-direction assessment, and sector/asset opportunities; the
rule engine supplies evidence and hard constraints, and does not become the
final investment decision-maker.

This ruling matches `stocks/VISION.md`'s original positioning and replaces,
as forward direction:

- Continuing to thicken the rule engine to cover every investment conflict;
- Treating the constrained `structured_outlook` as the full LLM analysis
  path;
- Letting `portfolio_adjudicator` own final recommendations exclusively;
- Re-validating Outlook semantics in the push layer via bare-number sets.

The existing production chain keeps running until the new Advisory path
passes shadow acceptance and user-value acceptance (see `ROADMAP.md` M2
gates) — no one-shot cutover.

## Decision log

- **2026-07-03** — Product positioning elevated to a personal investment
  analyst system: the deterministic system is the workbench, the LLM is the
  on-duty analyst, the user is the sole decision-maker.
- **2026-07-15** — Adversarial review confirmed the risk-monitoring and
  research foundation is usable, but rule-driven actions and portfolio
  capital deployment did not pass direct-execution acceptance. See
  `docs/archive/TRADING_SYSTEM_ADVERSARIAL_REVIEW_20260715.md`.
- **2026-07-22** — Confirmed the system should return to "unified evidence +
  LLM synthesis + hard-constraint validation" per the original vision (see
  above). Rebuilding the analyst decision hub is next; the existing data
  foundation is kept; production uses shadow running and phased migration.
- **2026-07-31** — Direction reset following an adversarial review of a
  real report. Roadmap collapses to three outcome-focused milestones
  (M1/M2/M3); TASK-002 through TASK-005 retired or folded. See detailed
  entry above.
