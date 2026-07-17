"""Strict structured outlook validation and hostile-output sanitization.

Validates a synthesised outlook against the whitelisted evidence package,
rejects internal-token leaks, trade-instruction phrasing, confidence-cap
violations, unauthorized sources/instruments/numbers, and incomplete
required fields.  Provides ``sanitize_unavailable_outlook`` for graceful
degradation when validation fails.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

# ── Confidence ordering (high > medium > low) ──────────────────────────────

_CONFIDENCE_LEVELS: dict[str, int] = {
    "high": 3,
    "medium": 2,
    "low": 1,
}

# ── Forbidden action regex ─────────────────────────────────────────────────
# Matches a wide range of trade-instruction patterns while allowing
# descriptive phrases such as "配置风险上升".

_FORBIDDEN_ACTION_RE = re.compile(
    r"买入|卖出|减仓|加仓|清仓|止损\d|止盈\d|仓位\s*\d|¥|人民币"
)

# ── Internal token patterns ────────────────────────────────────────────────
# Catches position_id=, decision_id=, machine-pos-id, and UUID patterns.

_INTERNAL_TOKEN_RE = re.compile(
    r"position_id[=_:]\s*\w+|decision_id[=_:]\s*[\w-]+|"
    r"uuid[=_:]\s*[\w-]+|机器持仓ID"
)

# ── Narrative field keys subject to number scanning ────────────────────────

_NARRATIVE_FIELD_KEYS = {
    "summary",
    "portfolio_effect",
    "rationale",
    "portfolio_implications",
    "validation",
    "invalidation",
    "drivers",
}

# ── Number extraction ──────────────────────────────────────────────────────

_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")

# Patterns to exclude from number scanning
_DATE_LIKE = re.compile(r"\d{4}-\d{2}-\d{2}")
_URL_PROTOCOL = re.compile(r"https?://")
_HORIZON_PATTERN = re.compile(r"\d[wmdy]|\d-\d+[wmdy]")
_VERSION_PATTERN = re.compile(r"v\d+(?:\.\d+)*")

# Instrument key pattern
_INSTRUMENT_KEY_RE = re.compile(r"(?:a:|us:|hk:)[A-Za-z0-9.]+")


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════


def validate_structured_outlook(outlook: dict, evidence: dict) -> list[str]:
    """Validate a structured outlook against the evidence package.

    Parameters
    ----------
    outlook : dict
        The synthesised structured outlook to validate.
    evidence : dict
        The whitelisted evidence package from ``build_outlook_evidence``.

    Returns
    -------
    list[str]
        A (possibly empty) list of human-readable validation error messages.
        An empty list means the outlook is valid.
    """
    errors: list[str] = []

    # ── 1. Required top-level fields ──────────────────────────────────
    _check_required_fields(outlook, errors)
    _check_horizon_blocks(outlook, errors)
    _check_scenario_completeness(outlook, errors)
    _check_source_authorization(outlook, evidence, errors)
    _check_instrument_authorization(outlook, evidence, errors)
    _check_confidence_cap(outlook, evidence, errors)
    _check_internal_tokens(outlook, errors)
    _check_trade_instructions(outlook, errors)
    _check_numeric_authority(outlook, evidence, errors)

    return errors


def sanitize_unavailable_outlook(
    reasons: list[str],
    *,
    generated_at: str,
) -> dict:
    """Build a safe 'unavailable' response when validation fails.

    Parameters
    ----------
    reasons : list[str]
        Raw validation error messages, possibly containing internal
        codenames or implementation details.
    generated_at : str
        ISO-8601 timestamp of the original generation attempt.

    Returns
    -------
    dict
        A sanitised response dict with ``data_limitations`` capped at 3
        entries and internal codenames stripped.
    """
    sanitized: list[str] = []
    for reason in reasons:
        cleaned = _strip_internal_codes(reason)
        if cleaned and cleaned not in sanitized:
            sanitized.append(cleaned)

    return {
        "status": "unavailable",
        "generated_at": generated_at,
        "message": "本期研判未通过数据完整性校验，暂不输出",
        "data_limitations": sanitized[:3],
    }


# ═══════════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════════


def _check_required_fields(outlook: dict, errors: list[str]) -> None:
    """Assert all mandatory top-level fields are present and valid."""
    for field in ("status", "generated_at", "scenarios", "near_term",
                  "medium_term", "source_refs", "confidence"):
        if field not in outlook:
            errors.append(f"missing top-level field: {field}")

    # ── Status type/value enforcement ──────────────────────────────────
    if "status" in outlook:
        status = outlook["status"]
        if status is None:
            errors.append("invalid status: None (only 'ok' allowed for structured outlook)")
        elif not isinstance(status, str):
            errors.append(f"invalid status: expected string, got {type(status).__name__}")
        elif status != "ok":
            errors.append(f"invalid status: '{status}' (only 'ok' allowed for structured outlook)")

    # ── generated_at ISO-8601 enforcement ────────────────────────────────
    if "generated_at" in outlook:
        gen = outlook["generated_at"]
        if gen is None:
            errors.append("invalid generated_at: None (expected ISO-8601 string)")
        elif not isinstance(gen, str):
            errors.append(f"invalid generated_at: expected ISO-8601 string, got {type(gen).__name__}")
        else:
            # Attempt to parse ISO-8601
            try:
                datetime.fromisoformat(gen)
            except (ValueError, TypeError):
                errors.append(f"invalid generated_at: '{gen}' is not a valid ISO-8601 string")


def _check_horizon_blocks(outlook: dict, errors: list[str]) -> None:
    """Validate horizon values in near_term and medium_term."""
    VALID_NEAR_HORIZONS = {"1-2w"}
    VALID_MEDIUM_HORIZONS = {"1-3m"}

    near = outlook.get("near_term")
    if isinstance(near, dict):
        h = near.get("horizon")
        if h and h not in VALID_NEAR_HORIZONS:
            errors.append(f"invalid near_term horizon: {h}")

    medium = outlook.get("medium_term")
    if isinstance(medium, dict):
        h = medium.get("horizon")
        if h and h not in VALID_MEDIUM_HORIZONS:
            errors.append(f"invalid medium_term horizon: {h}")


def _check_scenario_completeness(outlook: dict, errors: list[str]) -> None:
    """Ensure all three required scenarios exist with mandatory sub-fields."""
    scenarios = outlook.get("scenarios")
    if not isinstance(scenarios, dict):
        return  # already reported as missing top-level field

    for name in ("base", "bull", "risk"):
        if name not in scenarios:
            errors.append(f"missing scenario: {name}")
            continue

        scene = scenarios[name]
        if not isinstance(scene, dict):
            continue

        for sub in ("drivers", "portfolio_effect", "validation", "invalidation"):
            if sub not in scene:
                errors.append(f"{name}: missing {sub}")


def _build_authorized_sources(evidence: dict) -> set[tuple[str, str, str, str]]:
    """Build set of (source, title, url) tuples from evidence events."""
    authorized: set[tuple[str, str, str, str]] = set()
    for event in evidence.get("intelligence_events", []):
        for src in event.get("sources", []):
            s = str(src.get("source", "") or "")
            t = str(src.get("title", "") or "")
            u = str(src.get("url", "") or "")
            p = str(src.get("published_at", "") or "")
            if s and t and u and p:
                authorized.add((s, t, u, p))
    return authorized


def _check_source_authorization(
    outlook: dict, evidence: dict, errors: list[str],
) -> None:
    """Reject source_refs that don't match evidence intelligence sources."""
    authorized = _build_authorized_sources(evidence)
    for i, src_ref in enumerate(outlook.get("source_refs", [])):
        if not isinstance(src_ref, dict):
            errors.append(f"invalid source_ref at index {i}: expected dict, got {type(src_ref).__name__}")
            continue
        s = str(src_ref.get("source", "") or "")
        t = str(src_ref.get("title", "") or "")
        u = str(src_ref.get("url", "") or "")
        p = str(src_ref.get("published_at", "") or "")
        # Missing required fields → not authorized
        if not (s and t and u and p):
            errors.append(f"unauthorized source: {s or 'unknown'}")
            continue
        if (s, t, u, p) not in authorized:
            errors.append(f"unauthorized source: {s}")


