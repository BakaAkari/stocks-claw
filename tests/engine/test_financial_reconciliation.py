"""Independent confirmed statements reconcile quantities and settled cash only."""

from copy import deepcopy

import pytest

from stocks.domain.models import ExecutionRecord
from stocks.engine.financial_reconciliation import (
    reconcile,
    validate_cash_movement,
    validate_statement,
)

OPEN = "2026-10-01T00:00:00+00:00"
TRADE = "2026-10-02T00:00:00+00:00"
SETTLED = "2026-10-03T00:00:00+00:00"
CLOSE = "2026-10-04T00:00:00+00:00"
KNOWN = "2026-10-05T00:00:00+00:00"
SYMBOL = "a:518880"


def statement(*, closing=False, balance="1000", quantity="10", **changes):
    return {"schema_version": 1, "id": "close" if closing else "open", "account_id": "broker1",
            "as_of": CLOSE if closing else OPEN, "recorded_at": KNOWN,
            "source": "simulated broker statement", "cash_basis": "settled",
            "position_basis": "trade_date", "scope": "complete_account",
            "cash": [{"currency": "CNY", "balance": balance}],
            "positions": [{"instrument_id": SYMBOL, "quantity": quantity, "unit": "share"}],
            **changes}


def fill(**changes):
    return ExecutionRecord.create(**{
        "schema_version": 3, "id": "fill1", "record_origin": "manual",
        "source": "simulated broker fill", "account_id": "broker1", "unit": "share",
        "fee_booking": "cash", "settled_at": SETTLED, "executed_at": TRADE, "recorded_at": KNOWN,
        "status": "executed", "target": SYMBOL, "side": "buy", "price": 100, "quantity": 2,
        "currency": "CNY", "fee_amount": 1, "fee_currency": "CNY", **changes,
    }).to_dict()


def movement(**changes):
    return {"schema_version": 1, "id": "cash1", "account_id": "broker1", "currency": "CNY",
            "amount": "50", "effective_at": SETTLED, "recorded_at": KNOWN, "kind": "deposit",
            "source": "simulated cash confirmation", "reference": "external-reference", **changes}


def run(opening=None, closing=None, fills=None, movements=None, **kwargs):
    return reconcile(opening or statement(), closing or statement(closing=True), fills or [],
                     movements or [], as_of=KNOWN, **kwargs)


def pos(report, instrument=SYMBOL, unit="share"):
    return report["positions_by_instrument"][instrument][unit]


def test_manual_trade_with_external_cash_matches_independent_ending_and_preserves_inputs():
    opening, closing = statement(), statement(closing=True, balance="849", quantity="12")
    trade, cash = fill(), movement()
    before = deepcopy((opening, closing, trade, cash))
    result = run(opening, closing, [trade], [cash])
    assert result["status"] == "matched"
    assert result["cash_by_currency"]["CNY"]["expected"] == "849"  # 1000 - 2*100 - 1 + 50
    assert result["cash_by_currency"]["CNY"]["known_delta"] == "-151"
    assert pos(result)["expected"] == "12"
    assert result["cash_by_currency"]["CNY"]["used_event_ids"] == ["cash_movement:cash1", "execution:fill1"]
    assert (opening, closing, trade, cash) == before
    assert result == run(opening, closing, [trade], [cash])
    assert "coverage" in result["warnings"][0]


def test_mismatch_is_observed_minus_expected_not_profit():
    result = run(closing=statement(closing=True, balance="800", quantity="11"), fills=[fill()])
    assert result["status"] == "mismatch"
    assert result["cash_by_currency"]["CNY"]["difference"] == "1"
    assert pos(result)["difference"] == "-1"
    assert "profit" not in result and "return" not in result


