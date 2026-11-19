from datetime import datetime, timedelta, timezone

from stocks.engine.disaster_alerts import (
    apply_delivery_state,
    evaluate_snapshot,
    render_alerts,
)


def bars(count=60, close=100.0):
    return [
        {
            "close": close,
            "high": close + 1,
            "low": close - 1,
            "volume": 1_000_000,
        }
        for _ in range(count)
    ]


def test_single_day_crash_returns_disaster_with_evidence():
    snapshot = {
        "net_liquidation": 10_000,
        "positions": [{
            "symbol": "TSLA", "quantity": 10, "avg_cost": 110,
            "price": 80, "prev_close": 100, "bars": bars(), "delayed": False,
        }],
    }

    alerts = evaluate_snapshot(snapshot)

    disaster = next(alert for alert in alerts if alert["key"] == "position:TSLA")
    assert disaster["severity"] == "disaster"
    assert "single_day_crash" in disaster["reasons"]
    assert disaster["metrics"]["day_pct"] == -20.0
    assert disaster["metrics"]["atr_pct"] == 2.0


def test_normal_pullback_only_warns():
    snapshot = {
        "net_liquidation": 10_000,
        "positions": [{
            "symbol": "GOOG", "quantity": 1, "avg_cost": 100,
            "price": 94, "prev_close": 100, "bars": bars(), "delayed": False,
        }],
    }

    alerts = evaluate_snapshot(snapshot)

    position = next(alert for alert in alerts if alert["key"] == "position:GOOG")
    assert position["severity"] == "warning"
    assert "single_day_drop" in position["reasons"]
    assert "single_day_crash" not in position["reasons"]


def test_technical_warnings_disabled_suppresses_ma_and_unrealized_noise():
    # Close bars at 200 so price=175 is well below MA20/MA50 and down -12% vs avg_cost=200,
    # yet today is flat (-0.5%) and not hitting any sentinel.
    snapshot = {
        "net_liquidation": 10_000,
        "positions": [{
            "symbol": "NVDA", "quantity": 10, "avg_cost": 200,
            "price": 175, "prev_close": 176, "bars": bars(close=178.0), "delayed": True,
        }],
    }

    noisy = evaluate_snapshot(snapshot, {"technical_warnings_enabled": True})
    assert any(a["key"] == "position:NVDA" and "below_ma20" in a["reasons"] for a in noisy)

    quiet = evaluate_snapshot(snapshot, {"technical_warnings_enabled": False})
    assert quiet == []


def test_price_sentinels_trigger_and_escalate_cleanly():
    cfg = {
        "technical_warnings_enabled": False,
        "sentinels": {
            "NVDA": {
                "stop_1r": 148.80,
                "hard_stop": 131.55,
                "entry_zone_max": 145.00,
                "take_profit": 215.00,
                "note": "Type A/Cycle rule",
            },
        },
    }

    # 1) Hit 1R stop (still above hard_stop & entry_zone)
    snap_1r = {
        "net_liquidation": 10_000,
        "positions": [{
            "symbol": "NVDA", "quantity": 10, "avg_cost": 180,
            "price": 148.50, "prev_close": 150.0, "bars": bars(close=180.0), "delayed": True,
        }],
    }
    a1 = evaluate_snapshot(snap_1r, cfg)
    assert len(a1) == 1
    assert a1[0]["severity"] == "warning"
    assert "sentinel_stop_1r" in a1[0]["reasons"]
    assert "sentinel_hard_stop" not in a1[0]["reasons"]
    assert "148.80" in render_alerts(a1)

    # 2) Enter left-side entry zone without breaking hard_stop
    snap_entry = {
        "net_liquidation": 10_000,
        "positions": [{
            "symbol": "NVDA", "quantity": 10, "avg_cost": 180,
            "price": 144.00, "prev_close": 146.0, "bars": bars(close=180.0), "delayed": False,
        }],
    }
    a2 = evaluate_snapshot(snap_entry, cfg)
    assert "sentinel_entry_zone" in a2[0]["reasons"]

    # 3) Break hard_stop -> critical, entry_zone invalidated
    snap_hard = {
        "net_liquidation": 10_000,
        "positions": [{
            "symbol": "NVDA", "quantity": 10, "avg_cost": 180,
            "price": 131.00, "prev_close": 135.0, "bars": bars(close=136.0), "delayed": False,
        }],
    }
    a3 = evaluate_snapshot(snap_hard, cfg)
    assert a3[0]["severity"] == "critical"
    assert a3[0]["band"] >= 2
    assert "sentinel_hard_stop" in a3[0]["reasons"]
    assert "sentinel_entry_zone" not in a3[0]["reasons"]
    rendered = render_alerts(a3)
    assert "关键风险告警" in rendered
    assert "131.55" in rendered

    # 4) Reach take profit target -> info alert with sentinel heading
    snap_tp = {
        "net_liquidation": 10_000,
        "positions": [{
            "symbol": "NVDA", "quantity": 10, "avg_cost": 180,
            "price": 216.00, "prev_close": 210.0, "bars": bars(close=180.0), "delayed": False,
        }],
    }
    a4 = evaluate_snapshot(snap_tp, cfg)
    assert a4[0]["severity"] == "info"
    assert a4[0]["reasons"] == ["sentinel_take_profit"]
    assert "哨兵价位到达提醒" in render_alerts(a4)


def test_account_loss_uses_previous_close_and_net_liquidation():
    snapshot = {
        "net_liquidation": 9_500,
        "positions": [{
            "symbol": "C", "quantity": 10, "avg_cost": 100,
            "price": 50, "prev_close": 100, "bars": bars(), "delayed": False,
        }],
    }

    alerts = evaluate_snapshot(snapshot)

    account = next(alert for alert in alerts if alert["key"] == "account")
    assert account["severity"] == "disaster"
    assert account["metrics"]["estimated_day_pnl"] == -500.0
    assert account["metrics"]["estimated_day_pct"] == -5.0


