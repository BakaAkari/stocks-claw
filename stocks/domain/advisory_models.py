"""Data models for the LLM investment advisory architecture.

These models are immutable-by-convention dataclasses. They describe the
contract between the deterministic data layer and the LLM investment analyst.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

SnapshotTrigger = Literal["scheduled", "event", "manual", "test"]
MarketScope = Literal["cn", "us", "global", "test"]


@dataclass(frozen=True, slots=True)
class FactRef:
    """A typed, traceable fact inside a snapshot.

    `metric` is a stable name, e.g. "vix_close", "aapl_price", "news_count".
    `unit` is the unit of the value, e.g. "index_points", "cny", "count".
    `source_ref` points to an entry in `source_registry`.
    """

    fact_id: str
    metric: str
    value: Any
    unit: str
    as_of: str  # ISO-8601 UTC
    source_ref: str


@dataclass(frozen=True, slots=True)
class SourceEntry:
    """One data source used by the snapshot."""

    source_id: str
    provider: str
    endpoint_type: str
    as_of: str
    freshness: str
    status: str
    fallback_chain: tuple[str, ...] = ()
    error_category: str = ""


@dataclass(frozen=True, slots=True)
class DataQualitySummary:
    """Minimal, deterministic data quality node."""

    status: str
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UnifiedAnalysisSnapshot:
    """Single source of truth consumed by the LLM investment analyst.

    Every field is either a typed fact or a structured collection of facts.
    No internal reasoning, no API keys, no opaque blobs.
    """

    snapshot_id: str
    generated_at: str
    trigger: SnapshotTrigger
    session: str
    market_scope: MarketScope
    portfolio: tuple[FactRef, ...] = ()
    profile: tuple[FactRef, ...] = ()
    quotes: tuple[FactRef, ...] = ()
    history_features: tuple[FactRef, ...] = ()
    technical_evidence: tuple[FactRef, ...] = ()
    news_clusters: tuple[FactRef, ...] = ()
    filings: tuple[FactRef, ...] = ()
    macro: tuple[FactRef, ...] = ()
    upcoming_events: tuple[FactRef, ...] = ()
    rotation: tuple[FactRef, ...] = ()
    portfolio_constraints: tuple[FactRef, ...] = ()
    risk_context: tuple[FactRef, ...] = ()
    candidate_signals: tuple[FactRef, ...] = ()
    data_quality: tuple[FactRef, ...] = ()
    source_registry: tuple[SourceEntry, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)

    def facts_by_metric(self, metric: str) -> list[FactRef]:
        return [f for f in self.all_facts() if f.metric == metric]

    def all_facts(self) -> list[FactRef]:
        return [
            f
            for collection in (
                self.portfolio,
                self.profile,
                self.quotes,
                self.history_features,
                self.technical_evidence,
                self.news_clusters,
                self.filings,
                self.macro,
                self.upcoming_events,
                self.rotation,
                self.portfolio_constraints,
                self.risk_context,
                self.candidate_signals,
                self.data_quality,
            )
            for f in collection
        ]

    def source_by_id(self, source_id: str) -> Optional[SourceEntry]:
        for s in self.source_registry:
            if s.source_id == source_id:
                return s
        return None
