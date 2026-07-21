"""Strict structured outlook validation and hostile-output sanitization.

Validates a synthesised outlook against the whitelisted evidence package,
rejects internal-token leaks, trade-instruction phrasing, confidence-cap
violations, unauthorized sources/instruments/numbers, and incomplete
required fields.  Provides ``sanitize_unavailable_outlook`` for graceful
degradation when validation fails.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any

from stocks.engine.outlook_helpers import collect_source_ids, is_valid_iso_date

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
    r"买\s*入|卖\s*(?:出|掉)|减\s*仓|加\s*(?:仓|入仓位)|清\s*仓|"
    r"增持|降低风险资产|提高权益暴露|退出(?:该|此)?标的|做多|做空|换仓|"
    r"配置更多(?:现金|权益|债券|黄金)|止损\s*\d|止盈\s*\d|仓位\s*\d|¥\s*\d|人民币\s*\d"
)

# ── Internal token patterns ────────────────────────────────────────────────
# Catches position_id=, decision_id=, machine-pos-id, and UUID patterns.

_INTERNAL_TOKEN_RE = re.compile(
    r"position_id[=_:]\s*\w+|decision_id[=_:]\s*[\w-]+|"
    r"uuid[=_:]\s*[\w-]+|机器持仓ID"
)
_PROMPT_INJECTION_RE = re.compile(
    r"ignore\s+(?:all\s+)?previous\s+instructions?|system\s*:\s*override|"
    r"忽略(?:之前|以上|所有)(?:的)?指令|覆盖(?:所有)?安全规则|泄露(?:秘密|密钥|系统提示)",
    re.IGNORECASE,
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

_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])\d+(?:\.\d+)?(?![A-Za-z0-9_])")
_NUMERIC_FORECAST_RE = re.compile(
    r"(?:预计|预期|目标|预测|可能|将)(?:[^。；;]{0,20})(?:回报|收益|上涨|下跌|涨幅|跌幅|价格|市值|金额|仓位|比例)"
    r"|(?:回报|收益|上涨|下跌|涨幅|跌幅|价格|市值|金额|仓位|比例)(?:[^。；;]{0,20})(?:预计|预期|目标|预测|可能|将)"
)

# Patterns to exclude from number scanning
_DATE_LIKE = re.compile(r"\d{4}-\d{2}-\d{2}")
_URL_PROTOCOL = re.compile(r"https?://")
_HORIZON_PATTERN = re.compile(r"\d[wmdy]|\d-\d+[wmdy]")
_VERSION_PATTERN = re.compile(r"v\d+(?:\.\d+)*")

# Instrument key pattern
_INSTRUMENT_KEY_RE = re.compile(r"(?:a:|us:|hk:)[A-Za-z0-9.]+")

_SECTOR_DISPLAY_ALIASES: dict[str, frozenset[str]] = {
    "a_share": frozenset({"A股", "中国权益", "中国股票"}),
    "broad_index": frozenset({"宽基指数", "A股宽基", "宽基"}),
    "blue_chip": frozenset({"蓝筹"}),
    "dividend_low_vol": frozenset({"红利低波", "红利低波动"}),
    "high_dividend": frozenset({"高股息", "红利"}),
    "tech": frozenset({"科技", "美国科技", "中国科技", "A股科技", "美股科技"}),
    "star_board": frozenset({"科创板", "科技"}),
    "nasdaq100": frozenset({"纳斯达克100", "美国科技"}),
    "gold": frozenset({"黄金"}),
    "commodity": frozenset({"商品", "大宗商品", "黄金"}),
    "fixed_income": frozenset({"固定收益", "固收", "货币市场"}),
    "money_market": frozenset({"货币市场", "货币基金"}),
    "cash_like": frozenset({"现金及现金等价物", "现金", "货币市场"}),
    "energy": frozenset({"能源"}),
    "oil_gas": frozenset({"油气", "能源"}),
    "defense": frozenset({"国防", "军工", "防御"}),
    "aerospace": frozenset({"航空航天", "军工"}),
    "semiconductor": frozenset({"半导体"}),
    "cyclical": frozenset({"周期", "周期行业"}),
    "ai": frozenset({"人工智能", "AI"}),
    "mining": frozenset({"矿业", "黄金"}),
}


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
    _check_forbidden_probability_fields(outlook, errors)
    _check_source_authorization(outlook, evidence, errors)
    _check_instrument_authorization(outlook, evidence, errors)
    _check_confidence_cap(outlook, evidence, errors)
    _check_authorized_view_names(outlook, evidence, errors)
    _check_internal_tokens(outlook, errors)
    _check_prompt_injection(outlook, errors)
    _check_trade_instructions(outlook, errors)
    _check_source_attribution(outlook, evidence, errors)
    _check_numeric_authority(outlook, evidence, errors)
    _check_forecast_candidates(outlook, evidence, errors)

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


def _check_forbidden_probability_fields(outlook: dict, errors: list[str]) -> None:
    """Reject exact scenario probabilities; the product contract is scenario-based."""
    scenarios = outlook.get("scenarios") or {}
    if not isinstance(scenarios, dict):
        return
    for name, scenario in scenarios.items():
        if isinstance(scenario, dict) and "probability" in scenario:
            errors.append(f"forbidden probability field in scenario: {name}")


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
    """Reject every confidence field that exceeds the deterministic evidence cap."""
    cap = str(evidence.get("confidence_cap") or "")
    cap_level = _CONFIDENCE_LEVELS.get(cap, 0)
    if not cap_level:
        return

    def check(path: str, value: Any) -> None:
        if not isinstance(value, str):
            return
        if _CONFIDENCE_LEVELS.get(value, 0) > cap_level:
            errors.append(f"{path} confidence '{value}' exceeds evidence cap '{cap}'")

    check("top-level", outlook.get("confidence"))
    for key in ("near_term", "medium_term"):
        block = outlook.get(key)
        if isinstance(block, dict):
            check(key, block.get("confidence"))
    for key in ("sector_views", "asset_views"):
        for index, item in enumerate(outlook.get(key, [])):
            if isinstance(item, dict):
                check(f"{key}[{index}]", item.get("confidence"))


def _check_authorized_view_names(outlook: dict, evidence: dict, errors: list[str]) -> None:
    """Reject sector/asset labels that cannot be derived from the evidence package."""
    authorized_tags = set((evidence.get("sector_snapshot") or {}).get("exposures") or {})
    for inst in evidence.get("authorized_instruments", []):
        authorized_tags.update(str(x) for x in (inst.get("exposure_tags") or []))
    authorized_sectors = set(authorized_tags)
    for tag in authorized_tags:
        authorized_sectors.update(_SECTOR_DISPLAY_ALIASES.get(tag, frozenset()))
    authorized_assets = set((evidence.get("asset_class_snapshot") or {}).keys())

    # Minimal legacy fixtures may omit snapshots. In that case, preserve the
    # existing contract and rely on instrument/source/numeric validation.
    if authorized_sectors:
        for index, item in enumerate(outlook.get("sector_views", [])):
            if not isinstance(item, dict):
                continue
            sector = str(item.get("sector") or "")
            parts = [part.strip() for part in re.split(r"[/、]", sector) if part.strip()]
            if sector and not parts:
                errors.append(f"unauthorized sector: {sector} at sector_views[{index}]")
            elif any(part not in authorized_sectors for part in parts):
                errors.append(f"unauthorized sector: {sector} at sector_views[{index}]")
    if authorized_assets:
        for index, item in enumerate(outlook.get("asset_views", [])):
            if not isinstance(item, dict):
                continue
            asset = str(item.get("asset_class") or item.get("asset") or "")
            if asset and asset not in authorized_assets:
                errors.append(f"unauthorized asset class: {asset} at asset_views[{index}]")


def _check_prompt_injection(outlook: dict, errors: list[str]) -> None:
    text = _narrative_text(outlook)
    match = _PROMPT_INJECTION_RE.search(text)
    if match:
        errors.append(f"prompt injection language detected: {match.group()}")


def _check_source_attribution(outlook: dict, evidence: dict, errors: list[str]) -> None:
    """Reject explicit source attribution when that source is not cited by the outlook."""
    authorized_names = {source for source, _, _, _ in _build_authorized_sources(evidence)}
    cited_names = {
        str(ref.get("source") or "")
        for ref in outlook.get("source_refs", [])
        if isinstance(ref, dict)
    }
    narrative = _narrative_text(outlook)
    for source in sorted(authorized_names):
        if source and re.search(re.escape(source), narrative, re.IGNORECASE) and source not in cited_names:
            errors.append(f"source attribution without matching source_ref: {source}")


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

    summary = outlook.get("summary")
    if isinstance(summary, str) and _NUMERIC_FORECAST_RE.search(summary):
        for match in _NUMBER_RE.finditer(summary):
            if not _is_skippable_context(summary, match.start(), match.group()):
                errors.append(f"unauthorized number in numeric claim: {match.group()}")
        if any("numeric claim" in error for error in errors):
            return

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
            # Free-form evidence prose is not numeric authority. Numbers are
            # authorized only when stored as typed numeric fields above.
            return

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



_VALID_COMPARATORS = frozenset({"above", "below", "at_or_above", "at_or_below", "equal"})
_VALID_CONFIDENCES = frozenset({"high", "medium", "low"})


def _check_forecast_candidates(outlook: dict, evidence: dict, errors: list[str]) -> None:
    """Validate top-level forecast_candidates structure and content."""
    raw = outlook.get("forecast_candidates")
    if raw is None:
        return  # optional field; skip
    if not isinstance(raw, list):
        errors.append("forecast_candidates must be a list")
        return
    if len(raw) > 5:
        errors.append(f"forecast_candidates exceeds max 5, got {len(raw)}")

    evidence_numbers = _collect_evidence_numbers(evidence)
    authorized_sources = _build_authorized_sources(evidence)
    authorized_instruments = _build_authorized_instruments(evidence)

    for i, candidate in enumerate(raw):
        if not isinstance(candidate, dict):
            errors.append(f"forecast_candidates[{i}] must be a dict")
            continue
        _check_single_candidate(
            candidate, i, evidence_numbers, authorized_sources,
            authorized_instruments, outlook, errors,
        )


def _check_single_candidate(
    candidate: dict, idx: int, evidence_numbers: set[float],
    authorized_sources: set[tuple[str, str, str, str]],
    authorized_instruments: set[str],
    outlook: dict, errors: list[str],
) -> None:
    """Validate a single forecast_candidate."""
    prefix = f"forecast_candidates[{idx}]"

    target = candidate.get("target")
    if not isinstance(target, str) or not target.strip():
        errors.append(f"{prefix}.target must be a non-empty string")
        return
    target_stripped = target.strip()
    if _INSTRUMENT_KEY_RE.match(target_stripped):
        if target_stripped not in authorized_instruments:
            errors.append(f"{prefix}.target not authorized: {target_stripped}")

    metric = candidate.get("metric")
    if not isinstance(metric, str) or not metric.strip():
        errors.append(f"{prefix}.metric must be a non-empty string")

    comparator = candidate.get("comparator")
    if comparator not in _VALID_COMPARATORS:
        errors.append(f"{prefix}.comparator invalid: {comparator}")

    level = candidate.get("level")
    if level is None or isinstance(level, bool):
        errors.append(f"{prefix}.level is None or bool")
    elif not isinstance(level, (int, float)):
        errors.append(f"{prefix}.level must be numeric")
    else:
        if isinstance(level, float) and (math.isnan(level) or math.isinf(level)):
            errors.append(f"{prefix}.level is NaN or Inf")
        else:
            check_num = float(int(level)) if level == int(level) else float(level)
            if check_num not in evidence_numbers:
                errors.append(f"{prefix}.level {level} not in evidence numbers")

    deadline = candidate.get("deadline")
    if not isinstance(deadline, str) or not deadline.strip():
        errors.append(f"{prefix}.deadline must be a string")
    elif not is_valid_iso_date(deadline):
        errors.append(f"{prefix}.deadline not valid YYYY-MM-DD: {deadline}")

    confidence = candidate.get("confidence")
    if confidence not in _VALID_CONFIDENCES:
        errors.append(f"{prefix}.confidence invalid: {confidence}")

    source_ids = candidate.get("source_ref_ids")
    if not isinstance(source_ids, list) or not source_ids:
        errors.append(f"{prefix}.source_ref_ids must be non-empty list")
    else:
        outlook_source_ids = collect_source_ids(outlook)
        for sid in source_ids:
            if not isinstance(sid, str) or not sid.strip():
                errors.append(f"{prefix}.source_ref_ids contains invalid id")
            elif sid not in outlook_source_ids:
                errors.append(f"{prefix}.source_ref_ids {sid} not in outlook refs")

        for ref in outlook.get("source_refs", []):
            if isinstance(ref, dict):
                s = str(ref.get("source", "") or "")
                t = str(ref.get("title", "") or "")
                u = str(ref.get("url", "") or "")
                p = str(ref.get("published_at", "") or "")
                if s and t and u and p and (s, t, u, p) not in authorized_sources:
                    errors.append(f"{prefix} unauthorized source: {s}")
                    break

    rc = candidate.get("requires_confirmation")
    if rc is not None and not isinstance(rc, bool):
        errors.append(f"{prefix}.requires_confirmation must be bool")

    stmt = candidate.get("statement")
    if stmt is not None and not isinstance(stmt, str):
        errors.append(f"{prefix}.statement must be string")

    if isinstance(target, str) and target.strip().startswith("macro:"):
        macro_val = target.strip()[6:]
        if not macro_val:
            errors.append(f"{prefix}.target is macro: with empty name")


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
