"""Read-only account equations from explicitly confirmed statements and events.

Settled cash is distinct from available cash. Position quantities use trade-date
accounting. These equations do not establish investment returns or event coverage.
"""

import re
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation, localcontext

from stocks.domain.models import ExecutionRecord
from stocks.engine.entry_snapshot import canonical_identity
from stocks.engine.evaluation_contract import canonical_json, digest, utc

_UNITS = {"share", "gram", "unit"}
_KINDS = {"deposit", "withdrawal", "dividend", "interest", "transfer", "fx", "fee", "other"}


def _text(value, field):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{field}_required")
    return value


def _decimal(value, field):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError(f"{field}_must_be_finite_decimal")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"{field}_must_be_finite_decimal") from None
    if not number.is_finite():
        raise ValueError(f"{field}_must_be_finite_decimal")
    return number


def _number(value):
    if value == 0:
        return "0"
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def _amount(value, field, *, nonnegative=False):
    number = _decimal(value, field)
    if nonnegative and number < 0:
        raise ValueError(f"{field}_must_be_nonnegative")
    return _number(number)


def _currency(value):
    text = _text(value, "currency").upper()
    if re.fullmatch(r"[A-Z][A-Z0-9]{1,11}", text) is None:
        raise ValueError("invalid_currency")
    return text


def _time(value, field):
    try:
        return utc(value).isoformat()
    except (ValueError, TypeError):
        raise ValueError(f"invalid_{field}") from None


def _fields(payload, allowed, kind):
    if not isinstance(payload, dict):
        raise ValueError(f"{kind}_must_be_object")
    if set(payload) - allowed:
        raise ValueError(f"unsupported_{kind}_fields:{','.join(sorted(set(payload) - allowed))}")


def validate_statement(payload):
    """Return a canonical copy; never derive a statement from current valuation."""
    _fields(payload, {"schema_version", "id", "account_id", "as_of", "recorded_at", "source",
                      "cash_basis", "position_basis", "scope", "cash", "positions", "reference"}, "statement")
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise ValueError("statement_schema_version_must_be_1")
    result = {"schema_version": 1}
    for field in ("id", "account_id", "source"):
        result[field] = _text(payload.get(field), field)
    for field in ("as_of", "recorded_at"):
        result[field] = _time(payload.get(field), field)
    if utc(result["as_of"]) > utc(result["recorded_at"]):
        raise ValueError("statement_as_of_after_recorded_at")
    for field, allowed in (("cash_basis", {"settled"}), ("position_basis", {"trade_date"}),
                           ("scope", {"complete_account", "listed_only"})):
        if payload.get(field) not in allowed:
            raise ValueError(f"unsupported_statement_{field}")
        result[field] = payload[field]
    result["reference"] = payload.get("reference")
    if result["reference"] is not None:
        _text(result["reference"], "reference")
    cash, positions = payload.get("cash"), payload.get("positions")
    if not isinstance(cash, list) or not isinstance(positions, list):
        raise ValueError("statement_cash_and_positions_must_be_lists")
    cash_rows, position_rows = {}, {}
    for row in cash:
        _fields(row, {"currency", "balance", "available_balance"}, "cash_row")
        currency = _currency(row.get("currency"))
        if currency in cash_rows:
            raise ValueError("duplicate_statement_currency")
        available = row.get("available_balance")
        cash_rows[currency] = {"currency": currency, "balance": _amount(row.get("balance"), "balance"),
                               "available_balance": None if available is None else _amount(available, "available_balance", nonnegative=True)}
    for row in positions:
        _fields(row, {"instrument_id", "quantity", "unit", "available_quantity"}, "position_row")
        instrument = canonical_identity(row.get("instrument_id"))
        unit = row.get("unit")
        if unit not in _UNITS:
            raise ValueError("unsupported_position_unit")
        key = (instrument, unit)
        if key in position_rows:
            raise ValueError("duplicate_statement_position")
        quantity = _amount(row.get("quantity"), "quantity", nonnegative=True)
        available = row.get("available_quantity")
        available = None if available is None else _amount(available, "available_quantity", nonnegative=True)
        if available is not None and Decimal(available) > Decimal(quantity):
            raise ValueError("available_quantity_exceeds_quantity")
        position_rows[key] = {"instrument_id": instrument, "unit": unit, "quantity": quantity,
                              "available_quantity": available}
    result["cash"] = [cash_rows[key] for key in sorted(cash_rows)]
    result["positions"] = [position_rows[key] for key in sorted(position_rows)]
    return result


