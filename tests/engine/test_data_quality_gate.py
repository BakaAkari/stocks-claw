"""Test: 数据异常守门 (Task 2)

覆盖场景：
- 半导体 ETF a_512480 真实 fixture: single_bar_jump + mixed_adjustment_regime + prev_close_mismatch
- 正常趋势下跌（常规 5-10% 波动）不得误报
- 除息小跳变（<5%）不得误报
- 不足数据（<2 bars）返回空
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from stocks.engine.data_quality_gate import DEFAULT_THRESHOLDS, detect_price_anomalies

# ── Fixture 路径 ──
# git 跟踪的最小真实形态 fixture（15 根 bar，含 2.70→1.33 拆细跳变）。
# 历史说明：该 fixture 原位于未入库的 .superpowers/sdd/ 路径，导致干净
# checkout 上 6 个测试失败；2026-07-30 重建并移入 tests/fixtures/。
FIXTURE_PATH = Path(__file__).parents[1] / "fixtures/a512480_split_jump_fixture.json"


# ============================================================
# 反例（异常场景 — 必须检测到）
# ============================================================


def _load_fixture() -> list[dict]:
    """加载最小真实 fixture：半导体 ETF a_512480 2026-06-25 → 2026-07-15"""
    with open(FIXTURE_PATH) as f:
        return json.load(f)


def _to_frame(records: list[dict]) -> Any:
    """将 fixture 记录转换为 DataFrame（与 history_cache 输出兼容的列名）。"""
    import pandas as pd

    df = pd.DataFrame(records)
    df = df.rename(columns={"price": "close", "timestamp": "datetime"})
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)
    return df


def _semiconductor_fixture_frame() -> Any:
    """半导体 a_512480 fixture 数据框（15 根 bar, 含 2.70→1.33 跳变）。"""
    return _to_frame(_load_fixture())


class TestSemiconductorAnomaly:
    """半导体 ETF a_512480 真实跳变场景 — 必须检测"""

    def test_single_bar_jump_detected(self):
        """第 7 根 bar (07-03) 2.70→1.33 绝对变化 >35% 必须检出 single_bar_jump"""
        frame = _semiconductor_fixture_frame()
        anomalies = detect_price_anomalies(frame, current_price=1.33, ma20=None)
        codes = {a["code"] for a in anomalies}
        assert "single_bar_jump" in codes, (
            f"Expected single_bar_jump in anomalies, got: {codes}"
        )

    def test_mixed_adjustment_regime_detected(self):
        """60 根范围内 >35% 跳变且比例接近 1:2/2:1 — 检出 mixed_adjustment_regime"""
        frame = _semiconductor_fixture_frame()
        anomalies = detect_price_anomalies(frame, current_price=1.33, ma20=None)
        codes = {a["code"] for a in anomalies}
        assert "mixed_adjustment_regime" in codes, (
            f"Expected mixed_adjustment_regime in anomalies, got: {codes}"
        )

    def test_block_level_anomaly_detected(self):
        """半导体 fixture 至少有 1 个 high/critical 级 anomaly"""
        frame = _semiconductor_fixture_frame()
        anomalies = detect_price_anomalies(frame, current_price=1.33, ma20=None)
        block_codes = [a for a in anomalies if a.get("severity") in ("high", "critical")]
        assert len(block_codes) >= 1, (
            f"Expected at least one block-level anomaly, got: {anomalies}"
        )


# ============================================================
# 正例（正常场景 — 不得误报）
# ============================================================


class TestNormalTrend:
    """正常趋势下跌场景 — 5-10% 连续波动，不得误报"""

    def test_gradual_decline_no_false_positive(self):
        """连续下跌每天 -2% 到 -8%，累积 -40%，不应被误判为 single_bar_jump"""
        import pandas as pd

        prices = [100.0]
        daily_pct = [-0.02, -0.03, -0.05, -0.08, -0.06, -0.04, -0.03, -0.05, -0.04, -0.02]
        for pct in daily_pct:
            prices.append(round(prices[-1] * (1 + pct), 4))

        rows = []
        for i, p in enumerate(prices):
            rows.append({
                "close": p,
                "prev_close": prices[i - 1] if i > 0 else p,
                "datetime": f"2026-07-0{(i % 9) + 1:02d}T00:00:00+00:00",
            })
        frame = pd.DataFrame(rows)
        anomalies = detect_price_anomalies(frame, current_price=prices[-1], ma20=85.0)
        block_codes = {a["code"] for a in anomalies if a.get("severity") in ("high", "critical")}
        assert "single_bar_jump" not in block_codes, (
            f"False positive single_bar_jump on gradual decline: {anomalies}"
        )
        assert "mixed_adjustment_regime" not in block_codes, (
            f"False positive mixed_adjustment_regime on gradual decline: {anomalies}"
        )

    def test_normal_volatility_no_false_positive(self):
        """常规 3-8% 双向波动不应触发 block"""
        import pandas as pd

        prices = [50.0, 52.0, 51.0, 53.5, 52.0, 50.5, 51.5, 50.0, 49.0, 48.5,
                  47.0, 48.0, 49.0, 50.5, 52.0]
        rows = []
        for i, p in enumerate(prices):
            rows.append({
                "close": p,
                "prev_close": prices[i - 1] if i > 0 else p,
                "datetime": f"2026-07-{(i % 30) + 1:02d}T00:00:00+00:00",
            })
        frame = pd.DataFrame(rows)
        anomalies = detect_price_anomalies(frame, current_price=prices[-1], ma20=49.5)
        for a in anomalies:
            assert a.get("severity") not in ("high", "critical"), (
                f"False positive block-level anomaly on normal volatility: {a}"
            )

    def test_dividend_dip_no_false_positive(self):
        """除息小跳变（≈4.5%）不得触发 single_bar_jump（阈值 >35%）"""
        import pandas as pd

        prices = [100.0, 101.0, 102.0, 97.5, 98.0, 99.0]
        rows = []
        for i, p in enumerate(prices):
            rows.append({
                "close": p,
                "prev_close": prices[i - 1] if i > 0 else p,
                "datetime": f"2026-07-{(i % 20) + 1:02d}T00:00:00+00:00",
            })
        frame = pd.DataFrame(rows)
        anomalies = detect_price_anomalies(frame, current_price=prices[-1], ma20=None)
        codes = {a["code"] for a in anomalies}
        assert "single_bar_jump" not in codes, (
            f"False positive single_bar_jump on dividend dip: {anomalies}"
        )

    def test_approaching_ma20_no_false_positive(self):
        """现价接近 MA20（偏离 <30%）不触发 dislocation"""
        import pandas as pd

        prices = [100.0] * 20 + [99.0, 98.0, 95.0, 93.0, 90.0]
        rows = []
        for i, p in enumerate(prices):
            rows.append({
                "close": p,
                "prev_close": prices[i - 1] if i > 0 else p,
                "datetime": f"2026-07-{(i % 31) + 1:02d}T00:00:00+00:00",
            })
        frame = pd.DataFrame(rows)
        anomalies = detect_price_anomalies(frame, current_price=90.0, ma20=100.0)
        codes = {a["code"] for a in anomalies}
        assert "price_ma20_dislocation" not in codes, (
            f"False positive price_ma20_dislocation when <30%: {anomalies}"
        )


# ============================================================
# 边界场景
# ============================================================


class TestEdgeCases:
    """边界 & 反例场景"""

    def test_empty_frame(self):
        """空 DataFrame 返回空列表"""
        import pandas as pd
        anomalies = detect_price_anomalies(pd.DataFrame(), current_price=None, ma20=None)
        assert anomalies == []

    def test_single_bar(self):
        """单 bar 数据返回空（不足以判断）"""
        import pandas as pd
        frame = pd.DataFrame([
            {"close": 100.0, "prev_close": 100.0, "datetime": "2026-07-01T00:00:00+00:00"}
        ])
        anomalies = detect_price_anomalies(frame, current_price=100.0, ma20=None)
        assert anomalies == []

    def test_two_bars_no_anomaly(self):
        """两 bar 小幅变化不得报错"""
        import pandas as pd
        frame = pd.DataFrame([
            {"close": 100.0, "prev_close": 100.0, "datetime": "2026-07-01T00:00:00+00:00"},
            {"close": 101.0, "prev_close": 100.0, "datetime": "2026-07-02T00:00:00+00:00"},
        ])
        anomalies = detect_price_anomalies(frame, current_price=101.0, ma20=None)
        assert anomalies == []

    def test_source_change_creates_evidence(self):
        """数据源变化在异常点附近应产出 info 级 evidence"""
        import pandas as pd
        rows = [
            {"close": 100.0, "prev_close": 100.0, "datetime": "2026-07-01T00:00:00+00:00", "data_source": "provider"},
            {"close": 101.0, "prev_close": 100.0, "datetime": "2026-07-02T00:00:00+00:00", "data_source": "provider"},
            {"close": 60.0, "prev_close": 101.0, "datetime": "2026-07-03T00:00:00+00:00", "data_source": "realtime"},
        ]
        frame = pd.DataFrame(rows)
        anomalies = detect_price_anomalies(frame, current_price=60.0, ma20=None)
        codes = {a["code"] for a in anomalies}
        assert "single_bar_jump" in codes, f"Expected single_bar_jump: {anomalies}"
        assert "source_regime_change" in codes, f"Expected source_regime_change: {anomalies}"


# ============================================================
# 配置验证
# ============================================================


class TestThresholdConfig:
    """阈值可配置性验证"""

    def test_default_thresholds_have_required_keys(self):
        assert "single_bar_jump_pct" in DEFAULT_THRESHOLDS
        assert "ma20_dislocation_pct" in DEFAULT_THRESHOLDS
        assert "prev_close_mismatch_pct" in DEFAULT_THRESHOLDS
        assert "price_jump_window" in DEFAULT_THRESHOLDS

    def test_custom_thresholds_override_defaults(self):
        """传入自定义阈值应覆盖默认值"""
        import pandas as pd
        custom = {**DEFAULT_THRESHOLDS, "single_bar_jump_pct": 4.0}
        prices = [100.0, 103.0, 101.0, 106.0, 102.0]
        rows = []
        for i, p in enumerate(prices):
            rows.append({
                "close": p,
                "prev_close": prices[i - 1] if i > 0 else p,
                "datetime": f"2026-07-{(i % 20) + 1:02d}T00:00:00+00:00",
            })
        frame = pd.DataFrame(rows)
        anomalies = detect_price_anomalies(frame, current_price=102.0, ma20=None, thresholds=custom)
        codes = {item["code"] for item in anomalies}
        assert "single_bar_jump" in codes


class TestDefensiveValidation:
    """非有限数值和 lookback 配置必须产生确定行为。"""

    def test_nan_close_blocks_instead_of_silently_passing(self):
        import pandas as pd

        frame = pd.DataFrame({"close": [100.0, float("nan"), 101.0]})
        anomalies = detect_price_anomalies(frame, current_price=101.0, ma20=100.0)

        assert anomalies[0]["code"] == "insufficient_data"
        assert anomalies[0]["severity"] == "high"

    def test_nan_current_price_blocks_instead_of_silently_passing(self):
        import pandas as pd

        frame = pd.DataFrame({"close": [100.0, 101.0, 102.0]})
        anomalies = detect_price_anomalies(
            frame, current_price=float("nan"), ma20=100.0
        )

        assert anomalies[0]["code"] == "insufficient_data"
        assert anomalies[0]["severity"] == "high"

    def test_regime_lookback_limits_mixed_adjustment_detection(self):
        import pandas as pd

        frame = pd.DataFrame({"close": [100.0, 50.0, 51.0, 52.0, 53.0]})
        anomalies = detect_price_anomalies(
            frame,
            current_price=53.0,
            ma20=None,
            thresholds={"regime_lookback": 3},
        )

        codes = {item["code"] for item in anomalies}
        assert "single_bar_jump" in codes
        assert "mixed_adjustment_regime" not in codes


# ============================================================
# 集成验证
# ============================================================


class TestIntegrationShape:
    """验证 data_quality_gate 的输出形状可被上下文消费"""

    def test_history_cache_price_column_detects_real_regime_jump(self):
        """真实 HistoryCache 使用 price 列，检测器必须直接兼容该接口。"""
        import pandas as pd

        records = _load_fixture()
        frame = pd.DataFrame(records)
        frame["timestamp"] = pd.to_datetime(frame["timestamp"])

        anomalies = detect_price_anomalies(frame, current_price=1.33, ma20=None)

        codes = {item["code"] for item in anomalies}
        assert "single_bar_jump" in codes
        assert "mixed_adjustment_regime" in codes


    def test_anomaly_result_shape(self):
        """每条 anomaly 必须有 code/severity/description/bar_index"""
        frame = _semiconductor_fixture_frame()
        anomalies = detect_price_anomalies(frame, current_price=1.33, ma20=None)
        for a in anomalies:
            assert "code" in a, f"Missing code in anomaly: {a}"
            assert "severity" in a, f"Missing severity in anomaly: {a}"
            assert "description" in a, f"Missing description in anomaly: {a}"
            assert isinstance(a.get("bar_index"), int) or a.get("bar_index") is None, (
                f"bar_index must be int or None: {a}"
            )

    def test_evidence_fields_are_serializable(self):
        """anomaly 结果必须可 JSON 序列化（用于 evidence 字段）"""
        import json
        frame = _semiconductor_fixture_frame()
        anomalies = detect_price_anomalies(frame, current_price=1.33, ma20=None)
        serialized = json.dumps(anomalies, ensure_ascii=False, default=str)
        assert len(serialized) > 0
        loaded = json.loads(serialized)
        assert len(loaded) == len(anomalies)
