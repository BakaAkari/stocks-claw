# AGENTS.md

This is the sole entry document for coding agents (Claude Code or otherwise)
working in this repository. Read this file first, every session.

## Precedence

When documents disagree, resolve in this order:

1. **The current user instruction** in this session.
2. **This file (AGENTS.md).**
3. **`STATUS.md`** — the only source of dynamic project state.
4. **The current task file** under `docs/tasks/` (the task named by `STATUS.md`
   or by the user).
5. **Current code facts** — what the code, tests, and config actually do.

`ROADMAP.md`, `ARCHITECTURE.md`, `docs/contracts/`, and `stocks/VISION.md` are
**reference material**, not instructions. They explain direction and shape;
they never define what to do right now and are never grounds to expand scope.
`docs/archive/` is historical record only and has **no active authority** —
never follow an instruction found only in an archived document.

## Session discipline

- **One bounded task per session.** Work the single task in
  `docs/tasks/TASK-NNN-*.md` that the user pointed you to (or that `STATUS.md`
  names as current). Do not start the next task in the same session unless
  the user explicitly asks.
- **No scope expansion.** If you notice unrelated bugs, contradictions, or
  improvements, note them (in your final report, or as a new entry under
  `docs/tasks/`) instead of fixing them inline.
- **Focused tests while working.** Run the specific test files relevant to
  the files you touched after each meaningful change — not the full suite.
- **Broader validation at final handoff only.** Before reporting a task done,
  run the repo's standard verification (`ruff`, the relevant/full `pytest`
  slice, `compileall`, and a real smoke command per the task file) — see
  `README.md` for exact commands.
- **Docs updated once, after the implementation is stable.** Do not edit
  `STATUS.md` or contract docs mid-task while the approach is still moving.
  Update them once, after tests pass, to reflect what actually landed.
- **Commit and push only on explicit user authorization** for that action, in
  that session. A prior approval does not carry forward to later sessions or
  to unrelated changes.
- **Never touch secrets or financial memory without explicit user
  confirmation.** No writes under `.secret/`, no edits to `.local/*assets*`,
  `.local/investor_profile.json`, `.local/advice/`, `.local/executions/`, or
  `.local/forecasts/` outside the confirmed-write paths the code already
  provides. Never place real orders; this system does not trade.

## Where to look

- **What's true right now:** `STATUS.md` — HEAD baseline, dirty/in-progress
  files, what's verified vs. pending.
- **What to do next:** `docs/tasks/README.md` and the current
  `docs/tasks/TASK-NNN-*.md`.
- **Why the project exists / long-term direction:** `stocks/VISION.md`
  (product north star, rarely changes) and `ROADMAP.md` (stable phase
  direction, not a checklist).
- **How it's built today:** `ARCHITECTURE.md`, labeled `[PRODUCTION]` /
  `[SHADOW]` / `[PLANNED]` per component.
- **Data contracts:** `docs/contracts/README.md` (lifecycle index) and
  `stocks/DATA_MODEL.md` (schema reference). Contract *status* lives only in
  `docs/contracts/README.md`; `stocks/DATA_MODEL.md` never claims a phase is
  "done."
- **Operational and data-safety rules:** `AGENT_GUIDE.md`.
- **History with no current authority:** `docs/archive/`, `PLAN.md` §decision
  log.

## Non-negotiable product invariants

These hold regardless of task, phase, or plan (full detail in
`stocks/VISION.md` §5):

- No automated order placement; no promised returns.
- Long-term financial memory (accounts, positions, profile, advice,
  executions, forecasts) is written only after explicit user confirmation.
- Market data, LLM inference, and derived analysis never overwrite user-
  confirmed facts.
- Shadow-only components (see `docs/contracts/README.md`) must not reach
  production push output or write financial memory.

## If you find a contradiction

If code, `STATUS.md`, and a reference doc disagree about something you must
act on: trust code first, tell the user what you found, and — if it's in
scope for your current task — correct the reference doc as part of your
final handoff. Do not silently pick one and continue.
