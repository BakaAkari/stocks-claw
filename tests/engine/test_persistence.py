"""最小化上下文快照持久化测试。"""

import os
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

    persistence.save_execution(
        ExecutionRecord.create(
            id="one",
            decision_id="dec-run1",
            status="executed",
            advice_id="advice-1",
            target="a:588000",
            action="increase",
            extent="full",
            note="已执行",
            executed_at="2026-07-03T10:00:00+08:00",
            price=1.5,
            executed_ratio=0.5,
        )
    )
    persistence.save_execution(
        ExecutionRecord.create(
            id="two",
            decision_id="dec-run1",
            status="rejected",
            advice_id="advice-1",
            target="现金",
            action="none",
            note="未执行",
            executed_at="2026-07-03T11:00:00+08:00",
            rejection_reason="market_condition",
        )
    )
    persistence.save_execution(
        ExecutionRecord.create(
            id="three",
            decision_id="dec-run2",
            status="executed",
            advice_id=None,
            target="a:588000",
            action="increase",
            extent="partial",
            note="无 advice_id 不参与匹配",
            executed_at="2026-07-03T12:00:00+08:00",
            price=1.6,
            executed_ratio=0.3,
        )
    )

    records = persistence.list_executions()
    assert len(records) == 2
    assert records[0]["id"] == "three"
    assert records[0]["extent"] == "partial"
    assert records[0]["status"] == "executed"
    assert records[0]["price"] == 1.6
    assert records[0]["executed_ratio"] == 0.3
    assert records[1]["status"] == "rejected"
    assert records[1]["rejection_reason"] == "market_condition"


def test_execution_record_validates_action_and_extent():
    """Legacy extent validation: extent with action=none is invalid."""
    with pytest.raises(ValueError, match="extent must be omitted"):
        ExecutionRecord.create(
            decision_id="dec-test",
            status="rejected",
            target="a:588000",
            action="none",
            extent="full",
            rejection_reason="test",
        )
    # extent is now optional in the new schema — no error expected
    ok = ExecutionRecord.create(
        decision_id="dec-test",
        status="executed",
        target="a:588000",
        action="increase",
        price=1.0,
        executed_ratio=0.5,
    )
    assert ok.extent is None


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


def test_rollover_uses_record_time_when_file_mtime_ties(tmp_path):
    """NAS/FUSE/sync dirs may give rapid writes identical mtimes; keep newest records."""
    persistence = DataPersistence(
        str(tmp_path / "snapshots"),
        advice_dir=str(tmp_path / "advice"),
        execution_dir=str(tmp_path / "executions"),
        forecast_dir=str(tmp_path / "forecasts"),
        max_advice_records=10,
        max_execution_records=10,
        max_forecast_records=10,
    )

    for index in (1, 2, 3):
        persistence.save_advice(_advice(index))
        persistence.save_execution(
            ExecutionRecord(
                id=f"execution-{index}",
                decision_id="dec-rollover",
                status="executed",
                advice_id="advice-1",
                target="a:588000",
                action="increase",
                extent="full",
                note="执行记录",
                executed_at=f"2026-07-03T0{index}:00:00+00:00",
                recorded_at=f"2026-07-03T0{index}:00:00+00:00",
                price=1.0,
                executed_ratio=0.5,
            )
        )
        persistence.save_forecast(_forecast(index))

    for directory in (persistence.advice_dir, persistence.execution_dir, persistence.forecast_dir):
        for path in directory.glob("*.json"):
            os.utime(path, (1, 1))

    persistence.max_advice_records = 2
    persistence.max_execution_records = 2
    persistence.max_forecast_records = 2
    persistence._trim_advice()
    persistence._trim_executions()
    persistence._trim_forecasts()

    advice_records = persistence.list_advice()
    assert advice_records[0]["created_at"].endswith("03+00:00")
    assert advice_records[1]["created_at"].endswith("02+00:00")
    assert [item["id"] for item in persistence.list_executions()] == [
        "execution-3",
        "execution-2",
    ]
    assert [item["id"] for item in persistence.list_forecasts()] == [
        "forecast-3",
        "forecast-2",
    ]


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


# ── Task 10: Decision Trust — ExecutionRecord 新 schema 验证 ──

