"""板块轮动脚手架测试。"""

from __future__ import annotations

import pandas as pd

from stocks.domain.models import Instrument
from stocks.engine.rotation import compute_rotation


def _frame(prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": [
                ts.isoformat()
                for ts in pd.date_range(
                    "2026-06-01", periods=len(prices), freq="D", tz="UTC"
                )
            ],
            "price": prices,
        }
    )


def _instruments() -> dict[str, Instrument]:
    return {
        "us:XLK": Instrument(code="XLK", name="科技", market="us", category="us_tech"),
        "us:XLE": Instrument(code="XLE", name="能源", market="us", category="us_energy"),
        "a:512880": Instrument(code="512880", name="证券", market="a", category="券商"),
    }


class TestComputeRotation:
    def test_returns_and_ranking(self):
        frames = {
            "us:XLK": _frame([100 + i for i in range(25)]),   # 上升
            "us:XLE": _frame([100 - i for i in range(25)]),   # 下降
            "a:512880": _frame([100.0] * 25),                  # 走平
        }
        result = compute_rotation(frames, _instruments(), scan_keys={"us:XLE"})

        assert result["status"] == "ok"
        assert result["missing"] == []
        by_symbol = {item["symbol"]: item for item in result["items"]}

        xlk = by_symbol["us:XLK"]
        # 124 vs 119 (5根前) 和 vs 104 (20根前)
        assert xlk["r5"] == round((124 / 119 - 1) * 100, 4)
        assert xlk["r20"] == round((124 / 104 - 1) * 100, 4)
        assert xlk["rank"] == 1
        assert xlk["above_ma20"] is True
        assert xlk["universe"] == "watchlist"

        xle = by_symbol["us:XLE"]
        assert xle["universe"] == "scan"
        assert xle["above_ma20"] is False

        assert result["leaders"][0] == "us:XLK"
        assert result["laggards"] == []  # 只有 3 个有 r20 的标的时不重复列出
        assert result["category_momentum"]["us_tech"]["r20"] == xlk["r20"]

    def test_insufficient_history_is_partial_with_missing(self):
        frames = {
            "us:XLK": _frame([100 + i for i in range(25)]),
            "us:XLE": _frame([100.0]),  # 只有 1 根,连 r5 都算不出
            "a:512880": _frame([100, 101, 102, 103, 104, 105, 106]),  # 只够 r5
        }
        result = compute_rotation(frames, _instruments())

        assert result["status"] == "partial"
        assert result["missing"] == ["us:XLE"]
        by_symbol = {item["symbol"]: item for item in result["items"]}
        assert by_symbol["a:512880"]["r20"] is None
        assert by_symbol["a:512880"]["r5"] is not None
        # r20 缺失的标的排在有 r20 的后面
        assert by_symbol["a:512880"]["rank"] > by_symbol["us:XLK"]["rank"]

    def test_top_level_as_of_is_newest_instrument_timestamp(self):
        newer = _frame([100 + i for i in range(25)])
        older = _frame([100 - i * 0.1 for i in range(25)])
        newer["timestamp"] = pd.to_datetime(newer["timestamp"]) + pd.Timedelta(days=1)
        result = compute_rotation(
            {"us:XLK": newer, "us:XLE": older},
            {
                "us:XLK": _instruments()["us:XLK"],
                "us:XLE": _instruments()["us:XLE"],
            },
        )

        # as_of = newest instrument timestamp (not oldest — stale bars don't drag it)
        assert result["as_of"] == pd.Timestamp(
            newer["timestamp"].iloc[-1]
        ).isoformat()
        # data_freshness: older bar > 24h ago (exact age depends on test time)
        assert result["data_freshness"] in ("fresh", "partial", "stale")

    def test_no_frames_is_no_data(self):
        result = compute_rotation({}, _instruments())
        assert result["status"] == "no_data"
        assert result["items"] == []
        assert sorted(result["missing"]) == sorted(_instruments())

    def test_empty_universe_is_no_data(self):
        result = compute_rotation({}, {})
        assert result["status"] == "no_data"
        assert result["missing"] == []

    def test_serializable(self):
        import json

        frames = {"us:XLK": _frame([100 + i for i in range(25)])}
        result = compute_rotation(
            frames, {"us:XLK": _instruments()["us:XLK"]}
        )
        text = json.dumps(result, ensure_ascii=False)
        assert "us:XLK" in text
