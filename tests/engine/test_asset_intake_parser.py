"""Tests for natural-language asset intake parser.

The parser is conservative: it extracts what it can, marks uncertainty, and never
writes anything to the authoritative memory.
"""
from __future__ import annotations

from stocks.domain.advisory_models import FactRef
from stocks.engine.asset_intake_parser import (
    parse_asset_intake,
    verify_draft_token,
)


class TestAssetIntakeParser:
    def test_extracts_a_stock_product_and_amount(self) -> None:
        text = "我今天买了 510300 一万元"
        draft = parse_asset_intake(text)
        assert draft.draft_id
        assert draft.positions_to_add
        pos = draft.positions_to_add[0]
        assert pos["instrument_key"] == "a:510300"
        assert pos["amount_cny"] == 10_000
        assert pos["confidence"] == "low"

    def test_marks_missing_amount_as_ambiguity(self) -> None:
        text = "我想买点 510300"
        draft = parse_asset_intake(text)
        assert draft.positions_to_add
        assert draft.positions_to_add[0]["amount_cny"] is None
        assert any(a["field"] == "amount" for a in draft.ambiguities)

    def test_marks_unknown_product_as_ambiguity(self) -> None:
        text = "我买了一万元"
        draft = parse_asset_intake(text)
        assert not draft.positions_to_add
        assert any(a["field"] == "instrument_key" for a in draft.ambiguities)

    def test_draft_hash_changes_when_content_changes(self) -> None:
        d1 = parse_asset_intake("买了 510300 10000")
        d2 = parse_asset_intake("买了 510300 20000")
        assert d1.draft_hash != d2.draft_hash
        assert d1.draft_hash

    def test_base_memory_hash_in_draft_id(self) -> None:
        d1 = parse_asset_intake("买了 510300 10000", base_memory_hash="h1")
        d2 = parse_asset_intake("买了 510300 10000", base_memory_hash="h2")
        assert d1.draft_id != d2.draft_id

    def test_source_quote_attached_to_draft(self) -> None:
        quote = FactRef(
            fact_id="q1",
            metric="quote:a:510300:price",
            value=4.92,
            unit="cny",
            as_of="2026-07-22T10:00:00+00:00",
            source_ref="eastmoney",
        )
        draft = parse_asset_intake("买了 510300 10000", source_quote=quote)
        assert len(draft.source_quotes) == 1
        assert draft.source_quotes[0].value == 4.92

    def test_token_verification_requires_current_memory_hash(self) -> None:
        draft = parse_asset_intake("买了 510300 10000", base_memory_hash="h1")
        token = _content_hash(f"{draft.draft_id}:h1")
        assert verify_draft_token(draft, token, "h1") is True
        assert verify_draft_token(draft, token, "h2") is False
        assert verify_draft_token(draft, "wrong", "h1") is False


def _content_hash(value: str) -> str:
    import hashlib
    h = hashlib.sha256()
    h.update(value.encode("utf-8"))
    return h.hexdigest()[:24]
