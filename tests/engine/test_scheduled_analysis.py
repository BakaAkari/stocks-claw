from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

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
                        "id": "cn_post_open",
                        "time": "09:55",
                        "intent": "post_open_decision",
                        "push": "digest",
                    },
                    {
                        "id": "cn_after_close",
                        "time": "15:00",
                        "intent": "after_close_review",
                        "push": "digest",
                    },
                ],
            },
            "us": {
                "enabled": True,
                "exchange_timezone": "America/New_York",
                "holidays": [],
                "sessions": [
                    {
                        "id": "us_post_open",
                        "time": "09:55",
                        "intent": "post_open_decision",
                        "push": "digest",
                    },
                    {
                        "id": "us_after_close",
                        "time": "16:00",
                        "intent": "after_close_review",
                        "push": "digest",
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
        datetime.fromisoformat("2026-07-06T09:55:00+08:00")
    )
    assert [item.session.id for item in cn_due] == ["cn_post_open"]

    us_dst = calendar.due_sessions(
        datetime.fromisoformat("2026-07-06T21:55:00+08:00")
    )
    assert [item.session.id for item in us_dst] == ["us_post_open"]
    assert us_dst[0].scheduled_for.tzinfo == ZoneInfo("America/New_York")

    us_winter = calendar.due_sessions(
        datetime.fromisoformat("2026-12-01T22:55:00+08:00")
    )
    assert [item.session.id for item in us_winter] == ["us_post_open"]


def test_calendar_skips_weekends(tmp_path):
    calendar = MarketSessionCalendar(_config(tmp_path))

    due = calendar.due_sessions(datetime.fromisoformat("2026-07-04T09:55:00+08:00"))

    assert due == []


def test_runner_writes_latest_and_skips_duplicate(tmp_path):
    config = _config(tmp_path)
    runner = ScheduledAnalysisRunner(
        FakeEngine(_context()),
        config=config,
        artifact_dir=config["artifact_dir"],
    )
    now = datetime.fromisoformat("2026-07-06T09:55:00+08:00")

    first = _run(runner.run_session("cn_post_open", now=now))
    assert first["status"] == "ok"
    assert first["paths"]["json_path"].endswith("_cn_post_open.json")

    latest = runner.latest("cn_post_open")
    assert latest["success"] is True
    assert latest["data"]["session"] == "cn_post_open"
    assert latest["data"]["position_reviews"][0]["session_facts"]

    duplicate = _run(runner.run_session("cn_post_open", now=now))
    assert duplicate["status"] == "skipped_duplicate"


def test_due_run_reports_degraded_when_artifact_is_generated(tmp_path):
    config = _config(tmp_path)
    runner = ScheduledAnalysisRunner(
        FakeEngine(_degraded_context()),
        config=config,
        artifact_dir=config["artifact_dir"],
    )
    now = datetime.fromisoformat("2026-07-06T09:55:00+08:00")

    result = _run(runner.run_due(now=now))

    assert result["status"] == "degraded"
    assert result["runs"][0]["status"] == "degraded"
    assert runner.latest("cn_post_open")["success"] is True


@pytest.mark.skip(reason="quiet_hour behavior changed — all sessions PRIMARY")
def test_build_run_blocks_quiet_hour_noncritical_us_post_open(tmp_path):
    calendar = MarketSessionCalendar(_config(tmp_path))
    occurrence = calendar.occurrence_for(
        "us_post_open",
        datetime.fromisoformat("2026-07-07T03:30:00+08:00"),
    )

    run = build_scheduled_run(
        _context(fired=False),
        occurrence=occurrence,
        generated_at=datetime.fromisoformat("2026-07-07T03:30:00+08:00"),
        config=_config(tmp_path),
    )

    # All sessions are PRIMARY now, quiet_hours disabled
    assert run["notification"]["policy"] in ("generate_only", "send")


@pytest.mark.skip(reason="quiet_hour behavior changed — all sessions PRIMARY")
def test_build_run_allows_critical_despite_quiet_hours(tmp_path):
    calendar = MarketSessionCalendar(_config(tmp_path))
    occurrence = calendar.occurrence_for(
        "us_post_open",
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
    adapter.run(["--scheduled-run-due", "--now", "2026-07-06T09:55:00+08:00", "--force"])
    due = json.loads(capsys.readouterr().out)
    assert due["status"] == "ok"
    assert due["force"] is True

    adapter.run(["--scheduled-run-latest", "cn_post_open"])
    latest = json.loads(capsys.readouterr().out)
    assert latest["data"]["session"] == "cn_post_open"


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
        "cn_post_open", datetime.fromisoformat("2026-07-06T09:55:00+08:00")
    )
    run = build_scheduled_run(
        _context(), occurrence=occurrence,
        generated_at=datetime.fromisoformat("2026-07-06T09:55:00+08:00"),
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
        trusted = {
            "window_delta", "portfolio_decision", "risk_state",
            "data_boundaries", "research_candidates",
        }
        assert run["agent_task"]["task_version"] == 5, session.id
        assert set(run["agent_task"]["data_reference"]) == trusted, session.id
        for field in trusted:
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



def test_scheduled_run_attaches_human_user_view_inside_portfolio_decision(tmp_path):
    calendar = MarketSessionCalendar(_config(tmp_path))
    occurrence = calendar.occurrence_for(
        "cn_post_open", datetime.fromisoformat("2026-07-06T09:55:00+08:00")
    )
    run = build_scheduled_run(
        _context(), occurrence=occurrence,
        generated_at=datetime.fromisoformat("2026-07-06T09:55:00+08:00"),
        config=_config(tmp_path),
    )
    view = run["portfolio_decision"]["user_view"]
    assert set(view) == {"instruction_card", "assistant_brief"}
    assert view["instruction_card"]["status_label"] in {
        "需要操作", "今日无需操作", "等待人工确认",
    }
    rendered = json.dumps(view, ensure_ascii=False)
    assert "decision_id" not in rendered



def test_agent_task_is_trade_card_first_and_bans_internal_tokens(tmp_path):
    config = _config(tmp_path)
    session = MarketSessionCalendar(config).find_session("cn_post_open")
    from stocks.engine.scheduled_analysis import build_agent_task
    task = build_agent_task(session)
    sections = task["output_structure"]["sections"]
    assert [s["name"] for s in sections] == ["交易指令卡", "私人投资助理"]
    instructions = json.dumps(task, ensure_ascii=False)
    assert "portfolio_decision.user_view.instruction_card" in instructions
    assert "portfolio_decision.user_view.assistant_brief" in instructions
    assert "不得向用户展示 position_id" in instructions
    assert "真实名称 + 公开代码" in instructions
    assert "比例 + 预计金额" in instructions


def test_main_window_no_action_card_and_watch_window_silence_are_explicit(tmp_path):
    config = _config(tmp_path)
    calendar = MarketSessionCalendar(config)
    from stocks.engine.scheduled_analysis import build_agent_task
    main = build_agent_task(calendar.find_session("cn_post_open"))
    main_text = json.dumps(main, ensure_ascii=False)
    assert "今日无需操作" in main_text
    assert "1-2个关键原因" in main_text

    # Watch-window silence behavior removed — all sessions are now PRIMARY


# Primary-window synthesis


class FakeSynth:
    """Fake outlook synthesizer that returns a fixed valid outlook."""

    def __init__(self):
        self.calls = 0

    def generate(self, evidence: dict, *, now: str) -> dict:
        self.calls += 1
        return {
            "status": "ok",
            "generated_at": now,
            "summary": "组合研判",
            "near_term": {"horizon": "1-2w", "direction": "supportive", "confidence": "high"},
            "medium_term": {"horizon": "1-3m", "direction": "supportive", "confidence": "medium"},
            "scenarios": {
                "base": {"label": "基准情景", "drivers": ["经济数据温和"], "portfolio_effect": "小幅上涨",
                         "validation": ["GDP符合预期"], "invalidation": ["通胀超预期"]},
                "bull": {"label": "乐观情景", "drivers": ["政策刺激"], "portfolio_effect": "明显上涨",
                         "validation": ["社融超预期"], "invalidation": ["地缘升级"]},
                "risk": {"label": "风险情景", "drivers": ["地缘冲突"], "portfolio_effect": "下跌",
                         "validation": ["VIX>25"], "invalidation": ["政策干预"]},
            },
            "sector_views": [{"sector": "科技", "direction": "supportive", "rationale": "政策支持"}],
            "asset_views": [],
            "source_refs": [],
            "confidence": "high",
            "forecast_candidates": [],
        }


class FakeSynthWithForecast(FakeSynth):
    def generate(self, evidence: dict, *, now: str) -> dict:
        result = super().generate(evidence, now=now)
        result["source_refs"] = [{
            "id": "src-vix", "source": "CBOE", "title": "VIX Index",
            "url": "https://example.test/vix", "published_at": now,
        }]
        result["forecast_candidates"] = [{
            "target": "macro:VIX", "metric": "close", "comparator": "above",
            "level": 25.0, "deadline": "2026-08-01", "confidence": "low",
            "source_ref_ids": ["src-vix"], "requires_confirmation": True,
        }]
        return result


def _engine_with_enough_for_outlook(tmp_path, context_payload=None):
    """Config with cn_after_close and cn_post_open sessions."""
    from tests.engine.test_scheduled_analysis import FakeEngine, _context
    config = {
        "schema_version": 1,
        "user_timezone": "Asia/Shanghai",
        "artifact_dir": str(tmp_path / "scheduled_runs"),
        "default_duplicate_window_minutes": 90,
        "quiet_hours": {"enabled": True, "start": "00:00", "end": "07:30",
                        "timezone": "Asia/Shanghai", "allow_critical": True},
        "markets": {
            "cn": {
                "enabled": True, "exchange_timezone": "Asia/Shanghai",
                "holidays": [],
                "sessions": [
                    {"id": "cn_after_close", "time": "15:30", "intent": "after_close_review",
                     "push": "normal"},
                    {"id": "cn_post_open", "time": "09:55", "intent": "post_open_decision",
                     "push": "normal", "delta_silent_when_unchanged": True},
                ],
            },
        },
    }
    payload = context_payload or _context()
    return FakeEngine(payload), config


def test_primary_session_calls_synthesizer_once(tmp_path):
    """cn_after_close (primary) calls synthesizer exactly once."""
    from stocks.engine.scheduled_analysis import ScheduledAnalysisRunner
    from tests.engine.test_scheduled_analysis import _run
    engine, config = _engine_with_enough_for_outlook(tmp_path)
    runner = ScheduledAnalysisRunner(
        engine, config=config, artifact_dir=config["artifact_dir"],
    )
    fake = FakeSynth()
    runner.outlook_synthesizer = fake

    now = datetime.fromisoformat("2026-07-06T15:00:00+08:00")
    result = _run(runner.run_session("cn_after_close", now=now))
    artifact = json.loads(Path(result["paths"]["json_path"]).read_text())

    assert fake.calls == 1
    assert artifact["structured_outlook"]["status"] == "ok"
    assert artifact["forecast_candidates"] == []


def test_primary_session_exposes_confirmable_forecast_candidates_without_saving(tmp_path):
    from stocks.engine.scheduled_analysis import ScheduledAnalysisRunner
    from tests.engine.test_scheduled_analysis import _run

    engine, config = _engine_with_enough_for_outlook(tmp_path)
    runner = ScheduledAnalysisRunner(
        engine, config=config, artifact_dir=config["artifact_dir"],
    )
    runner.outlook_synthesizer = FakeSynthWithForecast()
    now = datetime.fromisoformat("2026-07-06T15:00:00+08:00")

    result = _run(runner.run_session("cn_after_close", now=now))
    artifact = json.loads(Path(result["paths"]["json_path"]).read_text())

    assert artifact["forecast_candidates"] == [{
        "target": "macro:VIX",
        "metric": "close",
        "comparator": "above",
        "level": 25.0,
        "deadline": "2026-08-01",
        "confidence": "low",
        "source_ref_ids": ["src-vix"],
        "statement": "VIX 在 2026-08-01 前高于 25",
        "requires_confirmation": True,
    }]
    assert not (tmp_path / "forecasts").exists()
    assert artifact["portfolio_decision"]["user_view"]["assistant_brief"]["outlook"]["summary"] == "组合研判"


@pytest.mark.skip(reason="watch/observation sessions removed — feature retired")
def test_watch_session_does_not_call_synthesizer(tmp_path):
    """cn_post_open (observation) does NOT call synthesizer."""
    from stocks.engine.scheduled_analysis import ScheduledAnalysisRunner
    from tests.engine.test_scheduled_analysis import _run
    engine, config = _engine_with_enough_for_outlook(tmp_path)
    runner = ScheduledAnalysisRunner(
        engine, config=config, artifact_dir=config["artifact_dir"],
    )
    fake = FakeSynth()
    runner.outlook_synthesizer = fake

    now = datetime.fromisoformat("2026-07-06T10:00:00+08:00")
    _run(runner.run_session("cn_post_open", now=now))

    assert fake.calls == 0


def test_synthesis_exception_saves_artifact_with_unavailable_outlook(tmp_path):
    """Synthesis exception → unavailable outlook, trade card intact."""
    from stocks.engine.scheduled_analysis import ScheduledAnalysisRunner
    from tests.engine.test_scheduled_analysis import _run
    engine, config = _engine_with_enough_for_outlook(tmp_path)

    class CrashingSynth:
        calls = 0

        def generate(self, evidence, *, now):
            self.calls += 1
            msg = "deliberate crash"
            raise RuntimeError(msg)

    runner = ScheduledAnalysisRunner(
        engine, config=config, artifact_dir=config["artifact_dir"],
    )
    fake = CrashingSynth()
    runner.outlook_synthesizer = fake

    now = datetime.fromisoformat("2026-07-06T15:00:00+08:00")
    result = _run(runner.run_session("cn_after_close", now=now))
    artifact = json.loads(Path(result["paths"]["json_path"]).read_text())

    assert artifact["structured_outlook"]["status"] == "unavailable"
    # Trade card must be intact
    assert "portfolio_decision" in artifact
    assert "user_view" in artifact["portfolio_decision"]
    assert "instruction_card" in artifact["portfolio_decision"]["user_view"]


@pytest.mark.skip(reason="watch/observation sessions removed — feature retired")
def test_observation_session_attaches_delta_when_primaries_differ(tmp_path):
    """With two differing primary outlooks, observation attaches delta."""
    from stocks.engine.scheduled_analysis import ScheduledAnalysisRunner
    from tests.engine.test_scheduled_analysis import _run
    _seed_primary_artifact(tmp_path, "cn_after_close", "2026-07-06",
                           outlook_summary="前期研判")
    _seed_primary_artifact(tmp_path, "cn_after_close", "2026-07-07",
                           outlook_summary="最新研判")

    engine, config = _engine_with_enough_for_outlook(tmp_path)
    runner = ScheduledAnalysisRunner(
        engine, config=config, artifact_dir=config["artifact_dir"],
    )
    runner.outlook_synthesizer = FakeSynth()

    now = datetime.fromisoformat("2026-07-07T10:00:00+08:00")
    result = _run(runner.run_session("cn_post_open", now=now))
    artifact = json.loads(Path(result["paths"]["json_path"]).read_text())

    uv = artifact["portfolio_decision"]["user_view"]["assistant_brief"]
    assert "outlook_delta" in uv
    assert uv["outlook_delta"]["changes"]["summary"] == {"from": "前期研判", "to": "最新研判"}


@pytest.mark.skip(reason="watch/observation sessions removed — feature retired")
def test_observation_session_suppresses_duplicate_delta(tmp_path):
    """First differing delta emitted, second identical delta suppressed."""
    from stocks.engine.scheduled_analysis import ScheduledAnalysisRunner
    from tests.engine.test_scheduled_analysis import _run
    # Seed two differing primaries to produce a real delta
    _seed_primary_artifact(tmp_path, "cn_after_close", "2026-07-06",
                           outlook_summary="前期研判")
    _seed_primary_artifact(tmp_path, "cn_after_close", "2026-07-07",
                           outlook_summary="最新研判")

    engine, config = _engine_with_enough_for_outlook(tmp_path)
    runner = ScheduledAnalysisRunner(
        engine, config=config, artifact_dir=config["artifact_dir"],
    )
    runner.outlook_synthesizer = FakeSynth()

    now = datetime.fromisoformat("2026-07-07T10:00:00+08:00")
    # First run: should emit the delta
    result1 = _run(runner.run_session("cn_post_open", now=now))
    art1 = json.loads(Path(result1["paths"]["json_path"]).read_text())
    uv1 = art1["portfolio_decision"]["user_view"]["assistant_brief"]
    assert "outlook_delta" in uv1
    assert uv1["outlook_delta"]["changes"]["summary"] == {"from": "前期研判", "to": "最新研判"}

    # Second run: force overwrite to produce same delta, which should be suppressed
    result2 = _run(runner.run_session("cn_post_open", now=now, force=True))
    art2 = json.loads(Path(result2["paths"]["json_path"]).read_text())
    uv2 = art2["portfolio_decision"]["user_view"]["assistant_brief"]
    assert "outlook_delta" not in uv2


def _seed_primary_artifact(tmp_path, session_id, market_date, *, outlook_summary):
    """Write a pre-existing primary artifact for delta tests."""
    artifact_dir = Path(tmp_path / "scheduled_runs")
    run_suffix = market_date.replace("-", "")
    run_id = f"{run_suffix}T073000Z_{session_id}"
    path = (
        artifact_dir
        / market_date
        / "cn"
        / session_id
        / f"{run_id}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    run = {
        "schema_version": 1,
        "run_id": run_id,
        "session": session_id,
        "market": "cn",
        "market_date": market_date,
        "generated_at": f"{market_date}T07:30:00+00:00",
        "status": "ok",
        "structured_outlook": {
            "status": "ok",
            "generated_at": f"{market_date}T07:30:00+00:00",
            "summary": outlook_summary,
            "scenarios": {
                "base": {"label": "基准", "drivers": [], "portfolio_effect": "", "validation": [], "invalidation": []},
                "bull": {"label": "乐观", "drivers": [], "portfolio_effect": "", "validation": [], "invalidation": []},
                "risk": {"label": "风险", "drivers": [], "portfolio_effect": "", "validation": [], "invalidation": []},
            },
            "sector_views": [],
            "asset_views": [],
            "source_refs": [],
            "confidence": "high",
            "near_term": {"horizon": "1-2w", "direction": "neutral", "confidence": "medium"},
            "medium_term": {"horizon": "1-3m", "direction": "neutral", "confidence": "medium"},
            "forecast_candidates": [],
        },
        "portfolio_decision": {
            "status": "no_action_needed",
            "user_view": {"instruction_card": {}, "assistant_brief": {}},
        },
    }
    path.write_text(json.dumps(run, ensure_ascii=False))
    # Also write to latest dir
    latest_dir = artifact_dir / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    (latest_dir / f"{session_id}.json").write_text(json.dumps(run, ensure_ascii=False))


# ── _get_delta_state path tests ──────────────────────────────────────────

def test_delta_state_path_for_local_production_dir(tmp_path):
    """When artifact_dir is .local/scheduled_runs, state goes to parent/.local/outlook_delta_state.json."""
    from stocks.engine.scheduled_analysis import ScheduledAnalysisRunner
    from tests.engine.test_scheduled_analysis import FakeEngine, _context

    # Simulate production layout: artifact_dir inside .local
    local_artifact = tmp_path / ".local" / "scheduled_runs"
    local_artifact.mkdir(parents=True)

    engine = FakeEngine(_context())
    config = {
        "schema_version": 1, "user_timezone": "Asia/Shanghai",
        "artifact_dir": str(local_artifact), "default_duplicate_window_minutes": 90,
        "quiet_hours": {"enabled": False},
        "markets": {"cn": {"enabled": True, "exchange_timezone": "Asia/Shanghai",
                        "holidays": [], "sessions": []}},
    }
    runner = ScheduledAnalysisRunner(engine, config=config, artifact_dir=str(local_artifact))
    state = runner._get_delta_state()
    expected = tmp_path / ".local" / "outlook_delta_state.json"
    assert state.path == expected, f"Expected {expected}, got {state.path}"


def test_delta_state_path_for_tmp_scheduled_runs(tmp_path):
    """When artifact_dir is /tmp/scheduled_runs, state goes to tmp/.local/outlook_delta_state.json."""
    from stocks.engine.scheduled_analysis import ScheduledAnalysisRunner
    from tests.engine.test_scheduled_analysis import FakeEngine, _context

    scheduled_dir = tmp_path / "scheduled_runs"
    scheduled_dir.mkdir(parents=True)

    engine = FakeEngine(_context())
    config = {
        "schema_version": 1, "user_timezone": "Asia/Shanghai",
        "artifact_dir": str(scheduled_dir), "default_duplicate_window_minutes": 90,
        "quiet_hours": {"enabled": False},
        "markets": {"cn": {"enabled": True, "exchange_timezone": "Asia/Shanghai",
                        "holidays": [], "sessions": []}},
    }
    runner = ScheduledAnalysisRunner(engine, config=config, artifact_dir=str(scheduled_dir))
    state = runner._get_delta_state()
    expected = tmp_path / ".local" / "outlook_delta_state.json"
    assert state.path == expected, f"Expected {expected}, got {state.path}"


def test_unknown_session_does_not_access_delta_state(tmp_path):
    """A session not in OBSERVATION_OUTLOOK_SESSIONS never creates delta state."""
    from stocks.engine.scheduled_analysis import ScheduledAnalysisRunner
    from tests.engine.test_scheduled_analysis import _run

    engine, config = _engine_with_enough_for_outlook(tmp_path)
    runner = ScheduledAnalysisRunner(
        engine, config=config, artifact_dir=config["artifact_dir"],
    )
    runner.outlook_synthesizer = FakeSynth()

    now = datetime.fromisoformat("2026-07-06T15:00:00+08:00")
    # cn_after_close is a PRIMARY session, not OBSERVATION -- should not create delta state
    result = _run(runner.run_session("cn_after_close", now=now))
    assert result["status"] == "ok"

    delta_state_path = Path(config["artifact_dir"]).parent / ".local" / "outlook_delta_state.json"
    assert not delta_state_path.exists(), (
        "Primary session should not create delta state file"
    )


# ── Outlook projection trust boundary tests ──────────────────────


def test_primary_outlook_projection_strips_unknown_keys(tmp_path):
    """FakeSynth returns outlook with unknown_extra; assistant_brief.outlook strips it."""
    from stocks.engine.scheduled_analysis import ScheduledAnalysisRunner
    from tests.engine.test_scheduled_analysis import _run

    class LeakySynth:
        calls = 0
        def generate(self, evidence, *, now):
            self.calls += 1
            return {
                "status": "ok",
                "generated_at": now,
                "summary": "测试",
                "near_term": {"horizon": "1-2w", "direction": "supportive", "confidence": "high"},
                "unknown_extra": "should_be_stripped",
                "internal_code": "leaked_position_id_12345",
            }

    engine, config = _engine_with_enough_for_outlook(tmp_path)
    runner = ScheduledAnalysisRunner(engine, config=config, artifact_dir=config["artifact_dir"])
    runner.outlook_synthesizer = LeakySynth()

    now = datetime.fromisoformat("2026-07-06T15:00:00+08:00")
    result = _run(runner.run_session("cn_after_close", now=now))
    artifact = json.loads(Path(result["paths"]["json_path"]).read_text())

    brief = artifact["portfolio_decision"]["user_view"]["assistant_brief"]
    assert "outlook" in brief
    outlook = brief["outlook"]
    assert outlook["summary"] == "测试"
    # Unknown keys MUST be stripped
    assert "unknown_extra" not in outlook
    assert "internal_code" not in outlook


def test_primary_outlook_projection_strips_position_id(tmp_path):
    """position_id in synthetic outlook is stripped from assistant_brief."""
    from stocks.engine.scheduled_analysis import ScheduledAnalysisRunner
    from tests.engine.test_scheduled_analysis import _run

    class LeakySynth2:
        calls = 0
        def generate(self, evidence, *, now):
            self.calls += 1
            return {
                "status": "ok",
                "generated_at": now,
                "summary": "正常判断",
                "position_id": "cn_588000",
                "near_term": {"horizon": "1-2w", "direction": "supportive", "confidence": "high"},
            }

    engine, config = _engine_with_enough_for_outlook(tmp_path)
    runner = ScheduledAnalysisRunner(engine, config=config, artifact_dir=config["artifact_dir"])
    runner.outlook_synthesizer = LeakySynth2()

    now = datetime.fromisoformat("2026-07-06T15:00:00+08:00")
    result = _run(runner.run_session("cn_after_close", now=now))
    artifact = json.loads(Path(result["paths"]["json_path"]).read_text())

    brief = artifact["portfolio_decision"]["user_view"]["assistant_brief"]
    assert "outlook" in brief
    assert "position_id" not in brief["outlook"]
    assert brief["outlook"]["summary"] == "正常判断"


def test_unavailable_outlook_is_projected(tmp_path):
    """Unavailable outlook (status=unavailable) goes through projection."""
    from stocks.engine.scheduled_analysis import ScheduledAnalysisRunner
    from tests.engine.test_scheduled_analysis import _run

    class UnavailableSynth:
        calls = 0
        def generate(self, evidence, *, now):
            self.calls += 1
            msg = "deliberate"
            raise RuntimeError(msg)

    engine, config = _engine_with_enough_for_outlook(tmp_path)
    runner = ScheduledAnalysisRunner(engine, config=config, artifact_dir=config["artifact_dir"])
    runner.outlook_synthesizer = UnavailableSynth()

    now = datetime.fromisoformat("2026-07-06T15:00:00+08:00")
    result = _run(runner.run_session("cn_after_close", now=now))
    artifact = json.loads(Path(result["paths"]["json_path"]).read_text())

    brief = artifact["portfolio_decision"]["user_view"]["assistant_brief"]
    assert "outlook" in brief
    ul = brief["outlook"]
    assert ul["status"] == "unavailable"
    assert "message" in ul
    assert "data_limitations" in ul


@pytest.mark.skip(reason="watch/observation sessions removed — feature retired")
def test_observation_delta_projection_strips_unknown_nested(tmp_path):
    """Delta with unknown keys in changes is projected to whitelist."""
    from stocks.engine.scheduled_analysis import ScheduledAnalysisRunner
    from tests.engine.test_scheduled_analysis import _run

    _seed_primary_artifact(tmp_path, "cn_after_close", "2026-07-06",
                           outlook_summary="前期研判")
    _seed_primary_artifact(tmp_path, "cn_after_close", "2026-07-07",
                           outlook_summary="最新研判")

    engine, config = _engine_with_enough_for_outlook(tmp_path)
    runner = ScheduledAnalysisRunner(engine, config=config, artifact_dir=config["artifact_dir"])
    runner.outlook_synthesizer = FakeSynth()

    # Monkey-patch compute_outlook_delta to inject malicious delta
    import stocks.engine.scheduled_analysis as sa_mod
    original = sa_mod.compute_outlook_delta
    def _poisoned_delta(p1, p2):
        return {
            "schema_version": 1,
            "market": "cn",
            "changes": {
                "summary": {"from": "中性", "to": "偏有利"},
                "position_id": {"from": "cn_588000", "to": "cn_588001"},
                "_secret": "leaked_internal_data",
            },
            "extra_top_key": "should_not_pass",
        }
    sa_mod.compute_outlook_delta = _poisoned_delta
    try:
        now = datetime.fromisoformat("2026-07-07T10:00:00+08:00")
        result = _run(runner.run_session("cn_post_open", now=now, force=True))
        artifact = json.loads(Path(result["paths"]["json_path"]).read_text())
        uv = artifact["portfolio_decision"]["user_view"]["assistant_brief"]
        assert "outlook_delta" in uv
        od = uv["outlook_delta"]
        # Whitelisted fields present
        assert od.get("changes", {}).get("summary") == {"from": "中性", "to": "偏有利"}
        # Unknown keys MUST be stripped
        assert "position_id" not in od.get("changes", {})
        assert "_secret" not in od.get("changes", {})
        assert "extra_top_key" not in od
    finally:
        sa_mod.compute_outlook_delta = original
