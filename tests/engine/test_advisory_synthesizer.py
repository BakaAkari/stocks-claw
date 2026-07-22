"""Tests for the LLM advisory synthesizer.

The synthesizer is the boundary where the LLM becomes the final investment
analyst. Without an LLM client it must fall back to a safe hold advisory.
"""
from __future__ import annotations

import json
from dataclasses import asdict

from stocks.domain.advisory_models import UnifiedAnalysisSnapshot
from stocks.engine.advisory_synthesizer import (
    synthesize_advisory,
    synthesize_and_validate,
)


def _snapshot() -> UnifiedAnalysisSnapshot:
    return UnifiedAnalysisSnapshot(
        snapshot_id="s1",
        generated_at="2026-07-22T10:00:00+00:00",
        trigger="scheduled",
        session="cn_pre_open",
        market_scope="cn",
    )


class TestAdvisorySynthesizer:
    def test_fallback_without_llm_client(self) -> None:
        snapshot = _snapshot()
        advisory = synthesize_advisory(snapshot)
        assert advisory.advisory_id
        assert advisory.snapshot_id == "s1"
        assert advisory.market_assessment == "data captured, no LLM analysis available"
        assert len(advisory.hold_decisions) == 1
        assert advisory.hold_decisions[0].action == "hold"
        assert advisory.do_not_do

    def test_fallback_has_data_limitations(self) -> None:
        snapshot = _snapshot()
        advisory = synthesize_advisory(snapshot)
        assert any("LLM analyst unavailable" in lim for lim in advisory.data_limitations)

    def test_llm_dict_response_parsed(self) -> None:
        class FakeClient:
            def complete(self, prompt: str) -> dict:
                return {
                    "market_assessment": "oversold",
                    "portfolio_assessment": "hold",
                    "actions": [
                        {
                            "action_id": "a1",
                            "target": "a:510300",
                            "action": "add",
                            "size": "10%",
                            "size_type": "ratio",
                            "reasoning": "RSI below 30",
                            "evidence_refs": ["fact:510300:rsi_14"],
                            "execute_when": "RSI below 30",
                            "cancel_when": "RSI above 50",
                            "horizon": "short",
                            "confidence": "medium",
                        }
                    ],
                    "next_checkpoints": ["check RSI in 3 sessions"],
                    "data_limitations": ["macro data stale"],
                }

        snapshot = _snapshot()
        advisory = synthesize_advisory(snapshot, llm_client=FakeClient())
        assert advisory.market_assessment == "oversold"
        assert len(advisory.actions) == 1
        action = advisory.actions[0]
        assert action.target == "a:510300"
        assert action.size_type == "ratio"
        assert action.evidence_refs

    def test_llm_string_response_parsed(self) -> None:
        class FakeClient:
            def complete(self, prompt: str) -> str:
                return json.dumps({
                    "market_assessment": "neutral",
                    "do_not_do": ["do not chase high beta"],
                })

        snapshot = _snapshot()
        advisory = synthesize_advisory(snapshot, llm_client=FakeClient())
        assert advisory.market_assessment == "neutral"
        assert advisory.do_not_do == ("do not chase high beta",)

    def test_llm_failure_returns_fallback(self) -> None:
        class BadClient:
            def complete(self, prompt: str) -> str:
                return "not valid json"

        snapshot = _snapshot()
        advisory = synthesize_advisory(snapshot, llm_client=BadClient())
        assert advisory.hold_decisions
        assert any("synthesis error" in lim for lim in advisory.data_limitations)

    def test_synthesize_and_validate_returns_receipt(self) -> None:
        class FakeClient:
            def complete(self, prompt: str) -> dict:
                return {
                    "market_assessment": "neutral",
                    "actions": [
                        {
                            "action_id": "a1",
                            "target": "a:510300",
                            "action": "add",
                            "size": "10%",
                            "size_type": "ratio",
                            "reasoning": "RSI oversold",
                            "evidence_refs": ["fact:510300:rsi_14"],
                        }
                    ],
                    "next_checkpoints": ["check RSI"],
                }

        snapshot = _snapshot()
        advisory, receipt = synthesize_and_validate(snapshot, llm_client=FakeClient())
        assert advisory.advisory_id
        assert receipt["status"] in {"ok", "warnings"}
        assert receipt["snapshot_hash"] == snapshot.snapshot_id
        assert receipt["advisory_content_hash"]

    def test_advisory_does_not_contain_api_keys(self) -> None:
        snapshot = _snapshot()
        advisory = synthesize_advisory(snapshot)
        dump = json.dumps(asdict(advisory), default=str)
        assert "sk-" not in dump
        assert "OPENAI" not in dump
