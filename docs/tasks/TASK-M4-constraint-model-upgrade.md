# TASK-M4 — constraint model upgrade (irreversibility, pools, hard caps)

> Status: **backlog candidate** — do not start before the current task
> named in `STATUS.md` closes. Sequencing vs M2/M3 is re-evaluated when
> M1 closes. Requirements rationale:
> `docs/analysis/kimi-report-constraint-comparison-2026-07-31.md`.

## Objective

Extend the constraint model from "four bucket min/max ratios"
(`stocks/config/portfolio_constraints.json` today) to express three
constraint semantics that real portfolio decisions depend on, so the
adjudicator and the report stop producing advice that is infeasible or
irreversible under the user's actual constraints:

1. **Irreversibility** — a position that cannot be bought back once sold
   (e.g. purchase-restricted funds) must make every sell suggestion carry
   an irreversibility warning, and soft "take partial profit" suggestions
   on such positions must be re-justified against that irreversibility.
2. **Segregated pools** — positions belong to named pools (e.g. domestic
   CNY pool vs an overseas broker pool isolated by capital controls).
   Capital-allocation logic must never propose funding a purchase in one
   pool with proceeds or cash from another pool, and each pool is checked
   against its own ratio constraints.
3. **Hard caps** — a category/position cap flagged `hard: true` means
   "breach ⇒ must reduce", producing a mandatory reduce candidate with a
   concrete reason, distinct from the current soft min/max hints.

## Scope

- Constraint config schema extension + validation (fail closed on
  malformed config — unknown keys rejected, types checked).
- Adjudicator / capital-allocation consumption of the new semantics.
- Report rendering of the new constraint outcomes (warning lines and
  must-reduce candidates projected through the existing user_view path,
  respecting the six-section report contract).
- Tests + smoke.

Do **not** change: factor rules, quant action thresholds, outlook
synthesizer (M2), feedback ledger (M3), push freshness/integrity gates
(E1 behavior retained).

## Config schema (proposed, validate in `config_loader`)

```jsonc
{
  "pools": {
    "domestic": { "label": "国内池", "currency": "CNY" },
    "overseas": { "label": "海外封闭池", "currency": "USD",
                  "isolated": true }   // isolated: no cross-pool funding
  },
  "position_pool": { "<position_id>": "domestic" },  // default: domestic
  "bucket_limits": {                    // existing soft min/max, per pool
    "domestic": { "权益": {"min": 0.25, "max": 0.65}, "…": {} },
    "overseas": { "权益": {"min": 0.0, "max": 1.0} }
  },
  "hard_caps": [
    { "pool": "domestic", "category": "纳指QDII", "max": 0.12,
      "on_breach": "must_reduce",
      "reason": "限购无法买回，超上限必须减" }
  ],
  "position_restrictions": {
    "<position_id>": {
      "no_buyback": true,
      "restriction_note": "平台每日限购极低额度，卖出后事实不可买回"
    }
  }
}
```

Exact key names may be adjusted during implementation, but the three
semantics (irreversibility / pool isolation / hard cap) must all be
expressible, validated, and documented in `docs/contracts/README.md`.

## Required behavior

1. **Hard-cap breach → mandatory reduce candidate.** When a hard-capped
   category exceeds its cap within its pool, the adjudicator emits a
   reduce candidate for the largest position(s) in that category with
   `reason` naming the cap, even when no technical signal fires. This
   candidate flows through the normal manual-review/truth-gate path —
   it is a proposal, never an auto-execution.
2. **Sell suggestion on a `no_buyback` position carries an irreversibility
   warning.** The warning text (from `restriction_note`) must reach the
   report's action or manual-review section verbatim. Soft-profit-taking
   suggestions (partial sell with intent to re-enter) on such positions
   must be suppressed or re-labeled: re-entry is not available.
3. **No cross-pool funding.** `_build_capital_allocation` and the cash
   schedule must compute available cash **per pool**; an add action in
   pool A must never count pool B's cash/proceeds as funding source when
   pool B is `isolated`.
4. **Per-pool ratio checks.** Bucket min/max evaluation runs per pool;
   the report's portfolio section labels which pool a breach belongs to.
5. **Fail closed.** Malformed new config (unknown keys, bad types,
   references to undefined pools) → config load error, not silent ignore.
6. **Confirmed writes.** Any CLI/config path that mutates these
   constraints goes through the existing user-confirmation write path;
   constraints are financial memory, never overwritten by market data or
   LLM output (product invariant).

## Acceptance

Tests must prove:

1. Hard-cap breach with no technical signal still yields a reduce
   candidate naming the cap; below-cap yields none.
2. Sell on `no_buyback` position renders the `restriction_note` warning
   in the push text; partial-profit-taking suggestion on it is suppressed
   or re-labeled.
3. Cross-pool funding never appears: with isolated overseas pool holding
   large cash and domestic pool cash-poor, a domestic add proposal's
   funding computation excludes overseas cash.
4. Ratio breach is reported with its pool label.
5. Malformed config (unknown key / undefined pool reference) fails
   config load with a clear error.
6. Legacy config shape (current four-bucket file, no new keys) still
   loads and behaves exactly as today (backward compatibility).
7. Focused tests, full suite, ruff, compileall, diff-check.

## Files likely to touch

- `stocks/config/portfolio_constraints.json` — schema extension
  (user-confirmed content; repo carries only the shape/example).
- `stocks/engine/config_loader.py` — schema validation, defaults.
- `stocks/engine/portfolio_adjudicator.py` — hard-cap candidates,
  irreversibility warnings.
- `stocks/engine/scheduled_analysis.py` — per-pool capital allocation
  and cash schedule.
- `scripts/build_push_payload.py` — render warning lines / pool labels
  (coordinate with M1's six-section contract; if M1 is still open,
  render through its section rules).
- `docs/contracts/README.md` — constraint contract status update.
- `tests/engine/test_portfolio_adjudicator.py`,
  `tests/engine/test_config_loader.py`,
  `tests/engine/test_scheduled_analysis.py`,
  `tests/test_push_payload.py`.

## Non-goals (must not do in this task)

- Multi-period glide-path planning (e.g. "6-month staged buying
  schedule"). That depends on M3 feedback data and is a separate future
  task.
- Outlook mainline (M2), feedback loop (M3), report structure (M1).
- Auto-tuning any threshold from outcomes.
- Execution surface of any kind; all outputs remain proposals.
- Commit, push, deploy, or operate cron without explicit user
  authorization in that session.

## Smoke check

After implementation and focused tests pass, run:

```bash
.venv/bin/python -m stocks.adapters.cli \
  --scheduled-run-session cn_after_close \
  --now "2026-07-30T15:00:00+08:00" --force --output json
```

with a constraints fixture that includes: one `no_buyback` position with
a pending soft take-profit signal, one breached hard cap, and one
isolated pool with excess cash. Manually verify the rendered report
shows: the irreversibility warning, the must-reduce candidate naming the
cap, and no cross-pool funding language. Save a copy of the rendered
output alongside the STATUS.md update.