def test_cash_by_currency_and_account_never_merges_or_converts():
    opening = statement(cash=[{"currency": "CNY", "balance": "1000"}, {"currency": "USD", "balance": "10"}])
    closing = statement(closing=True, quantity="12", cash=[
        {"currency": "CNY", "balance": "800"}, {"currency": "USD", "balance": "9"}])
    result = run(opening, closing, [fill(fee_currency="USD"), fill(id="other", account_id="broker2")],
                 [movement(id="othercash", account_id="broker2")])
    assert result["status"] == "matched"
    assert result["cash_by_currency"]["USD"]["known_delta"] == "-1"
    assert result["cash_by_currency"]["CNY"]["known_delta"] == "-200"
    assert len(result["excluded_events"]) == 2


def test_two_cash_legs_fx_kept_in_original_currencies():
    opening = statement(cash=[{"currency": "CNY", "balance": "1000"}, {"currency": "USD", "balance": "10"}])
    closing = statement(closing=True, cash=[{"currency": "CNY", "balance": "930"}, {"currency": "USD", "balance": "20"}])
    result = run(opening, closing, movements=[movement(kind="fx", amount="-70"),
                                              movement(id="fx-usd", kind="fx", amount="10", currency="USD")])
    assert result["status"] == "matched"
    assert result["cash_by_currency"]["USD"]["known_delta"] == "10"


def test_early_trade_settles_in_interval_changes_cash_not_quantity():
    trade = fill(executed_at="2026-09-30T00:00:00Z")
    result = run(closing=statement(closing=True, balance="799"), fills=[trade])
    assert result["status"] == "matched"
    assert pos(result)["known_delta"] == "0"
    assert result["cash_by_currency"]["CNY"]["known_delta"] == "-201"


def test_trade_in_interval_but_later_settlement_changes_only_quantity():
    result = run(closing=statement(closing=True, quantity="12"),
                 fills=[fill(settled_at="2026-10-04T12:00:00Z")])
    assert result["status"] == "matched"
    assert result["cash_by_currency"]["CNY"]["known_delta"] == "0"
    assert pos(result)["expected"] == "12"


@pytest.mark.parametrize("executed,reason", [(TRADE, "settlement_unknown"),
                                             ("2026-09-30T00:00:00Z", "settlement_unknown_before_opening")])
def test_unknown_settlement_cannot_assume_included_in_opening(executed, reason):
    result = run(fills=[fill(executed_at=executed, settled_at=None)])
    row = result["cash_by_currency"]["CNY"]
    assert row["expected"] is None
    assert reason + ":fill1" in row["gaps"]
    assert result["status"] == "incomplete"


def test_unknown_fee_blocks_only_known_fee_currency_without_assuming_zero():
    opening = statement(cash=[{"currency": "CNY", "balance": "1000"}, {"currency": "USD", "balance": "10"}])
    closing = statement(closing=True, quantity="12", cash=[{"currency": "CNY", "balance": "800"}, {"currency": "USD", "balance": "10"}])
    result = run(opening, closing, [fill(fee_amount=None, fee_currency="USD")])
    assert result["cash_by_currency"]["CNY"]["status"] == "matched"
    assert result["cash_by_currency"]["USD"]["expected"] is None
    assert result["status"] == "incomplete"
    assert pos(result)["status"] == "matched"


def test_unknown_fee_currency_leaves_all_cash_incomplete_but_not_positions():
    result = run(closing=statement(closing=True, quantity="12"), fills=[fill(fee_amount=None, fee_currency=None)])
    assert result["cash_by_currency"]["CNY"]["expected"] is None
    assert pos(result)["status"] == "matched"


def test_schema2_missing_unit_and_settlement_remain_explicit_gaps():
    old = ExecutionRecord.create(schema_version=2, id="v2", decision_id="old-decision", account_id="broker1",
                                 status="executed", target=SYMBOL, side="buy", quantity=2, price=100,
                                 currency="CNY", executed_at=TRADE, recorded_at=KNOWN).to_dict()
    result = run(fills=[old])
    assert result["status"] == "incomplete"
    assert pos(result)["expected"] is None
    assert "execution_unit_unknown:v2" in pos(result)["gaps"]
    assert result["cash_by_currency"]["CNY"]["expected"] is None


