"""Independent actual fills, explicit settlement and visible ledger gaps."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from stocks.domain.models import ExecutionRecord, Position
from stocks.engine.persistence import DataPersistence

T0 = "2026-10-01T00:00:00Z"
T1 = "2026-10-01T01:00:00Z"
T2 = "2026-10-01T02:00:00Z"


def fill(**overrides):
    return ExecutionRecord.create(**{
        "schema_version": 3, "id": "manual-fill", "record_origin": "manual",
        "source": "simulated brokerage statement", "account_id": "account-a", "status": "executed",
        "target": "us:AAPL", "side": "buy", "unit": "share", "quantity": 2,
        "price": 100, "currency": "USD", "fee_booking": "cash",
        "executed_at": T0, "recorded_at": T2, **overrides,
    })


def test_independent_fill_requires_no_system_recommendation_and_does_not_invent_settlement():
    record = fill()
    assert record.decision_id is None and record.advice_id is None
    assert record.settled_at is None and record.fee_amount is None
    assert record.fee_booking == "cash"
    assert "settled_at" not in record.to_dict()
    assert ExecutionRecord.from_dict(record.to_dict()) == record
    assert fill(decision_id="", advice_id="", source_run_id="", user_decision_id="") == record


@pytest.mark.parametrize("field", ["decision_id", "advice_id", "source_run_id", "user_decision_id"])
def test_independent_fill_rejects_fabricated_recommendation_links(field):
    with pytest.raises(ValueError, match="manual execution"):
        fill(**{field: "fake-link"})


def test_advice_linked_fill_has_complete_identity_and_can_have_no_recorded_acceptance():
    record = fill(record_origin="advice_linked", decision_id="decision", advice_id="advice",
                  source_run_id="run", settled_at=T1, fee_amount=2, fee_currency="CNY")
    assert record.user_decision_id is None
    assert record.fee_currency == "CNY" and record.currency == "USD"
    assert ExecutionRecord.from_dict(record.to_dict()) == record
    for field in ("decision_id", "advice_id", "source_run_id"):
        with pytest.raises(ValueError, match=field):
            replace(record, **{field: None})


@pytest.mark.parametrize("changes", [
    {"source": None}, {"source": ""}, {"account_id": None}, {"unit": None}, {"unit": "lot"},
    {"record_origin": None}, {"record_origin": "system"}, {"status": "planned"},
    {"fee_booking": None}, {"fee_booking": "asset"}, {"fee_amount": 0},
    {"settled_at": "2026-10-01"}, {"settled_at": ""},
    {"settled_at": "2026-09-30T23:00:00Z"}, {"settled_at": "2026-10-01T03:00:00Z"},
    {"price": -1}, {"price": float("nan")}, {"quantity": 0}, {"quantity": float("inf")},
    {"executed_at": None}, {"recorded_at": "2026-09-30T23:00:00Z"},
])
def test_actual_fill_and_settlement_contract(changes):
    with pytest.raises(ValueError):
        fill(**changes)


def test_settlement_compares_instants_not_lexical_timezone_strings():
    record = fill(executed_at="2026-10-01T08:00:00+08:00", settled_at=T0)
    assert record.settled_at == T0


def test_checked_reader_exposes_all_damage_and_keeps_valid_history(tmp_path):
    persistence = DataPersistence(str(tmp_path / "snapshots"))
    record = fill(settled_at=T1, fee_amount=0, fee_currency="USD")
    original = Path(persistence.save_execution(record))
    before = original.read_bytes()
    persistence.save_execution(record)
    directory = persistence.execution_dir
    (directory / "broken.json").write_text("{bad", encoding="utf-8")
    (directory / "array.json").write_text("[]", encoding="utf-8")
    bad_record = {**record.to_dict(), "id": "bad-time", "settled_at": "2026-10-01T03:00:00Z"}
    (directory / "invalid-time.json").write_text(json.dumps(bad_record), encoding="utf-8")
    result = persistence.list_executions_checked()
    assert result["records"] == [record.to_dict()]
    assert {Path(error["file"]).name for error in result["errors"]} == {
        "broken.json", "array.json", "invalid-time.json",
    }
    assert all(error["reason"] for error in result["errors"])
    assert persistence.list_executions() == [record.to_dict()]
    assert original.read_bytes() == before
    with pytest.raises(ValueError, match="different content"):
        persistence.save_execution(replace(record, quantity=3))


def test_position_horizon_survives_storage_roundtrip():
    raw = {
        "position_id": "p", "account_id": "a", "display_name": "holding", "currency": "USD",
        "classification": {"asset_class": "equity", "product_type": "stock"},
        "instrument": {"instrument_key": "us:AAPL"}, "holding": {"quantity": 2},
        "valuation_input": {"method": "market_quote"}, "liquidity": {}, "horizon": "swing",
    }
    position = Position.from_dict(raw)
    assert position.to_storage_dict()["horizon"] == "swing"
    assert Position.from_dict(position.to_storage_dict()).horizon == "swing"
