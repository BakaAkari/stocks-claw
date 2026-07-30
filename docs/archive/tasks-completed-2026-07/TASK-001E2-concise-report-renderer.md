# TASK-001E2 — concise decision report renderer

## Objective

Replace the current 70–130 line database-dump trading report with a concise decision report that reduces user decision cost. E1 truth gates are complete and must remain intact.

## Scope

Change only the trading-session rendering in `scripts/build_push_payload.py` and its tests. Intelligence rendering remains unchanged. Do not change strategy, action selection, valuation, financial memory, Advisory, cron, or artifacts.

## Required trading report structure

Exactly these five sections, in this order:

1. `本窗口变化`
2. `可执行动作`
3. `禁止与延后`
4. `组合影响`
5. `下一检查点`

The title line may precede them.

### 1. 本窗口变化

- If `assistant_brief.outlook_delta` contains material changes, render at most 3 concise delta lines using existing `_render_delta_changes` semantics.
- Otherwise render one line: `本窗口未发现需要改变计划的新证据`.
- Do not dump full scenario trees, asset views, sector views, or full Outlook. If successful Outlook exists and has a summary, it may contribute one short summary line only when no delta exists. If unavailable, one line `中长期研判暂不可用` is enough; do not expose internal validation errors such as `narrative outlook has no source_refs`.

### 2. 可执行动作

- Render up to 3 `instruction_card.actions`.
- Each action should fit in 2–3 lines and include: action + instrument, final percentage, executable quantity when available, estimated amount with estimate label, platform/settlement, and cancel condition.
- Never repeat `next_checkpoint` per action.
- If no action, render status + up to 2 no-action reasons.

### 3. 禁止与延后

- Combine and deduplicate `instruction_card.no_action_reasons`, `assistant_brief.why` entries that are not the executable actions' own `reason_summary`, `assistant_brief.do_not_do`, stale/data notes, and risk suspension.
- Render at most 4 lines, sorted by decision impact: stale/deferred/manual review first, risk suspension second, locked/unavailable third, generic long-term/research-only last.
- Do not render conflict count statistics such as `减仓: 5 项`.
- Do not render the full research list. At most one line: `研究候选 N 个，当前均不构成交易动作`.

### 4. 组合影响

- Render current risk label + transition.
- Render up to 2 actual risk reasons.
- Render cash compactly in one line: available_now, confirmed_settling, planned_release (when non-zero), locked. Omit strategic_exit unless there is an executable sell action; then include it as `卖出后可释放`.
- If risk evidence is missing and the system says review, show it honestly.

### 5. 下一检查点

- Render exactly one checkpoint from `instruction_card.next_checkpoint`.
- Add one concise condition if available: first action cancel condition, risk release condition, or first data note.

## Size and language gates

- Trading report <= 45 non-empty lines and <= 1400 Chinese/ASCII characters for the current real artifacts.
- No internal English validator errors, internal IDs, machine enums, tables, or code blocks.
- Do not use headings other than bold Feishu headings.
- Preserve E1 `validate_push_truth` and `validate_payload_text` behavior.

## Acceptance

Tests must prove:

1. Exactly five section headings in required order.
2. No legacy headings: `交易指令卡`, `私人投资助理`, `为什么这样安排`, `待人工确认的信号分类`, `仅供观察`, `中长期研判`, `资产类别`, `行业观察`, `基准情景`, `乐观情景`, `风险情景`.
3. No conflict count lines.
4. Up to 3 actions; quantity appears when available; no repeated checkpoint per action.
5. Research compressed to one count line max.
6. Unavailable Outlook hides internal errors.
7. Current regenerated `cn_post_open` output meets <=45 non-empty lines and <=1400 chars.
8. Run focused push/run tests, full suite, Ruff, compileall, diff-check; render the real current `cn_post_open` artifact and inspect it.

Do not commit, push, deploy, operate cron, or begin other tasks.
