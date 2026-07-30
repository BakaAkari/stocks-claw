# TASK-001E1 — production report truth gate

## Objective

Prevent contradictory, non-executable, stale, duplicated, or unsupported advice from entering `portfolio_decision.user_view` and production push output. This is a bounded correctness task; do not redesign the report layout yet.

## Verified production defects

1. `final_ratio` differs from percentages embedded in `action_description` / `reason_summary` / `assistant_brief.why`.
2. `execution_status=deferred_min_unit`, `final_ratio=0`, `executable_quantity=0` still appears under `action_required`.
3. A report can approve actions for a market whose quote freshness is stale, especially cross-market actions.
4. The same instrument can be both an approved action and a research candidate.
5. `structured_outlook` can contain narrative claims with `source_refs=[]`.
6. `risk_state=hedge/reduce` can render with no human-readable trigger reasons.

## Scope

1. Generate every user-visible action sentence from the finalized `PortfolioAction`, never reuse raw pre-adjudication percentage text. The displayed reason must agree with `final_ratio`, `executable_quantity`, `execution_status`, and signal.
2. Split finalized actions into executable vs deferred/review. Only executable actions (`execution_status in {full, adjusted_to_step}` with `final_ratio>0` and `executable_quantity>0` when quantity applies) may enter `instruction_card.actions` or make card status `action_required`. Deferred/review actions must appear as concise no-action/manual-review reasons.
3. Fail closed on stale market facts. An action targeting market M must not be executable if `data_quality.quotes.by_market[M].freshness` is stale/old/missing/no_data. This applies to primary and cross-market positions. Do not merely add a data note.
4. Remove research candidates whose instrument identity matches any finalized approved/deferred action identity. Deduplicate by authoritative instrument key, not display label.
5. Require at least one valid `source_ref` whenever a successful Outlook contains summary, sector views, asset views, or scenarios. If none, fail the Outlook to `unavailable`; do not publish unsupported narrative.
6. Populate user-facing risk reasons from actual risk triggers/evidence without exposing machine IDs. `hedge`/`reduce` must not have an empty reasons list unless the risk state itself is explicitly invalid, in which case fail closed to review.
7. Add a deterministic push truth gate before delivery. Reject output if any remaining action text percentage contradicts final ratio, any action is zero/deferred, action/research identities overlap, or successful Outlook narratives have no source refs.

## Non-goals

- Do not implement the new concise report layout; that is TASK-001E2.
- No Advisory v2 / shadow promotion / report mode.
- No trading strategy threshold changes.
- No financial memory writes, live orders, commit, push, deployment, or cron changes.
- Do not rewrite historical artifacts.

## Likely files

- `stocks/engine/portfolio_adjudicator.py`
- `stocks/engine/presentation.py`
- `stocks/engine/scheduled_analysis.py`
- `stocks/engine/outlook_validation.py`
- `scripts/build_push_payload.py`
- relevant tests under `tests/engine/` and `tests/test_push_payload.py`, `tests/test_run_push_report.py`, `tests/test_audit_report_quality.py`
- `STATUS.md` and task docs only after stable

If another source file is required, report why before expanding.

## Acceptance tests

Add regression tests for all six defects and the delivery gate. At minimum use a real `build_scheduled_run` integration test proving:

- raw 50% becomes finalized 25%, and every user-visible percentage says 25%;
- zero/deferred action is absent from executable actions and card is not action_required solely because of it;
- stale A-market action is blocked in a US session and stale US action is blocked in a CN session;
- approved/deferred instrument does not appear in research;
- narrative Outlook with empty refs becomes unavailable;
- hedge/reduce risk output has readable reasons;
- hostile payload is rejected by `validate_payload_text` or the build gate.

Run focused tests, Ruff, compileall, `git diff --check`, then one full suite. Finally regenerate current `cn_after_close` and `us_post_open` artifacts using current code without live order/memory writes, render payloads, and run `scripts/audit_report_quality.py --start 2026-07-29 --end 2026-07-29`. Record exact remaining findings. Stop after E1; do not begin E2.
