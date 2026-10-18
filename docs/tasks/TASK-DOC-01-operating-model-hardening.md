# DOC-01: operating model and acceptance hardening

## Objective

Make the product design implementable before adding more code. Establish one
operating model for financial truth, advice, user decisions, executions,
reviews, personalization, strategy research and deployment gates.

## Scope

- reconcile the vision, architecture, roadmap, contract index and current status;
- define fact authority, readiness states, mode transitions and stop conditions;
- define what real-account validation and NAS shadow deployment must prove;
- define personal strategy research without prematurely adding win-rate automation;
- identify implementation work as separate follow-up tasks.

## Non-goals

- no business-code changes;
- no real financial-memory writes;
- no NAS deployment, cron restoration or push delivery;
- no return, win-rate, drawdown or auto-tuning implementation;
- no claim that simulated smoke is real-user validation.

## Acceptance

The documents must agree on the current checkout, lifecycle labels, current task,
deployment state and the difference between local implementation and production
operation. The operating model must let a future task answer:

1. what facts are authoritative;
2. which mode the system is in;
3. whether an action is allowed or must be blocked;
4. what evidence is needed to move to the next readiness state;
5. how a personal strategy candidate becomes research, paper or advice eligible.

The follow-up code tasks must be sequenced behind the real-account read-only gate.

## Follow-up order

1. **DOC-02 provenance hardening**: record confirmation events and content hashes
   for existing profiles, constraints and imported financial facts.
2. **DOC-03 real-account capture**: define the supported import format and a
   read-only local intake/reconciliation procedure for the user's accounts.
3. **DOC-04 local real-account gate**: validate one account end to end without
   allowing analysis to write financial memory.
4. **DOC-05 NAS shadow**: deploy the exact commit with cron and push disabled,
   verify paths, backups, artifacts and rollback.
5. **DOC-06 controlled advisory**: run a defined user-reviewed trial, then decide
   whether strategy research and outcome metrics have enough evidence.

No code task may skip DOC-02 through DOC-04 by relying on the existing simulated
smoke. No metric task may start before DOC-06 produces a stable review record.

## DOC-02 outcome: confirmation provenance hardening

Confirmed profile, portfolio constraints, the v2 asset ledger, account statements, cash movements and
execution records now create content-addressed sidecar events under
`.local/confirmation_events/`. Current content must match the event hash before
the source is marked `local_confirmed`; old or tampered files remain
`legacy_unverified`. The event envelope is also integrity-checked and user-confirmed
events require a CLI or MCP channel. CLI and MCP channel/actor metadata are preserved,
same-content retries are idempotent, and financial reconciliation exposes missing
confirmation provenance as an explicit gap.
