"""Deterministic contract validation for InvestmentAdvisory v1.

The validator does not re-invest; it only checks evidence, feasibility, and
structural integrity. It may reject or request correction, but it must not
silently rewrite an action into a different one.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from stocks.domain.advisory_models import (
    AdvisoryAction,
    AdvisoryForecast,
    AdvisoryOutlook,
    AdvisoryScenario,
    AdvisoryValidationReceipt,
    InvestmentAdvisory,
)

_SCHEMA_VERSION = "1"
_VALIDATOR_VERSION = "1"


def _content_hash(value: Any) -> str:
    h = hashlib.sha256()
    h.update(str(value).encode("utf-8"))
    return h.hexdigest()[:24]


def _iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_action(action: AdvisoryAction, errors: list[str], warnings: list[str]) -> None:
    if not action.action_id:
        errors.append("action.action_id is required")
    if not action.target:
        errors.append(f"action {action.action_id}: target is required")
    if action.action not in {
        "buy",
        "sell",
        "reduce",
        "add",
        "hold",
        "watch",
        "defer",
        "info_only",
    }:
        errors.append(f"action {action.action_id}: unknown action '{action.action}'")
    if action.size_type not in {"ratio", "shares", "cny_value", "defer"}:
        errors.append(f"action {action.action_id}: invalid size_type '{action.size_type}'")
    if action.size_type in {"ratio", "shares", "cny_value"} and not action.size:
        errors.append(f"action {action.action_id}: size required for size_type {action.size_type}")
    if action.action in {"buy", "add", "reduce", "sell"} and not action.execute_when:
        warnings.append(f"action {action.action_id}: execute_when is recommended for actionable items")
    if not action.evidence_refs:
        warnings.append(f"action {action.action_id}: no evidence_refs")
    if not action.reasoning:
        warnings.append(f"action {action.action_id}: reasoning is empty")


def _validate_scenario(scenario: AdvisoryScenario, errors: list[str], warnings: list[str]) -> None:
    if not scenario.name:
        errors.append("scenario.name is required")
    if not scenario.trigger:
        warnings.append(f"scenario {scenario.name}: trigger is empty")
    if not scenario.invalidation:
        warnings.append(f"scenario {scenario.name}: invalidation is empty")


def _validate_forecast(forecast: AdvisoryForecast, errors: list[str], warnings: list[str]) -> None:
    if not forecast.forecast_id:
        errors.append("forecast.forecast_id is required")
    if not forecast.target:
        warnings.append(f"forecast {forecast.forecast_id}: target is empty; will be treated as manual")
    if not forecast.level:
        warnings.append(f"forecast {forecast.forecast_id}: level is empty")
    if not forecast.deadline:
        errors.append(f"forecast {forecast.forecast_id}: deadline is required")
    if not forecast.requires_confirmation:
        warnings.append(f"forecast {forecast.forecast_id}: requires_confirmation should be true")


def _validate_outlook(
    outlook: AdvisoryOutlook | None,
    field: str,
    errors: list[str],
    warnings: list[str],
) -> None:
    """M2: a present horizon judgment must be complete and evidence-bound."""
    if outlook is None:
        warnings.append(f"{field} outlook is absent")
        return
    if outlook.direction not in {"supportive", "neutral", "adverse", "uncertain", "mixed"}:
        errors.append(f"{field}.direction '{outlook.direction}' is not in the allowed vocabulary")
    if not outlook.rationale:
        warnings.append(f"{field}.rationale is empty")
    if not outlook.validation:
        warnings.append(f"{field}.validation is empty")
    if not outlook.falsification:
        errors.append(f"{field}.falsification is required (never an unfalsifiable judgment)")
    if not outlook.source_refs:
        warnings.append(f"{field}.source_refs is empty")


def validate_advisory(
    advisory: InvestmentAdvisory,
    *,
    snapshot_hash: str = "",
    prompt_contract_hash: str = "",
) -> AdvisoryValidationReceipt:
    """Validate advisory structure and feasibility without re-investing."""
    errors: list[str] = []
    warnings: list[str] = []
    validated_fields: list[str] = []

    if not advisory.advisory_id:
        errors.append("advisory_id is required")
    if not advisory.snapshot_id:
        errors.append("snapshot_id is required")
    if not advisory.market_assessment:
        warnings.append("market_assessment is empty")
    if not advisory.portfolio_assessment:
        warnings.append("portfolio_assessment is empty")

    _validate_outlook(advisory.short_term, "short_term", errors, warnings)
    validated_fields.append("short_term")
    _validate_outlook(advisory.medium_term, "medium_term", errors, warnings)
    validated_fields.append("medium_term")

    for action in advisory.actions:
        _validate_action(action, errors, warnings)
    validated_fields.append("actions")

    for action in advisory.hold_decisions:
        _validate_action(action, errors, warnings)
    validated_fields.append("hold_decisions")

    for scenario in advisory.scenarios:
        _validate_scenario(scenario, errors, warnings)
    validated_fields.append("scenarios")

    for forecast in advisory.forecast_candidates:
        _validate_forecast(forecast, errors, warnings)
    validated_fields.append("forecast_candidates")

    if advisory.actions and not advisory.next_checkpoints:
        warnings.append("actions present but no next_checkpoints")
    validated_fields.append("next_checkpoints")

    if errors:
        status = "errors"
    elif warnings:
        status = "warnings"
    else:
        status = "ok"

    return AdvisoryValidationReceipt(
        status=status,
        schema_version=_SCHEMA_VERSION,
        validator_version=_VALIDATOR_VERSION,
        prompt_contract_hash=prompt_contract_hash or _content_hash("prompt_contract_default"),
        snapshot_hash=snapshot_hash or _content_hash(advisory.snapshot_id),
        advisory_content_hash=_content_hash(asdict(advisory)),
        validated_at=_iso_utc(),
        warnings=tuple(warnings),
        errors=tuple(errors),
        validated_fields=tuple(validated_fields),
    )
