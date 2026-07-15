"""Structured, noise-resistant deltas between scheduled decision windows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class WindowDelta:
    session_id: str
    market: str
    previous_run_id: Optional[str]
    current_run_id: str
    material: bool
    changes: list[dict]
    first_in_session: bool
    priority: str = "normal"

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "market": self.market,
            "previous_run_id": self.previous_run_id,
            "current_run_id": self.current_run_id,
            "material": self.material,
            "has_material_change": self.material,
            "changes": self.changes,
            "first_in_session": self.first_in_session,
            "priority": self.priority,
        }


def _action_key(action: dict) -> str:
    return ":".join(str(action.get(key) or "") for key in ("position_id", "signal", "action"))


def _normalized_actions(actions: list[dict]) -> dict[str, dict]:
    result = {}
    for action in actions:
        key = _action_key(action)
        result[key] = {
            "position_id": action.get("position_id"),
            "signal": action.get("signal"),
            "action": action.get("action"),
            "ratio": action.get("ratio"),
            "decision_id": action.get("decision_id"),
        }
    return result


def _anomaly_codes(run: dict) -> list[str]:
    values = []
    for review in run.get("position_reviews") or []:
        evidence = review.get("evidence") or {}
        pid = review.get("position_id") or ""
        for anomaly in evidence.get("data_anomalies") or []:
            code = anomaly.get("code")
            if code:
                values.append(f"{pid}:{code}")
    return sorted(set(values))


def _fired_triggers(run: dict) -> list[str]:
    values = []
    for trigger in run.get("trigger_reviews") or []:
        if trigger.get("status") != "fired":
            continue
        identity = trigger.get("trigger_id") or ":".join(
            str(trigger.get(key) or "") for key in ("type", "instrument", "threshold")
        )
        values.append(identity)
    return sorted(set(values))


def _normalized(run: dict) -> dict:
    state = run.get("risk_state") or {}
    decision = run.get("portfolio_decision") or {}
    return {
        "risk_state": {
            "level": state.get("level"),
            "transition": state.get("transition"),
            "candidate_level": state.get("candidate_level"),
        },
        "decision_status": decision.get("status"),
        "approved": _normalized_actions(decision.get("approved_actions") or []),
        "suppressed": _normalized_actions(decision.get("suppressed_actions") or []),
        "conflicts": decision.get("unresolved_conflicts") or [],
        "anomalies": _anomaly_codes(run),
        "triggers": _fired_triggers(run),
    }


def _compare_action_group(name: str, previous: dict, current: dict, changes: list[dict]) -> None:
    for key in sorted(set(previous) | set(current)):
        old = previous.get(key)
        new = current.get(key)
        # A decision_id changes with run_id by design.  Compare the executable
        # semantics; keep IDs in the emitted evidence when another field changed.
        old_semantic = {k: v for k, v in (old or {}).items() if k != "decision_id"}
        new_semantic = {k: v for k, v in (new or {}).items() if k != "decision_id"}
        if old_semantic != new_semantic:
            changes.append({"field": f"{name}.{key}", "old": old, "new": new})


def compute_window_delta(
    previous_run: Optional[dict], current_run: dict, *, session_id: str, market: str
) -> WindowDelta:
    if previous_run is None:
        return WindowDelta(
            session_id=session_id,
            market=market,
            previous_run_id=None,
            current_run_id=current_run.get("run_id", ""),
            material=True,
            changes=[{"field": "initial", "old": None, "new": "first_window"}],
            first_in_session=True,
        )

    previous = _normalized(previous_run)
    current = _normalized(current_run)
    changes: list[dict] = []
    for key in ("level", "transition", "candidate_level"):
        old = previous["risk_state"].get(key)
        new = current["risk_state"].get(key)
        if old != new:
            changes.append({"field": f"risk_state.{key}", "old": old, "new": new})
    if previous["decision_status"] != current["decision_status"]:
        changes.append(
            {
                "field": "portfolio_decision.status",
                "old": previous["decision_status"],
                "new": current["decision_status"],
            }
        )
    _compare_action_group("approved_action", previous["approved"], current["approved"], changes)
    _compare_action_group(
        "suppressed_action", previous["suppressed"], current["suppressed"], changes
    )
    for key, field in (
        ("conflicts", "portfolio_decision.unresolved_conflicts"),
        ("anomalies", "position_anomaly_codes"),
        ("triggers", "fired_triggers"),
    ):
        if previous[key] != current[key]:
            change = {"field": field, "old": previous[key], "new": current[key]}
            if key == "triggers":
                change["newly_fired"] = sorted(set(current[key]) - set(previous[key]))
            changes.append(change)
    material = bool(changes)
    return WindowDelta(
        session_id=session_id,
        market=market,
        previous_run_id=previous_run.get("run_id"),
        current_run_id=current_run.get("run_id", ""),
        material=material,
        changes=changes,
        first_in_session=False,
    )
