"""Persistent, auditable risk-state lifecycle."""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
_LEVEL_ORDER = {"normal": 0, "watch": 1, "reduce": 2, "hedge": 3}
_DEFAULT_CONFIG = {
    "critical_ttl_minutes": 360,
    "hedge_independent_evidence": 2,
    "hedge_confirmations": 2,
    "deescalation_confirmations": 2,
}


@dataclass(frozen=True)
class RiskObservation:
    candidate_level: str
    evidence_keys: tuple[str, ...] = ()
    observed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    @property
    def level(self) -> str:
        return self.candidate_level


@dataclass
class RiskState:
    level: str = "normal"
    candidate_level: Optional[str] = None
    confirmations_remaining: int = 0
    deescalation_count: int = 0
    evidence_keys: list[str] = field(default_factory=list)
    observed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_observation_id: Optional[str] = None
    transition: str = "initial"

    @property
    def suspend_accumulation(self) -> bool:
        return self.level in {"reduce", "hedge"}

    @property
    def cash_target_pct(self) -> Optional[float]:
        return 0.15 if self.level == "hedge" else 0.10 if self.level == "reduce" else None

    def to_dict(self) -> dict:
        result = asdict(self)
        for key in ("observed_at", "expires_at", "updated_at"):
            value = result.get(key)
            if value is not None:
                result[key] = value.isoformat()
        result["suspend_accumulation"] = self.suspend_accumulation
        result["cash_target_pct"] = self.cash_target_pct
        return result


def _parse_datetime(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _from_dict(data: dict) -> RiskState:
    allowed = set(RiskState.__dataclass_fields__)
    clean = {k: v for k, v in data.items() if k in allowed}
    if "last_obs_hash" in data and "last_observation_id" not in clean:
        clean["last_observation_id"] = data["last_obs_hash"]
    if "deescalation_remaining" in data and "deescalation_count" not in clean:
        clean["deescalation_count"] = data["deescalation_remaining"]
    for key in ("observed_at", "expires_at", "updated_at"):
        clean[key] = _parse_datetime(clean.get(key))
    return RiskState(**clean)


def _observation_id(observation, observed_at):
    raw = json.dumps(
        {
            "candidate_level": observation.candidate_level,
            "evidence_keys": sorted(set(observation.evidence_keys)),
            "observed_at": observed_at.isoformat(),
            "expires_at": observation.expires_at.isoformat() if observation.expires_at else None,
        },
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class RiskStateStore:
    def __init__(self, path=None, config=None):
        self.path = Path(path) if path else Path(".local/risk_state.json")
        self.config = {**_DEFAULT_CONFIG, **(config or {})}

    def load(self, *, as_of=None):
        state = self._load()
        now = as_of or datetime.now(timezone.utc)
        if state.expires_at and now >= state.expires_at and state.level != "normal":
            return RiskState(updated_at=now, transition="expired")
        return state

    def update(self, observation):
        """Atomically apply one observation across concurrent processes."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        with lock_path.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                return self._update_locked(observation)
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def _update_locked(self, observation):
        now = observation.observed_at or datetime.now(timezone.utc)
        if observation.candidate_level not in _LEVEL_ORDER:
            raise ValueError(f"unknown risk level: {observation.candidate_level}")
        state = self._load()
        if state.expires_at and now >= state.expires_at:
            state = RiskState(updated_at=now, transition="expired")
        obs_id = _observation_id(observation, now)
        if state.last_observation_id == obs_id:
            state.transition = "unchanged"
            self._save(state)
            return state
        candidate = observation.candidate_level
        evidence = sorted(set(observation.evidence_keys))
        current_rank = _LEVEL_ORDER[state.level]
        candidate_rank = _LEVEL_ORDER[candidate]
        if candidate_rank < current_rank:
            state.deescalation_count += 1
            state.transition = "deescalating"
            if state.deescalation_count >= int(self.config["deescalation_confirmations"]):
                target_rank = max(current_rank - 1, candidate_rank)
                state.level = next(
                    level for level, rank in _LEVEL_ORDER.items() if rank == target_rank
                )
                state.candidate_level = None
                state.confirmations_remaining = 0
                state.deescalation_count = 0
                state.evidence_keys = evidence
                state.transition = "deescalated"
        elif candidate == "hedge" and state.level != "hedge":
            merged = sorted(set(state.evidence_keys) | set(evidence))
            independent = int(self.config["hedge_independent_evidence"])
            confirmations = int(self.config["hedge_confirmations"])
            same = state.candidate_level == "hedge"
            remaining = state.confirmations_remaining - 1 if same else confirmations - 1
            if len(merged) >= independent or remaining <= 0:
                state.level = "hedge"
                state.candidate_level = None
                state.confirmations_remaining = 0
                state.transition = "escalated"
            else:
                if state.level == "normal":
                    state.level = "watch"
                state.candidate_level = "hedge"
                state.confirmations_remaining = remaining
                state.transition = "candidate"
            state.deescalation_count = 0
            state.evidence_keys = merged
        elif candidate_rank > current_rank:
            state.level = candidate
            state.candidate_level = None
            state.confirmations_remaining = 0
            state.deescalation_count = 0
            state.evidence_keys = evidence
            state.transition = "escalated"
        else:
            old_evidence = set(state.evidence_keys)
            state.deescalation_count = 0
            state.evidence_keys = sorted(old_evidence | set(evidence))
            state.transition = (
                "reconfirmed" if set(state.evidence_keys) != old_evidence else "unchanged"
            )
        state.observed_at = now
        if candidate_rank >= current_rank or state.level == "normal":
            state.expires_at = observation.expires_at
        state.updated_at = now
        state.last_observation_id = obs_id
        self._save(state)
        return state

    def _load(self):
        if not self.path.exists():
            return RiskState()
        try:
            return _from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            logger.warning("Invalid risk state at %s; reset to normal", self.path)
            return RiskState(transition="invalid_state_reset")

    def _save(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f"{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        except (OSError, TypeError, ValueError):
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
