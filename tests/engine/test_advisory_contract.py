"""Tests for InvestmentAdvisory v1 contract and validation.

These tests verify that the advisory contract is structured, traceable, and
feasible-checkable without letting the validator silently rewrite decisions.
"""
from __future__ import annotations

from stocks.domain.advisory_models import (
    AdvisoryAction,
    AdvisoryForecast,
    AdvisoryScenario,
    InvestmentAdvisory,
)
from stocks.engine.advisory_contract import validate_advisory


class TestAdvisoryContract:
    def test_minimal_valid_advisory(self) -> None:
        advisory = InvestmentAdvisory(
            advisory_id="a1",
            snapshot_id="s1",
            generated_at="2026-07-22T10:00:00+00:00",
            market_assessment="neutral",
            portfolio_assessment="hold",
        )
        receipt = validate_advisory(advisory)
        assert receipt.status in {"ok", "warnings"}
        assert receipt.schema_version == "1"
        assert receipt.validator_version == "1"
        assert receipt.advisory_content_hash
        assert receipt.validated_at

    def test_advisory_action_requires_evidence_and_conditions(self) -> None:
        action = AdvisoryAction(
            action_id="ac1",
            target="a:510300",
            action="add",
            size="10%",
            size_type="ratio",
            reasoning=" RSI oversold on high volume",
            evidence_refs=("fact:vix_close", "fact:rsi_510300"),
            execute_when=" RSI < 30 and price above 20dma",
            cancel_when="RSI > 50",
        )
        advisory = InvestmentAdvisory(
            advisory_id="a1",
            snapshot_id="s1",
            generated_at="2026-07-22T10:00:00+00:00",
            market_assessment="oversold",
            actions=(action,),
            next_checkpoints=("check RSI in 3 sessions",),
        )
        receipt = validate_advisory(advisory)
        assert receipt.status in {"ok", "warnings"}

    def test_validator_rejects_unknown_action(self) -> None:
        action = AdvisoryAction(
            action_id="ac1",
            target="a:510300",
            action="YOLO",
            size="all",
            size_type="ratio",
            reasoning="",
        )
        advisory = InvestmentAdvisory(
            advisory_id="a1",
            snapshot_id="s1",
            generated_at="2026-07-22T10:00:00+00:00",
            actions=(action,),
        )
        receipt = validate_advisory(advisory)
        assert receipt.status == "errors"
        assert any("unknown action" in e for e in receipt.errors)

    def test_validator_warns_missing_evidence(self) -> None:
        action = AdvisoryAction(
            action_id="ac1",
            target="a:510300",
            action="hold",
            size="defer",
            size_type="defer",
            reasoning="await clarity",
        )
        advisory = InvestmentAdvisory(
            advisory_id="a1",
            snapshot_id="s1",
            generated_at="2026-07-22T10:00:00+00:00",
            actions=(action,),
        )
        receipt = validate_advisory(advisory)
        assert receipt.status == "warnings"
        assert any("no evidence_refs" in w for w in receipt.warnings)

    def test_validator_can_only_warn_or_reject_not_rewrite(self) -> None:
        """The validator must never change the action direction."""
        action = AdvisoryAction(
            action_id="ac1",
            target="a:510300",
            action="sell",
            size="20%",
            size_type="ratio",
            reasoning="stop loss",
            evidence_refs=("fact:price_510300",),
        )
        advisory = InvestmentAdvisory(
            advisory_id="a1",
            snapshot_id="s1",
            generated_at="2026-07-22T10:00:00+00:00",
            actions=(action,),
        )
        _ = validate_advisory(advisory)
        # Validator is allowed to warn, but not to silently change the action.
        assert action.action == "sell"
        assert action.target == "a:510300"
        assert action.size_type == "ratio"

    def test_forecast_requires_confirmation_and_deadline(self) -> None:
        forecast = AdvisoryForecast(
            forecast_id="f1",
            statement="VIX will fall below 15",
            target="^VIX",
            metric="close",
            comparator="below",
            level="15",
            deadline="2026-07-29",
        )
        advisory = InvestmentAdvisory(
            advisory_id="a1",
            snapshot_id="s1",
            generated_at="2026-07-22T10:00:00+00:00",
            forecast_candidates=(forecast,),
        )
        receipt = validate_advisory(advisory)
        assert receipt.status in {"ok", "warnings"}
        assert forecast.requires_confirmation is True

    def test_forecast_without_deadline_is_error(self) -> None:
        forecast = AdvisoryForecast(
            forecast_id="f1",
            statement="market will crash",
            target="",
            metric="",
            comparator="",
            level="",
            deadline="",
        )
        advisory = InvestmentAdvisory(
            advisory_id="a1",
            snapshot_id="s1",
            generated_at="2026-07-22T10:00:00+00:00",
            forecast_candidates=(forecast,),
        )
        receipt = validate_advisory(advisory)
        assert receipt.status == "errors"
        assert any("deadline is required" in e for e in receipt.errors)

    def test_scenario_needs_trigger_and_invalidation(self) -> None:
        scenario = AdvisoryScenario(
            name="bull_case",
            description="earnings beat",
            trigger="Q2 EPS > consensus 10%",
            invalidation="EPS miss or guidance cut",
        )
        advisory = InvestmentAdvisory(
            advisory_id="a1",
            snapshot_id="s1",
            generated_at="2026-07-22T10:00:00+00:00",
            scenarios=(scenario,),
        )
        receipt = validate_advisory(advisory)
        assert receipt.status in {"ok", "warnings"}

    def test_receipt_hashes_stable_for_same_advisory(self) -> None:
        advisory = InvestmentAdvisory(
            advisory_id="a1",
            snapshot_id="s1",
            generated_at="2026-07-22T10:00:00+00:00",
        )
        r1 = validate_advisory(advisory, snapshot_hash="abc", prompt_contract_hash="def")
        r2 = validate_advisory(advisory, snapshot_hash="abc", prompt_contract_hash="def")
        assert r1.advisory_content_hash == r2.advisory_content_hash
        assert r1.snapshot_hash == r2.snapshot_hash == "abc"
        assert r1.prompt_contract_hash == r2.prompt_contract_hash == "def"
        assert r1.validated_at != r2.validated_at

    def test_advisory_action_finds_by_id(self) -> None:
        action = AdvisoryAction(
            action_id="ac1",
            target="a:510300",
            action="add",
            size="10%",
            size_type="ratio",
            reasoning="RSI oversold",
        )
        advisory = InvestmentAdvisory(
            advisory_id="a1",
            snapshot_id="s1",
            generated_at="2026-07-22T10:00:00+00:00",
            actions=(action,),
        )
        assert advisory.action_by_id("ac1") == action
        assert advisory.action_by_id("missing") is None
