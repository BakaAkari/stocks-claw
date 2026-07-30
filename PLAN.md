# PLAN.md — decision history

This file is a decision log, not a status or task document. For current
state see `STATUS.md`; for long-term direction see `ROADMAP.md`; for the
next unit of work see `docs/tasks/`; for document precedence see
`AGENTS.md`.

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
passes shadow acceptance and user-value acceptance (see `ROADMAP.md` A3/A5
gates) — no one-shot cutover.

Full target responsibility chain, phase breakdown, acceptance gates, and
current structural gaps now live in `ROADMAP.md` (they are stable direction,
not decisions specific to a date) rather than duplicated here.

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
