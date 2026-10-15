"""The live smoke must use actual rule outputs without lookahead or fabrication."""

from datetime import timedelta

import pandas as pd
import pytest

from scripts import smoke_evaluation_pipeline as smoke
from stocks.domain.models import Instrument


def _history():
    prices = [100.0 * 1.02**day for day in range(80)]
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=80, tz="UTC"),
            "price": prices,
            "high": [price + 0.5 for price in prices],
            "low": [price - 0.5 for price in prices],
            "volume_lot": [1000.0] * 80,
        }
    )


def test_replay_uses_real_rules_and_only_prices_available_at_cutoff():
    history = _history()
    now = history.iloc[-1]["timestamp"].to_pydatetime() + timedelta(minutes=30)
    instrument = Instrument("BTCUSDT", "BTCUSDT", "crypto")
    candidate, cutoff, prefix, rotation, actions, _ = smoke._select_candidate(
        history, instrument, now=now, replay_days=5, hours=1
    )
    assert cutoff < history.iloc[-1]["timestamp"]
    assert prefix["timestamp"].max() == cutoff
    assert cutoff + timedelta(hours=1, minutes=1) <= now
    assert candidate in actions["items"]
    actual = smoke.compute_action_signals(
        {"crypto:BTCUSDT": prefix}, {"crypto:BTCUSDT": instrument}, rotation
    )
    assert candidate in actual["items"]
    assert candidate["signal"] in smoke.REPLAYABLE_ACTIONS


def test_no_eligible_rule_output_fails_without_inventing_a_candidate(monkeypatch):
    history = _history()
    instrument = Instrument("BTCUSDT", "BTCUSDT", "crypto")
    monkeypatch.setattr(
        smoke,
        "compute_action_signals",
        lambda *args: {"items": [{"symbol": "crypto:BTCUSDT", "signal": "neutral_hold"}]},
    )
    now = history.iloc[-1]["timestamp"].to_pydatetime() + timedelta(days=1)
    with pytest.raises(RuntimeError, match="No eligible historical rule candidate"):
        smoke._select_candidate(history, instrument, now=now, replay_days=2, hours=1)
