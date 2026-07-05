"""建议闭环端到端守门测试。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pandas as pd

from stocks.domain.models import FinancialAsset, Instrument
from stocks.engine import StocksEngine
from tests.engine.test_engine import MINIMAL_CONFIG


async def test_confirmed_advice_loop_injects_review_and_uses_real_prompt_amounts(
    tmp_path,
):
    config = deepcopy(MINIMAL_CONFIG)
    config["paths"]["local_data_dir"] = str(tmp_path / "local")
    config["macro"]["enabled"] = False
    with patch("stocks.engine.load_engine_config", return_value=config):
        engine = StocksEngine()

    instrument = Instrument(code="000001", name="平安银行", market="a")
    engine._assets = [
        FinancialAsset(name="股票基金", platform="支付宝", amount=50000, asset_type="equity"),
        FinancialAsset(name="余额宝", platform="支付宝", amount=30000, asset_type="cash"),
    ]
    engine._constraints = {
        "权益": {"min": 0.4, "max": 0.7},
        "现金": {"min": 0.2, "max": 0.5},
    }
    engine._profile = {"risk_tolerance": "moderate"}
    engine._watchlist = [instrument]
    engine._history_warmed = True

    first_close = (
        datetime.now(timezone.utc).replace(hour=20, minute=0, second=0, microsecond=0)
        + timedelta(days=1)
    )
    await engine.history_cache.warm(
        instrument,
        pd.DataFrame([
            {
                "timestamp": first_close,
                "code": instrument.code,
                "name": instrument.name,
                "market": instrument.market,
                "price": 10.0,
                "open_price": 9.9,
                "high": 10.2,
                "low": 9.8,
                "prev_close": 9.8,
                "volume_lot": 1,
            },
            {
                "timestamp": first_close + timedelta(days=1),
                "code": instrument.code,
                "name": instrument.name,
                "market": instrument.market,
                "price": 11.0,
                "open_price": 10.9,
                "high": 11.2,
                "low": 10.8,
                "prev_close": 10.0,
                "volume_lot": 1,
            },
        ]),
    )

    first = await engine.build_context(include_news=False, include_quotes=False)
    assert first.recent_advice == []

    saved = engine.save_advice({
        "instruments": [{"market": "a", "code": "000001", "name": "平安银行"}],
        "direction": {"a:000001": "watch"},
        "rationale_summary": "权益与现金结构需要继续观察。",
        "based_on": ["portfolio", "profile", "quotes"],
        "boundary": [
            {"type": "fact", "text": "现金与权益是主要资产层"},
            {"type": "inference", "text": "平安银行适合继续观察"},
        ],
    })

    second = await engine.build_context(include_news=False, include_quotes=False)

    assert saved["created_at"]
    assert len(second.recent_advice) == 1
    performance = second.recent_advice[0]["performance"][0]
    assert performance["status"] == "ok"
    assert performance["direction"] == "watch"
    assert performance["pct_change"] == 10.0
    assert "【复盘】" in second.raw_prompt_input
    assert "缺失: 上期建议未保存结构化 actions" in second.raw_prompt_input
    assert "50,000.00 CNY" in second.raw_prompt_input
    assert "30,000.00 CNY" in second.raw_prompt_input
    assert "占比" in second.raw_prompt_input