def test_delayed_quote_is_labeled_and_price_rules_still_run():
    snapshot = {
        "net_liquidation": 10_000,
        "positions": [{
            "symbol": "ONDS", "quantity": 10, "avg_cost": 100,
            "price": 50, "prev_close": 100, "bars": bars(), "delayed": True,
        }],
    }

    alerts = evaluate_snapshot(snapshot)

    position = next(alert for alert in alerts if alert["key"] == "position:ONDS")
    assert position["severity"] == "disaster"
    assert "single_day_crash" in position["reasons"]
    assert "delayed_quote" in position["reasons"]


def test_delivery_state_deduplicates_but_escalates_loss_bands():
    alerts = [{
        "key": "position:TSLA", "severity": "warning", "band": 1,
        "title": "TSLA", "reasons": ["single_day_drop"], "metrics": {},
    }]
    now = datetime(2026, 11, 6, tzinfo=timezone.utc)

    first, state = apply_delivery_state(alerts, {}, now)
    second, state = apply_delivery_state(alerts, state, now)
    escalated = [dict(alerts[0], band=2, severity="critical")]
    third, state = apply_delivery_state(escalated, state, now)

    assert len(first) == 1
    assert second == []
    assert len(third) == 1
    assert state["alerts"]["position:TSLA"]["band"] == 2


def test_cooldown_suppresses_flapping_warning_but_allows_escalation_and_expiry():
    t0 = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
    warn = [{
        "key": "position:NVDA", "severity": "warning", "band": 1,
        "title": "NVDA 哨兵价位告警", "reasons": ["delayed_quote", "sentinel_stop_1r"], "metrics": {},
    }]
    first, state = apply_delivery_state(warn, {}, t0, cooldown_minutes=120)
    assert len(first) == 1

    # Resolves 5 min later, then re-triggers 10 min later -> suppressed inside 120m cooldown
    _, state = apply_delivery_state([], state, t0 + timedelta(minutes=5), cooldown_minutes=120)
    flap, state = apply_delivery_state(warn, state, t0 + timedelta(minutes=10), cooldown_minutes=120)
    assert flap == []

    # Escalation to hard_stop breaks through cooldown immediately
    hard = [{
        "key": "position:NVDA", "severity": "critical", "band": 2,
        "title": "NVDA 触发硬止损 / 证伪线", "reasons": ["delayed_quote", "sentinel_hard_stop"], "metrics": {},
    }]
    escalated, state = apply_delivery_state(hard, state, t0 + timedelta(minutes=15), cooldown_minutes=120)
    assert len(escalated) == 1

    # After cooldown expires (121 min later), cleared warning can re-arm
    _, state = apply_delivery_state([], state, t0 + timedelta(minutes=120), cooldown_minutes=120)
    rearmed, _ = apply_delivery_state(warn, state, t0 + timedelta(minutes=125), cooldown_minutes=120)
    assert len(rearmed) == 1


def test_render_alerts_contains_reason_and_timestamp_fields():
    alerts = [{
        "key": "position:TSLA", "severity": "disaster", "band": 4,
        "title": "TSLA 灾难风险", "reasons": ["single_day_crash"],
        "metrics": {"day_pct": -20.0, "price": 80.0, "data_as_of": "2026-11-06T14:00:00+00:00"},
    }]

    rendered = render_alerts(alerts)

    assert "TSLA" in rendered
    assert "single_day_crash" in rendered
    assert "-20.00%" in rendered
    assert "2026-11-06T14:00:00+00:00" in rendered


def test_delivery_state_rearms_after_condition_resolves():
    alerts = [{
        "key": "position:TSLA", "severity": "warning", "band": 1,
        "title": "TSLA", "reasons": ["single_day_drop"], "metrics": {},
    }]
    now = datetime(2026, 11, 6, tzinfo=timezone.utc)

    _, state = apply_delivery_state(alerts, {}, now)
    _, state = apply_delivery_state([], state, now)
    recurring, _ = apply_delivery_state(alerts, state, now)

    assert len(recurring) == 1


def test_system_failure_is_an_explicit_deduplicatable_alert():
    alerts = evaluate_snapshot({"positions": [], "system_errors": [{"message": "EOF"}]})

    assert len(alerts) == 1
    assert alerts[0]["key"] == "system:ibkr"
    assert alerts[0]["severity"] == "critical"
    assert alerts[0]["reasons"] == ["provider_failure"]


def test_delayed_quote_alone_does_not_create_position_alert():
    snapshot = {
        "net_liquidation": 10_000,
        "positions": [{
            "symbol": "TTWO", "quantity": 1, "avg_cost": 100,
            "price": 101, "prev_close": 100, "bars": bars(), "delayed": True,
        }],
    }

    alerts = evaluate_snapshot(snapshot)

    assert not any(alert["key"] == "position:TTWO" for alert in alerts)


def test_reason_rearms_when_it_recurs_during_another_active_alert():
    now = datetime(2026, 11, 6, tzinfo=timezone.utc)
    base = {"key": "position:TSLA", "severity": "warning", "band": 1, "title": "TSLA", "metrics": {}}
    initial = [dict(base, reasons=["single_day_drop", "below_ma20"])]
    remaining = [dict(base, reasons=["single_day_drop"])]

    _, state = apply_delivery_state(initial, {}, now)
    _, state = apply_delivery_state(remaining, state, now)
    recurring, _ = apply_delivery_state(initial, state, now)

    assert len(recurring) == 1
