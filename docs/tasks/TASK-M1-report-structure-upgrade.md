# TASK-M1 — report structure upgrade (six-section)

## Objective

Bring the production push report from a five-section concise trading report
to the six-section shape required by `stocks/VISION.md` §2.3, so that
already-computed internal fields (research candidates, post-trade
projection, truth-gate audit trail, richer data notes) reach the user. This
is a **report-layer** change; it does not modify strategy, action
selection, valuation, financial memory, Advisory, cron, or artifacts.

## Scope

Only the trading-session rendering in `scripts/build_push_payload.py` and
its tests. Intelligence rendering remains unchanged. Do not change:
- portfolio adjudicator, factor rules, quant action, scheduled analysis;
- outlook synthesizer (that's M2);
- feedback / advice ledger (that's M3);
- push validator's freshness / integrity gates (retain E1 behavior).

## Required trading report structure

Exactly these six sections, in this order:

1. `本窗口变化`
2. `走势研判`
3. `可执行动作`
4. `提前布局`
5. `禁止与延后`
6. `组合与检查点`

The title line may precede them.

### 1. 本窗口变化

- If `assistant_brief.outlook_delta` contains material changes, render at
  most 3 concise delta lines using existing `_render_delta_changes`
  semantics.
- Otherwise render one line: `本窗口未发现需要改变计划的新证据`.
- Do not dump full scenario trees, asset views, sector views, or full
  Outlook.

### 2. 走势研判

- If `assistant_brief.outlook` has `status: "unavailable"` (current
  production state until M2 lands), render one line: `中长期研判暂不可用，
  等待 M2 上线` — do **not** leak internal English error strings like
  `outlook synthesizer disabled or not configured`.
- If Outlook exists and has a short-form summary line (produced by the
  legacy `structured_outlook` path when it succeeds), render at most 2
  lines.
- If Outlook exists but has `validation_errors`, render one line:
  `中长期研判本期未通过校验，暂不输出`.

M2 will replace this section's rendering with a full LLM-Advisory
short-term + medium-term structure. This task does **not** implement that.

### 3. 可执行动作

- Render up to 3 `instruction_card.actions`.
- Each action should fit in 2–3 lines and include: action + instrument,
  final percentage, executable quantity when available, estimated amount
  with estimate label, platform/settlement, cancel condition.
- When `instruction_card.status == "manual_review"`, this section must
  still describe **the choice space the user needs to resolve**, not
  duplicate the "禁止与延后" content. Concretely: for each conflict, name
  the instrument, name the direction conflict in one line, then list the
  **rule-driven reference values** — reference `ratio`, reference
  `executable_quantity` if any, reference `estimated_amount_cny` if any —
  as a bulleted `参考:` sub-line. This gives the user a starting point to
  decide manually.
- If no action and no manual-review conflict, render status + up to 2
  no-action reasons.

### 4. 提前布局

New section. Reads from `assistant_brief.research` (already populated with
`display_label`, `action_hint`, `reassess_after`, and — via the underlying
research candidate object — a `composite_score`).

- Render top 2-3 candidates by `composite_score` when it exists, otherwise
  in list order.
- Each candidate: one line with `display_label`, one line with
  `action_hint` (may be truncated to ≤60 chars), and if `reassess_after`
  differs from the report's own next checkpoint, one line noting it.
- If more candidates exist beyond the top 2-3, append one line:
  `另有 N 个候选，详情待人工进一步筛选`.
- If `research` is empty, render one line: `本窗口无值得提前布局的候选`.

Never promote a research candidate into an action.

### 5. 禁止与延后

- Combine and deduplicate `instruction_card.no_action_reasons`,
  `assistant_brief.do_not_do`, stale/data notes, and risk suspension.
- When `instruction_card.status == "manual_review"`, the conflict content
  already shown in §3 must **not** be repeated here — this section only
  covers reasons unrelated to the pending manual choices (locked assets,
  data staleness on other instruments, risk-suspended categories, etc.).
- Render at most 4 lines, sorted by decision impact: stale/deferred/manual
  review first, risk suspension second, locked/unavailable third, generic
  long-term/research-only last.
- Do not render conflict count statistics (`减仓: 5 项` etc.).
- Do not repeat the research count (it's now §4).

### 6. 组合与检查点

Merges the previous "组合影响" and "下一检查点" sections into one, since M1
frees a section slot.

- Render current risk label + transition.
- Render up to 2 actual risk reasons.
- Render cash compactly, with these collapse rules:
  - `available_now` always shown;
  - `confirmed_settling` hidden when 0;
  - `planned_release` shown when non-zero;
  - `locked` shown when non-zero;
  - `strategic_exit` shown **only when there is an executable sell action
    or an approved-but-review-pending sell in `suppressed_actions`**;
    labeled `卖出后可释放`;
  - `safety_buffer` shown last with `(不计入可用)` suffix.
- **Data-notes capital gaps** — for each `data_notes` entry whose text
  mentions `¥` or currency amounts, render as its own line.
- If `post_trade_projection` exists and the report has at least one
  executable action, render one line: `执行后估算: 可用 ¥X → ¥Y, 权益比例
  A% → B%` (source: `post_trade_projection` fields; if any required field
  is missing, omit this line entirely — no fabrication).
- Next checkpoint line: exactly one checkpoint from
  `instruction_card.next_checkpoint`.
- Add one concise condition if available: first action cancel condition,
  risk release condition, or first data note (non-capital-gap kind).

## Truth-gate audit trail (cross-section addition)

When `validate_push_truth` rejects an action that had passed the
adjudicator (data-staleness rejection, conflict rejection, etc.), the
report must retain the **rule-driven proposed value** so the user has a
reference:

- In §3 `可执行动作`, for each rejected-but-shown item, append a `参考:`
  sub-line: `参考: <signal_type> <ratio>%<, 参考数量 Q><, 参考金额 ¥A>`.
- Preserve E1's rejection reason as the primary line — this is an
  additive audit trail, not a rewrite.

## Size and language gates

- Trading report ≤ 55 non-empty lines and ≤ 1800 Chinese/ASCII characters
  for the current real artifacts (loosened from E2's 45/1400 because §4
  now carries real content).
- No internal English validator errors, internal IDs, machine enums,
  tables, or code blocks.
- Do not use headings other than bold Feishu headings.
- Preserve E1 `validate_push_truth` and `validate_payload_text` behavior
  except: `validate_payload_text` must accept the new heading `走势研判`
  and `提前布局` and reject their absence, and must no longer accept the
  old 5-section report (`组合影响` and `下一检查点` as separate headings).

## Acceptance

Tests must prove:

1. Exactly six section headings in required order.
2. No legacy 5-section-only assertions leak through; specifically, `组合
   影响` and `下一检查点` as separate top-level headings must be rejected.
3. Up to 3 actions in §3; quantity appears when available; no repeated
   checkpoint per action; `参考:` sub-line appears for every truth-gate
   rejection that retains a proposed ratio.
4. §4 renders 2-3 top candidates by score with `action_hint`, plus an "另
   有 N" tail if applicable; renders `本窗口无值得提前布局的候选` when
   research is empty.
5. §5 excludes items already covered in §3's manual-review branch.
6. Cash rendering collapses `confirmed_settling=0`, `planned_release=0`,
   and hides `strategic_exit` unless there is an actual sell action;
   `safety_buffer` always trailing with `(不计入可用)`.
7. `data_notes` entries with currency amounts render as their own lines.
8. `post_trade_projection` line appears iff both projection and executable
   action exist.
9. Current regenerated `cn_after_close` output meets ≤ 55 non-empty lines
   and ≤ 1800 chars.
10. Focused push/run tests, full suite, ruff, compileall, diff-check.

## Files likely to touch

- `scripts/build_push_payload.py` — main change surface.
- `scripts/run_push_report.py` — verify E1 truth gate still wraps the new
  renderer.
- `stocks/engine/presentation.py` — may need helper additions if the six-
  section renderer needs new small projections, but no semantic change.
- `tests/test_push_payload.py`, `tests/test_run_push_report.py`,
  `tests/engine/test_presentation.py` — updated assertions and new cases.

## Non-goals (must not do in this task)

- Implement M2 outlook mainline. `走势研判` stays as a fallback line.
- Modify feedback loop (M3).
- Change adjudicator, factor rules, quant action, scheduled analysis.
- Add new fields to `AnalysisContext`, `portfolio_decision`, or
  `assistant_brief`.
- Commit, push, deploy, operate cron, or begin other tasks.
- Change E1 `validate_push_truth` behavior beyond the payload-text
  heading update.

## Smoke check

After implementation and focused tests pass, run:

```bash
.venv/bin/python -m stocks.adapters.cli \
  --scheduled-run-session cn_after_close \
  --now "2026-07-30T15:00:00+08:00" --force --output json

.venv/bin/python scripts/run_push_report.py \
  --session cn_after_close \
  --now "2026-07-30T15:05:00+08:00" \
  --payload-root /tmp/stocks-claw-m1-check
```

Then read the rendered payload and manually verify it matches the six-
section shape and the acceptance criteria above. Save a copy of the
rendered output alongside the STATUS.md update.

Do not commit until the user reviews the sample output.

## Outcome (2026-07-31)

Landed at `382207b`. The six-section renderer skeleton already existed
(landed with `503bad2`); this task closed the remaining spec gaps: 3-line
delta cap, sanitized unavailable-outlook fallback, per-conflict 参考:
audit lines (with presentation now emitting `suppressed_actions_reference`
— previously a write-never field), composite_score ordering, checkpoint-
aware 复核 lines, pending-sell strategic_exit visibility, and a
presentation-side score rounding fix for the number gate. Verified on a
regenerated real `cn_after_close` 2026-07-30 report: six sections in
order, 40 non-empty lines (≤55), 697 Chinese chars (≤1800),
`validate_payload_text` clean. Sample: `.local/m1-sample-cn_after_close-20260730.md`.
Full suite 1272 passed / 7 skipped; ruff / compileall / diff-check clean.
