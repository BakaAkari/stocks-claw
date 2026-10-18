# Operating Model v1: from financial truth to personal strategy

This contract defines the operating order for the product. It is more specific
than the product vision and more stable than an implementation task. Lifecycle
status is recorded in `docs/contracts/README.md`; current execution status is
recorded in `STATUS.md`.

## Product promise

The system reduces the user's decision cost by making the right facts easy to
inspect, the advice easy to challenge, and the eventual result easy to review.
It does not promise returns, infer a user's risk appetite from behavior, or
turn an LLM response into an order.

The value hierarchy is:

1. financial facts are correct, dated, scoped and attributable;
2. advice uses the same facts and the user's confirmed constraints;
3. the user's decision and actual execution remain separate recorded facts;
4. review explains what was known, decided, executed and observed;
5. strategy research uses the accumulated record only after the first four are reliable.

No lower layer may compensate for a failed higher layer. A polished report
cannot compensate for an unverified position. A high apparent win rate cannot
compensate for missing fills, changing denominators or unclear signal meaning.

## Fact classes and authority

Every important value belongs to exactly one class:

| Class | Examples | Authority | Can it be overwritten by analysis? |
|---|---|---|---|
| Confirmed user fact | account, holding, cash, cost, preference, mandate | user-confirmed write path | never |
| External evidence | quote, filing, news, calendar, broker statement | provider or imported document with source/time | refreshed as a new observation |
| Derived fact | exposure, reconciliation equation, stale flag, indicator | deterministic code and input hashes | recomputed, never written into user facts |
| LLM proposal | rationale, scenario, action, condition | model response with snapshot and prompt lineage | preserved verbatim; may be rejected |
| User decision | accepted, partial, rejected, deferred | explicit user confirmation | append-only |
| Actual execution | fill, fee, settlement, cash movement | user or imported execution evidence | append-only; corrections are new records |
| Outcome observation | later quote, statement, event, review note | frozen observation with target definition | preserved; revisions are new observations |

An import may be genuine without being confirmed. A file's presence does not
prove that the user confirmed its contents. Confirmation provenance must include
the confirmation event, time, actor and content hash.

## Operating modes

The product has six explicit modes. A run declares its mode and may not silently
skip to a later mode.

### 1. Capture

Create or update a draft from user language or an external statement. Show the
field-level diff, ambiguity, source and proposed effect. No user fact changes.

### 2. Reconcile

Compare independent opening and closing statements with fills and non-trade
cash movements. Keep account, currency, unit, trade time and settlement time
separate. The result is `matched`, `mismatch` or `incomplete`.

`matched` means the supplied records satisfy the declared equation. It does not
prove broker authenticity, completeness of records, tax treatment, corporate
action treatment or profit.

### 3. Advise

Build one timestamped snapshot containing mandate, financial integrity, market
evidence, data quality and execution rules. The LLM may propose judgment and
actions. It may not change facts, invent quantities, or turn a rule candidate
into an approved action.

Portfolio actions require current enough statements, explicit account and unit,
known available quantity or cash, valid instrument identity, and a supported
settlement rule. Market-only commentary may continue when portfolio action is
blocked, but the block must be visible.

### 4. Decide

The user accepts, changes, rejects or defers the proposal. The decision points
to the immutable proposal and records the user's chosen scope. Acceptance is
not execution.

### 5. Record execution

Record each fill independently, including side, quantity, price, currency,
fees, account, execution time and settlement time when known. Independent user
trades do not need a fabricated advice link. The system never places an order
and never updates a position merely because a fill was recorded.

### 6. Review and learn

Review the original evidence, proposal, decision, actual fills, cash movements
and frozen observations. Mark missing information explicitly. Strategy research
may consume the review, but it cannot rewrite the underlying facts.

## Readiness states

Each account and each proposed action has a readiness state:

