# HARDEN-01: analysis and delivery trust boundaries

## Objective

Close the verified gaps between confirmed financial memory, local analysis,
frozen advisory proposals and report delivery. This is the first bounded
implementation task under the user's system-hardening request.

## Scope

- Analysis must not settle or rewrite confirmed forecast records automatically.
- Populate declared AnalysisContext fields through its constructor contract.
- Verify advisory snapshot, receipt, decision and display linkage before report
  delivery; reject missing lineage for current primary sessions, including
  manual resend and LLM-rendering entrypoints.
- Use deterministic final primary reports; do not let a second LLM rewrite
  validated actions or bypass primary freshness on resend.
- Preserve noncritical diagnostic failures in logs and run artifacts.
- Record the broader audit and remaining capability/acceptance work separately.

## Non-goals

No real-account writes, secrets, order placement, deployment, cron changes,
commit/push, new strategy metrics or relaxation of existing action gates.
Hash consistency is not authentication or proof of investment quality.

## Acceptance

- A context build with an overdue forecast leaves the forecast bytes unchanged.
- A genuine simulated advisory artifact renders; modified snapshot, receipt,
  proposal, approved actions or display fails before delivery/output persistence.
- Missing/disabled LLM can still produce an honestly blocked market report.
- Current production entrypoints reject legacy primary artifacts without lineage.
- Optional diagnostics retain errors without restoring rule-action authority.

## Verification

Focused tests: financial service/context, advisory lineage, scheduled analysis,
artifact guard and deterministic/LLM report entrypoints. Final checks: README
ruff, pytest and compileall commands. Run the actual isolated production-chain
smoke with `.venv/bin/python scripts/smoke_advisory_lineage.py --output-dir`
pointing to a new directory outside the repository; no online model/private data.

## Stop criteria

This task is complete when these boundaries and their regression tests pass.
Record unmet product capabilities and L2-L5 acceptance explicitly; do not claim
that this task alone makes the system ready for routine financial use.

## Outcome

Implemented and verified with isolated production-chain replay, including final
report validation. Current results and remaining system gaps are in STATUS.md.
The user-account and deployment levels remain separate and unaccepted.
