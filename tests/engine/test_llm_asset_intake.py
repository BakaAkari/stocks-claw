"""Tests for LLM-assisted asset intake.

The LLM parser is a contract boundary. Without an LLM client it must fall back
to deterministic ambiguity, never invent a position.
"""
from __future__ import annotations

from stocks.engine.llm_asset_intake import parse_llm_asset_intake


class TestLlmAssetIntake:
    def test_without_llm_client_returns_ambiguity(self) -> None:
        draft = parse_llm_asset_intake("把红利低波减 10%")
        assert draft.draft_id
        assert len(draft.ambiguities) == 1
        assert draft.ambiguities[0]["field"] == "llm_client"
        assert not draft.positions_to_add
        assert not draft.positions_to_update

    def test_llm_client_dict_response_parsed(self) -> None:
        class FakeClient:
            def complete(self, prompt: str) -> dict:
                return {
                    "positions_to_update": [
                        {
                            "instrument_key": "a:512590",
                            "delta_amount_cny": -2400.0,
                            "confidence": "low",
                        }
                    ],
                    "ambiguities": [],
                }

        draft = parse_llm_asset_intake("把红利低波减 10%", llm_client=FakeClient())
        assert len(draft.positions_to_update) == 1
        assert draft.positions_to_update[0]["instrument_key"] == "a:512590"
        assert draft.positions_to_update[0]["delta_amount_cny"] == -2400.0

    def test_llm_client_string_response_parsed(self) -> None:
        class FakeClient:
            def complete(self, prompt: str) -> str:
                return '{"positions_to_add": [{"instrument_key": "a:510300", "amount_cny": 10000}]}'

        draft = parse_llm_asset_intake("买了 510300 一万元", llm_client=FakeClient())
        assert len(draft.positions_to_add) == 1
        assert draft.positions_to_add[0]["amount_cny"] == 10000

    def test_llm_failure_returns_ambiguity(self) -> None:
        class BadClient:
            def complete(self, prompt: str) -> str:
                return "not valid json"

        draft = parse_llm_asset_intake("买买买", llm_client=BadClient())
        assert len(draft.ambiguities) == 1
        assert draft.ambiguities[0]["field"] == "llm_parse"

    def test_llm_source_quotes_are_ignored(self) -> None:
        class FakeClient:
            def complete(self, prompt: str) -> dict:
                return {
                    "source_quotes": [{"instrument_key": "a:510300", "price": 4.92}],
                }

        draft = parse_llm_asset_intake("查看行情", llm_client=FakeClient())
        assert not draft.source_quotes
        assert not draft.positions_to_add
        assert not draft.positions_to_update
        assert not draft.positions_to_remove
