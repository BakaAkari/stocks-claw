import json
import subprocess
import sys
from pathlib import Path


def test_snapshot_replay_cli_outputs_alert_without_ibkr(tmp_path):
    snapshot = {
        "net_liquidation": 9_800,
        "data_as_of": "2026-11-06T14:00:00+00:00",
        "positions": [{
            "symbol": "TSLA", "quantity": 10, "avg_cost": 110,
            "price": 80, "prev_close": 100, "delayed": False,
            "data_as_of": "2026-11-06T14:00:00+00:00",
            "bars": [{"close": 100, "high": 101, "low": 99, "volume": 1000} for _ in range(60)],
        }],
    }
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot))
    repo = Path(__file__).resolve().parents[2]

    result = subprocess.run([
        sys.executable, "scripts/disaster_alert.py",
        "--snapshot-file", str(snapshot_path),
        "--state-file", str(tmp_path / "state.json"),
        "--journal-file", str(tmp_path / "runs.jsonl"),
        "--sentinels-file", str(tmp_path / "missing_sentinels.json"),
    ], cwd=repo, capture_output=True, text=True, timeout=20)

    assert result.returncode == 0, result.stderr
    assert "TSLA" in result.stdout
    assert "single_day_crash" in result.stdout


def test_snapshot_replay_cli_loads_external_sentinels_and_silences_healthy_positions(tmp_path):
    snapshot = {
        "net_liquidation": 16_000,
        "data_as_of": "2026-11-19T02:50:00+00:00",
        "positions": [
            {
                "symbol": "NVDA", "quantity": 10, "avg_cost": 190,
                "price": 180, "prev_close": 182, "delayed": True,
                "data_as_of": "2026-11-19T02:50:00+00:00",
                "bars": [{"close": 195, "high": 197, "low": 193, "volume": 1000} for _ in range(60)],
            },
            {
                "symbol": "ONDS", "quantity": 200, "avg_cost": 5.5,
                "price": 4.10, "prev_close": 4.25, "delayed": True,
                "data_as_of": "2026-11-19T02:50:00+00:00",
                "bars": [{"close": 5.5, "high": 5.7, "low": 5.3, "volume": 1000} for _ in range(60)],
            },
        ],
    }
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot))
    sentinels_path = tmp_path / "sentinels.json"
    sentinels_path.write_text(json.dumps({
        "sentinels": {
            "NVDA": {"stop_1r": 148.80, "hard_stop": 131.55},
            "ONDS": {"watch_below": 4.20, "hard_stop": 3.47},
        }
    }))
    repo = Path(__file__).resolve().parents[2]

    result = subprocess.run([
        sys.executable, "scripts/disaster_alert.py",
        "--snapshot-file", str(snapshot_path),
        "--state-file", str(tmp_path / "state.json"),
        "--journal-file", str(tmp_path / "runs.jsonl"),
        "--sentinels-file", str(sentinels_path),
    ], cwd=repo, capture_output=True, text=True, timeout=20)

    assert result.returncode == 0, result.stderr
    assert "NVDA" not in result.stdout
    assert "ONDS" in result.stdout
    assert "sentinel_watch_below" in result.stdout
