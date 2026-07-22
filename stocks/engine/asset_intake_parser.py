"""Natural-language asset intake: parse user text into a structured draft.

The parser is deterministic and conservative. It does not access market data or
make up values. Unknown quantities are recorded as ambiguities for the user to
clarify. The draft is not applied until confirmed.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from stocks.domain.advisory_models import AssetIntakeDraft, FactRef

# Regex patterns for conservative extraction
_AMOUNT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(wan|\u4e07)?", re.IGNORECASE)
_CN_AMOUNT_RE = re.compile(r"([\u4e00\u4e8c\u4e24\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\u767e\u5343\u4e07]+)\s*(\u4e07)?")
_PRODUCT_RE = re.compile(
    r"(\d{6})\s*(\u4e0a\u8bc1\u6307\u6570|\u6df1\u8bc1300|\u6caa\u6df1300|\u7eb3\u6307|ETF|etf|\u57fa\u91d1|\u80a1\u7968)?",
    re.IGNORECASE,
)
def _iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_hash(value: Any) -> str:
    h = hashlib.sha256()
    h.update(str(value).encode("utf-8"))
    return h.hexdigest()[:24]


def _build_draft_id(text: str, base_hash: str) -> str:
    return _content_hash(f"{text}:{base_hash}")


_CN_NUM = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "百": 100, "千": 1000, "万": 10_000,
}


def _parse_cn_number(token: str) -> float:
    if not token:
        return 0.0
    # simple cases: 一万元, 两万, 五千, 三百
    total = 0.0
    current = 0.0
    for ch in token:
        val = _CN_NUM.get(ch, 0)
        if val >= 10:
            if current == 0:
                current = 1.0
            total += current * val
            current = 0.0
        else:
            current = current * 10 + val if current > 0 else val
    return total + current


def _extract_amounts(text: str, excluded_code: str = "") -> list[float]:
    matches = []
    for m in _AMOUNT_RE.finditer(text):
        token = m.group(1)
        if not token:
            continue
        if excluded_code and token == excluded_code:
            continue
        value = float(token)
        if m.group(2):
            value *= 10_000
        matches.append(value)
    for m in _CN_AMOUNT_RE.finditer(text):
        token = m.group(1)
        if not token:
            continue
        value = _parse_cn_number(token)
        if m.group(2):
            value *= 10_000
        matches.append(value)
    return matches


def _extract_product_code(text: str) -> str:
    m = _PRODUCT_RE.search(text)
    if m and m.group(1):
        return m.group(1)
    return ""


def parse_asset_intake(
    text: str,
    *,
    base_memory_hash: str = "",
    source_quote: FactRef | None = None,
) -> AssetIntakeDraft:
    """Parse user text into a structured AssetIntakeDraft.

    Returns a draft with proposed changes and ambiguities. No authoritative write
    happens here.
    """
    draft_id = _build_draft_id(text, base_memory_hash)
    generated_at = _iso_utc()

    positions_to_add: list[dict[str, Any]] = []
    ambiguities: list[dict[str, Any]] = []

    product_code = _extract_product_code(text)
    amounts = _extract_amounts(text, excluded_code=product_code)

    if product_code and amounts:
        positions_to_add.append(
            {
                "instrument_key": f"a:{product_code}",
                "quantity": None,  # unknown until user confirms
                "amount_cny": max(amounts),
                "proposed_by": "nl_intake",
                "confidence": "low",
            }
        )
    elif product_code:
        positions_to_add.append(
            {
                "instrument_key": f"a:{product_code}",
                "quantity": None,
                "amount_cny": None,
                "proposed_by": "nl_intake",
                "confidence": "low",
            }
        )
    else:
        ambiguities.append(
            {
                "field": "instrument_key",
                "reason": "no recognizable product code or name",
                "quote": text[:200],
            }
        )

    if not amounts:
        ambiguities.append(
            {
                "field": "amount",
                "reason": "no numeric amount found",
                "quote": text[:200],
            }
        )

    source_quotes: tuple[FactRef, ...] = ()
    if source_quote is not None:
        source_quotes = (source_quote,)

    draft_data = {
        "draft_id": draft_id,
        "base_memory_hash": base_memory_hash,
        "generated_at": generated_at,
        "accounts_to_add": (),
        "positions_to_add": tuple(positions_to_add),
        "positions_to_update": (),
        "positions_to_remove": (),
        "profile_updates": (),
        "ambiguities": tuple(ambiguities),
        "source_quotes": source_quotes,
        "draft_hash": "",
        "requires_confirmation": True,
    }

    # Recalculate draft hash to ensure stability
    hash_data = dict(draft_data)
    hash_data["draft_hash"] = ""
    draft_data["draft_hash"] = _content_hash(hash_data)
    return AssetIntakeDraft(**draft_data)


def verify_draft_token(draft: AssetIntakeDraft, token: str, current_memory_hash: str) -> bool:
    """Verify that the confirmation token matches the current memory and draft."""
    if not draft.requires_confirmation:
        return False
    expected = _content_hash(f"{draft.draft_id}:{current_memory_hash}")
    return token == expected