def test_schema2_unknown_account_cannot_be_silently_excluded():
    old = ExecutionRecord.create(schema_version=2, id="v2", decision_id="old-decision",
                                 status="executed", target=SYMBOL, side="buy", quantity=2, price=100,
                                 currency="CNY", executed_at=TRADE, recorded_at=KNOWN).to_dict()
    result = run(fills=[old])
    assert result["status"] == "incomplete"
    assert "execution_account_unknown:v2" in result["gaps"]


def test_listed_only_missing_row_is_unknown_while_complete_scope_makes_zero_explicit():
    opening = statement(cash=[], positions=[], scope="listed_only")
    closing = statement(closing=True, balance="10", quantity="0")
    result = run(opening, closing, movements=[movement(amount="10")])
    assert result["cash_by_currency"]["CNY"]["expected"] is None
    assert pos(result)["expected"] is None
    opening["scope"] = "complete_account"
    result = run(opening, closing, movements=[movement(amount="10")])
    assert result["status"] == "matched"
    assert result["cash_by_currency"]["CNY"]["opening_zero_from_complete_scope"] is True


def test_missing_closing_row_in_listed_scope_is_not_zero():
    result = run(closing=statement(closing=True, cash=[], positions=[], scope="listed_only"))
    assert result["cash_by_currency"]["CNY"]["observed"] is None
    assert pos(result)["observed"] is None
    assert result["status"] == "incomplete"


def test_quantity_units_not_silently_converted():
    closing = statement(closing=True, positions=[{"instrument_id": SYMBOL, "unit": "unit", "quantity": "10"}])
    result = run(closing=closing)
    assert set(result["positions_by_instrument"][SYMBOL]) == {"share", "unit"}
    assert pos(result)["expected"] is None
    assert "statement_position_unit_mismatch" in pos(result)["gaps"]


def test_available_facts_preserved_and_never_used_as_equation_balance():
    opening = statement(cash=[{"currency": "CNY", "balance": "1000", "available_balance": "5"}],
                        positions=[{"instrument_id": SYMBOL, "quantity": "10", "unit": "share", "available_quantity": "3"}])
    result = run(opening)
    assert result["status"] == "matched"
    assert result["opening"]["cash"][0]["available_balance"] == "5"
    assert result["opening"]["positions"][0]["available_quantity"] == "3"
    assert result["closing"]["cash"][0]["available_balance"] is None


def test_idempotent_duplicates_conflicts_and_zero_net_do_not_erase_evidence():
    cash_in = movement(amount="50")
    cash_out = movement(id="cash2", kind="withdrawal", amount="-50")
    result = run(movements=[cash_in, cash_out, cash_in])
    assert result["status"] == "matched"
    assert result["cash_by_currency"]["CNY"]["used_event_ids"] == ["cash_movement:cash1", "cash_movement:cash2"]
    assert result["cash_by_currency"]["CNY"]["known_delta"] == "0"
    with pytest.raises(ValueError, match="conflicting_cash_movement_id"):
        run(movements=[cash_in, dict(cash_in, amount="60")])
    with pytest.raises(ValueError, match="conflicting_execution_id"):
        run(fills=[fill(), fill(quantity=3)])
    first = run(closing=statement(closing=True, balance="799", quantity="12"), fills=[fill(), fill()])
    assert first["status"] == "matched"


def test_decimal_arithmetic_no_binary_float_drift_and_order_independent_hash():
    opening = statement(balance="0.1")
    closing = statement(closing=True, balance="0.6")
    movements = [movement(id="a", amount="0.2"), movement(id="b", amount="0.3")]
    result = run(opening, closing, movements=movements)
    assert result["cash_by_currency"]["CNY"]["expected"] == "0.6"
    assert result["status"] == "matched"
    assert result == run(opening, closing, movements=list(reversed(movements)))
    assert result["input_hash"] != run(opening, closing, movements=movements[:1])["input_hash"]


def test_as_of_excludes_later_recording_but_uses_late_recorded_earlier_event():
    trade = fill(recorded_at="2026-10-06T00:00:00Z")
    result = run(fills=[trade])
    assert result["status"] == "matched"
    assert result["excluded_events"] == [{"event_id": "execution:fill1", "reason": "recorded_after_as_of"}]
    result = reconcile(statement(), statement(closing=True, balance="799", quantity="12"), [trade], [],
                       as_of="2026-10-07T00:00:00Z")
    assert result["status"] == "matched"