def validate_cash_movement(payload):
    """Validate an external cash leg; trade principal must come from executions."""
    _fields(payload, {"schema_version", "id", "account_id", "currency", "amount", "effective_at",
                      "recorded_at", "kind", "source", "reference"}, "cash_movement")
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise ValueError("cash_movement_schema_version_must_be_1")
    result = {"schema_version": 1}
    for field in ("id", "account_id", "source"):
        result[field] = _text(payload.get(field), field)
    for field in ("effective_at", "recorded_at"):
        result[field] = _time(payload.get(field), field)
    if utc(result["effective_at"]) > utc(result["recorded_at"]):
        raise ValueError("cash_movement_effective_after_recorded_at")
    kind = payload.get("kind")
    if kind not in _KINDS:
        raise ValueError("unsupported_cash_movement_kind")
    amount = _decimal(payload.get("amount"), "amount")
    if amount == 0:
        raise ValueError("cash_movement_amount_must_be_nonzero")
    if (kind in {"deposit", "dividend", "interest"} and amount < 0
            or kind in {"withdrawal", "fee"} and amount > 0):
        raise ValueError("cash_movement_direction_mismatch")
    result.update(kind=kind, currency=_currency(payload.get("currency")), amount=_number(amount),
                  reference=payload.get("reference"))
    if result["reference"] is not None:
        _text(result["reference"], "reference")
    return result


def _unique(records, kind, validator=None):
    indexed = {}
    for original in records or []:
        if not isinstance(original, dict):
            raise ValueError(f"{kind}_must_be_object")
        record = validator(original) if validator else deepcopy(original)
        identity = _text(record.get("id"), f"{kind}_id")
        # Reject non-JSON evidence, including NaN in otherwise unused fields.
        encoded = canonical_json(record)
        if identity in indexed and canonical_json(indexed[identity]) != encoded:
            raise ValueError(f"conflicting_{kind}_id:{identity}")
        indexed[identity] = record
    return [indexed[key] for key in sorted(indexed)]


def _validate_execution(payload):
    ExecutionRecord.from_dict(payload)
    return deepcopy(payload)


def _bucket():
    return {"delta": Decimal(0), "gaps": set(), "events": set()}


def _row(opening, observed, bucket, gaps, *, implicit_opening, implicit_closing):
    missing = set(bucket["gaps"]) | set(gaps)
    if opening is None:
        missing.add("opening_balance_missing")
    if observed is None:
        missing.add("closing_balance_missing")
    expected = None if missing else Decimal(opening) + bucket["delta"]
    difference = None if expected is None else Decimal(observed) - expected
    status = "incomplete" if missing else ("matched" if difference == 0 else "mismatch")
    return {"opening": opening, "known_delta": _number(bucket["delta"]),
            "expected": None if expected is None else _number(expected), "observed": observed,
            "difference": None if difference is None else _number(difference), "status": status,
            "gaps": sorted(missing), "used_event_ids": sorted(bucket["events"]),
            "opening_zero_from_complete_scope": implicit_opening,
            "closing_zero_from_complete_scope": implicit_closing}


def reconcile(opening, closing, executions, cash_movements, *, as_of):
    """Project (opening.as_of, closing.as_of] without writing any account facts.

    Amounts in the JSON result are exact decimal strings. Positions are keyed
    by instrument, then unit; no conversion or aggregation crosses these keys.
    """
    # Precision accommodates normal JSON floats through their full exponent
    # range, plus decimal-string inputs. It is chosen from all input literals.
    def precision(value):
        if isinstance(value, dict):
            return max((precision(v) for v in value.values()), default=1)
        if isinstance(value, (list, tuple)):
            return max((precision(v) for v in value), default=1)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            try:
                number = Decimal(str(value))
                if number.is_finite():
                    parts = number.as_tuple()
                    return len(parts.digits) + abs(parts.exponent)
            except InvalidOperation:
                pass
        return 1

    with localcontext() as context:
        context.prec = 4 * precision([opening, closing, executions, cash_movements]) + 100
        return _reconcile(opening, closing, executions, cash_movements, as_of=as_of)


