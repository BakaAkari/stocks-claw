# TASK-001 — production truth model: one authoritative final user_view

> Status of this task: **not started**. This is the approved next bounded
> step in the external strengthening plan (`STATUS.md` "Task 0–8"). It is
> **not** a push-time audit gate — an earlier draft of this file scoped a
> "wire `run_all_checks` into `run_push_report.py`" task; that draft was
> wrong and has been replaced. Task 0 (audit tool + two root-cause fixes) is
> separately, locally verified — see `STATUS.md`. Task 0's audit run against
> historical artifacts found **117 P0 / 17 P1** report-truth defects; this
> task is the first structural fix against that baseline, not a downstream
> patch on top of the audit tool.

## Objective

Make `portfolio_decision.user_view` (and its `outlook`/`outlook_delta`
attachments) **built once, per run, from one authoritative final
valuation/action per position** — with no post-build mutation path, no
duplicated or drifting valuations, and no financial-amount computation left
inside the presentation/projection layer. This is a structural fix to the
decision/presentation boundary, not a new detection or validation layer.

## Scope

1. **Single authoritative build, no post-build mutation.** `user_view` (and
   any attached `outlook` / `outlook_delta`) must be constructed exactly
   once per run from the final approved action/valuation set. Audit and
   remove any code path that mutates `user_view`, `instruction_card`, or
   `assistant_brief` after `build_user_view()` returns (e.g. downstream
   session/window code patching fields back in). If such a path exists,
   collapse it into the single build.
2. **One authoritative final valuation/action per position.** If a position
   can currently end up represented by more than one valuation or more than
   one action entry (e.g. through `position_valuations` construction in
   `context_builder.py` plus a second derivation later in
   `scheduled_analysis.py`/`portfolio_adjudicator.py`), fix this
   structurally — one position, one final valuation, one final action — not
   by de-duplicating at render time.
3. **Add explicit final vs. original ratio and decision provenance fields**
   to the approved-action record (name and place them wherever the existing
   action record already lives, e.g. `portfolio_adjudicator.py`'s decision
   dataclasses): `final_ratio`, `original_ratio` (already partially present
   — confirm and make consistent), `decision_reason`, `evidence_summary`,
   `settlement_rule`, `executable_quantity`, `execution_status`. These must
   flow through unchanged into `user_view` — not be re-derived or
   re-labeled in presentation.
4. **Restructure cash buckets** from the current `cash_schedule`
   (`immediate_cash_cny` / `settling_cash_cny` / `strategic_exit_value_cny` /
   `locked_value_cny`, see `stocks/DATA_MODEL.md`) into five explicit,
   exactly-named buckets: `available_now`, `confirmed_settling`,
   `planned_release`, `strategic_exit`, `locked`. Every cash-bearing position
   must land in exactly one bucket, derived deterministically — not
   estimated in presentation.
5. **Derive settlement deterministically from liquidity/redemption facts.**
   `settlement_rule` (item 3) and bucket placement (item 4) must come from
   each position's `liquidity` (`tier`, `redemption_rule`, `lockup_until`,
   `maturity_date` — see `stocks/DATA_MODEL.md` `Position.liquidity`) and
   institution settlement timing (`_settlement_timing_for_institution` in
   `scheduled_analysis.py`), not from a display-time guess.
6. **Exact, enumerated risk labels.** Replace any free-text or ad hoc risk
   wording surfaced in `user_view`/`assistant_brief.risk` with a fixed,
   documented label set (extend `risk_label()` in `presentation.py`
   accordingly); no label may be invented at render time.
7. **Minimum trade units; defer impossible fractional actions.** Where an
   approved action's computed quantity is not a valid tradable unit for its
   market/instrument (e.g. A-share round lots), the action must be deferred
   or adjusted to the nearest valid unit with that adjustment recorded in
   `decision_reason` — never silently presented as a fractional-share
   instruction. There is currently no minimum-trade-unit handling in the
   codebase (verified by grep) — this is new logic, not a fix to existing
   logic.
8. **Move financial-amount derivation out of presentation.** Today
   `estimated_amount_cny` / `amount_is_estimate` are computed inside
   `presentation.py` (`build_user_view`, ~line 605) — the presentation layer
   is doing financial math, not pure projection. Move that computation into
   the decision layer (`portfolio_adjudicator.py`, alongside where
   `original_ratio` is already set) so `presentation.py` only projects
   already-computed, already-authoritative amounts.
