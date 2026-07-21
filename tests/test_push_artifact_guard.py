from __future__ import annotations

import json
import subprocess
import sys
from os import environ
from pathlib import Path

from stocks.engine.presentation import (
    project_outlook_delta_for_display,
    project_outlook_for_display,
)

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
        "agent_task": {"task_version": task_version, "data_reference": {"window_delta": "", "portfolio_decision": "", "risk_state": "", "data_boundaries": "", "research_candidates": ""}},
        "window_delta": {},
        "portfolio_decision": {
            "user_view": {
                "instruction_card": {"status": "no_action", "status_label": "今日无需操作", "actions": [], "no_action_reasons": ["当前没有获批动作"]},
                "assistant_brief": {"why": [], "do_not_do": [], "cash": {}, "risk": {}, "research": []},
            },
        },
        "risk_state": {},
        "data_boundaries": {},
        "research_candidates": [],
    }


def _run(tmp_path, artifact, *, session="cn_open_watch", now="2026-07-17T10:10:00+08:00"):
    path = tmp_path / f"{session}.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    env = {**dict(environ), "PYTHONPATH": str(SCRIPT.parents[1])}
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--artifact", str(path),
         "--session", session, "--now", now],
        capture_output=True, text=True, env=env,
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


def test_guard_rejects_stale_artifact_far_before_scheduled_time(tmp_path):
    artifact = _artifact()
    artifact["generated_at"] = "2026-07-15T22:00:00+00:00"
    artifact["scheduled_for"] = "2026-07-17T08:50:00+08:00"
    result = _run(tmp_path, artifact, now="2026-07-17T08:50:00+08:00")
    assert result.returncode != 0
    assert "more than a day old" in result.stderr



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
    artifact["generated_at"] = "2026-07-15T02:00:00+00:00"
    artifact["market_date"] = "2026-07-17"
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



def test_guard_rejects_missing_human_user_view(tmp_path):
    artifact = _artifact()
    artifact["portfolio_decision"].pop("user_view")
    result = _run(tmp_path, artifact)
    assert result.returncode != 0
    assert "user_view" in result.stderr


def test_guard_rejects_incomplete_human_user_view(tmp_path):
    artifact = _artifact()
    artifact["portfolio_decision"]["user_view"] = {"instruction_card": {}}
    result = _run(tmp_path, artifact)
    assert result.returncode != 0
    assert "assistant_brief" in result.stderr



def test_guard_allows_pre_generated_artifact_before_scheduled_time(tmp_path):
    artifact = _artifact()
    artifact["generated_at"] = "2026-07-17T08:30:00+08:00"
    artifact["scheduled_for"] = "2026-07-17T08:50:00+08:00"
    result = _run(tmp_path, artifact, now="2026-07-17T08:50:00+08:00")
    assert result.returncode == 0
    assert "VALID" in result.stdout


# --- Task 7: Outlook in push artifact guard ---

_STRUCTURED_OUTLOOK = {
    "status": "ok",
    "generated_at": "2026-07-17T08:30:00+00:00",
    "summary": "\u7ec4\u5408\u6574\u4f53\u7814\u5224\u504f\u6b63\u9762\uff0c\u914d\u7f6e\u98ce\u9669\u4e0a\u5347",
    "near_term": {"horizon": "1-2w", "direction": "supportive", "confidence": "high"},
    "medium_term": {"horizon": "1-3m", "direction": "supportive", "confidence": "medium"},
    "scenarios": {
        "base": {
            "drivers": ["\u7ecf\u6d4e\u589e\u957f\u6e29\u548c"], "portfolio_effect": "\u5c0f\u5e45\u4e0a\u6da8",
            "validation": ["GDP\u7b26\u5408\u9884\u671f"], "invalidation": ["\u901a\u80c0\u8d85\u9884\u671f\u4e0a\u884c"],
        },
        "bull": {
            "drivers": ["\u653f\u7b56\u523a\u6fc0"], "portfolio_effect": "\u660e\u663e\u4e0a\u6da8",
            "validation": ["\u793e\u878d\u6570\u636e\u5927\u5e45\u8d85\u9884\u671f"], "invalidation": ["\u5730\u7f18\u98ce\u9669\u7a81\u7136\u5347\u7ea7"],
        },
        "risk": {
            "drivers": ["\u5730\u7f18\u51b2\u7a81\u5347\u7ea7"], "portfolio_effect": "\u7ec4\u5408\u9884\u8ba1\u4e0b\u8dcc",
            "validation": ["VIX\u6307\u6570\u6301\u7eed\u9ad8\u4f4d"], "invalidation": ["\u653f\u7b56\u5f3a\u529b\u5e72\u9884"],
        },
    },
    "source_refs": [],
    "confidence": "high",
    "forecast_candidates": [],
}

