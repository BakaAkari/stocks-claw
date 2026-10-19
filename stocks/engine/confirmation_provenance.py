"""Immutable confirmation events for user-owned financial facts.

Business payloads stay compatible with their existing schemas. Confirmation
metadata lives in a sidecar ledger and is valid only when its content hash
matches the current payload.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from stocks.engine.atomic_io import atomic_write_json
from stocks.engine.evaluation_contract import digest, utc

SCHEMA_VERSION = 1


def confirmation_content(payload: dict, *, target: str) -> dict:
    """Return the immutable portion of a user fact for hashing."""
    if not isinstance(payload, dict):
        raise ValueError("confirmation_payload_must_be_object")
    value = deepcopy(payload)
    if target == "profile":
        value.pop("updated_at", None)
    elif target == "constraints":
        from stocks.engine.constraint_model import validate_constraints
        value = validate_constraints(value)
    value.pop("confirmation_provenance", None)
    return value


def confirmation_hash(payload: dict, *, target: str) -> str:
    return digest(confirmation_content(payload, target=target))


def _event_id(target: str, content_hash: str, source_kind: str) -> str:
    return digest({"target": target, "content_hash": content_hash, "source_kind": source_kind})


def _event_integrity(event: dict) -> str:
    """Hash the complete event envelope, excluding its self-hash."""
    return digest({key: value for key, value in event.items() if key != "event_integrity"})


class ConfirmationStore:
    """Append-only, content-addressed confirmation event sidecar."""

    def __init__(self, local_data_dir: str | Path):
        self.root = Path(local_data_dir) / "confirmation_events"

    def record(
        self,
        target: str,
        payload: dict,
        *,
        channel: str | None = None,
        actor: str | None = None,
        source_path: str | None = None,
        confirmed_at: str | None = None,
    ) -> dict:
        if not isinstance(target, str) or not target.strip():
            raise ValueError("confirmation_target_required")
        if channel not in {"cli", "mcp"}:
            source_kind = "unverified"
            channel = channel or "engine"
        else:
            source_kind = "user_confirmed"
        actor = actor or ("user" if source_kind == "user_confirmed" else "unknown")
        confirmed_at = confirmed_at or datetime.now(timezone.utc).isoformat()
        utc(confirmed_at)
        content_hash = confirmation_hash(payload, target=target)
        event_id = _event_id(target, content_hash, source_kind)
        event = {
            "schema_version": SCHEMA_VERSION,
            "event_id": event_id,
            "target": target,
            "content_hash": content_hash,
            "source_kind": source_kind,
            "channel": channel,
            "actor": actor,
            "confirmed_at": confirmed_at,
            "source_path": source_path,
        }
        event["event_integrity"] = _event_integrity(event)
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{event_id}.json"
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ValueError("confirmation_event_unreadable") from exc
            required = {"schema_version", "event_id", "target", "content_hash", "source_kind"}
            if any(existing.get(key) != event.get(key) for key in required):
                raise ValueError("confirmation_event_id_conflict")
            if existing.get("event_integrity") != _event_integrity(existing):
                raise ValueError("confirmation_event_tampered")
            return existing
        atomic_write_json(path, event)
        return event

    def find(self, target: str, payload: dict) -> dict | None:
        content_hash = confirmation_hash(payload, target=target)
        event_id = _event_id(target, content_hash, "user_confirmed")
        path = self.root / f"{event_id}.json"
        if not path.exists():
            return None
        try:
            event = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if (
            event.get("schema_version") == SCHEMA_VERSION
            and event.get("event_id") == event_id
            and event.get("target") == target
            and event.get("content_hash") == content_hash
            and event.get("source_kind") == "user_confirmed"
            and event.get("channel") in {"cli", "mcp"}
            and event.get("actor") == "user"
            and event.get("event_integrity") == _event_integrity(event)
        ):
            return event
        return None