- `unverified`: no confirmed current statement;
- `captured`: facts exist but opening/closing reconciliation is absent;
- `reconciled`: the declared interval matches, with explicit scope and gaps;
- `advisory_ready`: the snapshot is fresh enough and required evidence is present;
- `decision_recorded`: the user choice is frozen;
- `execution_recorded`: every known fill and cash leg is recorded;
- `review_ready`: the review window and outcome definition are frozen.

State transitions are monotonic for a given evidence version. A newer statement
can create a new state; it does not silently upgrade an older record.

An account may be `reconciled` while an action is still `review_required`. For
example, an old statement can reconcile historically but cannot authorize a
current sell. Missing available quantity, unknown fee budget, unsupported FX,
ambiguous position identity or stale evidence blocks the action.

## Personalization and strategy research

Personalization has three separate inputs:

1. confirmed constraints and needs: what the user says must be respected;
2. observed behavior: what the user actually does, which may be a preference or
   a mistake;
3. research evidence: what an external strategy claims and under which conditions.

The system must never silently promote (2) into (1), or (3) into a recommendation.

Every personal strategy candidate must record:

- hypothesis and intended user problem;
- source, publication date, assumptions and mechanism;
- eligible instruments, market regime and liquidity requirements;
- risk, costs, taxes, currency and corporate-action assumptions;
- test sample, exclusion rules, benchmark and comparison horizon;
- in-sample and out-of-sample results with fixed definitions;
- failure conditions and a stop rule;
- user approval status and whether it is research-only, paper-only or usable for advice.

One trade, one market episode or one attractive backtest cannot promote a strategy.
No strategy may auto-tune user facts, action sizes or constraints.

## Metrics that matter before win rate

Before strategy statistics, the system should establish:

- statement coverage and reconciliation completeness;
- unmatched or late-recorded event count;
- action feasibility error rate;
- percentage of advice with complete evidence and user decision records;
- review completion and time-to-decision;
- user correction, override and rejection reasons;
- data freshness and provider failure rates.

Any future return, win-rate or drawdown metric must declare its signal meaning,
entry/exit rule, denominator, neutral cases, costs, corporate-action treatment,
currency, sample independence and observation horizon. A metric without these
definitions is descriptive text, not strategy evidence.

## Deployment levels and gates

Deployment is a separate state from code completion:

| Level | Meaning | Required gate |
|---|---|---|
| L0 | documents and contracts | contradiction review complete |
| L1 | local simulated replay | deterministic tests and smoke pass |
| L2 | local real-account read-only | confirmed imports reconcile; no writes from analysis |
| L3 | NAS shadow | same commit, isolated data path, no push or cron restoration |
| L4 | controlled advisory | user reviews artifacts and delivery for a defined trial window |
| L5 | routine operation | rollback, backup, monitoring and user-value evidence exist |

Passing L1 never implies L2. Passing L2 never implies L3. A failed gate returns
the affected account or mode to `review_required`; it does not use a fallback
that hides the failure.

## Stop conditions

The system must stop portfolio actions and surface the reason when any of these
occur: account identity is ambiguous; current statement is missing or stale;
available quantity or cash is unknown; a fee or settlement leg is unknown when
the requested action depends on it; the instrument or currency is ambiguous;
the evidence snapshot changes after proposal creation; an LLM proposal has an
unsupported citation or conflicts with a confirmed hard constraint; or the
delivery artifact cannot be linked to the frozen proposal.

The system may continue collecting evidence and presenting a clearly labelled
market-only view while portfolio actions are stopped.

## Acceptance rule

The product is ready for the next level only when a reviewer can reconstruct,
without hidden manual fields:

`confirmed facts → reconciliation → snapshot → proposal → validation → user decision → actual execution → review`.

The reconstruction must work with real account evidence for the intended user,
repeat deterministically, preserve unknowns, and leave the financial memory
unchanged during read-only analysis. Simulated smoke data is evidence of the
implementation path only; it is never evidence of user value or investment
performance.