_UNAVAILABLE_STRUCTURED = {
    "status": "unavailable",
    "generated_at": "2026-07-17T08:30:00+00:00",
    "message": "\u672c\u671f\u7814\u5224\u672a\u901a\u8fc7\u6570\u636e\u5b8c\u6574\u6027\u6821\u9a8c",
    "data_limitations": [],
}

_OUTLOOK_DELTA = {
    "schema_version": 1,
    "changes": {
        "summary": {"from": "\u65e7\u7814\u5224", "to": "\u65b0\u7814\u5224"},
    },
}


def _artifact_with_outlook(
    *,
    structured=None,
    user_outlook=None,
    outlook_delta=None,
    **kw,
):
    a = _artifact(**kw)
    view = a["portfolio_decision"]["user_view"]
    assistant = view["assistant_brief"]
    if structured is not None:
        a["structured_outlook"] = structured
    if user_outlook is not None:
        assistant["outlook"] = user_outlook
    if outlook_delta is not None:
        assistant["outlook_delta"] = outlook_delta
    return a


def test_guard_accepts_valid_ok_outlook(tmp_path):
    projected = project_outlook_for_display(_STRUCTURED_OUTLOOK)
    artifact = _artifact_with_outlook(
        structured=_STRUCTURED_OUTLOOK,
        user_outlook=projected,
    )
    result = _run(tmp_path, artifact)
    assert result.returncode == 0, result.stderr
    assert "VALID" in result.stdout


def test_guard_accepts_valid_unavailable_outlook(tmp_path):
    projected = project_outlook_for_display(_UNAVAILABLE_STRUCTURED)
    artifact = _artifact_with_outlook(
        structured=_UNAVAILABLE_STRUCTURED,
        user_outlook=projected,
    )
    result = _run(tmp_path, artifact)
    assert result.returncode == 0, result.stderr


def test_guard_accepts_legal_outlook_delta(tmp_path):
    projected = project_outlook_delta_for_display(_OUTLOOK_DELTA)
    artifact = _artifact_with_outlook(outlook_delta=projected)
    result = _run(tmp_path, artifact)
    assert result.returncode == 0, result.stderr


def test_guard_rejects_outlook_mismatch_with_structured(tmp_path):
    projected = project_outlook_for_display(_STRUCTURED_OUTLOOK)
    tampered = dict(projected)
    tampered["summary"] = "\u7ec4\u5408\u9884\u8ba1\u56de\u62a5\u73878.5%"
    artifact = _artifact_with_outlook(
        structured=_STRUCTURED_OUTLOOK,
        user_outlook=tampered,
    )
    result = _run(tmp_path, artifact)
    assert result.returncode != 0


def test_guard_rejects_outlook_with_unauthorized_source_in_user(tmp_path):
    projected = project_outlook_for_display(_STRUCTURED_OUTLOOK)
    tampered = dict(projected)
    tampered["source_refs"] = [{"id": "fake", "source": "Fake", "title": "Fake",
                                "url": "https://fake.test", "published_at": "2026-07-17T00:00:00+00:00"}]
    artifact = _artifact_with_outlook(
        structured=_STRUCTURED_OUTLOOK,
        user_outlook=tampered,
    )
    result = _run(tmp_path, artifact)
    assert result.returncode != 0


def test_guard_rejects_mismatched_delta(tmp_path):
    bad_delta = {"has_outlook": False, "changed_since_last": True}
    artifact = _artifact_with_outlook(outlook_delta=bad_delta)
    result = _run(tmp_path, artifact)
    assert result.returncode != 0


def test_guard_fails_closed_on_user_outlook_without_structured(tmp_path):
    projected = project_outlook_for_display(_STRUCTURED_OUTLOOK)
    artifact = _artifact_with_outlook(user_outlook=projected)
    result = _run(tmp_path, artifact)
    assert result.returncode != 0


def test_guard_rejects_outlook_with_trade_instructions(tmp_path):
    projected = project_outlook_for_display(_STRUCTURED_OUTLOOK)
    tampered = dict(projected)
    tampered["summary"] = "\u5efa\u8bae\u52a0\u4ed325%"
    artifact = _artifact_with_outlook(
        structured=_STRUCTURED_OUTLOOK,
        user_outlook=tampered,
    )
    result = _run(tmp_path, artifact)
    assert result.returncode != 0


def test_guard_rejects_outlook_with_internal_tokens(tmp_path):
    projected = project_outlook_for_display(_STRUCTURED_OUTLOOK)
    tampered = dict(projected)
    tampered["summary"] = "position_id=a_510300 \u6cc4\u9732"
    artifact = _artifact_with_outlook(
        structured=_STRUCTURED_OUTLOOK,
        user_outlook=tampered,
    )
    result = _run(tmp_path, artifact)
    assert result.returncode != 0


def test_guard_fails_closed_on_malformed_outlook(tmp_path):
    artifact = _artifact_with_outlook(
        structured=_STRUCTURED_OUTLOOK,
        user_outlook="not-a-dict",
    )
    result = _run(tmp_path, artifact)
    assert result.returncode != 0