def test_execution_record_new_schema_status_validation():
    """status=executed 必填 price+executed_ratio; status=rejected 必填 rejection_reason."""
    # executed: price + executed_ratio 必填
    with pytest.raises(ValueError, match="price is required when status=executed"):
        ExecutionRecord.create(
            decision_id="dec-1", status="executed",
            target="a:588000", action="increase",
        )
    with pytest.raises(ValueError, match="executed_ratio is required when status=executed"):
        ExecutionRecord.create(
            decision_id="dec-1", status="executed",
            target="a:588000", action="increase", price=1.0,
        )
    # executed_ratio 范围
    with pytest.raises(ValueError, match="executed_ratio must be between 0 and 1"):
        ExecutionRecord.create(
            decision_id="dec-1", status="executed",
            target="a:588000", action="increase", price=1.0, executed_ratio=1.5,
        )
    # rejected: rejection_reason 必填
    with pytest.raises(ValueError, match="rejection_reason is required when status=rejected"):
        ExecutionRecord.create(
            decision_id="dec-1", status="rejected",
            target="a:588000", action="increase",
        )
    # deferred: next_review_at 可选
    d = ExecutionRecord.create(
        decision_id="dec-1", status="deferred",
        target="a:588000", action="increase",
        next_review_at="2026-07-20",
    )
    assert d.next_review_at == "2026-07-20"
    d2 = ExecutionRecord.create(
        decision_id="dec-1", status="deferred",
        target="a:588000", action="increase",
    )
    assert d2.next_review_at is None
    # status 必须合法
    with pytest.raises(ValueError, match="execution.status must be one of"):
        ExecutionRecord.create(
            decision_id="dec-1", status="cancelled",
            target="a:588000",
        )


def test_execution_record_decision_id_required():
    """decision_id 必填。"""
    with pytest.raises(ValueError, match="decision_id is required"):
        ExecutionRecord(
            id="test", decision_id="", status="planned",
            target="a:588000", action="hold", note="", executed_at="", recorded_at="",
        )


def test_execution_record_matching_by_decision_id():
    """按 decision_id 匹配：同一 decision_id 匹配到多条。"""
    r1 = ExecutionRecord.create(
        id="match-1", decision_id="dec-run-1", status="executed",
        target="a:588000", action="increase", price=1.0, executed_ratio=0.5,
    )
    r2 = ExecutionRecord.create(
        id="match-2", decision_id="dec-run-1", status="rejected",
        target="现金", action="none", rejection_reason="test",
    )
    r3 = ExecutionRecord.create(
        id="match-3", decision_id="dec-run-2", status="executed",
        target="a:588001", action="add", price=2.0, executed_ratio=1.0,
    )
    assert r1.decision_id == r2.decision_id == "dec-run-1"
    assert r3.decision_id == "dec-run-2"


def test_execution_record_legacy_backward_compat():
    """旧 schema 存量数据 with action+extent 仍可加载。"""
    legacy_dict = {
        "id": "legacy-1",
        "decision_id": "legacy-dec-1",
        "status": "executed",
        "advice_id": "advice-1",
        "target": "a:588000",
        "action": "increase",
        "extent": "full",
        "note": "test",
        "executed_at": "2026-07-03T10:00:00Z",
        "recorded_at": "2026-07-03T10:00:01Z",
        "price": 1.0,
        "executed_ratio": 0.5,
    }
    r = ExecutionRecord.from_dict(legacy_dict)
    assert r.id == "legacy-1"
    assert r.decision_id == "legacy-dec-1"
    assert r.status == "executed"
    assert r.advice_id == "advice-1"
    assert r.action == "increase"
    assert r.extent == "full"
    assert r.price == 1.0
    assert r.executed_ratio == 0.5


def test_persistence_find_by_decision_id(tmp_path):
    """DataPersistence.find_executions_by_decision_id 正确返回。"""
    p = DataPersistence(
        str(tmp_path / "snapshots"),
        execution_dir=str(tmp_path / "executions"),
        max_execution_records=10,
    )
    r1 = ExecutionRecord.create(
        id="f1", decision_id="dec-group-1", status="executed",
        target="a:001", action="increase", price=1.0, executed_ratio=0.5,
    )
    r2 = ExecutionRecord.create(
        id="f2", decision_id="dec-group-1", status="rejected",
        target="a:002", action="reduce", rejection_reason="no_liquidity",
    )
    r3 = ExecutionRecord.create(
        id="f3", decision_id="dec-group-2", status="planned",
        target="a:003", action="hold",
    )
    for r in (r1, r2, r3):
        p.save_execution(r)

    matched = p.find_executions_by_decision_id("dec-group-1")
    assert len(matched) == 2
    ids = {m["id"] for m in matched}
    assert ids == {"f1", "f2"}

    matched2 = p.find_executions_by_decision_id("dec-nonexistent")
    assert matched2 == []


