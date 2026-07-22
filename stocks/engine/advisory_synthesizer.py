"""Generate an InvestmentAdvisory from a UnifiedAnalysisSnapshot.

The LLM is the final investment analyst. It consumes the evidence snapshot and
produces a structured InvestmentAdvisory. It is not allowed to write to memory,
output free-form currency amounts, or make decisions without evidence references.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from stocks.domain.advisory_models import (
    AdvisoryAction,
    AdvisoryForecast,
    AdvisoryScenario,
    InvestmentAdvisory,
    UnifiedAnalysisSnapshot,
)
from stocks.engine.advisory_contract import validate_advisory
from stocks.logging_utils import get_logger

logger = get_logger("advisory_synthesizer")


DEFAULT_ADVISORY_PROMPT = """You are a conservative private investment analyst.

You are given a UnifiedAnalysisSnapshot containing evidence: portfolio facts,
quotes, technical indicators, news digest, macro data, upcoming events, rotation
signals, and action signals. Produce an InvestmentAdvisory in JSON only.

Output JSON format:
{{
  "advisory_id": "auto-generated-by-system",
  "market_assessment": "string",
  "portfolio_assessment": "string",
  "actions": [],
  "hold_decisions": [],
  "do_not_do": ["string"],
  "sector_opportunities": [],
  "asset_class_opportunities": [],
  "watchlist_candidates": [],
  "scenarios": [],
  "forecast_candidates": [],
  "next_checkpoints": ["string"],
  "data_limitations": ["string"]
}}

Action object fields:
{{
  "action_id": "string",
  "target": "market:code or position_id",
  "action": "buy|sell|reduce|add|hold|watch|defer",
  "size": "e.g. 10% or 100 shares or defer",
  "size_type": "ratio|shares|cny_value|defer",
  "reasoning": "string",
  "evidence_refs": ["fact_id"],
  "execute_when": "string",
  "cancel_when": "string",
  "horizon": "short|medium|long",
  "confidence": "low|medium|high"
}}

Scenario object fields:
{{
  "name": "string",
  "description": "string",
  "trigger": "string",
  "invalidation": "string",
  "evidence_refs": ["fact_id"],
  "confidence": "low|medium|high"
}}

Forecast object fields:
{{
  "forecast_id": "string",
  "statement": "string",
  "target": "market:code",
  "metric": "close|pnl_pct|etc",
  "comparator": "above|below",
  "level": "string",
  "deadline": "YYYY-MM-DD",
  "confidence": "low|medium|high"
}}

Rules:
- Every action must include at least one `evidence_refs` from the snapshot.
- Do NOT output absolute CNY amounts. Use ratios, share counts, or "defer".
- Include "do_not_do" items where the evidence is too weak or the risk is high.
- In "data_limitations", list missing or stale data that would change your view.
- The user is the final decision maker. You are providing evidence-based advice.