def test_interval_is_open_left_closed_right_in_absolute_time():
    result = run(movements=[movement(effective_at=OPEN),
                            movement(id="end", effective_at="2026-10-04T08:00:00+08:00")],
                 closing=statement(closing=True, balance="1050"))
    assert result["status"] == "matched"
    assert result["cash_by_currency"]["CNY"]["used_event_ids"] == ["cash_movement:end"]


@pytest.mark.parametrize("value", [None, True, "NaN", float("nan"), "Infinity", "bad"])
def test_invalid_statement_amounts_rejected(value):
    with pytest.raises(ValueError):
        validate_statement(statement(balance=value))


@pytest.mark.parametrize("changes", [
    {"schema_version": True}, {"account_id": ""}, {"source": ""}, {"scope": "guess"},
    {"cash_basis": "available"}, {"position_basis": "settled"},
    {"as_of": "2026-10-01"}, {"recorded_at": "2026-09-01T00:00:00Z"},
    {"cash": [{"currency": "CNY", "balance": "1"}, {"currency": "CNY", "balance": "1"}]},
    {"cash": [{"currency": "CNY", "balance": "1", "available_balance": "-1"}]},
    {"positions": [{"instrument_id": SYMBOL, "quantity": "1", "unit": "share", "available_quantity": "2"}]},
    {"positions": [{"instrument_id": SYMBOL, "quantity": "-1", "unit": "share"}]},
])
def test_invalid_or_ambiguous_statement_contract_rejected(changes):
    with pytest.raises(ValueError):
        validate_statement(statement(**changes))


@pytest.mark.parametrize("changes", [
    {"kind": "trade"}, {"kind": "deposit", "amount": "-1"},
    {"kind": "withdrawal", "amount": "1"}, {"kind": "fee", "amount": "1"},
    {"amount": 0}, {"amount": None}, {"source": ""}, {"currency": ""},
    {"effective_at": "2026-10-06T00:00:00Z"},
])
def test_invalid_cash_events_rejected(changes):
    with pytest.raises(ValueError):
        validate_cash_movement(movement(**changes))


def test_cross_account_statements_future_statements_and_nan_execution_rejected():
    with pytest.raises(ValueError, match="statement_account_mismatch"):
        run(closing=statement(closing=True, account_id="other"))
    with pytest.raises(ValueError, match="statement_not_visible_as_of"):
        reconcile(statement(), statement(closing=True), [], [], as_of=CLOSE)
    with pytest.raises(ValueError):
        run(fills=[dict(fill(), price=float("nan"))])
    with pytest.raises(ValueError):
        run(fills=[dict(fill(), settled_at="2026-09-01T00:00:00Z")])


def test_legacy_naive_timestamps_are_visible_gaps_not_discarded_or_promoted():
    legacy = ExecutionRecord.create(schema_version=1, id="old", decision_id="old-decision",
                                    status="executed", target=SYMBOL, price=100, executed_ratio=1,
                                    executed_at="2026-10-02T10:00:00", recorded_at="2026-10-03T10:00:00").to_dict()
    result = run(fills=[legacy])
    assert result["status"] == "incomplete"
    assert "legacy_execution_unverified:old" in result["gaps"]
    assert result["input_event_ids"] == ["execution:old"]
    assert result["cash_by_currency"]["CNY"]["expected"] is None


def test_round_trip_trades_have_zero_quantity_change_but_both_event_ids_survive():
    buy = fill(fee_amount=0)
    sell = fill(id="fill2", side="sell", fee_amount=0)
    result = run(fills=[buy, sell])
    assert result["status"] == "matched"
    assert pos(result)["known_delta"] == "0"
    assert pos(result)["used_event_ids"] == ["execution:fill1", "execution:fill2"]
    assert result["cash_by_currency"]["CNY"]["used_event_ids"] == ["execution:fill1", "execution:fill2"]
