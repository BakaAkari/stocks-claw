"""Shared helpers for structured outlook validation and forecast candidates."""
from __future__ import annotations

from datetime import date
from typing import Any


def is_valid_iso_date(value: Any) -> bool:
    """Return whether *value* is a strict YYYY-MM-DD calendar date."""
    if not isinstance(value, str):
        return False
    raw = value.strip()
    if len(raw) != 10 or raw[4] != "-" or raw[7] != "-":
        return False
    try:
        date.fromisoformat(raw)
        return True
    except ValueError:
        return False


def collect_source_ids(outlook: dict) -> set[str]:
    """Collect non-empty source_ref IDs from a structured outlook."""
    return {
        rid
        for ref in outlook.get("source_refs", [])
        if isinstance(ref, dict)
        for rid in [ref.get("id")]
        if isinstance(rid, str) and rid
    }
