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
    # 参数语义测试：显式控制 cutoff/extra，不依赖 engine.yaml 当前值
    # （yaml 权威值 2026-08-22 起为 cutoff 0.99 + extra 1.0）
    _CUTOFF = {"trend_ma20_break_cutoff": 0.995}

    def test_default_cutoff_triggers_below_995(self):
        assert _review({**self._CUTOFF, "trend_break_extra_deviation_pct": 0.0}, price=99.4).signal == "reduce"

    def test_extra_one_pct_requires_price_below_985(self):
        cfg = {**self._CUTOFF, "trend_break_extra_deviation_pct": 1.0}
        assert _review(cfg, price=99.0).signal == "hold"
        assert _review(cfg, price=98.4).signal == "reduce"

    def test_old_three_day_behavior_equals_new_one_pct(self):
        old_cutoff = 0.995 - (3 - 1) * 0.005
        new_cutoff = 0.995 - 1.0 / 100
        assert old_cutoff == pytest.approx(new_cutoff)

    def test_fact_never_claims_consecutive_days(self):
        review = _review(
            {"trend_ma20_break_cutoff": 0.995, "trend_break_extra_deviation_pct": 1.0},
            price=98.4,
        )
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


class TestHighConvictionAdd:
    """变现侧：高置信(技术面确定性高) → 加仓比例与上限上调。"""

    @staticmethod
    def review(price: float, r20: float = 3.0):
        engine = QuantActionEngine(
            {"ma_20": 100.0, "macd": {"hist": 1.0}, "rsi_14": 50.0, "r20": r20},
            # 显式档位：参数语义测试不依赖 engine.yaml 当前值
            {"ma20_pullback_add_ratios": [0.02, 0.03, 0.05]},
        )
        return engine.review_position(
            position_id="p", price=price, cost=100.0, pnl_pct=0.0,
            one_day_change_pct=0.0, current_weight_pct=1.0, quantity=1.0,
        )

    def test_deep_pullback_high_conviction_adds_5pct_and_15pct_limit(self):
        r = self.review(97.5)  # deviation 2.5% → evidence 0.725 ≥ 0.7
        assert r.signal == "add"
        assert r.ratio == pytest.approx(-0.05)
        assert r.position_limit_pct == pytest.approx(15.0)

    def test_shallow_pullback_regular_add_2pct_5pct_limit(self):
        r = self.review(99.5, r20=1.0)  # deviation 0.5% → evidence 0.425, 趋势未确认
        assert r.signal == "add"
        assert r.ratio == pytest.approx(-0.02)
        assert r.position_limit_pct == pytest.approx(5.0)

    def test_trend_confirmed_add_uses_10pct_limit(self):
        r = self.review(101.0)  # price > ma20, r20=3 → 趋势确认 → 上限 10%
        assert r.signal == "add"
        assert r.ratio == pytest.approx(-0.02)
        assert r.position_limit_pct == pytest.approx(10.0)

    def test_take_profit_does_not_trigger_high_conviction_limit(self):
        engine = QuantActionEngine(
            {"ma_20": 100.0, "macd": {"hist": 1.0}, "rsi_14": 50.0, "r20": 3.0},
            {},
        )
        r = engine.review_position(
            position_id="p", price=120.0, cost=100.0, pnl_pct=40.0,
            one_day_change_pct=0.0, current_weight_pct=1.0, quantity=1.0,
        )
        assert r.signal == "take_profit"
        assert r.position_limit_pct == pytest.approx(10.0)  # 止盈不触发高置信15%,走趋势确认10%
