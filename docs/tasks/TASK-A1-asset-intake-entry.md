# TASK-A1 — natural-language asset intake entry (draft → confirm → audited v2 write)

## Objective

Give the SHADOW `AssetIntakeDraft` library a user-facing entry point so the
advisory terminal's core write loop — "我买入了 5 股 AAPL，成本 301.4" →
draft → user confirm → audited write into the v2 financial memory — works
end to end. Direct hand-edits of `.local/financial_assets.json` bypass the
`_confirmed` gate and are not a supported path; legacy v1 CRUD is disabled
on v2 files, so today **no working conversational write path exists** —
this task builds it.

## Background facts (verified 2026-08-01)

- `.local/financial_assets.json` is schema_version=2 (5 accounts, 23
  positions; `accounts` + `positions` top-level keys).
- `StocksEngine.add_asset/update_asset/remove_asset` raise on v2 files
  (`_ensure_legacy_asset_writable`); the only v2 writer today is the
  one-shot `migrate_assets_v2`.
- `parse_llm_asset_intake(text, llm_client=, base_memory_hash=)` returns
  an `AssetIntakeDraft`; with no client it falls back to a fully-ambiguous
  draft (never writes). `parse_asset_intake` is the deterministic
  regex-based fallback (A-share codes + amounts only).
- `AssetIntakeWriter(get_memory_hash=, apply_change=)` enforces:
  valid token == hash(draft_id:current_memory_hash), memory unchanged
  since draft, no unresolved ambiguities, non-empty changes.
- LLM credentials resolve via
  `stocks.engine.advisory_mainline.resolve_mainline_llm_client(config)`
  (env → `.secret/openai-key.md` / `openai-base-url.md`; both present).
- `Position.from_dict` / `Account.from_dict` validate v2 structures.

## Scope

1. **New `stocks/engine/asset_intake_service.py`** — the translation and
   orchestration layer:
   - `intake_memory_hash(engine) -> str`: sha256 of the canonical JSON
     (sort_keys) of the current v2 assets file dict; stable across the
     draft→confirm gap as long as the file is untouched.
   - `build_intake_draft(engine, text, llm_client="auto") -> dict`:
     resolves the client (`resolve_mainline_llm_client`, explicit client
     in tests), builds a *memory context* block (current account_ids +
     position_ids/instrument_keys, no amounts needed beyond what the LLM
     must reference) prepended to the user text, calls
     `parse_llm_asset_intake` (deterministic `parse_asset_intake`
     fallback when no client), stamps `base_memory_hash`, generates the
     confirmation token, returns
     `{draft: <json-safe dict>, confirmation_token, ambiguities, used_llm: bool}`.
     **Never writes.**
   - `apply_intake_draft(engine, draft_dict, token) -> dict`: rehydrates
     `AssetIntakeDraft`, runs `AssetIntakeWriter.apply` with
     `get_memory_hash` and an `apply_change` that translates each change
     into v2 file edits (below), writes the file with a timestamped
     backup (same pattern as `migrate_assets_v2`), reloads engine assets,
     returns the writer result (`applied` / `rejected` with reason).
   - **v2 translation rules (conservative):**
     - `add_position`: requires `instrument_key` or `display_name`, plus
       `quantity` or `amount`; `currency` defaults from market prefix
       (`a:`/`hk:`→CNY, `us:`→USD); `account_id` must be present in the
       draft item or uniquely resolvable (exactly one existing account
       whose currency/scope matches) — otherwise the item is rejected
       with a reason, never guessed. Builds a full v2 Position dict:
       `position_id` derived from instrument_key (`us:aapl`→`us_aapl`),
       `classification` (default equity + market from prefix),
       `valuation_input` (`market_quote` when instrument_key present,
       else `manual` with the amount), `liquidity` (`t1`/tradable for
       exchange instruments), `holding` (quantity + cost_basis when
       given). Validates via `Position.from_dict` before writing.
     - `update_position`: matches by `position_id` or `instrument_key`
       against existing positions (not found → rejected); supports
       `quantity`, `cost_basis`, `delta_quantity`, `delta_amount`,
       `notes`. Deltas apply to the cash position's holding.
     - `remove_position`: by `position_id`; not found → rejected.
     - `add_account`: requires `account_id`, `display_name`,
       `institution_type`; validates via `Account.from_dict`.
     - `update_profile`: delegates to `engine.update_profile`.
     - v1 asset files → rejected with "migrate to v2 first"
       (`--asset-migrate-v2 --confirmed` exists).
