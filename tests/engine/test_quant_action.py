"""Task 6: QuantActionEngine parameter semantics."""
from __future__ import annotations

import pytest

from stocks.engine.quant_action import QuantActionEngine


def _review(config: dict, *, price: float, ma20: float = 100.0):
    engine = QuantActionEngine(
        {"ma_20": ma20, "macd": {"hist": -1.0}, "rsi_14": 50.0},
        config,
    )
    return engine.review_position(
        position_id="p", price=price, cost=100.0, pnl_pct=0.0,
        one_day_change_pct=0.0, current_weight_pct=1.0, quantity=1.0,
    )


class TestTrendBreakExtraDeviation:
    def test_default_cutoff_triggers_below_995(self):
        assert _review({}, price=99.4).signal == "reduce"

    def test_extra_one_pct_requires_price_below_985(self):
        assert _review({"trend_break_extra_deviation_pct": 1.0}, price=99.0).signal == "hold"
        assert _review({"trend_break_extra_deviation_pct": 1.0}, price=98.4).signal == "reduce"

    def test_old_three_day_behavior_equals_new_one_pct(self):
        old_cutoff = 0.995 - (3 - 1) * 0.005
        new_cutoff = 0.995 - 1.0 / 100
        assert old_cutoff == pytest.approx(new_cutoff)

    def test_fact_never_claims_consecutive_days(self):
        review = _review({"trend_break_extra_deviation_pct": 1.0}, price=98.4)
        text = " ".join(review.facts)
        assert "额外偏离 1.0%" in text
        assert "连续" not in text


class TestMa20PullbackAddRatios:
    @staticmethod
    def review(price: float):
        engine = QuantActionEngine(
            {"ma_20": 100.0, "macd": {"hist": 1.0}, "rsi_14": 50.0},
            {"ma20_pullback_add_ratios": [0.02, 0.03, 0.05]},
        )
        return engine.review_position(
            position_id="p", price=price, cost=100.0, pnl_pct=0.0,
            one_day_change_pct=0.0, current_weight_pct=1.0, quantity=1.0,
        )

    def test_first_tier_at_half_pct_below_ma20(self):
        assert self.review(99.5).ratio == pytest.approx(-0.02)

    def test_second_tier_below_one_pct(self):
        assert self.review(98.8).ratio == pytest.approx(-0.03)

    def test_third_tier_below_two_pct(self):
        assert self.review(97.5).ratio == pytest.approx(-0.05)
