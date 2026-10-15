"""Bounded replay checks; normal tests never fetch public prices."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from scripts import smoke_decision_review as smoke
from stocks.domain.models import Instrument


def _history(*, rising=True):
    prices = [10 * (1.003 if rising else 1.0) ** day for day in range(90)]
    return pd.DataFrame({
        "timestamp": pd.bdate_range("2026-05-06", periods=90, tz="UTC"),
        "price": prices, "high": [p * 1.001 for p in prices],
        "low": [p * 0.999 for p in prices], "volume_lot": [1000.0] * 90,
    })


def test_real_rule_recommendation_uses_only_cutoff_history():
    frame = _history()
    instrument = Instrument("518880", "518880", "a")
    now = frame.iloc[-1]["timestamp"].to_pydatetime()
    original = smoke._select_recommendation(
        frame, instrument, now=now, replay_days=20, sessions=3)
    prefix, cutoff, _, decision, position, cards, _, _ = original
    assert cutoff < now
    assert decision["approved_actions"][0]["signal"] == "take_profit"
    assert cards[0]["signal"] == "take_profit"
    assert position["scenario_origin"] == "simulated_portfolio"
    assert position["portfolio_weight"] is None
    changed_future = frame.copy()
    future = changed_future["timestamp"].dt.date > cutoff.date()
    assert future.any()
    changed_future.loc[future, "price"] = 0.001
    repeated = smoke._select_recommendation(
        changed_future, instrument, now=now, replay_days=20, sessions=3)
    assert repeated[1] == cutoff
    assert repeated[3] == decision
    assert prefix["timestamp"].max().date() == cutoff.date()


def test_no_natural_executable_action_fails_without_fabricating_one():
    frame = _history(rising=False)
    with pytest.raises(RuntimeError, match="No naturally executable rule recommendation"):
        smoke._select_recommendation(
            frame, Instrument("518880", "518880", "a"),
            now=datetime(2026, 10, 1, tzinfo=timezone.utc), replay_days=3, sessions=3)


def test_target_day_is_not_final_even_after_its_exchange_close():
    frame = _history()
    now = frame.iloc[-1]["timestamp"].to_pydatetime() + timedelta(hours=12)
    result = smoke._select_recommendation(
        frame, Instrument("518880", "518880", "a"), now=now, replay_days=20, sessions=3)
    target = smoke.target_after(result[1], "a", 3, basis="sessions")
    assert target["session"] < now.date().isoformat()


def test_entry_quote_must_match_actual_rule_price():
    with pytest.raises(ValueError, match="differs from the raw entry quote"):
        smoke._fixture_position(
            _history(), Instrument("518880", "518880", "a"), quote={"price": 0.01})


def test_manual_cashflow_is_signed_cash_not_profit():
    fills = [
        {"side": "buy", "quantity": 3, "price": 1.1, "fee_amount": 0.1},
        {"side": "sell", "quantity": 2, "price": 2.2, "fee_amount": 0.2},
    ]
    assert smoke._manual_cashflow(fills) == 0.8


def test_confirmed_mcp_pipeline_keeps_simulation_distinct_and_fills_append_only(tmp_path, monkeypatch):
    from stocks.engine.target_observation import observe

    frame = _history()
    instrument = Instrument("518880", "518880", "a")
    now = datetime.now(timezone.utc)
    prefix, cutoff, entry_target, _, _, _, _, _ = smoke._select_recommendation(
        frame, instrument, now=now, replay_days=20, sessions=3)
    price = float(prefix.iloc[-1]["price"])

    def fixture_observe(symbol, target, *, clock=None):
        # Exercise the real parsing, calendar validation and evidence hashing.
        # Only the network payload is synthetic, never a financial record.
        day = target["session"]
        close = price if day == entry_target["session"] else price * 1.01
        payload = {"data": {"sh518880": {"day": [[day, str(close), str(close),
                                                   str(close), str(close), "1000"]]}}}
        return observe(symbol, target, clock=clock, fetch=lambda _: payload)

    monkeypatch.setattr(smoke, "observe", fixture_observe)
    entry = fixture_observe("a:518880", entry_target, clock=now)
    quote = smoke.entry_quote_from_observation(entry, replay_as_of=cutoff)
    decision, position, _, run_id = smoke._produce_decision(prefix, instrument, cutoff, quote=quote)
    result = smoke._run_smoke(
        SimpleNamespace(sessions=3), tmp_path, decision, position, quote, cutoff, run_id, now)
    assert result["status"] == "ok"
    assert result["real_user_actions"] is False
    assert result["real_broker_executions"] is False
    assert result["unconfirmed_writes"] == 0
    assert result["user_decision_count"] == 2
    assert result["fill_count"] == 2
    assert result["idempotent"] is True
