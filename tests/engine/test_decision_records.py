"""Confirmed fact records: source identity, retry safety, and complete history."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from stocks.domain.models import AdviceRecord, ExecutionRecord, UserDecisionRecord
from stocks.engine.advice_review import attach_execution_review
from stocks.engine.persistence import DataPersistence

T0 = "2026-10-01T00:00:00+00:00"
T1 = "2026-10-01T01:00:00+00:00"
T2 = "2026-10-01T02:00:00+00:00"
T3 = "2026-10-01T03:00:00+00:00"


def advice(**overrides):
    return AdviceRecord.create(**{
        "id": "advice-1", "created_at": T0,
        "source_run_id": "run-1", "original_text": "原始建议：分批增加，条件不满足就等待。",
        "source_snapshot": {"decision_id": "decision-1", "report_view": {"status": "approved"}},
        "instruments": [{"market": "crypto", "code": "BTC/USDT", "name": "BTC"}],
        "direction": {"crypto:BTC/USDT": "watch"}, "rationale_summary": "待用户决定",
        "based_on": ["quotes"], "boundary": [{"type": "fact", "text": "只记录建议"}],
        "actions": [{"target": "crypto:BTC/USDT", "decision_id": "decision-1",
                     "action": "increase", "size_hint": "分批", "horizon": "unspecified"}],
        **overrides,
    })


def fill(**overrides):
    return ExecutionRecord.create(**{
        "id": "fill-1", "schema_version": 2, "advice_id": "advice-1",
        "decision_id": "decision-1", "source_run_id": "run-1", "status": "executed",
        "target": "crypto:BTC/USDT", "side": "buy", "price": 100,
        "quantity": 0.25, "currency": "USDT", "executed_at": T1, "recorded_at": T3,
        **overrides,
    })


def decision(**overrides):
    return UserDecisionRecord.create(**{
        "id": "user-decision-1", "advice_id": "advice-1", "decision_id": "decision-1",
        "status": "accepted", "decided_at": T1, "recorded_at": T3, **overrides,
    })


def test_source_identity_and_original_text_survive_real_model_roundtrip():
    record = advice()
    assert AdviceRecord.from_dict(record.to_dict()).to_dict() == record.to_dict()
    assert record.schema_version == 2
    assert record.actions[0]["decision_id"] == "decision-1"
    assert record.actions[0]["horizon"] == "unspecified"
    exported = record.to_dict()
    exported["source_snapshot"]["report_view"]["status"] = "changed"
    assert record.source_snapshot["report_view"]["status"] == "approved"
    legacy = record.to_dict()
    for key in ("schema_version", "id", "source_snapshot", "source_run_id", "original_text"):
        legacy.pop(key, None)
    legacy["actions"][0]["horizon"] = "short"
    restored = AdviceRecord.from_dict(legacy)
    assert restored.schema_version == 1 and restored.id is None


def test_fill_requires_actual_facts_and_never_assumes_fee_or_ratio():
    record = fill()
    assert record.fee_amount is None and record.fee_currency is None
    assert record.executed_ratio is None
    assert record.to_dict()["schema_version"] == 2
    assert "fee_amount" not in record.to_dict()
    assert ExecutionRecord.from_dict(record.to_dict()).to_dict() == record.to_dict()
    explicit_zero_fee = fill(fee_amount=0, fee_currency="USDT")
    assert explicit_zero_fee.fee_amount == 0


@pytest.mark.parametrize("field,value", [
    ("executed_at", None), ("executed_at", "2026-10-01T01:00:00"),
    ("recorded_at", "2026-10-01T00:00:00+00:00"), ("recorded_at", ""),
    ("id", ""),
    ("price", 0), ("price", -1), ("price", float("nan")), ("price", float("inf")),
    ("price", True), ("quantity", None), ("quantity", -1), ("quantity", float("nan")),
    ("side", None), ("side", "hold"), ("currency", ""), ("currency", None),
    ("fee_amount", -1), ("fee_amount", float("nan")), ("fee_amount", 0),
    ("executed_ratio", float("inf")),
])
def test_invalid_fill_facts_rejected(field, value):
    with pytest.raises(ValueError):
        fill(**{field: value})


@pytest.mark.parametrize("changes", [
    {"decided_at": "2026-10-01"}, {"decided_at": T3, "recorded_at": T1},
    {"status": "executed"}, {"advice_id": ""}, {"decision_id": ""},
])
def test_decision_is_intent_with_aware_ordered_time(changes):
    with pytest.raises(ValueError):
        decision(**changes)


def test_nested_nan_in_source_rejected_before_any_save():
    with pytest.raises(ValueError, match="finite JSON"):
        advice(source_snapshot={"position": {"quantity": float("nan")}})


@pytest.mark.parametrize("kind", ["advice", "execution", "decision"])
def test_same_id_retry_atomic_and_content_change_rejected(tmp_path, kind):
    persistence = DataPersistence(str(tmp_path / "snapshots"))
    record = {"advice": advice, "execution": fill, "decision": decision}[kind]()
    save = getattr(persistence, f"save_{kind}")
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(lambda _: save(record), range(8)))
    assert len(set(paths)) == 1
    assert len(list(Path(paths[0]).parent.glob("*.json"))) == 1
    changed = replace(record, **({"original_text": "altered"} if kind == "advice" else {"note": "altered"}))
    before = Path(paths[0]).read_bytes()
    with pytest.raises(ValueError, match="different content"):
        save(changed)
    assert Path(paths[0]).read_bytes() == before
    if kind != "advice":
        with pytest.raises(ValueError, match="different content"):
            save(replace(record, recorded_at="2026-10-02T00:00:00Z"))


def test_financial_history_not_trimmed_and_decisions_use_configured_directory(tmp_path):
    persistence = DataPersistence(
        str(tmp_path / "snapshots"), max_advice_records=1, max_execution_records=1,
        advice_dir=str(tmp_path / "confirmed" / "advice"),
    )
    assert persistence.decision_dir == tmp_path / "confirmed" / "user_decisions"
    for index in range(3):
        persistence.save_advice(advice(id=f"advice-{index}"))
        persistence.save_execution(fill(id=f"fill-{index}"))
        persistence.save_decision(decision(id=f"decision-{index}"))
    persistence._trim_advice()
    persistence._trim_executions()
    assert len(persistence.list_advice()) == len(persistence.list_executions()) == 3
    assert len(persistence.list_decisions()) == 3
    custom = DataPersistence(str(tmp_path / "other"), decision_dir=str(tmp_path / "custom"))
    assert custom.decision_dir == tmp_path / "custom"


def test_legacy_feedback_preserves_history_source_and_original_save_idempotence(tmp_path):
    persistence = DataPersistence(str(tmp_path / "snapshots"))
    original = advice()
    path = persistence.save_advice(original)
    first = {"status": "accepted", "note": "接受", "marked_at": T1}
    second = {"status": "rejected", "note": "不继续", "marked_at": T2}
    persistence.update_advice_feedback(original.id, first)
    updated = persistence.update_advice_feedback(original.id, second)
    assert updated["feedback_history"] == [first, second]
    assert updated["feedback"] == second
    assert updated["source_snapshot"] == original.source_snapshot
    assert updated["original_text"] == original.original_text
    assert persistence.save_advice(original) == path
    assert persistence.list_advice()[0]["feedback_history"] == [first, second]
    assert persistence.list_decisions() == []  # No made-up per-action acceptance.


def test_legacy_feedback_already_present_is_not_lost(tmp_path):
    persistence = DataPersistence(str(tmp_path / "snapshots"))
    first = {"status": "deferred", "marked_at": T0}
    record = replace(advice(), feedback=first)
    persistence.save_advice(record)
    second = {"status": "accepted", "marked_at": T1}
    assert persistence.update_advice_feedback(record.id, second)["feedback_history"] == [first, second]


def test_review_keeps_all_fills_after_rejection_and_never_mutates_inputs():
    original = advice().to_dict()
    # Same UTC moment notation deliberately differs to catch lexical time ordering.
    d1 = decision(decided_at="2026-10-01T09:00:00+08:00").to_dict()
    d2 = decision(id="user-decision-2", status="rejected", decided_at=T2).to_dict()
    f1 = fill().to_dict()
    f2 = fill(id="fill-2", executed_at=T2, quantity=0.1).to_dict()
    inputs = deepcopy([original, d1, d2, f1, f2])
    review = attach_execution_review([original], [f2, f1], [d2, d1])[0]["execution_review"][0]
    assert [item["id"] for item in review["fills"]] == ["fill-1", "fill-2"]
    assert [item["status"] for item in review["decision_history"]] == ["accepted", "rejected"]
    assert review["latest_decision"]["status"] == "rejected"
    assert review["execution_status"] == "recorded"
    assert review["status"] == "executed"
    assert [original, d1, d2, f1, f2] == inputs
    review["fills"][0]["price"] = -1
    assert f1["price"] == 100


def test_acceptance_without_fill_remains_unrecorded():
    review = attach_execution_review([advice().to_dict()], [], [decision().to_dict()])[0]["execution_review"][0]
    assert review["latest_decision"]["status"] == "accepted"
    assert review["status"] == "unknown"
    assert review["execution_status"] == "unrecorded" and review["fills"] == []


@pytest.mark.parametrize("changes", [
    {"advice_id": "other"}, {"decision_id": "other"}, {"target": "crypto:ETH/USDT"},
])
def test_new_review_does_not_cross_link_identity_or_target(changes):
    bad = fill(**changes).to_dict()
    review = attach_execution_review([advice().to_dict()], [bad])[0]["execution_review"][0]
    assert review["fills"] == [] and review["execution_records"] == []


def test_schema2_read_does_not_coerce_null_id_to_a_real_identity():
    data = fill().to_dict()
    data["decision_id"] = None
    with pytest.raises(ValueError, match="execution.decision_id"):
        ExecutionRecord.from_dict(data)


def test_latest_records_compare_instants_not_timezone_strings(tmp_path):
    persistence = DataPersistence(str(tmp_path / "snapshots"))
    persistence.save_advice(advice(id="earlier", created_at="2026-10-01T09:00:00+08:00"))
    persistence.save_advice(advice(id="later", created_at=T2))
    persistence.save_decision(decision(id="earlier", recorded_at="2026-10-01T10:00:00+08:00"))
    persistence.save_decision(decision(id="later", recorded_at=T3))
    assert persistence.list_advice()[0]["id"] == "later"
    assert persistence.list_decisions()[0]["id"] == "later"
    updated = persistence.update_advice_feedback("latest", {"status": "deferred", "marked_at": T3})
    assert updated["id"] == "later"


@pytest.mark.parametrize("reference", ["created_at", "id"])
def test_manual_advice_keeps_legacy_links_explicitly_unverified(reference):
    action = {"target": "crypto:BTC/USDT", "action": "increase",
              "size_hint": "分批", "horizon": "short"}
    manual = advice(source_snapshot=None, source_run_id=None, actions=[action]).to_dict()
    legacy = ExecutionRecord.create(
        decision_id="legacy-unrelated-id", advice_id=manual[reference], status="executed",
        target=action["target"], action="increase", extent="full", price=100,
        executed_ratio=0.5,
    ).to_dict()
    unrelated = {**legacy, "id": "unrelated", "advice_id": None}
    review = attach_execution_review([manual], [legacy, unrelated])[0]["execution_review"][0]
    assert review["status"] == "executed"
    assert review["fills"] == [legacy]
    assert review["linkage"] == "legacy_unverified"


def test_frozen_source_never_accepts_timestamp_as_stable_advice_id():
    original = advice().to_dict()
    # Even a legacy fill must not use timestamp fallback for a frozen-source advice.
    legacy = ExecutionRecord.create(
        decision_id="decision-1", advice_id=original["created_at"], status="executed",
        target="crypto:BTC/USDT", price=100, executed_ratio=0.5,
    ).to_dict()
    review = attach_execution_review([original], [legacy])[0]["execution_review"][0]
    assert review["status"] == "unknown" and review["fills"] == []


def test_new_fills_still_require_stable_ids_even_for_manual_advice():
    manual = advice(source_snapshot=None, source_run_id=None).to_dict()
    record = fill(advice_id=manual["created_at"]).to_dict()
    review = attach_execution_review([manual], [record])[0]["execution_review"][0]
    assert review["status"] == "unknown" and review["fills"] == []
    assert review["linkage"] == "legacy_unverified"
