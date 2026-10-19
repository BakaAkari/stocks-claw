# HARDEN-02: account intake preview and evidence time

## Objective

Prepare the real-account read-only gate without importing private facts or
claiming L2 acceptance. Make unknown, stale and future evidence visible.

## Scope

- Pure preview of a single-account statement/fill/cash-movement bundle, exposed
  through CLI/MCP, with A-share and US templates, validation and reconciliation.
- Read-only account readiness distinguishing matched history from current
  usable facts; no preview or readiness result authorizes an investment action.
- Reject invalid, materially future and expired analysis snapshot timestamps
  before model calls. Preserve unknown macro timestamps without borrowing
  another field's time or the context generation time.
- Tests and a standalone isolated smoke; document actual required user inputs.

## Non-goals

No broker login, private-data import, confirmation event creation, asset writes,
fees/projection implementation, metrics, deployment, push, commit or push.
Actual account capture and L2 acceptance require user-provided confirmed facts.

## Acceptance

Preview rejects ambiguous IDs, wrong accounts/markets, incomplete records,
future evidence and unsupported trade semantics; it never creates facts.
Matched but stale accounts cannot be marked ready for current analysis.
CLI/MCP preview/readiness leave all existing financial files byte-identical.
Missing macro observation time stays unknown. A future/invalid snapshot never
calls the LLM; a small documented clock skew is tolerated for live construction.

## Verification

Focused financial-intake/readiness, adapter, snapshot and Advisory tests.
Final README Ruff/pytest/compileall checks plus
`scripts/smoke_account_readiness.py --output-dir <new external directory>`.
Smoke uses synthetic fixtures with normal public APIs and no real external IO.

## Outcome

Implemented CLI/MCP readonly preview/readiness, A/US null templates, source-time
checks and isolated smoke. Full pytest: 1880 passed / 7 skipped; changed-file
Ruff, compileall and both account/advisory smokes pass. Global Ruff retains 39
pre-existing diagnostics. Real-account L2 remains pending user evidence and
explicit confirmation through existing write APIs. No deployment or real writes.