def _reconcile(opening, closing, executions, cash_movements, *, as_of):
    opening, closing = validate_statement(opening), validate_statement(closing)
    current = utc(_time(as_of, "as_of"))
    if opening["account_id"] != closing["account_id"]:
        raise ValueError("statement_account_mismatch")
    start, end = utc(opening["as_of"]), utc(closing["as_of"])
    if start >= end:
        raise ValueError("opening_must_precede_closing")
    if any(utc(statement["recorded_at"]) > current for statement in (opening, closing)):
        raise ValueError("statement_not_visible_as_of")
    fills = _unique(executions, "execution", _validate_execution)
    movements = _unique(cash_movements, "cash_movement", validate_cash_movement)
    cash = defaultdict(_bucket)
    positions = defaultdict(_bucket)
    cash_gaps, position_gaps = set(), set()
    instrument_gaps = defaultdict(set)
    exclusions = []
    account = opening["account_id"]
    open_cash = {r["currency"]: r for r in opening["cash"]}
    close_cash = {r["currency"]: r for r in closing["cash"]}
    open_positions = {(r["instrument_id"], r["unit"]): r for r in opening["positions"]}
    close_positions = {(r["instrument_id"], r["unit"]): r for r in closing["positions"]}
    for key in open_cash.keys() | close_cash.keys():
        cash[key]
    for key in open_positions.keys() | close_positions.keys():
        positions[key]

    def affect_cash(currency, event, *, delta=None, gap=None):
        if currency is None:
            cash_gaps.add(gap or "execution_currency_unknown")
            return
        bucket = cash[_currency(currency)]
        bucket["events"].add(event)
        if delta is not None:
            bucket["delta"] += delta
        if gap:
            bucket["gaps"].add(gap)

    for fill in fills:
        event = "execution:" + fill["id"]
        if fill.get("status") != "executed":
            exclusions.append({"event_id": event, "reason": "not_executed"})
            continue
        fill_account = fill.get("account_id")
        if fill_account not in (None, "") and fill_account != account:
            exclusions.append({"event_id": event, "reason": "different_account"})
            continue
        if fill.get("schema_version", 1) not in (2, 3):
            # Old records may lack aware timestamps. They remain evidence gaps,
            # rather than being silently dropped or promoted to verified fills.
            try:
                recorded = utc(fill.get("recorded_at"))
            except (ValueError, TypeError):
                recorded = None
            if recorded is not None and recorded > current:
                exclusions.append({"event_id": event, "reason": "recorded_after_as_of"})
                continue
            try:
                executed = utc(fill.get("executed_at"))
            except (ValueError, TypeError):
                executed = None
            if executed is not None and executed > end:
                exclusions.append({"event_id": event, "reason": "executed_after_closing"})
                continue
            cash_gaps.add("legacy_execution_unverified:" + fill["id"])
            if executed is None or executed > start:
                position_gaps.add("legacy_execution_unverified:" + fill["id"])
            continue
        recorded = utc(_time(fill.get("recorded_at"), "execution_recorded_at"))
        if recorded > current:
            exclusions.append({"event_id": event, "reason": "recorded_after_as_of"})
            continue
        executed = utc(_time(fill.get("executed_at"), "execution_executed_at"))
        if executed > recorded:
            raise ValueError("execution_executed_after_recorded_at")
        if executed > end:
            exclusions.append({"event_id": event, "reason": "executed_after_closing"})
            continue
        if not fill_account:
            cash_gaps.add("execution_account_unknown:" + fill["id"])
            if executed > start:
                position_gaps.add("execution_account_unknown:" + fill["id"])
            continue
        target = canonical_identity(fill.get("target"))
        side = fill.get("side")
        if side not in {"buy", "sell"}:
            raise ValueError("invalid_execution_side")
        quantity = _decimal(fill.get("quantity"), "execution_quantity")
        price = _decimal(fill.get("price"), "execution_price")
        if quantity <= 0 or price <= 0:
            raise ValueError("execution_quantity_and_price_must_be_positive")
        currency = _currency(fill.get("currency"))
        sign = Decimal(1 if side == "buy" else -1)
        if executed > start:
            unit = fill.get("unit")
            if unit not in _UNITS:
                instrument_gaps[target].add("execution_unit_unknown:" + fill["id"])
                known = [key for key in positions if key[0] == target]
                for key in known or [(target, "unknown")]:
                    positions[key]["events"].add(event)
            else:
                bucket = positions[target, unit]
                bucket["delta"] += sign * quantity
                bucket["events"].add(event)
        settled_at = fill.get("settled_at")
        if settled_at is None:
            reason = "settlement_unknown_before_opening" if executed <= start else "settlement_unknown"
            affect_cash(currency, event, gap=reason + ":" + fill["id"])
            fee_currency = fill.get("fee_currency")
            if fee_currency != currency:
                affect_cash(fee_currency, event, gap="fee_settlement_unknown:" + fill["id"])
            continue
        settled = utc(_time(settled_at, "execution_settled_at"))
        if not executed <= settled <= recorded:
            raise ValueError("execution_settlement_time_invalid")
        if not start < settled <= end:
            continue
        affect_cash(currency, event, delta=-sign * quantity * price)
        fee_currency = fill.get("fee_currency")
        fee = fill.get("fee_amount")
        if fill.get("fee_booking") != "cash":
            affect_cash(fee_currency, event, gap="fee_booking_unknown:" + fill["id"])
            continue
        if fee is None:
            affect_cash(fee_currency, event, gap="fee_amount_unknown:" + fill["id"])
            continue
        fee = _decimal(fee, "fee_amount")
        if fee < 0:
            raise ValueError("fee_amount_must_be_nonnegative")
        if fee_currency is None:
            affect_cash(None, event, gap="fee_currency_unknown:" + fill["id"])
        else:
            affect_cash(fee_currency, event, delta=-fee)

    for movement in movements:
        event = "cash_movement:" + movement["id"]
        if movement["account_id"] != account:
            exclusions.append({"event_id": event, "reason": "different_account"})
        elif utc(movement["recorded_at"]) > current:
            exclusions.append({"event_id": event, "reason": "recorded_after_as_of"})
        elif start < utc(movement["effective_at"]) <= end:
            affect_cash(movement["currency"], event, delta=Decimal(movement["amount"]))
        else:
            exclusions.append({"event_id": event, "reason": "effective_outside_interval"})

    def boundary(mapping, key, statement, field):
        if key in mapping:
            return mapping[key][field], False
        if statement["scope"] == "complete_account":
            return "0", True
        return None, False

    cash_rows, position_rows = {}, {}
    for currency, bucket in sorted(cash.items()):
        before, implicit_before = boundary(open_cash, currency, opening, "balance")
        after, implicit_after = boundary(close_cash, currency, closing, "balance")
        cash_rows[currency] = _row(before, after, bucket, cash_gaps,
                                   implicit_opening=implicit_before, implicit_closing=implicit_after)
    for (instrument, unit), bucket in sorted(positions.items()):
        before, implicit_before = boundary(open_positions, (instrument, unit), opening, "quantity")
        after, implicit_after = boundary(close_positions, (instrument, unit), closing, "quantity")
        gaps = position_gaps | instrument_gaps[instrument]
        open_units = {key[1] for key in open_positions if key[0] == instrument}
        close_units = {key[1] for key in close_positions if key[0] == instrument}
        if open_units and close_units and open_units != close_units:
            gaps = gaps | {"statement_position_unit_mismatch"}
        position_rows.setdefault(instrument, {})[unit] = _row(
            before, after, bucket, gaps, implicit_opening=implicit_before, implicit_closing=implicit_after)
    rows = list(cash_rows.values()) + [row for by_unit in position_rows.values() for row in by_unit.values()]
    gaps = sorted(cash_gaps | position_gaps)
    status = ("incomplete" if gaps or any(row["status"] == "incomplete" for row in rows)
              else "mismatch" if any(row["status"] == "mismatch" for row in rows) else "matched")
    inputs = {"opening": opening, "closing": closing, "executions": fills, "cash_movements": movements,
              "as_of": current.isoformat()}
    report = {"schema_version": 1, "account_id": account, "as_of": current.isoformat(), "status": status,
              "opening": opening, "closing": closing, "cash_by_currency": cash_rows,
              "positions_by_instrument": position_rows, "gaps": gaps,
              "input_event_ids": sorted(["execution:" + row["id"] for row in fills]
                                         + ["cash_movement:" + row["id"] for row in movements]),
              "excluded_events": sorted(exclusions, key=lambda row: row["event_id"]),
              "input_hash": digest(inputs),
              "warnings": ["Matching proves only the declared scope agrees with the recorded events; it does not prove complete event coverage or returns.",
                           "Settled balances and trade-date quantities do not establish cash or quantity available to trade."]}
    report["content_hash"] = digest(report)
    return report