def test_persistence_find_by_run_id(tmp_path):
    """DataPersistence.find_executions_by_run_id 按前缀匹配 decision_id。"""
    p = DataPersistence(
        str(tmp_path / "snapshots"),
        execution_dir=str(tmp_path / "executions"),
        max_execution_records=10,
    )
    r1 = ExecutionRecord.create(
        id="f1", decision_id="20260715T100000Z_morning_run_001", status="executed",
        target="a:001", action="increase", price=1.0, executed_ratio=0.5,
    )
    r2 = ExecutionRecord.create(
        id="f2", decision_id="20260715T100000Z_morning_run_002", status="rejected",
        target="a:002", action="reduce", rejection_reason="no_liquidity",
    )
    r3 = ExecutionRecord.create(
        id="f3", decision_id="20260716T100000Z_morning_run_001", status="planned",
        target="a:003", action="hold",
    )
    for r in (r1, r2, r3):
        p.save_execution(r)

    matched = p.find_executions_by_run_id("20260715T100000Z_morning_run")
    assert len(matched) == 2

    matched2 = p.find_executions_by_run_id("nonexistent")
    assert matched2 == []


@pytest.mark.asyncio
async def test_execution_integration_save_read_and_attach(tmp_path):
    """绿灯：保存 executed/rejected/deferred 各一条，读回并附加到下一 run 的 advice。"""
    from stocks.engine.advice_review import attach_execution_review

    p = DataPersistence(
        str(tmp_path / "snapshots"),
        execution_dir=str(tmp_path / "executions"),
        max_execution_records=10,
    )

    # 保存三种状态的记录
    exec_rec = ExecutionRecord.create(
        id="integration-exec",
        decision_id="run1_dec_001",
        status="executed",
        target="a:588000",
        action="increase",
        extent="full",
        note="已在 1.5 买入",
        price=1.5,
        executed_ratio=1.0,
    )
    rej_rec = ExecutionRecord.create(
        id="integration-rej",
        decision_id="run1_dec_002",
        status="rejected",
        target="现金",
        action="none",
        note="市场条件不符",
        rejection_reason="market_risk_too_high",
    )
    def_rec = ExecutionRecord.create(
        id="integration-def",
        decision_id="run1_dec_003",
        status="deferred",
        target="a:588001",
        action="add",
        note="推迟建仓",
        next_review_at="2026-07-20",
    )
    for r in (exec_rec, rej_rec, def_rec):
        p.save_execution(r)

    # 读回
    all_records = p.list_executions()
    assert len(all_records) == 3
    statuses = {r["status"] for r in all_records}
    assert statuses == {"executed", "rejected", "deferred"}

    # 构造假 advice 和 actions，模拟下一 run 的 execution_review
    fake_advice = [
        {
            "id": "adv-1",
            "created_at": "2026-07-15T00:00:00Z",
            "instruments": [{"market": "a", "code": "588000", "name": "科创50"}],
            "direction": {"a:588000": "buy"},
            "rationale_summary": "test",
            "based_on": ["quotes"],
            "boundary": [{"type": "fact", "text": "test"}],
            "actions": [
                {"target": "a:588000", "action": "increase", "size_hint": "一半仓位", "horizon": "short", "decision_id": "run1_dec_001"},
                {"target": "现金", "action": "hold", "size_hint": "保持现金", "horizon": "short", "decision_id": "run1_dec_002"},
                {"target": "a:588001", "action": "add", "size_hint": "小仓位", "horizon": "medium", "decision_id": "run1_dec_003"},
            ],
        }
    ]

    # attach_execution_review 使用 decision_id 匹配
    reviewed = attach_execution_review(fake_advice, all_records)
    assert len(reviewed) == 1
    reviews = reviewed[0].get("execution_review") or []
    assert len(reviews) == 3

    # 按 target 验证状态
    by_target = {r["target"]: r["status"] for r in reviews}
    assert by_target["a:588000"] == "executed", f"Got {by_target}"
    assert by_target["现金"] == "rejected", f"Got {by_target}"
    assert by_target["a:588001"] == "deferred", f"Got {by_target}"

    # 验证 execution 字段携带完整执行信息
    exec_info = next(r for r in reviews if r["target"] == "a:588000")
    assert exec_info["execution"]["price"] == 1.5
    assert exec_info["execution"]["executed_ratio"] == 1.0
    rej_info = next(r for r in reviews if r["target"] == "现金")
    assert rej_info["execution"]["rejection_reason"] == "market_risk_too_high"
    def_info = next(r for r in reviews if r["target"] == "a:588001")
    assert def_info["execution"]["next_review_at"] == "2026-07-20"
