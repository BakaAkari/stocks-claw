"""Tests for AssetIntakeDraft confirmation and atomic write boundary.

The writer delegates to the existing memory layer; this test verifies the
contract: token, hash stability, ambiguity rejection, and change replay.
"""
from __future__ import annotations

from typing import Any

from stocks.domain.advisory_models import AssetIntakeDraft
from stocks.engine.asset_intake_writer import AssetIntakeWriter


class TestAssetIntakeWriter:
    def test_token_matches_current_memory_hash(self) -> None:
        memory_hash = "memory-v1"
        applied: list[Any] = []
        writer = AssetIntakeWriter(
            get_memory_hash=lambda: memory_hash,
            apply_change=applied.append,
        )
        draft = AssetIntakeDraft(
            draft_id="d1",
            base_memory_hash=memory_hash,
            generated_at="2026-07-22T10:00:00+00:00",
            positions_to_add=(
                {"instrument_key": "a:510300", "amount_cny": 10000},
            ),
        )
        token = writer.generate_token(draft)
        result = writer.apply(draft, token)
        assert result["status"] == "applied"
        assert len(applied) == 1

    def test_rejects_stale_token(self) -> None:
        memory_hash = "memory-v1"
        applied: list[Any] = []
        writer = AssetIntakeWriter(
            get_memory_hash=lambda: memory_hash,
            apply_change=applied.append,
        )
        draft = AssetIntakeDraft(
            draft_id="d1",
            base_memory_hash="memory-v0",
            generated_at="2026-07-22T10:00:00+00:00",
            positions_to_add=(
                {"instrument_key": "a:510300", "amount_cny": 10000},
            ),
        )
        token = writer.generate_token(draft)
        result = writer.apply(draft, token)
        assert result["status"] == "rejected"
        assert result["reason"] == "memory changed since draft was created"
        assert not applied

    def test_rejects_invalid_token(self) -> None:
        memory_hash = "memory-v1"
        applied: list[Any] = []
        writer = AssetIntakeWriter(
            get_memory_hash=lambda: memory_hash,
            apply_change=applied.append,
        )
        draft = AssetIntakeDraft(
            draft_id="d1",
            base_memory_hash=memory_hash,
            generated_at="2026-07-22T10:00:00+00:00",
            positions_to_add=(
                {"instrument_key": "a:510300", "amount_cny": 10000},
            ),
        )
        result = writer.apply(draft, "not-a-token")
        assert result["status"] == "rejected"
        assert "invalid" in result["reason"]
        assert not applied

    def test_rejects_ambiguities(self) -> None:
        memory_hash = "memory-v1"
        applied: list[Any] = []
        writer = AssetIntakeWriter(
            get_memory_hash=lambda: memory_hash,
            apply_change=applied.append,
        )
        draft = AssetIntakeDraft(
            draft_id="d1",
            base_memory_hash=memory_hash,
            generated_at="2026-07-22T10:00:00+00:00",
            positions_to_add=(
                {"instrument_key": "a:510300", "amount_cny": 10000},
            ),
            ambiguities=(
                {"field": "amount", "reason": "missing amount"},
            ),
        )
        token = writer.generate_token(draft)
        result = writer.apply(draft, token)
        assert result["status"] == "rejected"
        assert result["reason"] == "draft contains unresolved ambiguities"
        assert not applied

    def test_rejects_no_changes(self) -> None:
        memory_hash = "memory-v1"
        applied: list[Any] = []
        writer = AssetIntakeWriter(
            get_memory_hash=lambda: memory_hash,
            apply_change=applied.append,
        )
        draft = AssetIntakeDraft(
            draft_id="d1",
            base_memory_hash=memory_hash,
            generated_at="2026-07-22T10:00:00+00:00",
        )
        token = writer.generate_token(draft)
        result = writer.apply(draft, token)
        assert result["status"] == "rejected"
        assert result["reason"] == "no changes to apply"
        assert not applied
