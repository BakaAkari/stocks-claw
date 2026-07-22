"""Build a UnifiedAnalysisSnapshot from existing AnalysisContext.

This module is intentionally narrow: it converts the current data structures into
the v1 advisory snapshot contract without replacing the production pipeline. The
result is one evidence package with a single generated_at and a shared source
registry, suitable for the LLM investment analyst.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

from stocks.domain.advisory_models import (
    FactRef,
    SourceEntry,
    UnifiedAnalysisSnapshot,
)
from stocks.domain.models import AnalysisContext, Quote, UpcomingEvent
from stocks.logging_utils import get_logger

logger = get_logger("unified_snapshot")

_SNAPSHOT_SCHEMA_VERSION = 1


def _iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_hash(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
    return h.hexdigest()[:24]


def _source_id(provider: str, endpoint: str) -> str:
    return f"{provider}:{endpoint}"


def _build_source_registry(context: AnalysisContext) -> list[SourceEntry]:
    """Build minimal source registry from data_quality and context fields."""
    sources: list[SourceEntry] = []
    dq = context.data_quality or {}
    if not isinstance(dq, dict):
        dq = {}
    seen: set[str] = set()
    generated_at = str(context.generated_at or "")

    def add_source(provider: str, endpoint: str, info: dict[str, Any]) -> None:
        sid = _source_id(provider, endpoint)
        if sid in seen:
            return
        seen.add(sid)
        sources.append(
            SourceEntry(
                source_id=sid,
                provider=provider,
                endpoint_type=endpoint,
                as_of=str(info.get("as_of", generated_at)),
                freshness=str(info.get("freshness", "unknown")),
                status=str(info.get("status", "unknown")),
                fallback_chain=tuple(info.get("fallback_chain", [])),
                error_category=str(info.get("error_category", "")),
            )
        )

    # Quote providers
    quote_section = dq.get("quotes", {})
    if isinstance(quote_section, dict):
        quote_sources = quote_section.get("sources", {})
        if isinstance(quote_sources, dict):
            for provider, info in quote_sources.items():
                if isinstance(info, dict):
                    add_source(provider, "quote", info)

    # News sources
    news_section = dq.get("news", {})
    if isinstance(news_section, dict):
        news_sources = news_section.get("sources", {})
        if isinstance(news_sources, dict):
            for provider, info in news_sources.items():
                if isinstance(info, dict):
                    add_source(provider, "news", info)

    # History provider
    hist = dq.get("history", {})
    if isinstance(hist, dict) and hist:
        add_source("history", "backfill", hist)

    # Macro provider
    macro = dq.get("macro", {})
    if isinstance(macro, dict) and macro:
        add_source("macro", "aggregate", macro)

    return sources


def _fact(
    fact_id: str,
    metric: str,
    value: Any,
    unit: str,
    as_of: str,
    source_ref: str,
) -> Optional[FactRef]:
    if value is None or value == "":
        return None
    return FactRef(
        fact_id=fact_id,
        metric=metric,
        value=value,
        unit=unit,
        as_of=as_of,
        source_ref=source_ref,
    )


def _build_portfolio_facts(
    context: AnalysisContext,
    source_registry: list[SourceEntry],
) -> list[FactRef]:
    facts: list[FactRef] = []
    base_source = next(
        (s.source_id for s in source_registry if s.endpoint_type == "quote"),
        "unknown",
    )
    for pv in context.position_valuations or []:
        if not isinstance(pv, dict):
            continue
        instrument = str(pv.get("instrument_key", "") or "unknown")
        position_id = str(pv.get("position_id", "") or "unknown")
        as_of = str(pv.get("as_of", context.generated_at or ""))
        for metric, value, unit in (
            ("market_value_cny", pv.get("market_value_cny"), "cny"),
            ("unrealized_pnl_cny", pv.get("unrealized_pnl_cny"), "cny"),
            ("pnl_pct", pv.get("pnl_pct"), "percent"),
            ("quantity", pv.get("quantity"), "shares"),
        ):
            fact = _fact(
                f"{position_id}:{metric}",
                f"position:{instrument}:{metric}",
                value,
                unit,
                as_of,
                base_source,
            )
            if fact is not None:
                facts.append(fact)
    return facts


def _build_quote_facts(
    context: AnalysisContext,
    source_registry: list[SourceEntry],
) -> list[FactRef]:
    facts: list[FactRef] = []
    base_source = next(
        (s.source_id for s in source_registry if s.endpoint_type == "quote"),
        "unknown",
    )
    quotes = context.quotes or {}
    if not isinstance(quotes, dict):
        return facts
    for _market, quote_list in quotes.items():
        if not isinstance(quote_list, list):
            continue
        for quote in quote_list:
            if not isinstance(quote, Quote) or quote.instrument is None:
                continue
            instrument_key = f"{quote.instrument.market}:{quote.instrument.code}"
            as_of = str(quote.as_of or context.generated_at or "")
            for metric, value, unit in (
                ("price", quote.price, "cny"),
                ("pct_change", quote.pct_change, "percent"),
                ("volume", quote.volume_lot, "lots"),
                ("change", quote.change, "cny"),
            ):
                fact = _fact(
                    f"{instrument_key}:{metric}",
                    f"quote:{instrument_key}:{metric}",
                    value,
                    unit,
                    as_of,
                    base_source,
                )
                if fact is not None:
                    facts.append(fact)
    return facts


def _build_macro_facts(
    context: AnalysisContext,
    source_registry: list[SourceEntry],
) -> list[FactRef]:
    facts: list[FactRef] = []
    base_source = next(
        (s.source_id for s in source_registry if s.provider == "macro"),
        "unknown",
    )
    macro = getattr(context, "macro_snapshot", None) or {}
    if not isinstance(macro, dict):
        return facts
    as_of = str(context.generated_at or "")
    for key, value in macro.items():
        if value is None or value == "":
            continue
        unit = "index_points" if "vix" in key.lower() else "percent" if "rate" in key.lower() else "unknown"
        fact = _fact(f"macro:{key}", f"macro:{key}", value, unit, as_of, base_source)
        if fact is not None:
            facts.append(fact)
    return facts


def _build_technical_facts(context: AnalysisContext) -> list[FactRef]:
    facts: list[FactRef] = []
    indicators = context.technical_indicators or {}
    if not isinstance(indicators, dict):
        return facts
    as_of = str(context.generated_at or "")
    for instrument_key, indicator in indicators.items():
        if not isinstance(indicator, dict):
            continue
        for metric, value in indicator.items():
            fact = _fact(
                f"{instrument_key}:{metric}",
                f"technical:{instrument_key}:{metric}",
                value,
                "unknown",
                as_of,
                "system:technical",
            )
            if fact is not None:
                facts.append(fact)
    return facts


def _build_news_digest_facts(context: AnalysisContext) -> list[FactRef]:
    facts: list[FactRef] = []
    digest = context.news_digest or {}
    if not isinstance(digest, dict):
        return facts
    as_of = str(context.generated_at or "")
    for key, value in digest.items():
        if value is None or value == "":
            continue
        fact = _fact(f"news:{key}", f"news_digest:{key}", value, "count" if isinstance(value, (int, float)) else "text", as_of, "system:news_digest")
        if fact is not None:
            facts.append(fact)
    return facts


def _build_intelligence_digest_facts(context: AnalysisContext) -> list[FactRef]:
    facts: list[FactRef] = []
    intel = context.intelligence_digest or {}
    if not isinstance(intel, dict):
        return facts
    as_of = str(context.generated_at or "")
    for key, value in intel.items():
        if value is None or value == "":
            continue
        fact = _fact(f"intel:{key}", f"intelligence_digest:{key}", value, "text", as_of, "system:intelligence_digest")
        if fact is not None:
            facts.append(fact)
    return facts


def _build_rotation_facts(context: AnalysisContext) -> list[FactRef]:
    facts: list[FactRef] = []
    rotation = context.rotation or {}
    if not isinstance(rotation, dict):
        return facts
    as_of = str(context.generated_at or "")
    for key, value in rotation.items():
        if value is None or value == "":
            continue
        fact = _fact(f"rotation:{key}", f"rotation:{key}", value, "rank" if isinstance(value, (int, float)) else "text", as_of, "system:rotation")
        if fact is not None:
            facts.append(fact)
    return facts


def _build_action_signal_facts(context: AnalysisContext) -> list[FactRef]:
    facts: list[FactRef] = []
    signals = context.action_signals or {}
    if not isinstance(signals, dict):
        return facts
    as_of = str(context.generated_at or "")
    for instrument_key, signal in signals.items():
        if not isinstance(signal, dict):
            continue
        for metric, value in signal.items():
            fact = _fact(
                f"signal:{instrument_key}:{metric}",
                f"action_signal:{instrument_key}:{metric}",
                value,
                "text",
                as_of,
                "system:action_signals",
            )
            if fact is not None:
                facts.append(fact)
    return facts


def _build_data_quality_facts(context: AnalysisContext) -> list[FactRef]:
    facts: list[FactRef] = []
    dq = context.data_quality or {}
    if not isinstance(dq, dict):
        return facts
    as_of = str(context.generated_at or "")
    for key, value in dq.items():
        if not isinstance(value, dict):
            continue
        status = value.get("status", "unknown")
        fact = _fact(f"dq:{key}", f"data_quality:{key}", status, "status", as_of, "system:data_quality")
        if fact is not None:
            facts.append(fact)
    return facts


def _build_upcoming_event_facts(context: AnalysisContext) -> list[FactRef]:
    facts: list[FactRef] = []
    events = context.upcoming_events or []
    if not isinstance(events, list):
        return facts
    for event in events:
        if not isinstance(event, UpcomingEvent):
            continue
        as_of = str(event.date or context.generated_at or "")
        fact = _fact(
            f"event:{event.name}",
            f"upcoming_event:{event.name}",
            event.event_type,
            "type",
            as_of,
            "system:event_calendar",
        )
        if fact is not None:
            facts.append(fact)
    return facts


def _build_profile_facts(context: AnalysisContext) -> list[FactRef]:
    facts: list[FactRef] = []
    profile = context.portfolio_profile or {}
    if not isinstance(profile, dict):
        return facts
    as_of = str(context.generated_at or "")
    for key, value in profile.items():
        if value is None or value == "":
            continue
        fact = _fact(f"profile:{key}", f"profile:{key}", value, "text", as_of, "system:portfolio_profile")
        if fact is not None:
            facts.append(fact)
    return facts


def build_unified_snapshot(
    context: AnalysisContext,
    *,
    trigger: str = "scheduled",
    session: str = "unknown",
    market_scope: str = "cn",
) -> UnifiedAnalysisSnapshot:
    """Build a v1 snapshot from the current AnalysisContext.

    This is a lossy projection on purpose: the goal is to expose only the
