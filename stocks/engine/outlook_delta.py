"""Deterministic outlook delta computation and atomic deduplication.

Computes the difference between two validated primary-window outlooks and
maintains an atomic on-disk state file to suppress repeated identical deltas.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def compute_outlook_delta(previous: dict | None, current: dict | None) -> dict:
    """Compare two validated primary outlook artifacts and return changes.

    Parameters
    ----------
    previous : dict | None
        The older validated primary run artifact.
    current : dict | None
        The newer validated primary run artifact.

    Returns
    -------
    dict
        *Empty dict* when no difference is detected (or either is *None*).
        A delta dict with keys ``schema_version``, ``previous_session``,
        ``current_session``, ``previous_generated_at``, ``current_generated_at``,
        ``market``, and ``changes`` when a meaningful difference is found.
    """
    if previous is None or current is None:
        return {}

    prev_outlook: dict | None = previous.get("structured_outlook")
    curr_outlook: dict | None = current.get("structured_outlook")

    if not prev_outlook or not curr_outlook:
        return {}
    if prev_outlook.get("status") != "ok" or curr_outlook.get("status") != "ok":
        return {}

    changes = _deep_diff(prev_outlook, curr_outlook)
    if not changes:
        return {}

    return {
        "schema_version": 1,
        "previous_session": previous.get("session"),
        "current_session": current.get("session"),
        "previous_generated_at": previous.get("generated_at"),
        "current_generated_at": current.get("generated_at"),
        "market": current.get("market"),
        "changes": changes,
    }


def _stable_fingerprint(delta: dict) -> str:
    """Return a stable JSON fingerprint of the semantic delta content.

    Only the ``changes`` and ``market`` keys are fingerprinted, so that
    identical semantic differences (even with different session / generated_at
    metadata) are recognised as duplicates.
    """
    fp = {"changes": delta.get("changes", {}), "market": delta.get("market")}
    return json.dumps(fp, sort_keys=True, ensure_ascii=False, default=str)


# Whitelisted comparison keys
# Only these keys from the structured outlook are compared.
_DIFF_KEYS = frozenset({
    "summary",
    "confidence",
    "scenarios",
    "sector_views",
    "asset_views",
    "source_refs",
    "near_term",
    "medium_term",
})

# Scenario sub-keys that may be reported in the delta
_SCENARIO_DIFF_KEYS = frozenset({"label", "validation", "invalidation"})

# Horizon sub-keys that may be reported in the delta
_HORIZON_DIFF_KEYS = frozenset({"direction", "confidence", "horizon"})


def _deep_diff(prev: dict, curr: dict) -> dict:
    """Extract concrete changes between two outlook dicts.

    Returns a dict keyed by the changed field name, with ``{"from": ..., "to": ...}``
    values reflecting actual outlook content -- never counts, never bare booleans.
    Only whitelisted keys are compared; unknown keys are silently
    ignored so the model cannot inject conclusions via novel fields.
    """
    changes: dict[str, dict[str, Any]] = {}

    # Scalar fields (summary, confidence)
    for key in ("summary", "confidence"):
        if key not in _DIFF_KEYS:
            continue
        pv = prev.get(key)
        cv = curr.get(key)
        if pv != cv:
            changes[key] = {"from": pv, "to": cv}

    # Scenarios: label + validation + invalidation per scenario
    prev_scenarios: dict = prev.get("scenarios") or {}
    curr_scenarios: dict = curr.get("scenarios") or {}
    scenario_changes: dict[str, dict] = {}
    for sname in sorted(set(prev_scenarios) | set(curr_scenarios)):
        ps = prev_scenarios.get(sname) or {}
        cs = curr_scenarios.get(sname) or {}
        if not isinstance(ps, dict):
            ps = {}
        if not isinstance(cs, dict):
            cs = {}
        per_scenario: dict[str, dict] = {}
        for skey in _SCENARIO_DIFF_KEYS:
            pv = ps.get(skey)
            cv = cs.get(skey)
            if json.dumps(pv, sort_keys=True, default=str) != json.dumps(cv, sort_keys=True, default=str):
                per_scenario[skey] = {"from": pv, "to": cv}
        if per_scenario:
            scenario_changes[sname] = per_scenario
    if scenario_changes:
        changes["scenarios"] = scenario_changes

    # Sector views: identify by sector key, show direction only
    prev_sectors: list[dict] = prev.get("sector_views") or []
    curr_sectors: list[dict] = curr.get("sector_views") or []
    sector_changes: dict[str, dict] = _list_diff_by_key(
        prev_sectors, curr_sectors, key_field="sector", compare_keys=frozenset({"direction"}),
    )
    if sector_changes:
        changes["sector_views"] = sector_changes

    # Asset views: identify by key, show direction only
    prev_assets: list[dict] = prev.get("asset_views") or []
    curr_assets: list[dict] = curr.get("asset_views") or []
    asset_changes: dict[str, dict] = _list_diff_by_key(
        prev_assets, curr_assets, key_field="asset", compare_keys=frozenset({"direction"}),
    )
    if asset_changes:
        changes["asset_views"] = asset_changes

    # Source refs: show actual IDs of added/removed sources
    def _valid_src_id(s: dict) -> bool:
        sid = s.get("id")
        return isinstance(sid, str) and bool(sid.strip())
    prev_src_ids = {s["id"] for s in (prev.get("source_refs") or []) if isinstance(s, dict) and _valid_src_id(s)}
    curr_src_ids = {s["id"] for s in (curr.get("source_refs") or []) if isinstance(s, dict) and _valid_src_id(s)}
    added = curr_src_ids - prev_src_ids
    removed = prev_src_ids - curr_src_ids
    if added or removed:
        src_change: dict[str, list[str]] = {}
        if added:
            src_change["added"] = sorted(added)
        if removed:
            src_change["removed"] = sorted(removed)
        changes["source_refs"] = src_change

    # Horizon blocks (near_term, medium_term): direction + confidence
    for hkey in ("near_term", "medium_term"):
        if hkey not in _DIFF_KEYS:
            continue
        p_block = prev.get(hkey) or {}
        c_block = curr.get(hkey) or {}
        if not isinstance(p_block, dict):
            p_block = {}
        if not isinstance(c_block, dict):
            c_block = {}
        h_changes: dict[str, dict] = {}
        for hskey in _HORIZON_DIFF_KEYS:
            pv = p_block.get(hskey)
            cv = c_block.get(hskey)
            if pv != cv:
                h_changes[hskey] = {"from": pv, "to": cv}
        if h_changes:
            changes[hkey] = h_changes

    return changes


def _list_diff_by_key(
    prev_items: list[dict],
    curr_items: list[dict],
    *,
    key_field: str,
    compare_keys: frozenset[str],
) -> dict[str, dict]:
    """Diff two lists of dicts by a key field, comparing only *compare_keys*.

    Returns a dict mapping each changed key_field value to a dict of
    changes. Items present in only one side are marked ``_status``.
    """
    prev_map: dict[str, dict] = {item.get(key_field): item for item in prev_items if isinstance(item, dict)}
    curr_map: dict[str, dict] = {item.get(key_field): item for item in curr_items if isinstance(item, dict)}
    all_keys = sorted(set(prev_map) | set(curr_map))
    result: dict[str, dict] = {}
    for k in all_keys:
        pi = prev_map.get(k) or {}
        ci = curr_map.get(k) or {}
        per_item: dict[str, dict] = {}
        for skey in compare_keys:
            pv = pi.get(skey)
            cv = ci.get(skey)
            if pv != cv:
                per_item[skey] = {"from": pv, "to": cv}
        if k not in prev_map:
            result[k] = {"_status": "added"}
        elif k not in curr_map:
            result[k] = {"_status": "removed"}
        elif per_item:
            result[k] = per_item
    return result


# Atomic deduplication state


class OutlookDeltaState:
    """Atomic on-disk state file for de-duplicating outlook deltas.

    Use :meth:`should_emit` to decide whether a computed delta should
    be forwarded to the user. Deduplication is based on the semantic
    ``changes`` sub-dict and the ``market`` field -- metadata (session,
    generated_at) does not affect the comparison.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def should_emit(self, market: str, delta: dict) -> bool:
        """Check whether *delta* should be emitted for *market*."""
        if not delta:
            return False

        state = self._load()
        previous = state.get(market)

        if previous is None:
            self._save(market, delta)
            return True

        curr_fp = _stable_fingerprint(delta)
        prev_fp = _stable_fingerprint(previous)

        if curr_fp == prev_fp:
            return False

        self._save(market, delta)
        return True

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, market: str, delta: dict) -> None:
        state = self._load()
        state[market] = delta
        self._atomic_write(state)

    def _atomic_write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(data, ensure_ascii=False, default=str)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