def _build_authorized_instruments(evidence: dict) -> set[str]:
    """Build set of authorized instrument keys from evidence."""
    keys: set[str] = set()
    for inst in evidence.get("authorized_instruments", []):
        k = inst.get("instrument_key", "")
        if k:
            keys.add(k)
    return keys


def _check_instrument_authorization(
    outlook: dict, evidence: dict, errors: list[str],
) -> None:
    """Reject instrument keys in narrative fields not in evidence."""
    authorized = _build_authorized_instruments(evidence)
    found = _extract_instrument_keys(outlook)
    for key in found:
        if key not in authorized:
            errors.append(f"unauthorized instrument: {key}")


def _extract_instrument_keys(outlook: dict) -> set[str]:
    """Extract all instrument key references from narrative outlook fields."""
    keys: set[str] = set()
    text = _narrative_text(outlook)
    for m in _INSTRUMENT_KEY_RE.finditer(text):
        keys.add(m.group())
    return keys


def _check_confidence_cap(
    outlook: dict, evidence: dict, errors: list[str],
) -> None:
    """Reject outlook confidence that exceeds the evidence cap."""
    outlook_conf = outlook.get("confidence", "")
    cap = evidence.get("confidence_cap", "")
    if outlook_conf and cap:
        ol = _CONFIDENCE_LEVELS.get(outlook_conf, 0)
        cl = _CONFIDENCE_LEVELS.get(cap, 0)
        if ol > cl:
            errors.append(
                f"confidence '{outlook_conf}' exceeds evidence cap '{cap}'"
            )