2. **LLM prompt upgrade** (`llm_asset_intake.DEFAULT_INTAKE_PROMPT`):
   teach the v2-oriented output fields (`position_id`, `account_id`,
   `currency`, `quantity`, `cost_basis`, `delta_quantity`,
   `delta_amount`) and the rule that a buy/sell of a non-cash position
   also emits a cash `positions_to_update` delta on the funding
   account's cash position. The service injects the current
   account/position summary into the prompt text.
3. **CLI** (`stocks/adapters/cli.py`):
   - `--asset-intake "自然语言"` → prints `build_intake_draft` result
     (draft JSON + token). No `--confirmed` needed (nothing is written).
   - `--asset-intake-confirm --draft-json '<json>' --token '<token>'` →
     `apply_intake_draft`; prints applied/rejected. This *is* the
     confirmation — no separate `--confirmed` flag.
4. **Contract label**: `AssetIntakeDraft` v1 → PRODUCTION in
   `docs/contracts/README.md` (consumer: this service + CLI).

## Non-goals (must not do in this task)

- MCP adapter wiring for intake (follow-up; CLI proves the flow first).
- Watchlist (W1), feedback loop (M3), constraint model (M4).
- Auto-detecting commission/fees, FX conversion of cash deltas (the
  draft carries explicit currency; no implicit conversion).
- Broker connectivity or reading fills from IBKR.
- v1-file support beyond the explicit "migrate first" rejection.

## Acceptance

Tests must prove:

1. Draft creation with a fake LLM client returns draft JSON + token,
   writes nothing (file hash unchanged), and injects the memory context
   into the prompt.
2. Full round trip on a tmp v2 file: draft → confirm → position added
   with validated v2 structure; buy also updates the cash position via
   delta; engine reload sees the new position.
3. Token replay / stale memory (file touched between draft and confirm)
   → rejected; draft with ambiguities → rejected; unknown account →
   rejected with reason, nothing written.
4. No-LLM-client draft falls back deterministically and lands in
   ambiguities (never writes).
5. v1 file → rejected with migrate-first message.
6. CLI smoke passes on a **sandbox copy** of the assets file (see below);
   the real `.local/financial_assets.json` is never modified by tests or
   smoke.

## Files likely to touch

- `stocks/engine/asset_intake_service.py` — new.
- `stocks/engine/llm_asset_intake.py` — prompt upgrade.
- `stocks/engine/__init__.py` — thin `asset_intake_draft` /
  `asset_intake_apply` delegates (keeps CLI/MCP surface uniform).
- `stocks/adapters/cli.py` — two new flags.
- `docs/contracts/README.md` — label flip.
- `tests/engine/test_asset_intake_service.py` — new.

## Smoke check

```bash
cp .local/financial_assets.json /tmp/a1-sandbox-assets.json
# draft (no write; real file untouched):
.venv/bin/python -m stocks.adapters.cli \
  --asset-intake "测试：买入 us:AAPL 1 股，成本 300 美元，从 IBKR 美元现金扣减"
# confirm flow against a sandbox engine via pytest-level harness or a
# tmp STOCKS_DATA_DIR — verify applied + backup created, then discard.
```

Draft stage must print a draft + token without modifying
`.local/financial_assets.json`; the apply stage is exercised on the
sandbox copy only. Record the outcome in `STATUS.md`.
