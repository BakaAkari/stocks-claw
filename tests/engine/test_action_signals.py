"""引擎动作信号测试。"""

from __future__ import annotations

import json

import pandas as pd

from stocks.domain.models import Instrument, UpcomingEvent
from stocks.engine.action_signals import compute_action_signals
from stocks.engine.rotation import compute_rotation


def _frame(prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": [
                ts.isoformat()
                for ts in pd.date_range(
                    "2026-05-01", periods=len(prices), freq="D", tz="UTC"
                )
            ],
            "price": prices,
            "volume_lot": [100.0] * len(prices),
        }
    )


def _run(prices_by_key: dict[str, list[float]], instruments: dict, events=None, scan_keys=None):
    frames = {key: _frame(prices) for key, prices in prices_by_key.items()}
    rotation = compute_rotation(frames, instruments, scan_keys or set())
    return compute_action_signals(
        frames, instruments, rotation, upcoming_events=events, scan_keys=scan_keys or set()
    )


def _inst(code: str, pool: str = None) -> Instrument:
    return Instrument(code=code, name=code, market="us", category="tech", pool=pool)


def _by_symbol(result: dict) -> dict:
    return {item["symbol"]: item for item in result["items"]}


class TestSignalRules:
    def test_uptrend_not_hot_is_accumulate(self):
        # 带回撤的净上升趋势(+1.0/-0.6 交替):强度有效且 RSI 不过热
        prices = [100.0]
        for i in range(39):
            prices.append(prices[-1] + (1.0 if i % 2 == 0 else -0.6))
        result = _run({"us:AAA": prices}, {"us:AAA": _inst("AAA")})
        item = _by_symbol(result)["us:AAA"]
        assert item["signal"] == "accumulate_candidate"
        assert any("MA20" in reason for reason in item["reasons"])

    def test_hot_uptrend_is_wait_for_pullback(self):
        # 长期缓涨后近段加速拉升 → RSI/布林位置过热
        prices = [100 + i * 0.1 for i in range(30)] + [
            103 + i * 2.5 for i in range(10)
        ]
        result = _run({"us:AAA": prices}, {"us:AAA": _inst("AAA")})
        item = _by_symbol(result)["us:AAA"]
        assert item["signal"] == "wait_for_pullback"

    def test_deep_downtrend_with_slowing_decline_is_left_bottom_candidate(self):
        # 深跌但近5根未继续加速，符合左侧轻仓观察条件。
        prices = [150 - i * 1.2 for i in range(40)]
        result = _run({"us:AAA": prices}, {"us:AAA": _inst("AAA")})
        item = _by_symbol(result)["us:AAA"]
        assert item["signal"] == "left_bottom_candidate"
        assert any("跌势放缓" in reason for reason in item["reasons"])

    def test_crash_that_stops_accelerating_is_left_bottom_candidate(self):
        # 高位急跌后末段跌速稳定，不再满足“加速下跌”条件，转为左侧候选。
        prices = [150.0] * 30 + [150 - i * 4 for i in range(1, 11)]
        result = _run({"us:AAA": prices}, {"us:AAA": _inst("AAA")})
        item = _by_symbol(result)["us:AAA"]
        assert item["signal"] == "left_bottom_candidate"
        assert any("跌势放缓" in reason for reason in item["reasons"])

    def test_flat_is_neutral_hold(self):
        # 单标的宇宙(排名无统计意义)+横盘 → 不得误判为轮动候选
        prices = [100.0, 100.1] * 20
        result = _run({"us:AAA": prices}, {"us:AAA": _inst("AAA")})
        item = _by_symbol(result)["us:AAA"]
        assert item["signal"] == "neutral_hold"

    def test_slightly_positive_r20_is_not_accumulate(self):
        # 20 日仅约 +0.4% 是横盘噪声，不能包装成布局机会。
        prices = [100.0]
        for i in range(39):
            prices.append(prices[-1] + (0.12 if i % 2 == 0 else -0.08))
        result = _run({"us:AAA": prices}, {"us:AAA": _inst("AAA")})
        item = _by_symbol(result)["us:AAA"]
        assert item["signal"] == "neutral_hold"

    def test_leader_in_broad_universe_is_rotation_candidate(self):
        # 5 标的宇宙:4 个横盘 + 1 个高胜率小步上行(RSI 过高不满足 accumulate,
        # 涨幅不足 pullback 阈值) → 落到 rotation_candidate
        leader = [100.0]
        for i in range(39):
            leader.append(leader[-1] + (0.5 if i % 2 == 0 else -0.1))
        prices_by_key = {"us:LEAD": leader}
        instruments = {"us:LEAD": _inst("LEAD")}
        for code in ("F1", "F2", "F3", "F4"):
            prices_by_key[f"us:{code}"] = [100.0, 100.05] * 20
            instruments[f"us:{code}"] = _inst(code)
        result = _run(prices_by_key, instruments)
        item = _by_symbol(result)["us:LEAD"]
        assert item["signal"] == "rotation_candidate"
        assert any("轮动排名" in reason for reason in item["reasons"])

    def test_insufficient_history_is_no_data(self):
        result = _run({"us:AAA": [100.0] * 5}, {"us:AAA": _inst("AAA")})
        item = _by_symbol(result)["us:AAA"]
        assert item["signal"] == "no_data"
        assert result["status"] in ("partial", "no_data")

    def test_missing_frame_is_no_data(self):
        result = _run({}, {"us:AAA": _inst("AAA")})
        item = _by_symbol(result)["us:AAA"]
        assert item["signal"] == "no_data"
        assert result["status"] == "no_data"


