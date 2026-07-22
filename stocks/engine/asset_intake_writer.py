"""Confirmation and atomic-write boundary for AssetIntakeDraft.

This module enforces the rule: a draft is not a fact until the user confirms it
with a valid token and the underlying memory hash has not changed. Even then,
writing is delegated to the existing financial memory layer; this module only
validates the contract.
"""
from __future__ import annotations

from typing import Any, Callable

from stocks.domain.advisory_models import AssetIntakeDraft
from stocks.engine.asset_intake_parser import _content_hash


class AssetIntakeWriter:
    """Validate and apply a confirmed AssetIntakeDraft."""

    def __init__(
        self,
        *,
        get_memory_hash: Callable[[], str],
        apply_change: Callable[[dict[str, Any]], None],
    ) -> None:
        self.get_memory_hash = get_memory_hash
        self.apply_change = apply_change

    def generate_token(self, draft: AssetIntakeDraft) -> str:
        """Generate a confirmation token for the user to approve."""
        return _content_hash(f"{draft.draft_id}:{self.get_memory_hash()}")

    def apply(self, draft: AssetIntakeDraft, token: str) -> dict[str, Any]:
        """Apply the draft if the token is valid and memory hash matches."""
        current_hash = self.get_memory_hash()
        expected = _content_hash(f"{draft.draft_id}:{current_hash}")
        if token != expected:
            return {
                "status": "rejected",
                "reason": "invalid or stale confirmation token",
                "current_memory_hash": current_hash,
                "draft_base_hash": draft.base_memory_hash,
            }
        if current_hash != draft.base_memory_hash:
            return {
                "status": "rejected",
                "reason": "memory changed since draft was created",
                "current_memory_hash": current_hash,
                "draft_base_hash": draft.base_memory_hash,
            }
        if draft.ambiguities:
            return {
                "status": "rejected",
                "reason": "draft contains unresolved ambiguities",
                "ambiguities": [dict(a) for a in draft.ambiguities],
            }
        if not draft.all_confirmed_changes():
            return {
                "status": "rejected",
                "reason": "no changes to apply",
            }

        applied: list[dict[str, Any]] = []
        for change in draft.all_confirmed_changes():
            self.apply_change(change)
            applied.append(change)

        return {
            "status": "applied",
            "applied": applied,
            "new_memory_hash": self.get_memory_hash(),
        }
