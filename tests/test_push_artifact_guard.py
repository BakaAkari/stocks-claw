from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "validate_push_artifact.py"


def _artifact(*, session="cn_open_watch", market_date="2026-07-17", task_version=5):
    return {
        "schema_version": 1,
        "run_id": f"20260717T020000Z_{session}",
        "session": session,
        "market": "cn",
        "market_date": market_date,
        "generated_at": "2026-07-17T02:04:43+00:00",
        "scheduled_for": "2026-07-17T10:00:00+08:00",
        "agent_task": {"task_version": task_version},
        "window_delta": {},
        "portfolio_decision": {},
        "risk_state": {},
        "data_boundaries": {},
        "research_candidates": [],
    }


def _run(tmp_path, artifact, *, session="cn_open_watch", now="2026-07-17T10:10:00+08:00"):
    path = tmp_path / f"{session}.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--artifact", str(path),
         "--session", session, "--now", now],
        capture_output=True, text=True,
    )


def test_guard_accepts_current_v5_artifact(tmp_path):
    result = _run(tmp_path, _artifact())
    assert result.returncode == 0
    assert "VALID" in result.stdout


def test_guard_rejects_previous_market_date(tmp_path):
    result = _run(tmp_path, _artifact(market_date="2026-07-16"))
    assert result.returncode != 0
    assert "market_date" in result.stderr


def test_guard_rejects_legacy_contract(tmp_path):
    result = _run(tmp_path, _artifact(task_version=4))
    assert result.returncode != 0
    assert "task_version" in result.stderr


def test_guard_rejects_wrong_session(tmp_path):
    result = _run(tmp_path, _artifact(session="cn_pre_open"))
    assert result.returncode != 0
    assert "session" in result.stderr


def test_guard_rejects_artifact_generated_before_scheduled_time(tmp_path):
    artifact = _artifact()
    artifact["generated_at"] = "2026-07-17T01:00:00+00:00"
    result = _run(tmp_path, artifact)
    assert result.returncode != 0
    assert "generated_at" in result.stderr



def test_guard_accepts_us_artifact_when_market_date_is_previous_china_date(tmp_path):
    artifact = _artifact(session="us_pre_close", market_date="2026-07-16")
    artifact["market"] = "us"
    artifact["run_id"] = "20260716T193000Z_us_pre_close"
    artifact["scheduled_for"] = "2026-07-16T15:30:00-04:00"
    artifact["generated_at"] = "2026-07-16T19:34:00+00:00"
    result = _run(
        tmp_path, artifact, session="us_pre_close",
        now="2026-07-17T03:40:00+08:00",
    )
    assert result.returncode == 0, result.stderr


def test_guard_rejects_artifact_older_than_max_age(tmp_path):
    artifact = _artifact()
    result = _run(
        tmp_path, artifact, now="2026-07-17T11:00:00+08:00"
    )
    assert result.returncode != 0
    assert "age" in result.stderr



def test_guard_rejects_malformed_timestamp_without_traceback(tmp_path):
    artifact = _artifact()
    artifact["generated_at"] = "not-a-date"
    result = _run(tmp_path, artifact)
    assert result.returncode != 0
    assert "invalid timestamp" in result.stderr
    assert "Traceback" not in result.stderr



def test_guard_accepts_naive_utc_timestamps_by_normalizing_them(tmp_path):
    artifact = _artifact()
    artifact["generated_at"] = "2026-07-17T02:04:43"
    artifact["scheduled_for"] = "2026-07-17T02:00:00"
    result = _run(tmp_path, artifact, now="2026-07-17T02:10:00+00:00")
    assert result.returncode == 0, result.stderr
