"""建议触发器校验与核对测试。"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from stocks.domain.models import (
    AdviceRecord,
    Classification,
    CostBasis,
    Holding,
    Instrument,
    Liquidity,
    Position,
    ValuationInput,
)
from stocks.engine.advice_review import attach_advice_performance
from stocks.engine.history_cache import HistoryCache


def _history_frame(instrument: Instrument, prices: list[float]) -> pd.DataFrame:
    rows = []
    for index, price in enumerate(prices):
        rows.append(
            {
                "timestamp": datetime(2026, 7, 1 + index, 20, 0, tzinfo=timezone.utc),
                "code": instrument.code,
                "name": instrument.name,
                "market": instrument.market,
                "price": price,
                "open_price": price,
                "high": price + 0.2,
                "low": price - 0.2,
                "prev_close": price,
                "volume_lot": 1,
            }
        )
    return pd.DataFrame(rows)


def _advice_with_trigger(trigger: dict) -> dict:
    return {
        "created_at": "2026-07-02T09:00:00+00:00",
        "instruments": [{"market": "us", "code": "QCOM", "name": "高通"}],
        "direction": {"us:QCOM": "watch"},
        "rationale_summary": "等待止跌信号。",
        "based_on": ["quotes", "indicators"],
        "boundary": [{"type": "inference", "text": "等待止跌信号"}],
        "triggers": [trigger],
    }


class TestAdviceRecordTriggerValidation:
    def _payload(self, trigger: dict) -> dict:
        return {
            "instruments": [{"market": "us", "code": "QCOM", "name": "高通"}],
            "direction": {"us:QCOM": "watch"},
            "rationale_summary": "测试",
            "based_on": ["quotes"],
            "boundary": [{"type": "inference", "text": "测试"}],
            "triggers": [trigger],
        }

    def test_valid_trigger_roundtrip(self):
        record = AdviceRecord.create(
            **self._payload(
                {
                    "instrument": "us:QCOM",
                    "type": "price_above",
                    "level": 150.5,
                    "action": "收盘站回 5 日线上方则补回一半仓位",
                    "invalidation": "再创阶段新低则本条作废",
                }
            )
        )
        data = record.to_dict()
        assert data["triggers"][0]["type"] == "price_above"
        restored = AdviceRecord.from_dict(data)
        assert restored.triggers == record.triggers

    def test_legacy_record_without_triggers_still_loads(self):
        record = AdviceRecord.from_dict(
            {
                "created_at": "2026-07-01T00:00:00+00:00",
                "instruments": [{"market": "us", "code": "QCOM", "name": "高通"}],
                "direction": {"us:QCOM": "hold"},
                "rationale_summary": "旧记录",
                "based_on": ["quotes"],
                "boundary": [{"type": "fact", "text": "旧记录"}],
            }
        )
        assert record.triggers == []
        assert record.to_dict()["triggers"] == []

    @pytest.mark.parametrize(
        "trigger",
        [
            {"instrument": "QCOM", "type": "price_above", "level": 1, "action": "x"},
            {"instrument": "us:QCOM", "type": "magic", "level": 1, "action": "x"},
            {"instrument": "us:QCOM", "type": "price_above", "level": "high", "action": "x"},
            {"instrument": "us:QCOM", "type": "price_above", "level": 1, "action": ""},
            {"instrument": "us:QCOM", "type": "price_above", "level": 1, "action": "x", "extra": 1},
        ],
    )
    def test_invalid_triggers_rejected(self, trigger):
        with pytest.raises(ValueError):
            AdviceRecord.create(**self._payload(trigger))


class TestTriggerReview:
    async def test_price_above_fires_on_close(self, tmp_path):
        instrument = Instrument(code="QCOM", name="高通", market="us", category="tech")
        cache = HistoryCache(base_dir=str(tmp_path), ttl=86400)
        await cache.warm(instrument, _history_frame(instrument, [140, 145, 151, 148]))

        records = await attach_advice_performance(
            [
                _advice_with_trigger(
                    {
                        "instrument": "us:QCOM",
                        "type": "price_above",
                        "level": 150.0,
                        "action": "补回一半仓位",
                    }
                )
            ],
            watchlist=[instrument],
            history_cache=cache,
        )

        review = records[0]["trigger_review"][0]
        assert review["status"] == "fired"
        assert review["observed"]["max_price"] == 151.0
        assert review["observed"]["basis"] == "close"

    async def test_price_below_not_fired(self, tmp_path):
        instrument = Instrument(code="QCOM", name="高通", market="us", category="tech")
        cache = HistoryCache(base_dir=str(tmp_path), ttl=86400)
        await cache.warm(instrument, _history_frame(instrument, [140, 145, 151, 148]))

        records = await attach_advice_performance(
            [
                _advice_with_trigger(
                    {
                        "instrument": "us:QCOM",
                        "type": "price_below",
                        "level": 130.0,
                        "action": "减半仓",
                    }
                )
            ],
            watchlist=[instrument],
            history_cache=cache,
        )

        review = records[0]["trigger_review"][0]
        assert review["status"] == "not_fired"
        # 建议日(7/2)之前的 140 不参与核对,期间最低为建议日首根 145
        assert review["observed"]["min_price"] == 145.0

    async def test_price_above_requires_cross_not_merely_already_above(self, tmp_path):
        instrument = Instrument(code="QCOM", name="高通", market="us", category="tech")
        cache = HistoryCache(base_dir=str(tmp_path), ttl=86400)
        await cache.warm(instrument, _history_frame(instrument, [140, 151, 152, 153]))

        records = await attach_advice_performance(
            [
                _advice_with_trigger(
                    {
                        "instrument": "us:QCOM",
                        "type": "price_above",
                        "level": 150.0,
                        "action": "补回一半仓位",
                    }
                )
            ],
            watchlist=[instrument],
            history_cache=cache,
        )

        assert records[0]["trigger_review"][0]["status"] == "not_fired"

    async def test_pct_change_trigger(self, tmp_path):
        instrument = Instrument(code="QCOM", name="高通", market="us", category="tech")
        cache = HistoryCache(base_dir=str(tmp_path), ttl=86400)
        # 建议日(7/2)首根 145 → 最新 148: +2.07%
        await cache.warm(instrument, _history_frame(instrument, [140, 145, 151, 148]))

        records = await attach_advice_performance(
            [
                _advice_with_trigger(
                    {
                        "instrument": "us:QCOM",
                        "type": "pct_change_above",
                        "level": 2.0,
                        "action": "确认反弹后加仓",
                    }
                )
            ],
            watchlist=[instrument],
            history_cache=cache,
        )

        review = records[0]["trigger_review"][0]
        assert review["status"] == "fired"
        assert review["observed"]["pct_change"] == pytest.approx(2.0690, abs=1e-3)

    async def test_pnl_pct_trigger_uses_position_cost_basis(self, tmp_path):
        instrument = Instrument(code="QCOM", name="高通", market="us", category="tech")
        cache = HistoryCache(base_dir=str(tmp_path), ttl=86400)
        # 建议日(7/2)后最高 130，相对成本 100 浮盈 30%，触发 20% 止盈线。
        await cache.warm(instrument, _history_frame(instrument, [95, 110, 130, 118]))
        position = Position(
            position_id="ibkr_qcom",
            account_id="ibkr",
            display_name="QCOM",
            currency="USD",
            classification=Classification(asset_class="equity", product_type="stock"),
            instrument={"instrument_key": "us:QCOM"},
            holding=Holding(
                quantity=10,
                unit="share",
                cost_basis=CostBasis(unit_cost=100, currency="USD"),
            ),
            valuation_input=ValuationInput(method="market_quote"),
            liquidity=Liquidity(tradable=True, rebalance_eligible=True, tier="t1"),
        )

        records = await attach_advice_performance(
            [
                _advice_with_trigger(
                    {
                        "instrument": "us:QCOM",
                        "type": "pnl_pct_above",
                        "level": 20.0,
                        "action": "浮盈超过20%减半",
                    }
                )
            ],
            watchlist=[instrument],
            history_cache=cache,
            positions=[position],
        )

        review = records[0]["trigger_review"][0]
        assert review["status"] == "fired"
        assert review["observed"]["cost_basis_unit"] == 100.0
        assert review["observed"]["pnl_pct"] == 18.0
        assert review["observed"]["max_pnl_pct"] == 30.0

    async def test_unknown_instrument_is_no_data(self, tmp_path):
        cache = HistoryCache(base_dir=str(tmp_path), ttl=86400)
        records = await attach_advice_performance(
            [
                _advice_with_trigger(
                    {
                        "instrument": "us:NVDA",
                        "type": "price_above",
                        "level": 100.0,
                        "action": "x",
                    }
                )
            ],
            watchlist=[Instrument(code="QCOM", name="高通", market="us")],
            history_cache=cache,
        )
        review = records[0]["trigger_review"][0]
        assert review["status"] == "no_data"
        assert review["reason"] == "instrument_not_in_watchlist"

    async def test_missing_history_is_no_data(self, tmp_path):
        instrument = Instrument(code="QCOM", name="高通", market="us")
        cache = HistoryCache(base_dir=str(tmp_path), ttl=86400)
        records = await attach_advice_performance(
            [
                _advice_with_trigger(
                    {
                        "instrument": "us:QCOM",
                        "type": "price_above",
                        "level": 100.0,
                        "action": "x",
                    }
                )
            ],
            watchlist=[instrument],
            history_cache=cache,
        )
        review = records[0]["trigger_review"][0]
        assert review["status"] == "no_data"
        assert review["reason"] == "missing_history"

    async def test_advice_without_triggers_gets_empty_review(self, tmp_path):
        instrument = Instrument(code="QCOM", name="高通", market="us")
        cache = HistoryCache(base_dir=str(tmp_path), ttl=86400)
        advice = _advice_with_trigger(
            {"instrument": "us:QCOM", "type": "price_above", "level": 1, "action": "x"}
        )
        advice.pop("triggers")
        records = await attach_advice_performance(
            [advice], watchlist=[instrument], history_cache=cache
        )
        assert records[0]["trigger_review"] == []