class TestEventOverlayAndMeta:
    def test_event_within_3_days_adds_event_watch(self):
        prices = [100 + i * 0.4 for i in range(40)]
        event = UpcomingEvent(
            date="2026-07-03",
            name="美国 6 月 CPI",
            event_type="macro_release",
            market="us",
            affected_symbols=["us:AAA"],
            days_until=1,
        )
        result = _run(
            {"us:AAA": prices}, {"us:AAA": _inst("AAA")}, events=[event]
        )
        item = _by_symbol(result)["us:AAA"]
        assert item["event_watch"] == ["2026-07-03 美国 6 月 CPI"]
        assert "催化剂" in item["action_hint"]

    def test_far_event_no_overlay(self):
        prices = [100 + i * 0.4 for i in range(40)]
        event = UpcomingEvent(
            date="2026-07-14",
            name="美国 6 月 CPI",
            event_type="macro_release",
            market="us",
            affected_symbols=["us:AAA"],
            days_until=12,
        )
        result = _run(
            {"us:AAA": prices}, {"us:AAA": _inst("AAA")}, events=[event]
        )
        assert "event_watch" not in _by_symbol(result)["us:AAA"]

    def test_pool_and_universe_carried(self):
        prices = [100 + i * 0.4 for i in range(40)]
        result = _run(
            {"us:AAA": prices},
            {"us:AAA": _inst("AAA", pool="ai_chain")},
            scan_keys={"us:AAA"},
        )
        item = _by_symbol(result)["us:AAA"]
        assert item["pool"] == "ai_chain"
        assert item["universe"] == "scan"

    def test_every_directional_signal_has_reasons(self):
        prices_by_key = {
            "us:UP": [100 + i * 0.4 for i in range(40)],
            "us:DOWN": [150 - i * 1.2 for i in range(40)],
        }
        instruments = {"us:UP": _inst("UP"), "us:DOWN": _inst("DOWN")}
        result = _run(prices_by_key, instruments)
        for item in result["items"]:
            assert item["reasons"], f"{item['symbol']} 缺 reasons"
            assert item["action_hint"]

    def test_serializable(self):
        prices = [100 + i * 0.4 for i in range(40)]
        result = _run({"us:AAA": prices}, {"us:AAA": _inst("AAA")})
        text = json.dumps(result, ensure_ascii=False)
        assert "action_hint" in text
        assert result["disclaimer"]
