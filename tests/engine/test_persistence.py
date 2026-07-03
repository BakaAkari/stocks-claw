"""最小化上下文快照持久化测试。"""

from types import SimpleNamespace

import pytest

from stocks.domain.models import (
    AdviceRecord,
    DriftCheck,
    ExecutionRecord,
    ForecastRecord,
    MarketState,
    PortfolioMapping,
)
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


def test_execution_record_round_trip_and_rolls_over(tmp_path):
    persistence = DataPersistence(
        str(tmp_path / "snapshots"),
        execution_dir=str(tmp_path / "executions"),
        max_execution_records=2,
    )

    persistence.save_execution(ExecutionRecord.create(
        id="one",
        advice_id="advice-1",
        target="a:588000",
        action="increase",
        extent="full",
        note="已执行",
        executed_at="2026-07-03T10:00:00+08:00",
    ))
    persistence.save_execution(ExecutionRecord.create(
        id="two",
        advice_id="advice-1",
        target="现金",
        action="none",
        note="未执行",
        executed_at="2026-07-03T11:00:00+08:00",
    ))
    persistence.save_execution(ExecutionRecord.create(
        id="three",
        advice_id=None,
        target="a:588000",
        action="increase",
        extent="partial",
        note="无 advice_id 不参与匹配",
        executed_at="2026-07-03T12:00:00+08:00",
    ))

    records = persistence.list_executions()
    assert len(records) == 2
    assert records[0]["id"] == "three"
    assert records[0]["extent"] == "partial"
    assert records[1]["action"] == "none"
    assert "extent" not in records[1]


def test_execution_record_validates_action_and_extent():
    with pytest.raises(ValueError, match="extent must be omitted"):
        ExecutionRecord.create(
            target="a:588000",
            action="none",
            extent="full",
        )
    with pytest.raises(ValueError, match="extent must be full or partial"):
        ExecutionRecord.create(
            target="a:588000",
            action="increase",
        )


def _forecast(index: int, status: str = "open") -> ForecastRecord:
    return ForecastRecord(
        id=f"forecast-{index}",
        created_at=f"2026-07-0{index}T00:00:00+00:00",
        statement=f"科创50 到期收盘高于 1.{index}",
        target="a:588000",
        metric="close",
        comparator="above",
        level=1.0 + index / 10,
        deadline=f"2026-07-0{index + 1}",
        confidence="medium",
        status=status,
    )


def test_forecast_record_round_trip_and_rolls_over(tmp_path):
    persistence = DataPersistence(
        str(tmp_path / "snapshots"),
        forecast_dir=str(tmp_path / "forecasts"),
        max_forecast_records=2,
    )

    persistence.save_forecast(_forecast(1))
    persistence.save_forecast(_forecast(2))
    persistence.save_forecast(_forecast(3, status="hit"))

    records = persistence.list_forecasts()
    assert len(records) == 2
    assert records[0]["id"] == "forecast-3"
    assert records[0]["status"] == "hit"
    assert records[1]["target"] == "a:588000"


def test_forecast_record_manual_and_validation():
    manual = ForecastRecord.create(
        statement="市场风险偏好可能转弱，需要人工复盘。",
        comparator="below",
        deadline="2026-07-10",
        confidence="low",
    )

    assert manual.status == "manual"
    assert "missing target, level" in manual.resolution_note
    with pytest.raises(ValueError, match="metric"):
        ForecastRecord.create(
            statement="非法指标",
            target="a:588000",
            metric="volume",
            comparator="above",
            level=1.0,
            deadline="2026-07-10",
            confidence="medium",
        )
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        ForecastRecord.create(
            statement="非法日期",
            target="a:588000",
            comparator="above",
            level=1.0,
            deadline="2026/07/10",
            confidence="medium",
        )


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
