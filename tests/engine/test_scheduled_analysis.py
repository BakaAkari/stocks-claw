from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from stocks.adapters.cli import CLIAdapter
from stocks.engine.scheduled_analysis import (
    MarketSessionCalendar,
    ScheduledAnalysisRunner,
    build_scheduled_run,
)


def _config(tmp_path: Path) -> dict:
    return {
        "schema_version": 1,
        "user_timezone": "Asia/Shanghai",
        "artifact_dir": str(tmp_path / "scheduled_runs"),
        "default_duplicate_window_minutes": 90,
        "quiet_hours": {
            "enabled": True,
            "start": "00:00",
            "end": "07:30",
            "timezone": "Asia/Shanghai",
            "allow_critical": True,
        },
        "markets": {
            "cn": {
                "enabled": True,
                "exchange_timezone": "Asia/Shanghai",
                "holidays": [],
                "sessions": [
                    {
                        "id": "cn_pre_close",
                        "time": "14:35",
                        "intent": "pre_close_decision",
                        "push": "normal",
                    }
                ],
            },
            "us": {
                "enabled": True,
                "exchange_timezone": "America/New_York",
                "holidays": [],
                "sessions": [
                    {
                        "id": "us_pre_open",
                        "time": "09:00",
                        "intent": "pre_open_plan",
                        "push": "normal",
                    },
                    {
                        "id": "us_pre_close",
                        "time": "15:30",
                        "intent": "pre_close_decision",
                        "push": "critical_only",
                    },
                ],
            },
        },
    }


def _context(*, fired: bool = False) -> dict:
    trigger_status = "fired" if fired else "not_fired"
    return {
        "schema_version": 12,
        "generated_at": "2026-07-06T06:35:02+00:00",
        "data_quality": {
            "asset_completeness": {"status": "ok"},
            "quotes": {"status": "ok"},
            "history_backfill": {"status": "ok"},
            "rotation": {"status": "ok"},
            "action_signals": {"status": "ok"},
        },
        "position_valuations": [
            {
                "position_id": "cn_588000",
                "account_id": "cn_broker",
                "display_name": "科创50ETF",
                "instrument_key": "a:588000",
                "advice_granularity": "detailed",
                "price": 2.1,
                "price_source": "eastmoney_a",
                "as_of": "2026-07-06T06:30:00+00:00",
                "market_value_cny": 2100.0,
                "cost_amount": 1600.0,
                "unrealized_pnl_cny": 500.0,
                "pnl_pct": 31.25,
                "liquidity": {
                    "rebalance_eligible": True,
                    "tradable": True,
                    "tier": "t1",
                },
                "flags": [],
                "missing_fields": [],
            }
        ],
        "recent_advice": [
            {
                "id": "advice-1",
                "summary": "测试建议",
                "trigger_review": [
                    {
                        "target": "a:588000",
                        "status": trigger_status,
                        "observed": {"pnl_pct": 31.25},
                    }
                ],
            }
        ],
        "action_signals": {
            "status": "ok",
            "items": [
                {
                    "symbol": "a:512480",
                    "name": "半导体ETF",
                    "signal": "accumulate_candidate",
                    "action_hint": "可分批布局",
                    "reasons": ["趋势在 MA20 上方"],
                    "category": "半导体",
                    "pool": "sector",
                    "universe": "scan",
                    "as_of": "2026-07-06T00:00:00+00:00",
                }
            ],
        },
        "rotation": {"leaders": ["a:512480"]},
        "market_state": {"risk_appetite": "stable"},
        "portfolio_mapping": {"ratios": {"权益": 0.2}},
        "exposure_summary": {},
        "liquidity_summary": {},
        "advice_granularity": {},
    }


def _degraded_context() -> dict:
    payload = _context()
    payload["data_quality"] = {
        **payload["data_quality"],
        "quotes": {"status": "degraded", "errors": ["test degradation"]},
    }
    return payload


class FakeContext:
    def __init__(self, payload: dict):
        self.payload = payload

    def to_dict(self) -> dict:
        return self.payload


class FakeEngine:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0

    async def build_context(self, **kwargs):
        self.calls += 1
        return FakeContext(self.payload)


