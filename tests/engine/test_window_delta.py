"""Tests for Task 8: Window Delta, Priority, Notifications.

Strict TDD — these tests must fail RED before implementation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from stocks.engine.scheduled_analysis import (
    RunArtifactStore,
    ScheduledSession,
    _notification,
    _priority,
    build_scheduled_run,
)
from stocks.engine.window_delta import WindowDelta, compute_window_delta


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 7, 15, 14, 35, tzinfo=timezone.utc)


@pytest.fixture
def cn_session() -> ScheduledSession:
    return ScheduledSession(
        id="cn_pre_close",
        market="cn",
        primary_market="cn",
        exchange_timezone="Asia/Shanghai",
        user_timezone="Asia/Shanghai",
        time="14:35",
        intent="pre_close_decision",
        push="normal",
        enabled=True,
        duplicate_window_minutes=90,
        holidays=frozenset(),
        delta_silent_when_unchanged=True,
    )


@pytest.fixture
def cn_open_watch_session() -> ScheduledSession:
    return ScheduledSession(
        id="cn_open_watch",
        market="cn",
        primary_market="cn",
        exchange_timezone="Asia/Shanghai",
        user_timezone="Asia/Shanghai",
        time="10:00",
        intent="open_watch",
        push="normal",
        enabled=True,
        duplicate_window_minutes=90,
        holidays=frozenset(),
        delta_silent_when_unchanged=True,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. compute_window_delta — pure function
# ═══════════════════════════════════════════════════════════════════════════


class TestComputeWindowDelta:
    """Unit tests for compute_window_delta pure function."""

    def test_initial_run_no_previous_is_material(self):
        """Previous=None => first_in_session=True, material=True."""
        current = {"run_id": "run-1", "generated_at": "2026-07-15T14:35:00+00:00"}
        delta = compute_window_delta(None, current, session_id="cn_pre_close", market="cn")
        assert delta.first_in_session is True
        assert delta.material is True
        assert delta.previous_run_id is None
        assert delta.session_id == "cn_pre_close"

    def test_identical_artifacts_no_material_change(self):
        """Same structure, different run_id/generated_at => no material change."""
        previous = {
            "run_id": "run-1",
            "generated_at": "2026-07-15T14:35:00+00:00",
            "risk_state": {"level": "normal", "transition": "initial", "candidate_level": None},
            "portfolio_decision": {
                "status": "approved",
                "decision_id": "abc",
                "approved_actions": [],
                "suppressed_actions": [],
                "unresolved_conflicts": [],
            },
            "position_reviews": [],
            "trigger_reviews": [],
            "session_summary": {"priority": "normal"},
            "action_cards": [],
        }
        current = {
            "run_id": "run-2",
            "generated_at": "2026-07-15T14:36:00+00:00",
            "risk_state": {"level": "normal", "transition": "initial", "candidate_level": None},
            "portfolio_decision": {
                "status": "approved",
                "decision_id": "abc",
                "approved_actions": [],
                "suppressed_actions": [],
                "unresolved_conflicts": [],
            },
            "position_reviews": [],
            "trigger_reviews": [],
            "session_summary": {"priority": "normal"},
            "action_cards": [],
        }
        delta = compute_window_delta(previous, current, session_id="cn_pre_close", market="cn")
        assert delta.first_in_session is False
        assert delta.material is False
        assert delta.previous_run_id == "run-1"
        assert len(delta.changes) == 0

    def test_ratio_change_is_material(self):
        """Ratio change in approved action => material=True, changes listed."""
        previous = {
            "run_id": "run-1",
            "generated_at": "2026-07-15T14:00:00+00:00",
            "portfolio_decision": {
                "status": "approved",
                "decision_id": "dec-1",
                "approved_actions": [
                    {
                        "position_id": "cn_588000",
                        "signal": "reduce",
                        "ratio": 0.3,
                        "decision_id": "a1",
                    }
                ],
                "suppressed_actions": [],
                "unresolved_conflicts": [],
            },
            "risk_state": {"level": "normal", "transition": "initial", "candidate_level": None},
            "position_reviews": [],
            "trigger_reviews": [],
            "action_cards": [],
        }
        current = {
            "run_id": "run-2",
            "generated_at": "2026-07-15T14:35:00+00:00",
            "portfolio_decision": {
                "status": "approved",
                "decision_id": "dec-2",
                "approved_actions": [
                    {
                        "position_id": "cn_588000",
                        "signal": "reduce",
                        "ratio": 0.5,
                        "decision_id": "a2",
                    }
                ],
                "suppressed_actions": [],
                "unresolved_conflicts": [],
            },
            "risk_state": {"level": "normal", "transition": "initial", "candidate_level": None},
            "position_reviews": [],
            "trigger_reviews": [],
            "action_cards": [],
        }
        delta = compute_window_delta(previous, current, session_id="cn_pre_close", market="cn")
        assert delta.material is True
        assert any("ratio" in c.get("field", "") or "ratio" in str(c) for c in delta.changes)

    def test_decision_id_only_change_is_not_material(self):
        previous = {
            "run_id": "run-1",
            "risk_state": {"level": "normal"},
            "portfolio_decision": {
                "status": "approved",
                "approved_actions": [
                    {
                        "position_id": "p1",
                        "signal": "reduce",
                        "action": "reduce",
                        "ratio": 0.3,
                        "decision_id": "old",
                    }
                ],
            },
        }
        current = {
            "run_id": "run-2",
            "risk_state": {"level": "normal"},
            "portfolio_decision": {
                "status": "approved",
                "approved_actions": [
                    {
                        "position_id": "p1",
                        "signal": "reduce",
                        "action": "reduce",
                        "ratio": 0.3,
                        "decision_id": "new",
                    }
                ],
            },
        }
        delta = compute_window_delta(previous, current, session_id="cn_pre_close", market="cn")
        assert delta.material is False

    def test_risk_state_change_is_material(self):
        """Risk level change => material=True."""
        previous = {
            "run_id": "run-1",
            "generated_at": "2026-07-15T14:00:00+00:00",
            "risk_state": {"level": "normal", "transition": "initial", "candidate_level": None},
            "portfolio_decision": {
                "status": "approved",
                "approved_actions": [],
                "suppressed_actions": [],
                "unresolved_conflicts": [],
            },
            "position_reviews": [],
            "trigger_reviews": [],
            "action_cards": [],
        }
        current = {
            "run_id": "run-2",
            "generated_at": "2026-07-15T14:35:00+00:00",
            "risk_state": {"level": "hedge", "transition": "escalated", "candidate_level": None},
            "portfolio_decision": {
                "status": "approved",
                "approved_actions": [],
                "suppressed_actions": [],
                "unresolved_conflicts": [],
            },
            "position_reviews": [],
            "trigger_reviews": [],
            "action_cards": [],
        }
        delta = compute_window_delta(previous, current, session_id="cn_pre_close", market="cn")
        assert delta.material is True
        assert any("risk" in c.get("field", "").lower() for c in delta.changes)

    def test_trigger_fixture_restores_material_push(self):
        previous = {
            "run_id": "r1",
            "risk_state": {"level": "normal", "transition": "unchanged"},
            "portfolio_decision": {"status": "approved", "approved_actions": []},
            "trigger_reviews": [],
        }
        current = {
            "run_id": "r2",
            "risk_state": {"level": "normal", "transition": "unchanged"},
            "portfolio_decision": {"status": "approved", "approved_actions": []},
            "trigger_reviews": [
                {
                    "trigger_id": "sl-p1",
                    "type": "stop_loss",
                    "instrument": "p1",
                    "status": "fired",
                }
            ],
        }
        delta = compute_window_delta(previous, current, session_id="cn_pre_close", market="cn")
        assert delta.material is True
        assert delta.changes[-1]["newly_fired"] == ["sl-p1"]

    def test_new_fired_trigger_is_material(self):
        """Newly fired trigger => material=True."""
        previous = {
            "run_id": "run-1",
            "generated_at": "2026-07-15T14:00:00+00:00",
            "trigger_reviews": [],
            "risk_state": {"level": "normal", "transition": "initial", "candidate_level": None},
            "portfolio_decision": {
                "status": "approved",
                "approved_actions": [],
                "suppressed_actions": [],
                "unresolved_conflicts": [],
            },
            "position_reviews": [],
            "action_cards": [],
        }
        current = {
            "run_id": "run-2",
            "generated_at": "2026-07-15T14:35:00+00:00",
            "trigger_reviews": [{"type": "stop_loss", "status": "fired", "instrument": "a:588000"}],
            "risk_state": {"level": "normal", "transition": "initial", "candidate_level": None},
            "portfolio_decision": {
                "status": "approved",
                "approved_actions": [],
                "suppressed_actions": [],
                "unresolved_conflicts": [],
            },
            "position_reviews": [],
            "action_cards": [],
        }
        delta = compute_window_delta(previous, current, session_id="cn_pre_close", market="cn")
        assert delta.material is True
        assert any("trigger" in c.get("field", "").lower() for c in delta.changes)

    def test_ignores_noise_fields(self):
        """Changes in generated_at, run_id, schema_version, etc. are ignored."""
        previous = {
            "run_id": "run-1",
            "generated_at": "2026-07-15T14:00:00+00:00",
            "schema_version": 1,
            "risk_state": {"level": "normal", "transition": "initial", "candidate_level": None},
            "portfolio_decision": {
                "status": "approved",
                "approved_actions": [],
                "suppressed_actions": [],
                "unresolved_conflicts": [],
            },
            "position_reviews": [],
            "trigger_reviews": [],
            "action_cards": [],
        }
        current = {
            "run_id": "run-2",
            "generated_at": "2026-07-15T14:35:00+00:00",
            "schema_version": 1,
            "risk_state": {"level": "normal", "transition": "initial", "candidate_level": None},
            "portfolio_decision": {
                "status": "approved",
                "approved_actions": [],
                "suppressed_actions": [],
                "unresolved_conflicts": [],
            },
            "position_reviews": [],
            "trigger_reviews": [],
            "action_cards": [],
        }
        delta = compute_window_delta(previous, current, session_id="cn_pre_close", market="cn")
        assert delta.material is False
        assert len(delta.changes) == 0

    def test_anomaly_code_change_is_material(self):
        """New anomaly codes in position evidence => material=True."""
        previous = {
            "run_id": "run-1",
            "generated_at": "2026-07-15T14:00:00+00:00",
            "risk_state": {"level": "normal", "transition": "initial", "candidate_level": None},
            "portfolio_decision": {
                "status": "approved",
                "approved_actions": [],
                "suppressed_actions": [],
                "unresolved_conflicts": [],
            },
            "position_reviews": [{"position_id": "cn_588000", "evidence": {"data_anomalies": []}}],
            "trigger_reviews": [],
            "action_cards": [],
        }
        current = {
            "run_id": "run-2",
            "generated_at": "2026-07-15T14:35:00+00:00",
            "risk_state": {"level": "normal", "transition": "initial", "candidate_level": None},
            "portfolio_decision": {
                "status": "approved",
                "approved_actions": [],
                "suppressed_actions": [],
                "unresolved_conflicts": [],
            },
            "position_reviews": [
                {
                    "position_id": "cn_588000",
                    "evidence": {"data_anomalies": [{"code": "mixed_adjustment_regime"}]},
                }
            ],
            "trigger_reviews": [],
            "action_cards": [],
        }
        delta = compute_window_delta(previous, current, session_id="cn_pre_close", market="cn")
        assert delta.material is True
        assert any("anomal" in c.get("field", "").lower() for c in delta.changes)

    def test_suppressed_action_change_is_material(self):
        """Change in suppressed actions list => material."""
        previous = {
            "run_id": "run-1",
            "generated_at": "2026-07-15T14:00:00+00:00",
            "risk_state": {"level": "normal", "transition": "initial", "candidate_level": None},
            "portfolio_decision": {
                "status": "suppressed",
                "approved_actions": [],
                "suppressed_actions": [{"position_id": "cn_512480", "signal": "add", "ratio": 0.1}],
                "unresolved_conflicts": [],
            },
            "position_reviews": [],
            "trigger_reviews": [],
            "action_cards": [],
        }
        current = {
            "run_id": "run-2",
            "generated_at": "2026-07-15T14:35:00+00:00",
            "risk_state": {"level": "normal", "transition": "initial", "candidate_level": None},
            "portfolio_decision": {
                "status": "approved",
                "approved_actions": [],
                "suppressed_actions": [],
                "unresolved_conflicts": [],
            },
            "position_reviews": [],
            "trigger_reviews": [],
            "action_cards": [],
        }
        delta = compute_window_delta(previous, current, session_id="cn_pre_close", market="cn")
        assert delta.material is True


# ═══════════════════════════════════════════════════════════════════════════
# 2. _priority — enhanced
# ═══════════════════════════════════════════════════════════════════════════


class TestPriority:
    """Enhanced priority function tests."""

    def test_normal_risk_no_approved_urgent_is_normal(self, cn_session):
        """Normal risk, no approved urgent action => priority=normal."""
        risk_state = {"level": "normal", "transition": "initial"}
        portfolio_decision = {
            "status": "approved",
            "approved_actions": [{"signal": "add", "ratio": 0.1}],
        }
        fired_triggers: list[str] = []
        p = _priority(risk_state, portfolio_decision, fired_triggers)
        assert p == "normal"

    def test_high_loss_position_no_approved_urgent_is_not_critical(self, cn_session):
        """Even with high-loss positions, no approved urgent => not critical."""
        risk_state = {"level": "normal", "transition": "initial"}
        portfolio_decision = {
            "status": "approved",
            "approved_actions": [{"signal": "reduce", "ratio": 0.3}],
        }
        fired_triggers: list[str] = []
        p = _priority(risk_state, portfolio_decision, fired_triggers)
        assert p != "critical"

    def test_risk_hedge_is_critical(self, cn_session):
        """Risk level=hedge => priority=critical."""
        risk_state = {"level": "hedge", "transition": "escalated"}
        portfolio_decision = {"status": "approved", "approved_actions": []}
        fired_triggers: list[str] = []
        p = _priority(risk_state, portfolio_decision, fired_triggers)
        assert p == "critical"

    def test_risk_reduce_is_high(self, cn_session):
        """Risk level=reduce => priority=high."""
        risk_state = {"level": "reduce", "transition": "escalated"}
        portfolio_decision = {"status": "approved", "approved_actions": []}
        fired_triggers: list[str] = []
        p = _priority(risk_state, portfolio_decision, fired_triggers)
        assert p == "high"

    def test_review_required_is_high(self, cn_session):
        """PortfolioDecision status=review_required => priority=high."""
        risk_state = {"level": "normal", "transition": "initial"}
        portfolio_decision = {
            "status": "review_required",
            "approved_actions": [],
            "unresolved_conflicts": [{"code": "adjudication_failed"}],
        }
        fired_triggers: list[str] = []
        p = _priority(risk_state, portfolio_decision, fired_triggers)
        assert p == "high"

    def test_approved_stop_loss_is_critical(self, cn_session):
        """Approved stop_loss action => priority=critical."""
        risk_state = {"level": "normal", "transition": "initial"}
        portfolio_decision = {
            "status": "approved",
            "approved_actions": [{"signal": "stop_loss", "ratio": 1.0, "position_id": "cn_test"}],
        }
        fired_triggers: list[str] = []
        p = _priority(risk_state, portfolio_decision, fired_triggers)
        assert p == "critical"

    def test_approved_urgent_reduce_is_critical(self, cn_session):
        """Approved urgent action (reduce with high ratio) => critical."""
        risk_state = {"level": "normal", "transition": "initial"}
        portfolio_decision = {
            "status": "approved",
            "approved_actions": [{"signal": "reduce", "ratio": 0.5, "position_id": "cn_test"}],
        }
        fired_triggers: list[str] = []
        p = _priority(risk_state, portfolio_decision, fired_triggers)
        assert p == "critical"

    def test_fired_stop_loss_trigger_is_critical(self, cn_session):
        """Fired stop-loss trigger => priority=critical."""
        risk_state = {"level": "normal", "transition": "initial"}
        portfolio_decision = {"status": "approved", "approved_actions": []}
        fired_triggers = ["stop_loss:a:588000"]
        p = _priority(risk_state, portfolio_decision, fired_triggers)
        assert p == "critical"

    # ═══════════════════════════════════════════════════════════════════════════
    # 3. Delta-driven notification
    # ═══════════════════════════════════════════════════════════════════════════

    def test_persistent_hedge_is_high_not_repeated_critical(self):
        priority = _priority(
            {"level": "hedge", "transition": "unchanged"},
            {"status": "approved", "approved_actions": []},
            [],
        )
        assert priority == "high"

    def test_nonurgent_fired_trigger_is_not_critical(self):
        priority = _priority(
            {"level": "normal", "transition": "unchanged"},
            {"status": "approved", "approved_actions": []},
            ["price_watch:a:588000"],
            trigger_reviews=[{"status": "fired", "type": "price_watch"}],
        )
        assert priority == "normal"


class TestProductionArtifactFields:
    def test_position_review_carries_real_anomaly_evidence(self, tmp_path):
        from stocks.engine.scheduled_analysis import MarketSessionCalendar

        config = TestWindowDeltaIntegration()._config(tmp_path)
        calendar = MarketSessionCalendar(config)
        occurrence = calendar.occurrence_for(
            "cn_pre_close", datetime.fromisoformat("2026-07-15T14:35:00+08:00")
        )
        context = TestWindowDeltaIntegration()._context()
        context["position_valuations"] = [
            {
                "position_id": "p1",
                "instrument_key": "a:1",
                "display_name": "P1",
                "liquidity": {},
                "evidence": {"data_anomalies": [{"code": "price_jump"}]},
            }
        ]
        run = build_scheduled_run(
            context,
            occurrence=occurrence,
            generated_at=datetime.fromisoformat("2026-07-15T14:35:00+08:00"),
            config=config,
        )
        assert run["position_reviews"][0]["evidence"]["data_anomalies"][0]["code"] == "price_jump"


class TestDeltaDrivenNotification:
    """Notification policy driven by window delta."""

    def test_first_run_not_silent(self, cn_session, now):
        """First run (no previous) => not silent, push according to config."""
        delta = WindowDelta(
            session_id="cn_pre_close",
            market="cn",
            previous_run_id=None,
            current_run_id="run-1",
            material=True,
            changes=[],
            first_in_session=True,
            priority="normal",
        )
        notif = _notification(
            session=cn_session,
            priority="normal",
            now=now,
            quiet_hours={"enabled": False},
            window_delta=delta,
        )
        assert notif["recommended"] is True
        assert notif.get("policy") != "archive_only"

    def test_no_material_change_archive_only(self, cn_session, now):
        """No material change + delta_silent_when_unchanged => archive_only."""
        delta = WindowDelta(
            session_id="cn_pre_close",
            market="cn",
            previous_run_id="run-1",
            current_run_id="run-2",
            material=False,
            changes=[],
            first_in_session=False,
            priority="normal",
        )
        notif = _notification(
            session=cn_session,
            priority="normal",
            now=now,
            quiet_hours={"enabled": False},
            window_delta=delta,
        )
        assert notif["policy"] == "archive_only"
        assert notif["recommended"] is False

    def test_material_change_pushes(self, cn_session, now):
        """Material change => push according to config."""
        delta = WindowDelta(
            session_id="cn_pre_close",
            market="cn",
            previous_run_id="run-1",
            current_run_id="run-2",
            material=True,
            changes=[{"field": "risk_state.level", "old": "normal", "new": "hedge"}],
            first_in_session=False,
            priority="critical",
        )
        notif = _notification(
            session=cn_session,
            priority="critical",
            now=now,
            quiet_hours={"enabled": False},
            window_delta=delta,
        )
        assert notif["recommended"] is True
        assert notif["policy"] == "push_now"

    def test_open_watch_no_delta_silent(self, cn_open_watch_session, now):
        """open_watch with no material change => archive_only."""
        delta = WindowDelta(
            session_id="cn_open_watch",
            market="cn",
            previous_run_id="run-1",
            current_run_id="run-2",
            material=False,
            changes=[],
            first_in_session=False,
            priority="normal",
        )
        notif = _notification(
            session=cn_open_watch_session,
            priority="normal",
            now=now,
            quiet_hours={"enabled": False},
            window_delta=delta,
        )
        assert notif["policy"] == "archive_only"
        assert notif["recommended"] is False

    # ═══════════════════════════════════════════════════════════════════════════
    # 4. Same-market previous window lookup
    # ═══════════════════════════════════════════════════════════════════════════

    def test_critical_overrides_unchanged_archive(self, cn_session, now):
        delta = WindowDelta(
            session_id=cn_session.id,
            market="cn",
            previous_run_id="r1",
            current_run_id="r2",
            material=False,
            changes=[],
            first_in_session=False,
        )
        result = _notification(
            session=cn_session,
            priority="critical",
            now=now,
            quiet_hours={"enabled": False},
            window_delta=delta,
        )
        assert result["policy"] == "push_now"
        assert result["recommended"] is True


class TestSameMarketPrevious:
    """Same-market previous window lookup."""

    def test_finds_same_market_session(self, tmp_path, now):
        """Find previous artifact for same market, excluding current session."""
        store = RunArtifactStore(tmp_path / "scheduled_runs")
        # Save a previous session in the same market
        previous = {
            "run_id": "20260715T060000Z_cn_open_watch",
            "session": "cn_open_watch",
            "market": "cn",
            "market_date": "2026-07-15",
            "generated_at": "2026-07-15T06:00:00+00:00",
            "risk_state": {"level": "normal", "transition": "initial", "candidate_level": None},
            "portfolio_decision": {
                "status": "approved",
                "approved_actions": [],
                "suppressed_actions": [],
                "unresolved_conflicts": [],
            },
            "position_reviews": [],
            "trigger_reviews": [],
            "action_cards": [],
            "session_summary": {"priority": "normal"},
        }
        store.latest_dir.mkdir(parents=True, exist_ok=True)
        (store.latest_dir / f"{previous['session']}.json").write_text(
            json.dumps(previous), encoding="utf-8"
        )
        found = store.find_previous_for_session("cn_pre_close", "cn", market_date="2026-07-15")
        assert found is not None
        assert found["session"] == "cn_open_watch"
        assert found["run_id"] == "20260715T060000Z_cn_open_watch"

    def test_returns_none_when_no_previous(self, tmp_path):
        """No previous artifact in same market => None."""
        store = RunArtifactStore(tmp_path / "scheduled_runs")
        found = store.find_previous_for_session("cn_pre_close", "cn", market_date="2026-07-15")
        assert found is None

    def test_prefers_prior_same_session(self, tmp_path, now):
        """A forced rerun compares against the prior same-session artifact."""
        store = RunArtifactStore(tmp_path / "scheduled_runs")
        previous = {
            "run_id": "20260715T060000Z_cn_pre_close",
            "session": "cn_pre_close",
            "market": "cn",
            "market_date": "2026-07-15",
            "generated_at": "2026-07-15T06:00:00+00:00",
            "risk_state": {"level": "normal", "transition": "initial", "candidate_level": None},
            "portfolio_decision": {
                "status": "approved",
                "approved_actions": [],
                "suppressed_actions": [],
                "unresolved_conflicts": [],
            },
            "position_reviews": [],
            "trigger_reviews": [],
            "action_cards": [],
            "session_summary": {"priority": "normal"},
        }
        store.latest_dir.mkdir(parents=True, exist_ok=True)
        (store.latest_dir / f"{previous['session']}.json").write_text(
            json.dumps(previous), encoding="utf-8"
        )
        latest = {
            "run_id": "20260715T060000Z_cn_open_watch",
            "session": "cn_open_watch",
            "market": "cn",
            "market_date": "2026-07-15",
            "generated_at": "2026-07-15T06:00:00+00:00",
            "risk_state": {"level": "normal", "transition": "initial", "candidate_level": None},
            "portfolio_decision": {
                "status": "approved",
                "approved_actions": [],
                "suppressed_actions": [],
                "unresolved_conflicts": [],
            },
            "position_reviews": [],
            "trigger_reviews": [],
            "action_cards": [],
            "session_summary": {"priority": "normal"},
        }
        (store.latest_dir / f"{latest['session']}.json").write_text(
            json.dumps(latest), encoding="utf-8"
        )
        found = store.find_previous_for_session("cn_pre_close", "cn", market_date="2026-07-15")
        assert found is not None
        assert found["session"] == "cn_pre_close"


# ═══════════════════════════════════════════════════════════════════════════
# 5. Integration: delta computed in run_occurrence before save
# ═══════════════════════════════════════════════════════════════════════════


class TestWindowDeltaIntegration:
    """Integration tests: delta computed before save."""

    def _config(self, tmp_path):
        return {
            "schema_version": 1,
            "user_timezone": "Asia/Shanghai",
            "artifact_dir": str(tmp_path / "scheduled_runs"),
            "default_duplicate_window_minutes": 90,
            "quiet_hours": {"enabled": False},
            "risk_state": {"state_path": str(tmp_path / "risk_state.json")},
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
                            "delta_silent_when_unchanged": True,
                        },
                    ],
                },
            },
        }

    def _context(self, *, fired=False):
        return {
            "schema_version": 12,
            "generated_at": "2026-07-15T06:35:02+00:00",
            "data_quality": {
                "asset_completeness": {"status": "ok"},
                "quotes": {"status": "ok"},
                "history_backfill": {"status": "ok"},
                "rotation": {"status": "ok"},
                "action_signals": {"status": "ok"},
            },
            "position_valuations": [],
            "recent_advice": [],
            "action_signals": {},
            "rotation": {},
            "market_state": {"risk_appetite": "stable"},
            "portfolio_mapping": {},
            "exposure_summary": {},
            "liquidity_summary": {},
            "advice_granularity": {},
            "intelligence_digest": {
                "intelligence_health": {
                    "status": "missing",
                    "age_minutes": None,
                    "risk_eligible": False,
                },
                "intelligence_coverage": {
                    "field": 0,
                    "directional": 0,
                    "padding": 0,
                    "exact": 0,
                    "proxy": 0,
                    "category": 0,
                },
                "top_clusters": [],
                "top_signals": [],
            },
            "engine_config": {},
        }

    def test_first_run_has_window_delta(self, tmp_path):
        """First run builds scheduled run with window_delta info."""
        config = self._config(tmp_path)
        from stocks.engine.scheduled_analysis import MarketSessionCalendar

        calendar = MarketSessionCalendar(config)
        occurrence = calendar.occurrence_for(
            "cn_pre_close", datetime.fromisoformat("2026-07-15T14:35:00+08:00")
        )
        run = build_scheduled_run(
            self._context(),
            occurrence=occurrence,
            generated_at=datetime.fromisoformat("2026-07-15T14:35:00+08:00"),
            config=config,
        )
        assert "window_delta" in run
        assert run["window_delta"]["material"] is True
        assert run["window_delta"]["first_in_session"] is True
        assert run["session_summary"]["priority"] in ("normal", "high", "critical")

    def test_consecutive_identical_runs_second_no_material(self, tmp_path):
        """Two consecutive runs with identical data: second has no material delta."""
        from stocks.engine.scheduled_analysis import MarketSessionCalendar

        config = self._config(tmp_path)
        calendar = MarketSessionCalendar(config)
        occ = calendar.occurrence_for(
            "cn_pre_close", datetime.fromisoformat("2026-07-15T14:35:00+08:00")
        )
        previous_run = build_scheduled_run(
            self._context(),
            occurrence=occ,
            generated_at=datetime.fromisoformat("2026-07-15T14:35:00+08:00"),
            config=config,
            previous_run=None,
        )
        second_run = build_scheduled_run(
            self._context(),
            occurrence=occ,
            generated_at=datetime.fromisoformat("2026-07-15T15:35:00+08:00"),
            config=config,
            previous_run=previous_run,
        )
        assert second_run.get("window_delta", {}).get("material") is False
        assert second_run.get("window_delta", {}).get("first_in_session") is False

    def test_trigger_change_restores_push(self, tmp_path):
        """New trigger makes material=True on second run."""
        from stocks.engine.scheduled_analysis import MarketSessionCalendar

        config = self._config(tmp_path)
        calendar = MarketSessionCalendar(config)
        occ = calendar.occurrence_for(
            "cn_pre_close", datetime.fromisoformat("2026-07-15T14:35:00+08:00")
        )
        ctx1 = self._context(fired=False)
        previous_run = build_scheduled_run(
            ctx1,
            occurrence=occ,
            generated_at=datetime.fromisoformat("2026-07-15T14:35:00+08:00"),
            config=config,
        )
        ctx2 = self._context(fired=True)
        ctx2["recent_advice"] = [
            {
                "id": "advice-2",
                "summary": "止损触发",
                "trigger_review": [
                    {"target": "a:588000", "status": "fired", "observed": {"pnl_pct": -15.0}}
                ],
            }
        ]
        second_run = build_scheduled_run(
            ctx2,
            occurrence=occ,
            generated_at=datetime.fromisoformat("2026-07-15T15:35:00+08:00"),
            config=config,
            previous_run=previous_run,
        )
        assert second_run.get("window_delta", {}).get("material") is True


# ═══════════════════════════════════════════════════════════════════════════
# 6. ScheduledSession accepts delta_silent_when_unchanged
# ═══════════════════════════════════════════════════════════════════════════


class TestScheduledSessionNewField:
    """ScheduledSession accepts new delta_silent_when_unchanged field."""

    def test_default_value_is_false(self):
        """Existing sessions without the field default to False."""
        s = ScheduledSession(
            id="cn_pre_close",
            market="cn",
            primary_market="cn",
            exchange_timezone="Asia/Shanghai",
            user_timezone="Asia/Shanghai",
            time="14:35",
            intent="pre_close_decision",
            push="normal",
            enabled=True,
            duplicate_window_minutes=90,
            holidays=frozenset(),
        )
        assert s.delta_silent_when_unchanged is False

    def test_explicit_true(self):
        """Session can set delta_silent_when_unchanged=True."""
        s = ScheduledSession(
            id="cn_open_watch",
            market="cn",
            primary_market="cn",
            exchange_timezone="Asia/Shanghai",
            user_timezone="Asia/Shanghai",
            time="10:00",
            intent="open_watch",
            push="normal",
            enabled=True,
            duplicate_window_minutes=90,
            holidays=frozenset(),
            delta_silent_when_unchanged=True,
        )
        assert s.delta_silent_when_unchanged is True

    def test_loaded_from_config_preserves_field(self, tmp_path):
        """Loading config from JSON preserves delta_silent_when_unchanged."""
        config_path = tmp_path / "scheduled_sessions.json"
        import json as _json

        config_data = {
            "schema_version": 1,
            "user_timezone": "Asia/Shanghai",
            "artifact_dir": ".local/scheduled_runs",
            "default_duplicate_window_minutes": 90,
            "markets": {
                "cn": {
                    "enabled": True,
                    "exchange_timezone": "Asia/Shanghai",
                    "holidays": [],
                    "sessions": [
                        {
                            "id": "cn_open_watch",
                            "time": "10:00",
                            "intent": "open_watch",
                            "push": "normal",
                            "delta_silent_when_unchanged": True,
                        },
                        {
                            "id": "cn_pre_close",
                            "time": "14:35",
                            "intent": "pre_close_decision",
                            "push": "normal",
                        },
                    ],
                },
            },
        }
        config_path.write_text(
            _json.dumps(config_data, indent=2),
            encoding="utf-8",
        )
        from stocks.engine.scheduled_analysis import MarketSessionCalendar

        calendar = MarketSessionCalendar(json.loads(config_path.read_text(encoding="utf-8")))
        open_watch = calendar.find_session("cn_open_watch")
        assert open_watch.delta_silent_when_unchanged is True
        pre_close = calendar.find_session("cn_pre_close")
        assert pre_close.delta_silent_when_unchanged is False

    def test_window_delta_fields(self):
        delta = WindowDelta(
            session_id="cn_pre_close",
            market="cn",
            previous_run_id="run-1",
            current_run_id="run-2",
            material=False,
            changes=[],
            first_in_session=False,
            priority="normal",
        )
        assert delta.session_id == "cn_pre_close"
        assert delta.market == "cn"
        assert delta.previous_run_id == "run-1"
        assert delta.current_run_id == "run-2"
        assert delta.material is False
        assert delta.changes == []
        assert delta.first_in_session is False
        assert delta.priority == "normal"

    def test_window_delta_to_dict(self):
        delta = WindowDelta(
            session_id="cn_pre_close",
            market="cn",
            previous_run_id=None,
            current_run_id="run-1",
            material=True,
            changes=[{"field": "risk_state.level", "old": None, "new": "normal"}],
            first_in_session=True,
            priority="normal",
        )
        d = delta.to_dict()
        assert d["session_id"] == "cn_pre_close"
        assert d["material"] is True
        assert d["first_in_session"] is True
        assert d["previous_run_id"] is None
        assert len(d["changes"]) == 1


# ═══════════════════════════════════════════════════════════════════════════
# 7. WindowDelta dataclass structure
# ═══════════════════════════════════════════════════════════════════════════