9. **Structurally address duplicate position valuations.** Same
   position/instrument must not be representable by two different valuation
   numbers reaching `user_view` through different code paths (e.g. one via
   `position_valuations` and one via an action card's own embedded facts).
   Pick one source of truth and make the other a pure reference to it.

Because items 3–4 add/rename schema fields, this task's own completion must
include a synchronized update to `stocks/DATA_MODEL.md` (the
`portfolio_decision.user_view` and cash-bucket sections) and, if a contract's
consumer changes, `docs/contracts/README.md` — per `AGENTS.md`'s "docs
updated once, after the implementation is stable."

## Non-goals (explicitly out of scope for this task)

- Snapshot v2 / `UnifiedAnalysisSnapshot` work of any kind.
- Advisory v2, `advisory_synthesizer.py`, or any Advisory-shadow change.
- Building or changing the Advisory Validator (`advisory_contract.py` or a
  future `advisory_validator.py` — see `ARCHITECTURE.md` §5.6). This task is
  entirely inside the existing production rule/adjudicator/presentation
  chain.
- Any change to the shadow migration path (`scripts/run_shadow_advisory.py`,
  `scripts/compare_advisory_paths.py`, `.local/advisory_shadow/`).
- Re-wiring `scripts/audit_report_quality.py` into the push entrypoint (the
  previously-drafted push-time gate). That may become a later task once this
  structural fix lands, but is not part of this one.
- Changing any of Task 0's already-landed fixes in
  `stocks/engine/outlook_validation.py` or the session/dedup logic in
  `stocks/engine/scheduled_analysis.py`'s `MarketSessionCalendar` /
  `RunArtifactStore` (currently in the dirty working tree, out of scope
  here).
- Adding a `report_mode` config toggle — none exists today (see
  `STATUS.md`) and this task does not create one.
- A general rewrite of `portfolio_adjudicator.py`'s rule logic, signal
  thresholds, or `quant_action.py` — only the fields and structure named in
  Scope items 1–9.

## Likely files

- `stocks/engine/portfolio_adjudicator.py` — final action/decision
  dataclasses; add `final_ratio`/`decision_reason`/`evidence_summary`/
  `settlement_rule`/`executable_quantity`/`execution_status`; move amount
  derivation here; minimum-trade-unit / deferral logic.
- `stocks/engine/presentation.py` — `build_user_view`, `_cash_view`,
  `risk_label`; strip amount computation, project the five cash buckets,
  enumerate risk labels.
- `stocks/engine/scheduled_analysis.py` — orchestration; confirm single
  build site for `user_view`/`outlook`/`outlook_delta`, remove any
  post-build mutation found.
- `stocks/engine/context_builder.py` — `position_valuations` construction;
  fix for structural duplicate valuations if the second source lives here.
- `stocks/domain/models.py` — only if a new field needs a dataclass home
  (e.g. on `Liquidity`/`Position`) to support deterministic settlement
  derivation.
- `stocks/DATA_MODEL.md`, `docs/contracts/README.md` — schema/consumer sync,
  done once at the end per Scope's closing note.
- `tests/engine/test_portfolio_adjudicator.py`,
  `tests/engine/test_presentation.py`,
  `tests/engine/test_context_builder.py`,
  `tests/engine/test_scheduled_analysis.py`, `tests/test_push_payload.py` —
  focused tests for the above.
- `tests/test_run_push_report.py` — regression only; this task must not
  change `run_push_report.py`'s behavior beyond consuming the corrected
  `user_view` shape.

Do not create new files outside this list without first checking whether an
existing module already owns that responsibility.

## Focused tests

```bash
.venv/bin/pytest -q -o 'addopts=' \
  tests/engine/test_portfolio_adjudicator.py \
  tests/engine/test_presentation.py \
  tests/engine/test_context_builder.py \
  tests/engine/test_scheduled_analysis.py \
  tests/test_push_payload.py \
  tests/test_run_push_report.py
```

Add new focused tests per scope item — at minimum: one test proving
`user_view` is built exactly once with no post-build mutation path, one
proving a single valuation source per position, one per new field
(`final_ratio`/`decision_reason`/`evidence_summary`/`settlement_rule`/
`executable_quantity`/`execution_status`), one per cash bucket boundary
condition, one for minimum-trade-unit deferral, and one proving
`presentation.py` no longer computes amounts (e.g. assert the amount it
projects is read verbatim from the decision-layer record, not recomputed).

## Real smoke (two required)

Against real, already-generated artifacts (read-only where possible; live
network only if regenerating is necessary to exercise the new fields):

```bash
.venv/bin/python -m stocks.adapters.cli --scheduled-run-latest cn_after_close
.venv/bin/python -m stocks.adapters.cli --scheduled-run-latest us_after_close
```

For each, confirm `portfolio_decision.user_view` shows exactly one action
per position with the new fields populated and consistent (no
`final_ratio`/`original_ratio` contradiction, no two valuations for the same
position, cash buckets sum to the same total the old `cash_schedule` would
have reported), and that a full push render
(`scripts/run_push_report.py --session <session> --artifact-root
.local/scheduled_runs/latest --payload-root /tmp/task001-smoke-payload`)
still exits `0` and produces readable Feishu-formatted text.

## Audit stop criterion

Re-run the existing (unmodified) audit tool against the same historical
artifact set used for Task 0's reported 117 P0 / 17 P1 baseline:

```bash
.venv/bin/python scripts/audit_report_quality.py --start <same-start> --end <same-end>
```

This task is done only when the P0 findings belonging to the defect classes
this task targets — ratio/text drift
(`check_final_ratio_text_consistency`), unsettled proceeds shown as
available cash, and non-canonical settlement/risk vocabulary — are gone from
that re-run, and the new P0/P1 counts (whatever they are) are recorded in
`STATUS.md` next to the original 117/17 baseline for comparison. Do not
change any `check_*` function to make this pass — if a targeted defect class
still fires, the structural fix is incomplete, not the audit.

## Stop criteria

Task is done when:

- All nine scope items are implemented and each has a passing focused test.
- The full focused-test command above passes with no regressions.
- Both real smokes above produce a single, internally consistent action per
  position and an unchanged-format, exit-`0` push render.
- The audit stop criterion above is met and recorded in `STATUS.md`.
- `stocks/DATA_MODEL.md` (and `docs/contracts/README.md` if a consumer
  changed) reflect the new field/bucket shape, done once at the end.
- `STATUS.md` is updated once, after the above, moving this task from
  "pending" to done with the exact verification evidence (mirroring how Task
  0's evidence is recorded).

Do not start any Task 2+ work in this session, even if it becomes obvious
what it should be.
