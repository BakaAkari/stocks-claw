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


ActionSizeType = Literal["ratio", "shares", "cny_value", "defer"]
ActionHorizon = Literal["short", "medium", "long"]
ConfidenceLevel = Literal["low", "medium", "high"]


@dataclass(frozen=True, slots=True)
class AdvisoryAction:
    """One recommended or rejected action inside an InvestmentAdvisory.

    The LLM may output a `size` semantic such as a ratio or share count; the
    deterministic layer translates that into a monetary amount only if the
    evidence supports it. No free-form currency amounts from the LLM are allowed.
    """

    action_id: str
    target: str  # market:code, position_id, bucket name, or "none"
    action: str  # buy, sell, reduce, add, hold, watch, defer
    size: str  # e.g. "25%", "100 shares", "defer", "info_only"
    size_type: ActionSizeType
    reasoning: str
    evidence_refs: tuple[str, ...] = ()
    execute_when: str = ""
    cancel_when: str = ""
    horizon: ActionHorizon = "medium"
    confidence: ConfidenceLevel = "low"


@dataclass(frozen=True, slots=True)
class AdvisoryScenario:
    """A named scenario used in the advisory."""

    name: str  # base, bull, risk, inflation_shock, etc.
    description: str
    trigger: str
    invalidation: str
    evidence_refs: tuple[str, ...] = ()
    confidence: ConfidenceLevel = "low"


@dataclass(frozen=True, slots=True)
class AdvisoryForecast:
    """A testable forecast candidate; requires confirmation before becoming a bet."""

    forecast_id: str
    statement: str
    target: str  # market:code or empty
    metric: str  # close, pnl_pct, etc.
    comparator: str  # above, below
    level: str
    deadline: str  # ISO date
    confidence: ConfidenceLevel = "low"
    evidence_refs: tuple[str, ...] = ()
    requires_confirmation: bool = True


@dataclass(frozen=True, slots=True)
class InvestmentAdvisory:
    """Structured output from the LLM investment analyst.

    It is not the final user-visible report; it must still pass the
    AdvisoryValidator and produce a receipt before rendering or delivery.
    """

    advisory_id: str
    snapshot_id: str
    generated_at: str
    market_assessment: str = ""
    portfolio_assessment: str = ""
    actions: tuple[AdvisoryAction, ...] = ()
    hold_decisions: tuple[AdvisoryAction, ...] = ()
    do_not_do: tuple[str, ...] = ()
    sector_opportunities: tuple[AdvisoryAction, ...] = ()
    asset_class_opportunities: tuple[AdvisoryAction, ...] = ()
    watchlist_candidates: tuple[AdvisoryAction, ...] = ()
    scenarios: tuple[AdvisoryScenario, ...] = ()
    forecast_candidates: tuple[AdvisoryForecast, ...] = ()
    next_checkpoints: tuple[str, ...] = ()
    data_limitations: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)

    def all_actions(self) -> list[AdvisoryAction]:
        return list(self.actions) + list(self.hold_decisions)

    def action_by_id(self, action_id: str) -> Optional[AdvisoryAction]:
        for a in self.all_actions():
            if a.action_id == action_id:
                return a
        return None


@dataclass(frozen=True, slots=True)
class AdvisoryValidationReceipt:
    """Receipt produced after the deterministic validator checks an advisory."""

    status: str  # ok, warnings, errors, review_required
    schema_version: str
    validator_version: str
    prompt_contract_hash: str
    snapshot_hash: str
    advisory_content_hash: str
    validated_at: str
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    validated_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AssetIntakeDraft:
    """A proposed change to financial memory before user confirmation.

    Drafts are not facts. They must be confirmed by the user with a valid token
    before being atomically written to the authoritative memory store.
    """

    draft_id: str
    base_memory_hash: str
    generated_at: str
    accounts_to_add: tuple[dict[str, Any], ...] = ()
    positions_to_add: tuple[dict[str, Any], ...] = ()
    positions_to_update: tuple[dict[str, Any], ...] = ()
    positions_to_remove: tuple[str, ...] = ()
    profile_updates: tuple[dict[str, Any], ...] = ()
    ambiguities: tuple[dict[str, Any], ...] = ()
    source_quotes: tuple[FactRef, ...] = ()
    draft_hash: str = ""
    requires_confirmation: bool = True

    def all_confirmed_changes(self) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        for account in self.accounts_to_add:
            changes.append({"type": "add_account", "value": account})
        for position in self.positions_to_add:
            changes.append({"type": "add_position", "value": position})
        for update in self.positions_to_update:
            changes.append({"type": "update_position", "value": update})
        for position_id in self.positions_to_remove:
            changes.append({"type": "remove_position", "value": position_id})
        for profile in self.profile_updates:
            changes.append({"type": "update_profile", "value": profile})
        return changes
