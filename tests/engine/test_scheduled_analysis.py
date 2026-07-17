from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from stocks.adapters.cli import CLIAdapter
from stocks.engine.scheduled_analysis import (
    MarketSessionCalendar,
    ScheduledAnalysisRunner,
    _build_action_cards,
    _run_status,
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
                        "instrument": "a:588000",
                        "type": "stop_loss" if fired else "price_watch",
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


def _tradable_position(
    position_id: str,
    instrument_key: str,
    *,
    freshness: str,
    evidence: dict | None = None,
) -> dict:
    return {
        "position_id": position_id,
        "display_name": position_id,
        "instrument_key": instrument_key,
        "market_value_cny": 10_000.0,
        "price": 90.0,
        "cost_amount": 100.0,
        "pnl_pct": -10.5,
        "portfolio_weight": 1.0,
        "indicators": {},
        "classification": {"product_type": "stock", "exposure_tags": []},
        "liquidity": {"rebalance_eligible": True, "tradable": True},
        "holding": {"quantity": None},
        "evidence": {"price_freshness": freshness, **(evidence or {})},
    }


def test_action_cards_apply_freshness_per_position_in_mixed_market_portfolio():
    cards = _build_action_cards([
        _tradable_position("cn_current", "a:512480", freshness="current"),
        _tradable_position("us_previous_close", "us:ITA", freshness="previous_close"),
    ])

    by_id = {card["position_id"]: card for card in cards}
    assert by_id["cn_current"]["signal"] == "reduce"
    assert by_id["cn_current"]["ratio"] == 0.3
    assert by_id["us_previous_close"]["signal"] == "reduce"
    assert by_id["us_previous_close"]["ratio"] == 0.15


def test_action_card_blocks_anomalous_technical_action_but_keeps_raw_result():
    position = _tradable_position(
        "a_512480",
        "a:512480",
        freshness="current",
        evidence={
            "action_eligible": False,
            "data_anomalies": [{
                "code": "mixed_adjustment_regime",
                "severity": "high",
                "description": "价格序列混用复权口径",
                "bar_index": 7,
                "evidence": {},
            }],
            "blocked_reasons": ["mixed_adjustment_regime: 价格序列混用复权口径"],
        },
    )
    position.update({
        "price": 97.0,
        "cost_amount": 97.0,
        "pnl_pct": 0.0,
        "indicators": {"ma_20": 100.0, "macd": {"hist": -0.1}},
    })

    card = _build_action_cards([position])[0]

    assert card["signal"] == "hold"
    assert card["ratio"] == 0.0
    assert card["action"].startswith("数据异常，暂停技术动作")
    assert card["raw_signal"] == "reduce"
    assert card["raw_ratio"] == 0.5
    assert card["evidence_status"] == "blocked"


def _run(awaitable):
    import asyncio

    return asyncio.run(awaitable)


def test_report_contract_artifact_has_five_trusted_fields(tmp_path):
    calendar = MarketSessionCalendar(_config(tmp_path))
    occurrence = calendar.occurrence_for(
        "cn_pre_close", datetime.fromisoformat("2026-07-06T14:35:00+08:00")
    )
    run = build_scheduled_run(
        _context(), occurrence=occurrence,
        generated_at=datetime.fromisoformat("2026-07-06T14:35:00+08:00"),
        config=_config(tmp_path),
    )
    trusted = {"window_delta", "portfolio_decision", "risk_state", "data_boundaries", "research_candidates"}
    assert trusted <= set(run)
    assert set(run["agent_task"]["data_reference"]) == trusted



def test_all_configured_trading_sessions_emit_v5_trusted_contract(tmp_path):
    config = _config(tmp_path)
    calendar = MarketSessionCalendar(config)
    trading_sessions = [s for s in calendar.sessions if s.market in {"cn", "us"}]
    assert trading_sessions
    for session in trading_sessions:
        occurrence = calendar.occurrence_for(
            session, datetime(2026, 7, 6, 16, 0, tzinfo=timezone.utc)
        )
        run = build_scheduled_run(
            _context(), occurrence=occurrence,
            generated_at=datetime(2026, 7, 6, 16, 0, tzinfo=timezone.utc),
            config=config,
        )
        assert run["agent_task"]["task_version"] == 5, session.id
        for field in (
            "window_delta", "portfolio_decision", "risk_state",
            "data_boundaries", "research_candidates",
        ):
            assert field in run, f"{session.id} missing {field}"



def test_long_term_qdii_short_term_take_profit_is_research_only_not_executable():
    position = _tradable_position(
        "alipay_qdii", "us:QQQ", freshness="previous_close"
    )
    position.update({
        "valuation_method": "fund_nav",
        "price": 6.0,
        "cost_amount": 4.5,
        "pnl_pct": 33.33,
        "classification": {
            "product_type": "qdii_fund",
            "exposure_tags": ["qdii", "nasdaq100"],
        },
        "liquidity": {
            "rebalance_eligible": True,
            "tradable": True,
            "tier": "t2_plus",
        },
    })
    card = _build_action_cards([position])[0]
    assert card["raw_signal"] == "take_profit"
    assert card["routing"] == "fund"
    assert card["signal"] == "hold"
    assert card["ratio"] == 0.0
    assert card["evidence_status"] == "research_only"
    assert "长期配置" in card["action"]



def test_run_status_uses_primary_market_quote_status_not_other_market_staleness():
    quality = {
        "asset_completeness": {"status": "ok"},
        "quotes": {
            "status": "degraded",
            "freshness": "stale",
            "by_market": {
                "a": {"status": "ok", "freshness": "fresh"},
                "us": {"status": "ok", "freshness": "stale"},
            },
        },
        "history_backfill": {"status": "ok"},
        "rotation": {"status": "ok"},
        "action_signals": {"status": "ok"},
    }
    assert _run_status(quality, primary_market="cn") == "ok"
    assert _run_status(quality, primary_market="us") == "degraded"



def test_long_term_qdii_hard_stop_remains_executable_risk_discipline():
    position = _tradable_position(
        "alipay_qdii_stop", "us:QQQ", freshness="previous_close"
    )
    position.update({
        "valuation_method": "fund_nav",
        "price": 3.5,
        "cost_amount": 5.0,
        "pnl_pct": -30.0,
        "classification": {
            "product_type": "qdii_fund",
            "exposure_tags": ["qdii", "nasdaq100"],
        },
        "liquidity": {"rebalance_eligible": True, "tradable": True, "tier": "t2_plus"},
    })
    card = _build_action_cards([position])[0]
    assert card["raw_signal"] == "stop_loss"
    assert card["signal"] == "stop_loss"
    assert card["ratio"] > 0
    assert card["evidence_status"] == "ok"



def test_run_status_never_allows_market_ok_to_override_global_quote_failure():
    quality = {
        "asset_completeness": {"status": "ok"},
        "quotes": {
            "status": "failed",
            "by_market": {"a": {"status": "ok", "freshness": "fresh"}},
        },
        "history_backfill": {"status": "ok"},
        "rotation": {"status": "ok"},
        "action_signals": {"status": "ok"},
    }
    assert _run_status(quality, primary_market="cn") == "degraded"