Snapshot:
{snapshot_json}
"""


def _iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_actions(items: list[Any]) -> tuple[AdvisoryAction, ...]:
    actions = []
    for item in items:
        if isinstance(item, str):
            actions.append(
                AdvisoryAction(
                    action_id="",
                    target=item,
                    action="watch",
                    size="defer",
                    size_type="defer",
                    reasoning="Candidate identified by LLM analyst",
                )
            )
            continue
        if not isinstance(item, dict):
            continue
        actions.append(
            AdvisoryAction(
                action_id=str(item.get("action_id", "")),
                target=str(item.get("target", item.get("name", ""))),
                action=str(item.get("action", "")),
                size=str(item.get("size", "")),
                size_type=str(item.get("size_type", "defer")),
                reasoning=str(item.get("reasoning", "")),
                evidence_refs=tuple(item.get("evidence_refs", []) or []),
                execute_when=str(item.get("execute_when", "")),
                cancel_when=str(item.get("cancel_when", "")),
                horizon=str(item.get("horizon", "medium")),
                confidence=str(item.get("confidence", "low")),
            )
        )
    return tuple(actions)


def _parse_scenarios(items: list[dict[str, Any]]) -> tuple[AdvisoryScenario, ...]:
    return tuple(
        AdvisoryScenario(
            name=str(item.get("name", "")),
            description=str(item.get("description", "")),
            trigger=str(item.get("trigger", "")),
            invalidation=str(item.get("invalidation", "")),
            evidence_refs=tuple(item.get("evidence_refs", []) or []),
            confidence=str(item.get("confidence", "low")),
        )
        for item in items
    )


def _parse_forecasts(items: list[dict[str, Any]]) -> tuple[AdvisoryForecast, ...]:
    return tuple(
        AdvisoryForecast(
            forecast_id=str(item.get("forecast_id", "")),
            statement=str(item.get("statement", "")),
            target=str(item.get("target", "")),
            metric=str(item.get("metric", "")),
            comparator=str(item.get("comparator", "")),
            level=str(item.get("level", "")),
            deadline=str(item.get("deadline", "")),
            confidence=str(item.get("confidence", "low")),
            evidence_refs=tuple(item.get("evidence_refs", []) or []),
            requires_confirmation=True,
        )
        for item in items
    )


def synthesize_advisory(
    snapshot: UnifiedAnalysisSnapshot,
    *,
    llm_client: Any | None = None,
    advisory_id: str = "",
) -> InvestmentAdvisory:
    """Synthesize an InvestmentAdvisory from a snapshot using the LLM analyst.

    If no LLM client is available, the synthesizer falls back to a deterministic
    "hold and review" advisory with data limitations, never inventing a trade.
    """
    generated_at = _iso_utc()
    advisory_id = advisory_id or _advisory_id(snapshot, generated_at)

    if llm_client is None:
        logger.info("no LLM client; returning fallback hold advisory")
        return _fallback_advisory(snapshot, advisory_id, generated_at)

    try:
        snapshot_json = json.dumps(asdict(snapshot), ensure_ascii=False, default=str)
        prompt = DEFAULT_ADVISORY_PROMPT.format(snapshot_json=snapshot_json)
        response = llm_client.complete(prompt)
        parsed = _parse_llm_response(response)
    except Exception as e:
        logger.warning("advisory synthesis failed", extra={"error": str(e)})
        return _fallback_advisory(snapshot, advisory_id, generated_at, error=str(e))

    return InvestmentAdvisory(
        advisory_id=advisory_id,
        snapshot_id=snapshot.snapshot_id,
        generated_at=generated_at,
        market_assessment=str(parsed.get("market_assessment", "")),
        portfolio_assessment=str(parsed.get("portfolio_assessment", "")),
        actions=_parse_actions(parsed.get("actions", [])),
        hold_decisions=_parse_actions(parsed.get("hold_decisions", [])),
        do_not_do=tuple(parsed.get("do_not_do", [])),
        sector_opportunities=_parse_actions(parsed.get("sector_opportunities", [])),
        asset_class_opportunities=_parse_actions(parsed.get("asset_class_opportunities", [])),
        watchlist_candidates=_parse_actions(parsed.get("watchlist_candidates", [])),
        scenarios=_parse_scenarios(parsed.get("scenarios", [])),
        forecast_candidates=_parse_forecasts(parsed.get("forecast_candidates", [])),
        next_checkpoints=tuple(parsed.get("next_checkpoints", [])),
        data_limitations=tuple(parsed.get("data_limitations", [])),
    )


def _advisory_id(snapshot: UnifiedAnalysisSnapshot, generated_at: str) -> str:
    import hashlib

    h = hashlib.sha256()
    h.update(snapshot.snapshot_id.encode("utf-8"))
    h.update(generated_at.encode("utf-8"))
    return h.hexdigest()[:16]


def _parse_llm_response(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    if isinstance(response, str):
        return json.loads(response)
    return json.loads(str(response))


def _fallback_advisory(
    snapshot: UnifiedAnalysisSnapshot,
    advisory_id: str,
    generated_at: str,
    error: str = "",
) -> InvestmentAdvisory:
    limitations = ["LLM analyst unavailable; no discretionary advice generated"]
    if error:
        limitations.append(f"synthesis error: {error}")
    return InvestmentAdvisory(
        advisory_id=advisory_id,
        snapshot_id=snapshot.snapshot_id,
        generated_at=generated_at,
        market_assessment="data captured, no LLM analysis available",
        portfolio_assessment="hold current positions pending LLM review",
        hold_decisions=(
            AdvisoryAction(
                action_id="hold_default",
                target="portfolio",
                action="hold",
                size="defer",
                size_type="defer",
                reasoning="No LLM client configured; default to hold.",
                evidence_refs=(snapshot.snapshot_id,),
                execute_when="LLM analyst available",
                cancel_when="never",
            ),
        ),
        do_not_do=("do not trade on this fallback advisory",),
        next_checkpoints=("re-run with LLM client configured",),
        data_limitations=tuple(limitations),
    )


def synthesize_and_validate(
    snapshot: UnifiedAnalysisSnapshot,
    *,
    llm_client: Any | None = None,
    advisory_id: str = "",
    prompt_contract_hash: str = "",
) -> tuple[InvestmentAdvisory, dict[str, Any]]:
    """Convenience: synthesize advisory and immediately validate it."""
    advisory = synthesize_advisory(snapshot, llm_client=llm_client, advisory_id=advisory_id)
    receipt = validate_advisory(
        advisory,
        snapshot_hash=snapshot.snapshot_id,
        prompt_contract_hash=prompt_contract_hash,
    )
    return advisory, asdict(receipt)
