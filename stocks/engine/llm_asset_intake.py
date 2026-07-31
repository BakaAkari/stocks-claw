"""LLM-assisted natural language asset intake with evidence and ambiguity.

This module uses a deterministic LLM prompt to convert free-form user messages
into a structured AssetIntakeDraft. It is conservative: every proposed change
is recorded with confidence, and uncertain or unsupported items are placed in
`ambiguities`. The LLM is not allowed to write to the authoritative memory.
"""
from __future__ import annotations

import json
from typing import Any

from stocks.domain.advisory_models import AssetIntakeDraft, FactRef
from stocks.engine.asset_intake_parser import (
    _content_hash,
    _iso_utc,
)
from stocks.logging_utils import get_logger

logger = get_logger("llm_asset_intake")


DEFAULT_INTAKE_PROMPT = """You are a conservative financial memory parser.

The user has sent a message about their portfolio. Convert it into a structured
diff relative to the current financial memory (the reference list of existing
accounts and positions is prepended to the user message). Respond ONLY with a
JSON object (no markdown, no comments).

Output JSON format:
{{
  "accounts_to_add": [],
  "positions_to_add": [],
  "positions_to_update": [],
  "positions_to_remove": [],
  "profile_updates": [],
  "ambiguities": [],
  "source_quotes": []
}}

Field rules (v2 financial memory):
- positions_to_add items: instrument_key (e.g. "us:AAPL", "a:510300") when a
  ticker/code is known, display_name, account_id (from the reference list when
  resolvable), currency, quantity and/or amount, cost_basis (unit cost when
  the user states a price), product_type (stock / exchange_traded_fund /
  cash / manual_asset ...), notes.
- positions_to_update items: position_id or instrument_key from the reference
  list, plus one or more of: quantity, cost_basis, delta_quantity,
  delta_amount, notes.
- positions_to_remove items: position_id from the reference list.
- If the user says "reduce" or "sell", use positions_to_update with a negative
  delta_quantity / delta_amount.
- If the user says "buy" or "add", use positions_to_add or a positive
  positions_to_update.
- CASH RULE: a buy/sell of a non-cash position that states a total cost or
  proceeds must ALSO emit a positions_to_update with delta_amount on the
  funding account's cash position (negative for buys, positive for sells),
  in that cash position's own currency. If the funding cash position cannot
  be identified from the reference list, add an ambiguity instead of guessing.
- amount / cost_basis / deltas must be numbers, not sentences.
- Unknown product names, missing amounts, unclear direction, unresolvable
  accounts go to ambiguities — never guess an account_id or position_id that
  is not in the reference list.
- confidence is "low" for anything you infer beyond exact matching.
- Do not invent account numbers, prices, market values, or commission figures.

User message: {text}
"""


def parse_llm_asset_intake(
    text: str,
    *,
    llm_client: Any | None = None,
    base_memory_hash: str = "",
    source_quote: FactRef | None = None,
) -> AssetIntakeDraft:
    """Use LLM to parse a more complex natural-language asset intake message.

    If no LLM client is provided, fall back to a deterministic placeholder that
    marks the whole message as ambiguous. This keeps the contract runnable and
    testable even when the LLM is not configured.
    """
    draft_id = _content_hash(f"llm:{text}:{base_memory_hash}")
    generated_at = _iso_utc()

    parsed = _call_llm_or_default(text, llm_client)

    positions_to_add = tuple(parsed.get("positions_to_add", []))
    positions_to_update = tuple(parsed.get("positions_to_update", []))
    positions_to_remove = tuple(parsed.get("positions_to_remove", []))
    accounts_to_add = tuple(parsed.get("accounts_to_add", []))
    profile_updates = tuple(parsed.get("profile_updates", []))
    ambiguities = tuple(parsed.get("ambiguities", []))
    source_quotes_raw = parsed.get("source_quotes", [])

    source_quotes: tuple[FactRef, ...] = ()
    if source_quote is not None:
        source_quotes = (source_quote,)
    elif source_quotes_raw:
        # If the LLM returned source_quotes, we ignore them for now because we
        # require deterministic market data from the data layer, not LLM.
        logger.warning("LLM source_quotes ignored; use deterministic market data")

    draft_data = {
        "draft_id": draft_id,
        "base_memory_hash": base_memory_hash,
        "generated_at": generated_at,
        "accounts_to_add": accounts_to_add,
        "positions_to_add": positions_to_add,
        "positions_to_update": positions_to_update,
        "positions_to_remove": positions_to_remove,
        "profile_updates": profile_updates,
        "ambiguities": ambiguities,
        "source_quotes": source_quotes,
        "draft_hash": "",
        "requires_confirmation": True,
    }

    hash_data = dict(draft_data)
    hash_data["draft_hash"] = ""
    draft_data["draft_hash"] = _content_hash(hash_data)
    return AssetIntakeDraft(**draft_data)


def _call_llm_or_default(text: str, llm_client: Any | None) -> dict[str, Any]:
    """Call LLM if available; otherwise return a deterministic fallback."""
    if llm_client is None:
        return {
            "ambiguities": [
                {
                    "field": "llm_client",
                    "reason": "LLM client not available; manual review required",
                    "quote": text[:200],
                }
            ]
        }
    try:
        prompt = DEFAULT_INTAKE_PROMPT.format(text=text)
        response = llm_client.complete(prompt)
        if isinstance(response, str):
            return json.loads(response)
        if isinstance(response, dict):
            return response
        return json.loads(str(response))
    except Exception as e:
        logger.warning("LLM intake failed", extra={"error": str(e)})
        return {
            "ambiguities": [
                {
                    "field": "llm_parse",
                    "reason": f"LLM parse failed: {e}",
                    "quote": text[:200],
                }
            ]
        }
