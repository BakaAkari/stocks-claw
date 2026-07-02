"""最小化上下文快照持久化测试。"""

from types import SimpleNamespace

import pytest

from stocks.domain.models import AdviceRecord, DriftCheck, MarketState, PortfolioMapping
from stocks.engine.persistence import DataPersistence


def _context(index: int):
    return SimpleNamespace(
        generated_at=f"2026-07-02T00:00:0{index}+00:00",
        asset_count=index,
        portfolio_mapping=PortfolioMapping(ratios={"现金": 1.0}),
        market_state=MarketState(),
        drift_checks=[
            DriftCheck("现金", 1.0, 0.05, 0.3, "above_max", 0.7),
        ],
    )


def test_snapshot_is_minimal_and_rolls_over(tmp_path):
    persistence = DataPersistence(str(tmp_path), max_snapshots=2)

    persistence.save_context(_context(1))
    persistence.save_context(_context(2))
    persistence.save_context(_context(3))

    assert len(persistence.list_snapshots()) == 2
    recent = persistence.load_recent(2)
    assert len(recent) == 2
    assert "portfolio_summary" in recent[0]
    assert "market_state" in recent[0]
    assert "drift_checks" in recent[0]
    assert "assets" not in recent[0]


def test_disabled_persistence_does_not_write(tmp_path):
    persistence = DataPersistence(str(tmp_path), enabled=False)

    assert persistence.save_context(_context(1)) is None
    assert list(tmp_path.iterdir()) == []


def _advice(index: int) -> AdviceRecord:
    return AdviceRecord(
        created_at=f"2026-07-02T00:00:{index:02d}+00:00",
        instruments=[{"market": "a", "code": "000001", "name": "平安银行"}],
        direction={"a:000001": "watch"},
        rationale_summary=f"观察银行股量价变化 {index}",
        based_on=["quotes", "portfolio", "profile"],
        boundary=[
            {"type": "fact", "text": "组合现金占比较高"},
            {"type": "inference", "text": "银行股适合继续观察"},
        ],
    )


def test_advice_record_round_trip_and_rolls_over(tmp_path):
    persistence = DataPersistence(
        str(tmp_path / "snapshots"),
        advice_dir=str(tmp_path / "advice"),
        max_advice_records=2,
    )

    persistence.save_advice(_advice(1))
    persistence.save_advice(_advice(2))
    persistence.save_advice(_advice(3))

    records = persistence.list_advice()
    assert len(records) == 2
    assert records[0]["created_at"].endswith("03+00:00")
    assert records[0]["direction"]["a:000001"] == "watch"
    assert "rationale_summary" in records[0]


def test_advice_record_rejects_long_summary():
    with pytest.raises(ValueError, match="500 characters"):
        AdviceRecord(
            created_at="2026-07-02T00:00:00+00:00",
            instruments=[{"market": "a", "code": "000001", "name": "平安银行"}],
            direction={"a:000001": "watch"},
            rationale_summary="x" * 501,
            based_on=["quotes"],
            boundary=[{"type": "fact", "text": "事实"}],
        )


def test_advice_record_rejects_unknown_source_and_boundary():
    with pytest.raises(ValueError, match="Unsupported based_on"):
        AdviceRecord(
            created_at="2026-07-02T00:00:00+00:00",
            instruments=[{"market": "a", "code": "000001", "name": "平安银行"}],
            direction={"a:000001": "watch"},
            rationale_summary="摘要",
            based_on=["rumor"],
            boundary=[{"type": "fact", "text": "事实"}],
        )
    with pytest.raises(ValueError, match="boundary"):
        AdviceRecord(
            created_at="2026-07-02T00:00:00+00:00",
            instruments=[{"market": "a", "code": "000001", "name": "平安银行"}],
            direction={"a:000001": "watch"},
            rationale_summary="摘要",
            based_on=["quotes"],
            boundary=[{"type": "opinion", "text": "判断"}],
        )