def _check_internal_tokens(outlook: dict, errors: list[str]) -> None:
    """Reject internal identifier tokens in narrative text."""
    text = _narrative_text(outlook)
    m = _INTERNAL_TOKEN_RE.search(text)
    if m:
        errors.append(f"internal token leakage: {m.group()}")


def _check_trade_instructions(outlook: dict, errors: list[str]) -> None:
    """Reject forbidden trade-instruction patterns."""
    text = _narrative_text(outlook)
    m = _FORBIDDEN_ACTION_RE.search(text)
    if m:
        errors.append(f"trade instruction detected: {m.group()}")


def _check_numeric_authority(
    outlook: dict, evidence: dict, errors: list[str],
) -> None:
    """Reject numeric claims in narrative fields not backed by evidence."""
    evidence_numbers = _collect_evidence_numbers(evidence)
    narrative = _narrative_text(outlook)

    # Exclude source_refs.id values from numeric scanning to avoid src-1 false positives
    for ref in outlook.get("source_refs", []):
        if isinstance(ref, dict):
            rid = ref.get("id", "")
            if isinstance(rid, str) and rid:
                narrative = narrative.replace(rid, "")

    for m in _NUMBER_RE.finditer(narrative):
        num_str = m.group()
        pos = m.start()

        # Skip numbers in contexts that shouldn't be scanned
        if _is_skippable_context(narrative, pos, num_str):
            continue

        try:
            num = float(num_str)
        except ValueError:
            continue

        # Round-trip through int if whole number for matching
        check_num = float(int(num)) if num == int(num) else num
        if check_num not in evidence_numbers:
            errors.append(f"unauthorized number: {num_str}")