clearly
    evidence the LLM analyst may use, with source and freshness.
    """
    generated_at = _iso_utc()
    source_registry = _build_source_registry(context)
    snapshot_id = _stable_hash(
        generated_at,
        str(context.generated_at),
        session,
        market_scope,
        str(context.schema_version),
    )

    return UnifiedAnalysisSnapshot(
        snapshot_id=snapshot_id,
        generated_at=generated_at,
        trigger=trigger,  # type: ignore[arg-type]
        session=session,
        market_scope=market_scope,  # type: ignore[arg-type]
        portfolio=tuple(_build_portfolio_facts(context, source_registry)),
        profile=tuple(_build_profile_facts(context)),
        quotes=tuple(_build_quote_facts(context, source_registry)),
        history_features=(),
        technical_evidence=tuple(_build_technical_facts(context)),
        news_clusters=tuple(_build_news_digest_facts(context) + _build_intelligence_digest_facts(context)),
        filings=(),
        macro=tuple(_build_macro_facts(context, source_registry)),
        upcoming_events=tuple(_build_upcoming_event_facts(context)),
        rotation=tuple(_build_rotation_facts(context)),
        portfolio_constraints=tuple(),
        risk_context=tuple(),
        candidate_signals=tuple(_build_action_signal_facts(context)),
        data_quality=tuple(_build_data_quality_facts(context)),
        source_registry=tuple(source_registry),
        metadata={
            "schema_version": _SNAPSHOT_SCHEMA_VERSION,
            "context_schema_version": getattr(context, "schema_version", None),
            "context_generated_at": str(context.generated_at or ""),
        },
    )
