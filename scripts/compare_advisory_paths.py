"""Compare a new shadow advisory with the current production decision.

The script assumes the project root is on PYTHONPATH. If invoked directly,
add the project root to sys.path so that `stocks` is importable.

Usage:
    .venv/bin/python scripts/compare_advisory_paths.py --run-id <run-id>

Output:
    stdout JSON with status, deltas, and recommendations.

Exit codes:
    0: shadow can be reviewed
    1: missing shadow run or invalid arguments
    2: shadow advisory has errors that would block production
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
import json
from pathlib import Path
from typing import Any

from stocks.engine.advisory_shadow_store import AdvisoryShadowStore


def _load_production_user_view(decision_id: str, artifact_path: str) -> dict[str, Any]:
    """Load current production user_view artifact if available."""
    if artifact_path and Path(artifact_path).exists():
        return json.loads(Path(artifact_path).read_text(encoding="utf-8"))
    # Fallback: search scheduled runs
    candidate = Path(f".local/scheduled_runs/latest/{decision_id}.json")
    if candidate.exists():
        return json.loads(candidate.read_text(encoding="utf-8"))
    return {}


def _extract_actions(artifact: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    for key in ("actions", "action_cards", "recommendations"):
        for item in artifact.get(key, []):
            if isinstance(item, dict):
                action = item.get("action", "unknown")
                target = item.get("target", item.get("code", item.get("instrument_key", "unknown")))
                actions.append(f"{action}:{target}")
            elif isinstance(item, str):
                actions.append(item)
    return actions


def _compare(
    run_id: str,
    store: AdvisoryShadowStore,
    production: dict[str, Any],
) -> dict[str, Any]:
    manifest = store.load_manifest(run_id)
    if manifest is None:
        return {"status": "missing", "error": f"shadow run {run_id} not found"}

    snapshot, advisory, receipt = store.load(run_id)
    shadow_actions = {a["action_id"]: a for a in advisory.get("actions", [])}
    prod_actions = _extract_actions(production)

    deltas = []
    for action_id, action in shadow_actions.items():
        key = f"{action['action']}:{action['target']}"
        if key in prod_actions:
            deltas.append({"type": "same", "action": action})
        else:
            deltas.append({"type": "new", "action": action})

    missing_from_shadow = [a for a in prod_actions if a not in {f"{sa['action']}:{sa['target']}" for sa in shadow_actions.values()}]

    return {
        "status": "review" if receipt.get("status") in {"ok", "warnings"} else "blocked",
        "run_id": run_id,
        "snapshot_id": manifest.get("snapshot_id"),
        "advisory_id": manifest.get("advisory_id"),
        "receipt_status": receipt.get("status"),
        "production_decision_id": manifest.get("production_decision_id"),
        "shadow_action_count": len(shadow_actions),
        "production_action_count": len(prod_actions),
        "deltas": deltas,
        "missing_from_shadow": missing_from_shadow,
        "recommendation": "shadow_ok_for_review" if receipt.get("status") in {"ok", "warnings"} else "fix_shadow_errors",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare shadow advisory with production")
    parser.add_argument("--run-id", required=True, help="Shadow run ID")
    parser.add_argument("--production-artifact", default="", help="Path to production artifact")
    args = parser.parse_args()

    store = AdvisoryShadowStore()
    manifest = store.load_manifest(args.run_id)
    if manifest is None:
        print(json.dumps({"status": "missing", "error": f"run {args.run_id} not found"}, ensure_ascii=False))
        return 1

    production = _load_production_user_view(
        manifest.get("production_decision_id", ""),
        args.production_artifact,
    )
    result = _compare(args.run_id, store, production)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 2 if result["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