def _narrative_text(outlook: dict) -> str:
    """Concatenate all narrative fields from the outlook into a single string."""
    parts: list[str] = []

    # Top-level
    for key in ("summary",):
        val = outlook.get(key)
        if isinstance(val, str):
            parts.append(val)

    # Horizon blocks
    for hkey in ("near_term", "medium_term"):
        block = outlook.get(hkey)
        if isinstance(block, dict):
            for v in block.values():
                if isinstance(v, str):
                    parts.append(v)

    # Scenarios
    scenarios = outlook.get("scenarios", {})
    if isinstance(scenarios, dict):
        for scene in scenarios.values():
            if isinstance(scene, dict):
                for sub_key in ("portfolio_effect", "drivers", "validation",
                                "invalidation", "label"):
                    val = scene.get(sub_key)
                    if isinstance(val, list):
                        parts.extend(str(item) for item in val)
                    elif isinstance(val, str):
                        parts.append(val)

    # Sector views
    for sv in outlook.get("sector_views", []):
        if isinstance(sv, dict):
            for key in ("sector", "direction", "rationale"):
                val = sv.get(key)
                if isinstance(val, str):
                    parts.append(val)

    # Asset views
    for av in outlook.get("asset_views", []):
        if isinstance(av, dict):
            for key in ("asset_class", "direction", "rationale"):
                val = av.get(key)
                if isinstance(val, str):
                    parts.append(val)

    # Source refs (titles + id)
    for ref in outlook.get("source_refs", []):
        if isinstance(ref, dict):
            for key in ("title", "id"):
                val = ref.get(key)
                if isinstance(val, str):
                    parts.append(val)

    return " ".join(parts)


def _collect_evidence_numbers(evidence: dict) -> set[float]:
    """Recursively collect all numeric values from the evidence dict."""
    numbers: set[float] = set()

    def _walk(obj: Any) -> None:
        if isinstance(obj, bool):
            return
        if isinstance(obj, (int, float)):
            numbers.add(float(obj))
        elif isinstance(obj, dict):
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)
        elif isinstance(obj, str):
            for m in _NUMBER_RE.finditer(obj):
                try:
                    numbers.add(float(m.group()))
                except ValueError:
                    pass

    _walk(evidence)
    return numbers


def _is_skippable_context(text: str, pos: int, num_str: str) -> bool:
    """Check whether a number at *pos* in *text* should be excluded."""
    # Skip if part of ISO date (4-digit year followed by -, then 2-digit month)
    lookback = text[max(0, pos - 20):pos + len(num_str) + 20]
    if re.search(r"\d{4}-\d{2}-\d{2}", lookback):
        return True

    # Skip if number is inside a URL
    before = text[max(0, pos - 100):pos]
    proto_match = _URL_PROTOCOL.search(before)
    if proto_match:
        proto_start = max(0, pos - 100) + proto_match.start()
        after_proto = text[proto_start:pos + len(num_str)]
        if " " not in after_proto:
            return True

    # Skip horizon patterns like "1-2w" or "1-3m"
    lookback = text[max(0, pos - 5):pos + len(num_str) + 5]
    if _HORIZON_PATTERN.search(lookback):
        return True

    # Skip version numbers like v3.1.4
    lookback = text[max(0, pos - 5):pos + len(num_str) + 5]
    if _VERSION_PATTERN.search(lookback):
        return True

    return False


def _strip_internal_codes(reason: str) -> str:
    """Remove internal codenames/identifiers from a reason string.

    Strips patterns like ``cluster_id=oil``, ``XJY-2026-p3``,
    ``BETA-FLOW-07``, and any ``key=value`` fragments.
    """
    # Remove key=value patterns
    cleaned = re.sub(r"\b\w+=[\w\-.]+", "", reason)
    # Remove internal codename patterns: UPPER-WORD-NUMBER or WORD-NUMBER
    cleaned = re.sub(r"\b[A-Z]+-[\w-]+\b", "", cleaned)
    # Clean up double spaces
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned
