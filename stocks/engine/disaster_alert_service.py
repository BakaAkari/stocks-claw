"""Persistence boundary for read-only disaster & sentinel alert cycles."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from stocks.engine.disaster_alerts import apply_delivery_state, evaluate_snapshot, render_alerts


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "alerts": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def run_alert_cycle(snapshot: dict[str, Any], state_path: Path, journal_path: Path,
                    config: dict[str, Any] | None = None,
                    now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    state = _load_state(state_path)
    alerts = evaluate_snapshot(snapshot, config)
    cfg = config or {}
    deliver, next_state = apply_delivery_state(
        alerts,
        state,
        now,
        cooldown_minutes=int(cfg.get("rearm_cooldown_minutes", 0) or 0),
        provider_failure_cooldown_minutes=int(cfg.get("provider_failure_cooldown_minutes", 0) or 0),
    )
    delivered_keys = {alert["key"] for alert in deliver}
    record = {
        "timestamp": now.isoformat(),
        "data_as_of": snapshot.get("data_as_of"),
        "evaluated_positions": len(snapshot.get("positions", [])),
        "matched": len(alerts),
        "delivered": len(deliver),
        "suppressed": len(alerts) - len(deliver),
        "matched_keys": [alert["key"] for alert in alerts],
        "delivered_keys": [alert["key"] for alert in deliver],
        "suppressed_keys": [alert["key"] for alert in alerts if alert["key"] not in delivered_keys],
        "provider_errors": snapshot.get("errors", []),
    }
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    with journal_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    next_state["last_run_at"] = now.isoformat()
    _write_json_atomic(state_path, next_state)
    return render_alerts(deliver)
