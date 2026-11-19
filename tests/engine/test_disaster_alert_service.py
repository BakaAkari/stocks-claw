import json
from datetime import datetime, timezone

from stocks.engine.disaster_alert_service import run_alert_cycle


def snapshot():
    return {
        "net_liquidation": 9_800,
        "data_as_of": "2026-11-06T14:00:00+00:00",
        "positions": [{
            "symbol": "TSLA", "quantity": 10, "avg_cost": 110,
            "price": 80, "prev_close": 100, "delayed": False,
            "data_as_of": "2026-11-06T14:00:00+00:00",
            "bars": [{"close": 100, "high": 101, "low": 99, "volume": 1000} for _ in range(60)],
        }],
    }


def test_alert_cycle_persists_state_and_explains_silent_run(tmp_path):
    state_path = tmp_path / "state.json"
    journal_path = tmp_path / "runs.jsonl"
    now = datetime(2026, 11, 6, tzinfo=timezone.utc)

    first = run_alert_cycle(snapshot(), state_path, journal_path, now=now)
    second = run_alert_cycle(snapshot(), state_path, journal_path, now=now)

    assert "TSLA" in first
    assert second == ""
    records = [json.loads(line) for line in journal_path.read_text().splitlines()]
    assert len(records) == 2
    assert records[0]["delivered"] == 2
    assert records[1]["delivered"] == 0
    assert records[1]["suppressed"] == 2
    assert records[1]["evaluated_positions"] == 1
    assert json.loads(state_path.read_text())["alerts"]
