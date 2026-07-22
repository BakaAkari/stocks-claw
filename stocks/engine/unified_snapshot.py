"""Build a UnifiedAnalysisSnapshot from existing AnalysisContext.

This module is intentionally narrow: it converts the current data structures into
the v1 advisory snapshot contract without replacing the production pipeline.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from stocks.domain.advisory_models import (
    FactRef,
    SourceEntry,
    UnifiedAnalysisSnapshot,
)
from stocks.domain.models import AnalysisContext
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
    """Build minimal source registry from data_quality and source context."""
    sources: list[SourceEntry] = []
    dq = context.data_quality or {}
    seen: set[str] = set()

    # Quote providers
    quote_section = dq.get("quotes", {})
    if isinstance(quote_section, dict):
        quote_sources = quote_section.get("sources", {})
        if not isinstance(quote_sources, dict):
            quote_sources = {}
        for provider, info in quote_sources.items():
            if not isinstance(info, dict):
                continue
            sid = _source_id(provider, "quote")
            if sid in seen:
                continue
            seen.add(sid)
            sources.append(
                SourceEntry(
                    source_id=sid,
                    provider=provider,
                    endpoint_type="quote",
                    as_of=str(info.get("as_of", context.generated_at or "")),
                    freshness=str(info.get("freshness", "unknown")),
                    status=str(info.get("status", "unknown")),
                )
            )

    # News sources
    news_section = dq.get("news", {})
    news_sources = news_section.get("sources", {}) if isinstance(news_section, dict) else {}
    if not isinstance(news_sources, dict):
        news_sources = {}
    for provider, info in news_sources.items():
        if not isinstance(info, dict):
            continue
        sid = _source_id(provider, "news")
        if sid in seen:
            continue
        seen.add(sid)
        sources.append(
            SourceEntry(
                source_id=sid,
                provider=provider,
                endpoint_type="news",
                as_of=str(info.get("as_of", context.generated_at or "")),
                freshness=str(info.get("freshness", "unknown")),
                status=str(info.get("status", "unknown")),
            )
        )

    # History provider
    hist = dq.get("history", {})
    if isinstance(hist, dict) and hist:
        sid = _source_id("history", "backfill")
        if sid not in seen:
            seen.add(sid)
            sources.append(
                SourceEntry(
                    source_id=sid,
                    provider="history",
                    endpoint_type="backfill",
                    as_of=str(hist.get("as_of", context.generated_at or "")),
                    freshness=str(hist.get("freshness", "unknown")),
                    status=str(hist.get("status", "unknown")),
                )
            )

    return sources


def _build_portfolio_facts(
    context: AnalysisContext,
    source_registry: list[SourceEntry],
) -> list[FactRef]:
    """Convert position valuations into typed facts."""
    facts: list[FactRef] = []
    base_source = next((s.source_id for s in source_registry if s.endpoint_type == "quote"), "unknown")
    for pv in context.position_valuations or []:
        instrument = str(pv.get("instrument_key", "") or "unknown")
        position_id = str(pv.get("position_id", "") or "unknown")
        as_of = str(pv.get("as_of", context.generated_at or ""))
        for metric, value in (
            ("market_value_cny", pv.get("market_value_cny")),
            ("unrealized_pnl_cny", pv.get("unrealized_pnl_cny")),
            ("pnl_pct", pv.get("pnl_pct")),
            ("quantity", pv.get("quantity")),
        ):
            if value is None or value == "":
                continue
            facts.append(
                FactRef(
                    fact_id=f"{position_id}:{metric}",
                    metric=f"position:{instrument}:{metric}",
                    value=value,
                    unit="cny" if metric != "quantity" else "shares",
                    as_of=as_of,
                    source_ref=base_source,
                )
            )
    return facts


def _build_quote_facts(
    context: AnalysisContext,
    source_registry: list[SourceEntry],
) -> list[FactRef]:
    """Convert quotes into typed facts."""
    facts: list[FactRef] = []
    base_source = next((s.source_id for s in source_registry if s.endpoint_type == "quote"), "unknown")
    quotes = context.quotes or {}
    if not isinstance(quotes, dict):
        return facts
    for _market, quote_list in quotes.items():
        if not isinstance(quote_list, list):
            continue
        for quote in quote_list:
            if not hasattr(quote, "instrument") or quote.instrument is None:
                continue
            instrument_key = f"{quote.instrument.market}:{quote.instrument.code}"
            as_of = str(quote.as_of or context.generated_at or "")
            for metric, value, unit in (
                ("price", quote.price, "cny"),
                ("pct_change", quote.pct_change, "percent"),
                ("volume", quote.volume_lot, "lots"),
            ):
                if value is None or value == "":
                    continue
                facts.append(
                    FactRef(
                        fact_id=f"{instrument_key}:{metric}",
                        metric=f"quote:{instrument_key}:{metric}",
                        value=value,
                        unit=unit,
                        as_of=as_of,
                        source_ref=base_source,
                    )
                )
    return facts


def _build_macro_facts(
    context: AnalysisContext,
    source_registry: list[SourceEntry],
) -> list[FactRef]:
    """Convert macro data into typed facts."""
    facts: list[FactRef] = []
    base_source = next((s.source_id for s in source_registry if s.provider == "fred"), "unknown")
    macro = getattr(context, "macro_snapshot", None) or {}
    if not isinstance(macro, dict):
        return facts
    as_of = str(context.generated_at or "")
    for key, value in macro.items():
        if value is None or value == "":
            continue
        facts.append(
            FactRef(
                fact_id=f"macro:{key}",
                metric=f"macro:{key}",
                value=value,
                unit="index_points" if "vix" in key else "unknown",
                as_of=as_of,
                source_ref=base_source,
            )
        )
    return facts


def _build_data_quality_facts(context: AnalysisContext) -> list[FactRef]:
    """Surface data quality as explicit facts."""
    facts: list[FactRef] = []
    dq = context.data_quality or {}
    if not isinstance(dq, dict):
        return facts
    as_of = str(context.generated_at or "")
    for key, value in dq.items():
        if not isinstance(value, dict):
            continue
        status = value.get("status", "unknown")
        facts.append(
            FactRef(
                fact_id=f"dq:{key}",
                metric=f"data_quality:{key}",
                value=status,
                unit="status",
                as_of=as_of,
                source_ref="system:data_quality",
            )
        )
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
    evidence the LLM analyst may use, with clear source and freshness.
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
        quotes=tuple(_build_quote_facts(context, source_registry)),
        macro=tuple(_build_macro_facts(context, source_registry)),
        data_quality=tuple(_build_data_quality_facts(context)),
        source_registry=tuple(source_registry),
        metadata={
            "schema_version": _SNAPSHOT_SCHEMA_VERSION,
            "context_schema_version": getattr(context, "schema_version", None),
            "context_generated_at": str(context.generated_at or ""),
        },
    )


def snapshot_to_dict(snapshot: UnifiedAnalysisSnapshot) -> dict[str, Any]:
    """Serialize to a plain dict without internal inference."""
    from dataclasses import asdict

    return asdict(snapshot)