def test_calendar_due_sessions_use_exchange_timezones(tmp_path):
    calendar = MarketSessionCalendar(_config(tmp_path))

    cn_due = calendar.due_sessions(
        datetime.fromisoformat("2026-07-06T14:35:00+08:00")
    )
    assert [item.session.id for item in cn_due] == ["cn_pre_close"]

    us_dst = calendar.due_sessions(
        datetime.fromisoformat("2026-07-06T21:00:00+08:00")
    )
    assert [item.session.id for item in us_dst] == ["us_pre_open"]
    assert us_dst[0].scheduled_for.tzinfo == ZoneInfo("America/New_York")

    us_winter = calendar.due_sessions(
        datetime.fromisoformat("2026-12-01T22:00:00+08:00")
    )
    assert [item.session.id for item in us_winter] == ["us_pre_open"]


def test_calendar_skips_weekends(tmp_path):
    calendar = MarketSessionCalendar(_config(tmp_path))

    due = calendar.due_sessions(datetime.fromisoformat("2026-07-04T14:35:00+08:00"))

    assert due == []


def test_runner_writes_latest_and_skips_duplicate(tmp_path):
    config = _config(tmp_path)
    runner = ScheduledAnalysisRunner(
        FakeEngine(_context()),
        config=config,
        artifact_dir=config["artifact_dir"],
    )
    now = datetime.fromisoformat("2026-07-06T14:35:00+08:00")

    first = _run(runner.run_session("cn_pre_close", now=now))
    assert first["status"] == "ok"
    assert first["paths"]["json_path"].endswith("_cn_pre_close.json")

    latest = runner.latest("cn_pre_close")
    assert latest["success"] is True
    assert latest["data"]["session"] == "cn_pre_close"
    assert latest["data"]["position_reviews"][0]["session_facts"]

    duplicate = _run(runner.run_session("cn_pre_close", now=now))
    assert duplicate["status"] == "skipped_duplicate"


def test_due_run_reports_degraded_when_artifact_is_generated(tmp_path):
    config = _config(tmp_path)
    runner = ScheduledAnalysisRunner(
        FakeEngine(_degraded_context()),
        config=config,
        artifact_dir=config["artifact_dir"],
    )
    now = datetime.fromisoformat("2026-07-06T14:35:00+08:00")

    result = _run(runner.run_due(now=now))

    assert result["status"] == "degraded"
    assert result["runs"][0]["status"] == "degraded"
    assert runner.latest("cn_pre_close")["success"] is True


def test_build_run_blocks_quiet_hour_noncritical_us_pre_close(tmp_path):
    calendar = MarketSessionCalendar(_config(tmp_path))
    occurrence = calendar.occurrence_for(
        "us_pre_close",
        datetime.fromisoformat("2026-07-07T03:30:00+08:00"),
    )

    run = build_scheduled_run(
        _context(fired=False),
        occurrence=occurrence,
        generated_at=datetime.fromisoformat("2026-07-07T03:30:00+08:00"),
        config=_config(tmp_path),
    )

    assert run["session_summary"]["priority"] == "normal"
    assert run["notification"]["recommended"] is False
    assert run["notification"]["policy"] == "generate_only"


def test_build_run_allows_critical_despite_quiet_hours(tmp_path):
    calendar = MarketSessionCalendar(_config(tmp_path))
    occurrence = calendar.occurrence_for(
        "us_pre_close",
        datetime.fromisoformat("2026-07-07T03:30:00+08:00"),
    )

    run = build_scheduled_run(
        _context(fired=True),
        occurrence=occurrence,
        generated_at=datetime.fromisoformat("2026-07-07T03:30:00+08:00"),
        config=_config(tmp_path),
    )

    assert run["session_summary"]["priority"] == "critical"
    assert run["notification"]["recommended"] is True
    assert run["notification"]["policy"] == "push_now"


def test_cli_exposes_scheduled_due_and_latest(capsys):
    class CliEngine:
        async def scheduled_run_due(self, *, now=None, force=False):
            return {"success": True, "status": "ok", "now": now, "force": force}

        def scheduled_run_latest(self, session_id):
            return {"success": True, "data": {"session": session_id}}

    adapter = CLIAdapter(CliEngine())
    adapter.run(["--scheduled-run-due", "--now", "2026-07-06T14:35:00+08:00", "--force"])
    due = json.loads(capsys.readouterr().out)
    assert due["status"] == "ok"
    assert due["force"] is True

    adapter.run(["--scheduled-run-latest", "cn_pre_close"])
    latest = json.loads(capsys.readouterr().out)
    assert latest["data"]["session"] == "cn_pre_close"


def _run(awaitable):
    import asyncio

    return asyncio.run(awaitable)
