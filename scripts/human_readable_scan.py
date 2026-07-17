"""Scan scheduled run artifacts for human-readable contract violations."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

FORBIDDEN_PATTERNS = [
    re.compile(r"a_[A-Za-z0-9_]+"),
    re.compile(r"[0-9a-f]{16,}"),  # long hex hashes
    re.compile(r"prev_close_mismatch|source_regime_change|single_bar_jump|mixed_adjustment_regime|price_ma20_dislocation"),
    re.compile(r"research_only|periodic_open|t2_plus|manual_fallback"),
    re.compile(r"(take_profit|stop_loss|reduce|add|hedge|review_required)"),
]


def _leaf_values(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _leaf_values(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _leaf_values(item)
    elif isinstance(obj, str):
        yield obj


def scan_run(run: dict, source: str) -> list[str]:
    """Return human-readable leakage violations in user_view."""
    errors = []
    view = ((run.get("portfolio_decision") or {}).get("user_view")) or {}
    text = "\n".join(_leaf_values(view))
    for pat in FORBIDDEN_PATTERNS:
        for m in pat.finditer(text):
            snippet = text[max(0, m.start() - 15):m.end() + 15]
            errors.append(f"{source}: forbidden token `{m.group(0)}` in user view: {snippet}")
    return errors


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".local/scheduled_runs/latest")
    if not root.exists():
        print(f"NOT_FOUND {root}")
        sys.exit(2)
    errors = []
    for path in root.glob("*.json"):
        run = json.loads(path.read_text())
        errors.extend(scan_run(run, path.name))
    if errors:
        for e in errors:
            print(e)
        sys.exit(1)
    print(f"SCAN_OK {len(list(root.glob('*.json')))} artifacts")
