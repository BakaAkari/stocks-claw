"""Whitelisted outlook evidence package and deterministic confidence cap.

Builds the minimal evidence dictionary that the ConstrainedOutlookSynthesizer
may read.  Everything it returns is authorized — the synthesizer never sees
raw context, raw run artifacts, or any internal identifiers.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from stocks.engine.presentation import display_label

# ── Sessions that produce a full outlook ──────────────────────────────────
PRIMARY_OUTLOOK_SESSIONS: set[str] = {
    "cn_pre_open",
    "cn_after_close",
    "us_pre_open",
    "us_after_close",
}

# ── Sessions that only produce a delta ────────────────────────────────────
OBSERVATION_OUTLOOK_SESSIONS: set[str] = {
    "cn_open_watch",
    "cn_pre_close",
    "us_open_watch",
    "us_pre_close",
}

# ── Asset-class aggregation map ───────────────────────────────────────────
_ASSET_CLASS_ORDER = ("equity", "commodity", "fixed_income", "cash")

# ── Public helpers ────────────────────────────────────────────────────────


def build_outlook_evidence(
    context: dict,
    run: dict,
    *,
    session_id: str,
    generated_at: str,
) -> dict:
    """Build the deterministic, whitelisted evidence package.

    Parameters
    ----------
    context : dict
        Full AnalysisContext as dict (e.g. ``context.to_dict()``).
    run : dict
        Scheduled run artifact dict.
    session_id : str
        Current session id (e.g. ``"cn_after_close"``).
    generated_at : str
        ISO-8601 timestamp for this evidence.

    Returns
    -------
    dict with exactly the 17 keys declared in the evidence contract.
    """
    pv_list: list[dict] = context.get("position_valuations") or []
    intel_digest: dict = context.get("intelligence_digest") or {}
    risk_state: dict = context.get("risk_state") or run.get("risk_state") or {}
    cash_schedule: dict = context.get("cash_schedule") or run.get("cash_schedule") or {}
    data_quality: dict = context.get("data_quality") or {}

    # ── Intelligence events (filter source-less) ───────────────────────
    events, omitted = _build_intelligence_events(intel_digest, generated_at=generated_at)

    # ── Directional intelligence summary ────────────────────────────────
    directional = _build_directional_intelligence(intel_digest)

    # ── Portfolio snapshot ─────────────────────────────────────────────
    portfolio_snapshot = _build_portfolio_snapshot(
        pv_list, events, context, risk_state, run,
    )

    # ── Asset-class snapshot (aggregate) ────────────────────────────────
    asset_class_snapshot = _build_asset_class_snapshot(pv_list)

    # ── Sector snapshot (top exposure tags) ─────────────────────────────
    sector_snapshot = _build_sector_snapshot(context)

    # ── Technical evidence (capped at 8) ────────────────────────────────
    technical_evidence = _build_technical_evidence(context)

    # ── Rotation evidence (capped at 8) ─────────────────────────────────
    rotation_evidence = _build_rotation_evidence(context)

    # ── Macro evidence ──────────────────────────────────────────────────
    macro_evidence = _build_macro_evidence(data_quality)

    # ── Upcoming events ─────────────────────────────────────────────────
    upcoming_events = _build_upcoming_events(context)

    # ── Risk context ────────────────────────────────────────────────────
    risk_context = _build_risk_context(risk_state, cash_schedule)

    # ── Data boundaries / freshness ─────────────────────────────────────
    data_boundaries = _build_data_boundaries(data_quality, omitted)
    # Propagate top-5 anomaly flag through to data_boundaries
    data_boundaries['top5_position_anomaly'] = portfolio_snapshot.pop('_anomaly_top5', False)

    # ── Authorised instruments ──────────────────────────────────────────
    authorised = _build_authorized_instruments(pv_list)

    # ── Confidence cap ──────────────────────────────────────────────────
    evidence = {
        "version": 1,
        "generated_at": generated_at,
        "session": session_id,
        "market": str(run.get("market", "")),
        "portfolio_snapshot": portfolio_snapshot,
        "asset_class_snapshot": asset_class_snapshot,
        "sector_snapshot": sector_snapshot,
        "technical_evidence": technical_evidence,
        "rotation_evidence": rotation_evidence,
        "intelligence_events": events,
        "directional_intelligence": directional,
        "macro_evidence": macro_evidence,
        "upcoming_events": upcoming_events,
        "risk_context": risk_context,
        "data_boundaries": data_boundaries,
        "authorized_instruments": authorised,
        "confidence_cap": "",
        "confidence_reasons": [],
    }
    cap, reasons = compute_confidence_cap(evidence)
    evidence["confidence_cap"] = cap
    evidence["confidence_reasons"] = reasons
    return evidence


def compute_confidence_cap(evidence: dict) -> tuple[str, list[str]]:
    """Deterministic pre-computed confidence cap and reasons.

    Rules (checked in order, first match wins):
      1. Top-5 position has a data anomaly           → low
      2. No directional signals                   → low
      3. Directional coverage ratio < 20 %             → low
      4. Main market quotes stale (> 1 day)           → low
      5. Macro data stale (past publication cycle)    → low
      6. Single-source intelligence events only       → at most medium
      7. Otherwise fresh/current data                 → high / medium
    """
    reasons: list[str] = []

    # Rule 1: data anomaly in top-5
    top5_anomaly = evidence.get("data_boundaries", {}).get("top5_position_anomaly", False)
    if top5_anomaly:
        reasons.append("前5权重持仓存在数据质量异常，全局置信度降低")
        return ("low", reasons)

    # Rule 2: signal_count == 0 → low (no directional signal at all)
    directional = evidence.get("directional_intelligence", {}) or {}
    signal_count = len(directional.get("signals", []))
    if signal_count == 0:
        reasons.append("无方向性信号，缺乏方向判断依据")
        return ("low", reasons)

    # Rule 3: directional coverage ratio < 20 % → low
    coverage_ratio = directional.get("directional_coverage_ratio", 0)
    if coverage_ratio < 0.2:
        reasons.append(f"方向性情报覆盖率不足（{coverage_ratio:.0%}），缺乏方向判断依据")
        return ("low", reasons)

    # Rule 4: stale market quotes
    data_bounds = evidence.get("data_boundaries", {})
    dq = data_bounds.get("data_quality", {})
    quotes = dq.get("quotes", {})
    freshness = str(quotes.get("freshness") or "")
    if freshness in ("old", "stale", "missing", "unknown"):
        reasons.append(f"主市场行情数据过期（{freshness}）")
        return ("low", reasons)

    # Rule 4: stale macro
    macro = dq.get("macro", {})
    macro_freshness = str(macro.get("freshness") or "")
    if macro_freshness in ("old", "stale", "missing"):
        reasons.append("宏观数据超过发布周期")
        return ("low", reasons)

    # Rule 5: single-source or absent events → at most medium
    events = evidence.get("intelligence_events", [])
    if not events:
        reasons.append("无可验证新闻来源，置信度上限为中")
        return ("medium", reasons)
    has_single_source = False
    for ev in events:
        sources = ev.get("sources", [])
        if len(sources) < 2:
            has_single_source = True
            break
    if has_single_source:
        reasons.append("关键新闻事件仅有一个独立来源，置信度上限为中")
        return ("medium", reasons)

    # Rule 6: default — high
    reasons.append("行情、宏观及情报数据均在有效期内，置信度为高")
    return ("high", reasons)


def evidence_hash(evidence: dict) -> str:
    """Canonical SHA-256 hash of evidence minus volatile ``generated_at``."""
    # Strip volatile fields that would change every run
    sanitised = {k: v for k, v in evidence.items() if k not in ("generated_at",)}
    raw = json.dumps(sanitised, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── Internal builders ──────────────────────────────────────────────────────


def _parse_iso_utc(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _build_intelligence_events(
    digest: dict,
    *,
    generated_at: str,
) -> tuple[list[dict], int]:
    """Map fresh top clusters to evidence events; drop stale or source-less clusters."""
    top_clusters: list[dict] = digest.get("top_clusters") or []
    events: list[dict] = []
    omitted = 0
    now = _parse_iso_utc(generated_at)

    for cluster in top_clusters:
        formed_at = _parse_iso_utc(str(cluster.get("formed_at") or ""))
        if now is None or formed_at is None or (now - formed_at).total_seconds() > 72 * 3600:
            omitted += 1
            continue
        articles: list[dict] = cluster.get("articles") or []
        sources: list[dict] = []
        for article in articles:
            source = str(article.get("source") or "").strip()
            title = str(article.get("title") or "").strip()
            url = str(article.get("url") or "").strip()
            published_at = str(article.get("published_at") or "").strip()
            if not (source and title and url and published_at):
                continue
            sources.append({
                "source": source,
                "title": title,
                "url": url,
                "published_at": published_at,
            })
        if not sources:
            omitted += 1
            continue

        events.append({
            "event_id": str(cluster.get("cluster_id", "")),  # public event reference, not a position/decision internal ID
            "theme": str(cluster.get("theme", "")),
            "summary": str(cluster.get("summary", "")),
            "sources": sources,
            "urgency": str(cluster.get("urgency", "medium")),
            "sentiment": str(cluster.get("sentiment", "unknown")),
            "affected_exposures": list(cluster.get("affected_markets") or []),
            "affected_positions": list(cluster.get("affected_symbols") or []),
            "fact_statement": str(cluster.get("summary", "")),
            "as_of": str(cluster.get("formed_at", "")),
        })
    return events, omitted


def _build_directional_intelligence(digest: dict) -> dict:
    """Extract directional signals summary."""
    top_signals: list[dict] = digest.get("top_signals") or []
    signals = []
    for sig in top_signals:
        direction = sig.get("direction", "")
        if isinstance(direction, str):
            direction = direction.strip().lower()
        else:
            direction = str(direction)
        signals.append({
            "symbol": str(sig.get("symbol", "")),
            "direction": direction,
            "urgency": str(sig.get("urgency", "medium")),
            "rationale": str(sig.get("rationale", "")),
        })
    coverage = digest.get("intelligence_coverage") or {}
    directional_coverage = coverage.get("directional", 0)
    field_coverage = coverage.get("field", 0)
    return {
        "signal_count": len(signals),
        "signals": signals,
        "directional_coverage": directional_coverage,
        "field_coverage": field_coverage,
        "directional_coverage_ratio": directional_coverage / max(field_coverage, 1),
    }


def _build_portfolio_snapshot(
    pv_list: list[dict],
    events: list[dict],
    context: dict,
    risk_state: dict,
    run: dict,
) -> dict:
    """Portfolio total + focus positions (top-5 / conflict / event-tagged)."""
    total_value = sum(item.get("market_value_cny") or 0.0 for item in pv_list)

    # Determine effective event exposure tags / symbols
    event_symbols: set[str] = set()
    event_tags: set[str] = set()
    for ev in events:
        for sym in ev.get("affected_positions", []):
            event_symbols.add(str(sym).lower())
        for exp in ev.get("affected_exposures", []):
            event_tags.add(str(exp).lower())

    # Rank by portfolio_weight descending
    sorted_pv = sorted(
        pv_list,
        key=lambda x: x.get("portfolio_weight") or 0.0,
        reverse=True,
    )
    top5_keys: set[str] = set()
    for item in sorted_pv[:5]:
        key = str(item.get("instrument_key") or "")
        if key:
            top5_keys.add(key)

    # Conflict position keys (from run.portfolio_decision.unresolved_conflicts)
    conflict_keys: set[str] = set()
    decision = run.get("portfolio_decision") or {}
    for conflict in decision.get("unresolved_conflicts") or []:
        pid = str(conflict.get("position_id") or "")
        for pv in pv_list:
            if pv.get("position_id") == pid:
                key = str(pv.get("instrument_key") or "")
                if key:
                    conflict_keys.add(key)

    # Build focus positions
    focus: list[dict] = []
    seen_keys: set[str] = set()
    anomaly_top5 = False
    for item in sorted_pv:
        key = str(item.get("instrument_key") or "")
        if not key or key in seen_keys:
            continue
        weight = item.get("portfolio_weight") or 0.0

        # Check if this position has matching exposure tags with events
        tags: list[str] = (item.get("classification") or {}).get("exposure_tags") or []
        tag_matches = bool(
            event_tags & {t.lower() for t in tags}
            or event_symbols & {str(item.get("instrument_key", "")).lower(), str(item.get("public_code", "")).lower()}
        )

        in_top5 = key in top5_keys
        in_conflict = key in conflict_keys

        if not (in_top5 or in_conflict or tag_matches):
            continue

        label = _safe_label(item)
        entry: dict[str, Any] = {
            "display_label": label,
            "instrument_key": key,
            "weight": weight,
            "market_value_cny": item.get("market_value_cny"),
        }

        # Check for data anomalies (only matters for top-5)
        evidence_block = item.get("evidence") or {}
        anomalies = evidence_block.get("data_anomalies") or []
        if anomalies and in_top5:
            anomaly_top5 = True
            entry["data_note"] = "存在数据质量异常"

        focus.append(entry)
        seen_keys.add(key)

    result: dict[str, Any] = {
        "total_value_cny": total_value,
        # Human-scale value is deterministic evidence, not an LLM-side conversion.
        "total_value_wan_cny": round(total_value / 10000, 2),
        "total_value_wan_cny_1dp": round(total_value / 10000, 1),
        "focus_positions": focus,
    }
    # Attach anomaly flag for confidence cap
    result["_anomaly_top5"] = anomaly_top5
    return result


def _safe_label(item: dict) -> str:
    """Build display_label without leaking position_id."""
    classification = item.get("classification") or {}
    return display_label(
        item.get("display_name", ""),
        item.get("instrument_key", ""),
        classification.get("product_type", ""),
        public_code=item.get("public_code", ""),
    )


def _build_asset_class_snapshot(pv_list: list[dict]) -> dict:
    """Aggregate market_value_cny by classification.asset_class."""
    totals: dict[str, float] = {}
    for item in pv_list:
        ac = (item.get("classification") or {}).get("asset_class", "other")
        totals.setdefault(ac, 0.0)
        totals[ac] += item.get("market_value_cny") or 0.0
    return {ac: round(totals.get(ac, 0.0), 2) for ac in _ASSET_CLASS_ORDER if ac in totals}


def _build_sector_snapshot(context: dict) -> dict:
    """Exposure summary as sector snapshot."""
    exposure = context.get("exposure_summary") or {}
    result = {}
    for k, v in exposure.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            result[k] = round(v, 6)
    # Handle nested shape: total_value_cny / exposures / top
    nested = exposure.get("exposures")
    if isinstance(nested, dict):
        for k, v in nested.items():
            if isinstance(v, dict):
                val = v.get("ratio")
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    result[k] = round(val, 6)
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                result[k] = round(v, 6)
    return {"exposures": result}


def _build_technical_evidence(context: dict) -> list[dict]:
    """Technical evidence from action_signals items, capped at 8."""
    signals = (context.get("action_signals") or {}).get("items") or []
    capped = signals[:8]
    return [
        {
            "symbol": s.get("symbol", ""),
            "action": s.get("action", ""),
            "direction": s.get("direction", 0),
            "urgency": s.get("urgency", "medium"),
        }
        for s in capped
    ]


def _build_rotation_evidence(context: dict) -> list[dict]:
    """Rotation evidence, capped at 8."""
    items = (context.get("rotation") or {}).get("items") or []
    capped = items[:8]
    return [
        {
            "symbol": s.get("symbol", ""),
            "rank": s.get("rank"),
            "momentum": s.get("momentum"),
        }
        for s in capped
    ]


def _build_macro_evidence(data_quality: dict) -> dict:
    """Macro evidence from data_quality."""
    macro = data_quality.get("macro") or {}
    return {
        "freshness": macro.get("freshness", "unknown"),
        "as_of": macro.get("as_of"),
    }


def _build_upcoming_events(context: dict) -> list[dict]:
    """Upcoming calendar events with deterministic date components."""
    events: list = context.get("upcoming_events") or []
    result = []
    for event in events:
        scheduled = str(event.get("scheduled_at", ""))
        parsed = _parse_iso_utc(scheduled)
        item = {
            "name": str(event.get("name", "")),
            "scheduled_at": scheduled,
            "source": str(event.get("source", "")),
        }
        if parsed is not None:
            item.update({
                "year": parsed.year,
                "month": parsed.month,
                "day": parsed.day,
                "date_display": f"{parsed.month}月{parsed.day}日",
            })
        result.append(item)
    return result


def _build_risk_context(risk_state: dict, cash_schedule: dict) -> dict:
    """Risk level and cash position."""
    return {
        "level": str(risk_state.get("level", "normal")),
        "transition": str(risk_state.get("transition", "stable")),
        "evidence_keys": list(risk_state.get("evidence_keys") or []),
        "cash": {
            "immediate_cny": round(cash_schedule.get("immediate_cash_cny", 0.0), 2),
            "settling_cny": round(cash_schedule.get("settling_cash_cny", 0.0), 2),
            "locked_cny": round(cash_schedule.get("locked_value_cny", 0.0), 2),
        },
    }


def _build_data_boundaries(data_quality: dict, omitted_count: int) -> dict:
    """Data freshness + omission counters (whitelisted)."""
    return {
        "data_quality": _whitelist_data_quality(data_quality),
        "omitted_event_count": omitted_count,
    }


def _whitelist_data_quality(dq: dict) -> dict:
    """Only keep authorised fields from data_quality (quotes & macro, with
    safe sub-fields).  This prevents internal fields such as debug_info or
    internal_query_log from leaking into LLM-facing evidence."""
    result: dict[str, Any] = {}
    for key in ("quotes", "macro"):
        section = dq.get(key) or {}
        safe: dict[str, Any] = {}
        for field in ("freshness", "as_of", "by_market", "providers", "errors"):
            if field in section:
                safe[field] = section[field]
        if safe:
            result[key] = safe
    return result


def _build_authorized_instruments(pv_list: list[dict]) -> list[dict]:
    """Minimal instrument manifest (no position ids)."""
    result: list[dict] = []
    seen_keys: set[str] = set()
    for item in pv_list:
        key = str(item.get("instrument_key") or "")
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        classification = item.get("classification") or {}
        result.append({
            "display_label": _safe_label(item),
            "instrument_key": key,
            "asset_class": classification.get("asset_class", ""),
            "product_type": classification.get("product_type", ""),
            "exposure_tags": list(classification.get("exposure_tags") or []),
        })
    return result
