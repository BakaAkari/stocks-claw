# TASK-M3 — feedback loop (advice outcomes flow back as evidence)

## Objective

Close the feedback loop: the user marks each confirmed advice
(`accepted | partial | rejected | deferred`), the marks land in the advice
ledger, a weekly rollup summarizes them, and both the per-record feedback
and the rollup flow into the next Outlook run as evidence. Not an
auto-tuner — feedback informs the LLM analyst's judgment, never rewrites
rules or parameters.

## Background facts (verified 2026-08-01)

- `AdviceRecord` (`stocks/domain/models.py` ~line 1034): created_at,
  instruments, direction, rationale_summary, based_on, boundary, triggers,
  actions. **No feedback field today.** Files live in `.local/advice/`
  named by safe timestamp of `created_at`.
- `DataPersistence` (`stocks/engine/persistence.py`): `save_advice`
  (rolling trim at `max_advice_records=30`), `list_advice`,
  `load_recent_advice(count=3)`; records sorted by created_at desc.
  `advice_dir` = `<local_data_dir>/advice` (sandboxable via
  `STOCKS_PATHS__LOCAL_DATA_DIR`).
- `recent_advice` already flows into `AnalysisContext` and is enriched by
  `advice_review.attach_advice_performance` / `attach_execution_review`
  (price-since-advice facts + execution status), then rendered into
  `raw_prompt_input`. **It does NOT reach `build_unified_snapshot` /
  the advisory prompt today** (verified by grep).
- TASK-002 retired 2026-07-31; its feedback-ledger role folds into this
  task (PLAN.md decision log).

## Scope

1. **Model** (`stocks/domain/models.py`): `AdviceRecord` gains optional
   `feedback: Optional[dict]` — `{status, note, marked_at}` with
   `status ∈ {accepted, partial, rejected, deferred}`; validated in
   `__post_init__`; round-trips through `from_dict` / `to_dict`.
   Backward compatible (absent = unmarked).
2. **New `stocks/engine/advice_feedback.py`**:
   - `FEEDBACK_STATUSES` frozenset; `make_feedback(status, note) -> dict`
     (validates, stamps `marked_at`).
   - `compute_feedback_rollup(records, *, window_days=7, now=None) -> dict`:
     window filter on `created_at`; counts per status + `unmarked`;
     `marked_total`, `acceptance_rate` (accepted + 0.5·partial over
     marked); `recent_rejection_notes` (up to 3 newest notes from
     rejected/partial records); `oldest_unmarked` (created_at of the
     oldest unmarked record in window, nudge to mark). Pure function.
3. **Persistence** (`stocks/engine/persistence.py`):
   `update_advice_feedback(advice_ref, feedback) -> dict` — resolve
   `advice_ref`: `"latest"` → newest file, else prefix-match on the
   safe-timestamp filename stem / created_at (ambiguous → error);
   rewrite the record JSON in place; return updated record dict.
   This amends user-confirmed memory and is only reachable behind the
   CLI `--confirmed` gate.
4. **Engine** (`stocks/engine/__init__.py`):
   `mark_advice_feedback(advice_ref, status, note="") -> dict` (status
   vocabulary enforced) and `advice_feedback_rollup(window_days=7) -> dict`.
5. **CLI** (`stocks/adapters/cli.py`):
   - `--advice-feedback REF STATUS [--note "..."]` — write, requires
     `--confirmed` (same gate as other memory writes).
   - `--advice-rollup [DAYS]` — read-only rollup (default 7 days).
6. **回流 into the advisory snapshot** (`stocks/engine/unified_snapshot.py`):
   `_build_recent_advice_facts(context)` — for each `context.recent_advice`
   record: one `FactRef` (metric `advice_outcome`, value = compact
   direction+feedback+performance summary) appended to the snapshot's
   `profile` facts with source `system:advice_ledger`; plus one
   `advice_feedback_rollup_7d` fact when any feedback exists in the
   window. Records without feedback still surface (status `unmarked`) so
   the analyst sees the coverage gap.

## Non-goals (must not do in this task)

- Feishu inline feedback buttons (CLI first; Feishu is a delivery-layer
  follow-up).
- Any parameter auto-tuning, threshold changes, or rule rewrites driven
  by feedback.
- MCP wiring of the feedback channel (same follow-up batch as A1 MCP).
- W1 watchlist, M4 constraints.
- Weekly rollup *scheduling* (the rollup is computed on demand / at
  context-build time; no new cron surface).

## Acceptance

Tests must prove:

1. Feedback round-trips the model and rejects invalid statuses.
2. Persistence updates the right file for `latest` and for a created_at
   prefix; ambiguous prefix → error; unknown ref → error; other files
   untouched.
3. Engine rejects invalid status; CLI without `--confirmed` refuses the
   write; with `--confirmed` the mark lands and `--advice-list` shows it.
4. Rollup counts/rates/window/rejection-notes correct on a crafted set;
   empty ledger → honest zero-state rollup.
5. `build_unified_snapshot` on a context with marked recent_advice
   contains `advice_outcome` facts (and the rollup fact); unmarked
   records surface as `unmarked`; no recent_advice → no advice facts.
6. Focused tests, full suite, ruff, compileall, diff-check.

## Files likely to touch

- `stocks/domain/models.py` — AdviceRecord.feedback.
- `stocks/engine/advice_feedback.py` — new.
- `stocks/engine/persistence.py` — update_advice_feedback.
- `stocks/engine/__init__.py` — two delegates.
- `stocks/adapters/cli.py` — two flags + --note.
- `stocks/engine/unified_snapshot.py` — advice facts.
- `docs/contracts/README.md` — AdviceRecord row: consumer cell gains the
  feedback channel.
- `tests/engine/test_advice_feedback.py` — new; plus CLI test updates.

## Smoke check

```bash
# read-only against the real ledger:
.venv/bin/python -m stocks.adapters.cli --advice-list
.venv/bin/python -m stocks.adapters.cli --advice-rollup

# write path on a sandbox advice dir only:
STOCKS_PATHS__LOCAL_DATA_DIR=/tmp/m3-sandbox \
  .venv/bin/python -m stocks.adapters.cli \
  --advice-feedback latest accepted --note "smoke" --confirmed
```

The real `.local/advice/` is never modified by smoke; the sandbox run
must show the mark landing and the rollup reflecting it. Record the
outcome in `STATUS.md`.
