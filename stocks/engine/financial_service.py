"""Confirmed statement/event writes and read-only reconciliation integration."""

import json
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from stocks.domain.models import ExecutionRecord
from stocks.engine.confirmation_provenance import ConfirmationStore
from stocks.engine.entry_snapshot import canonical_identity
from stocks.engine.evaluation_contract import digest, utc
from stocks.engine.financial_reconciliation import (
    reconcile,
    validate_cash_movement,
    validate_statement,
)

_KINDS = {"account_statements", "cash_movements"}


def _memory_amount(value):
    if isinstance(value, bool):
        raise ValueError("invalid_financial_memory_amount")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("invalid_financial_memory_amount") from None
    if not number.is_finite() or number < 0:
        raise ValueError("invalid_financial_memory_amount")
    return number


def asset_document(engine):
    path = engine._asset_file_path()
    if not path.exists():
        raise ValueError("confirmed_asset_document_missing")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 2:
        raise ValueError("explicit_v2_asset_accounts_required")
    if not isinstance(data.get("accounts"), list) or not isinstance(data.get("positions"), list):
        raise ValueError("invalid_asset_document")
    if any(not isinstance(row, dict) for row in data["accounts"] + data["positions"]):
        raise ValueError("invalid_asset_document_row")
    return data


def _account(engine, account_id):
    matches = [a for a in asset_document(engine)["accounts"] if a.get("account_id") == account_id]
    if len(matches) != 1:
        raise ValueError("account_missing_or_ambiguous")
    return matches[0]


def load_records(engine, kind):
    if kind not in _KINDS:
        raise ValueError("unsupported_financial_record_kind")
    validator = validate_statement if kind == "account_statements" else validate_cash_movement
    records, errors = [], []
    for path in sorted((engine._local_data_dir / kind).glob("*.json")):
        try:
            records.append(validator(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors.append({"file": path.name, "reason": str(exc)})
    return {"records": records, "errors": errors}


def _recorded_payload(payload, existing):
    if not isinstance(payload, dict) or not isinstance(payload.get("id"), str) or not payload["id"].strip():
        raise ValueError("explicit_event_id_required")
    record = deepcopy(payload)
    old = next((r for r in existing if r.get("id") == record["id"]), None)
    if "recorded_at" not in record:
        record["recorded_at"] = (old or {}).get("recorded_at") or datetime.now(timezone.utc).isoformat()
    return record


def _confirmation(engine, target, payload):
    return ConfirmationStore(engine._local_data_dir).find(target, payload)


def save_record(engine, kind, payload, *, channel=None):
    loaded = load_records(engine, kind)
    if loaded["errors"]:
        raise ValueError("financial_record_read_errors")
    record = _recorded_payload(payload, loaded["records"])
    validator = validate_statement if kind == "account_statements" else validate_cash_movement
    record = validator(record)
    _account(engine, record["account_id"])
    engine.persistence._save_record_once(
        engine._local_data_dir / kind, record, timestamp=record["recorded_at"])
    ConfirmationStore(engine._local_data_dir).record(
        f"{kind}:{record['id']}", record, channel=channel,
        source_path=str(engine._local_data_dir / kind),
    )
    return record


def save_execution(engine, payload, *, channel=None):
    """Schema 3 records an actual fill, including independently chosen trades."""
    loaded = engine.persistence.list_executions_checked()
    if loaded["errors"]:
        raise ValueError("execution_record_read_errors")
    record = _recorded_payload(payload, loaded["records"])
    _account(engine, record.get("account_id"))
    if record.get("record_origin") == "advice_linked":
        from stocks.engine.decision_service import save_linked_execution
        linked = save_linked_execution(engine, record)
        ConfirmationStore(engine._local_data_dir).record(
            f"execution:{linked['id']}", linked,
            channel=channel,
            source_path=str(engine._local_data_dir / "executions"),
        )
        return linked
    if record.get("record_origin") != "manual":
        raise ValueError("explicit_execution_origin_required")
    record["target"] = canonical_identity(record.get("target"))
    action = {"buy": "add", "sell": "reduce"}.get(record.get("side"))
    if record.get("action", action) != action:
        raise ValueError("execution_action_side_mismatch")
    record["action"] = action
    fill = ExecutionRecord.create(**record)
    engine.persistence.save_execution(fill)
    ConfirmationStore(engine._local_data_dir).record(
        f"execution:{fill.id}", fill.to_dict(), channel=channel,
        source_path=str(engine._local_data_dir / "executions"),
    )
    return fill.to_dict()


def review(engine, opening_id, closing_id, *, as_of=None):
    cutoff = utc(as_of) if as_of is not None else datetime.now(timezone.utc)
    statements = load_records(engine, "account_statements")
    movements = load_records(engine, "cash_movements")
    fills = engine.persistence.list_executions_checked()
    selected = []
    for identity in (opening_id, closing_id):
        matches = [s for s in statements["records"] if s["id"] == identity]
        if len(matches) != 1:
            raise ValueError("statement_missing_or_ambiguous")
        selected.append(matches[0])
    report = reconcile(*selected, fills["records"], movements["records"], as_of=cutoff)
    errors = statements["errors"] + movements["errors"] + fills["errors"]
    if errors:
        report["status"] = "incomplete"
        report.setdefault("gaps", []).append("financial_record_read_errors")
    report["record_read_errors"] = errors
    report["confirmation_events"] = {
        f"account_statement:{statement['id']}": _confirmation(
            engine, f"account_statements:{statement['id']}", statement
        )
        for statement in selected
    }
    report["confirmation_events"].update({
        f"execution:{record['id']}": _confirmation(engine, f"execution:{record['id']}", record)
        for record in fills["records"]
    })
    report["confirmation_events"].update({
        f"cash_movement:{record['id']}": _confirmation(
            engine, f"cash_movements:{record['id']}", record
        )
        for record in movements["records"]
    })
    report["confirmation_gaps"] = sorted(
        key for key, event in report["confirmation_events"].items() if event is None
    )
    if report["confirmation_gaps"]:
        report["status"] = "incomplete"
        report.setdefault("gaps", []).append("confirmation_provenance_missing")
    report["content_hash"] = digest({k: v for k, v in report.items() if k != "content_hash"})
    return report


def _alignment(doc, statement):
    """Compare current confirmed memory with independently recorded closing facts."""
    account = statement["account_id"]
    gaps = []
    positions = {}
    quantities = defaultdict(Decimal)
    cash = defaultdict(Decimal)
    for p in doc["positions"]:
        if p.get("account_id") != account:
            continue
        if p.get("confirmed") is not True:
            gaps.append("position_not_explicitly_confirmed:" + str(p.get("position_id")))
            continue
        instrument = (p.get("instrument") or {}).get("instrument_key")
        if instrument:
            holding = p.get("holding") or {}
            if holding.get("quantity") is None or not holding.get("unit"):
                gaps.append("position_quantity_or_unit_missing:" + str(p.get("position_id")))
                continue
            instrument = canonical_identity(instrument)
            unit = holding["unit"]
            quantities[(instrument, unit)] += _memory_amount(holding["quantity"])
            matches = [s for s in statement["positions"]
                       if s["instrument_id"] == instrument and s["unit"] == unit]
            peers = [q for q in doc["positions"] if q.get("account_id") == account
                     and (q.get("instrument") or {}).get("instrument_key") == instrument]
            if len(matches) != 1 or len(peers) != 1:
                gaps.append("position_statement_link_ambiguous:" + str(p.get("position_id")))
                continue
            positions[p["position_id"]] = {
                "account_id": account, "instrument_id": instrument, "unit": unit,
                "quantity": matches[0]["quantity"],
                "available_quantity": matches[0].get("available_quantity"),
                "statement_id": statement["id"], "as_of": statement["as_of"],
            }
        elif (p.get("classification") or {}).get("product_type") == "cash":
            value = p.get("valuation_input") or {}
            if value.get("method") != "manual_amount" or value.get("manual_amount") is None:
                gaps.append("cash_memory_amount_missing")
            else:
                cash[p["currency"]] += _memory_amount(value["manual_amount"])
        else:
            gaps.append("unsupported_memory_position:" + str(p.get("position_id")))
    reported_q = {(r["instrument_id"], r["unit"]): Decimal(str(r["quantity"])) for r in statement["positions"]}
    reported_c = {r["currency"]: Decimal(str(r["balance"])) for r in statement["cash"]}
    for key in set(quantities) | set(reported_q):
        if key not in reported_q or quantities.get(key, Decimal(0)) != reported_q[key]:
            gaps.append("asset_memory_quantity_mismatch:" + ":".join(key))
    for ccy in set(cash) | set(reported_c):
        if ccy not in reported_c or cash.get(ccy, Decimal(0)) != reported_c[ccy]:
            gaps.append("asset_memory_cash_mismatch:" + ccy)
    return positions, gaps


def integrity(engine, *, as_of=None):
    """Latest explicit account reconciliation, bound to current asset memory."""
    cutoff = utc(as_of) if as_of is not None else datetime.now(timezone.utc)
    result = {"schema_version": 1, "as_of": cutoff.isoformat(), "status": "incomplete",
              "accounts": {}, "positions": {}, "reports": {}, "gaps": []}
    try:
        doc = asset_document(engine)
        result["asset_document_hash"] = digest(doc)
        asset_confirmation = _confirmation(engine, "financial_assets", doc)
        result["asset_confirmation"] = asset_confirmation
        if asset_confirmation is None:
            result["gaps"].append("financial_assets_confirmation_missing")
        statements = load_records(engine, "account_statements")
        fills = engine.persistence.list_executions_checked()
        movements = load_records(engine, "cash_movements")
        if any(group["errors"] for group in (statements, fills, movements)):
            result["gaps"].append("financial_record_read_errors")
        ids = [a["account_id"] for a in doc["accounts"]]
        if any(not isinstance(aid, str) or not aid.strip() for aid in ids):
            raise ValueError("invalid_asset_account_id")
        if len(set(ids)) != len(ids) or not ids:
            result["gaps"].append("asset_accounts_missing_or_duplicate")
        position_ids = [p.get("position_id") for p in doc["positions"]]
        if (any(not isinstance(pid, str) or not pid.strip() for pid in position_ids)
                or len(set(position_ids)) != len(position_ids)):
            raise ValueError("asset_position_ids_missing_or_duplicate")
        for row in doc["positions"]:
            if row.get("account_id") not in ids:
                result["gaps"].append("asset_position_account_unknown")
        for group in (statements, fills, movements):
            for row in group["records"]:
                if row.get("account_id") not in ids:
                    result["gaps"].append("financial_record_account_unknown:" + str(row.get("id")))
        for aid in sorted(set(ids)):
            rows = [s for s in statements["records"] if s["account_id"] == aid
                    and utc(s["recorded_at"]) <= cutoff and utc(s["as_of"]) <= cutoff]
            rows.sort(key=lambda s: (utc(s["as_of"]), s["id"]))
            if len(rows) < 2:
                result["gaps"].append("reconciliation_baselines_missing:" + aid)
                continue
            opening, closing = rows[-2:]
            if utc(opening["as_of"]) == utc(closing["as_of"]):
                result["gaps"].append("ambiguous_statement_cutoff:" + aid)
                continue
            report = reconcile(opening, closing, fills["records"], movements["records"], as_of=cutoff)
            statement_events = {
                statement["id"]: _confirmation(
                    engine, f"account_statements:{statement['id']}", statement
                )
                for statement in (opening, closing)
            }
            missing_statement_events = [sid for sid, event in statement_events.items() if event is None]
            gaps = []
            result["reports"][aid] = report
            positions, alignment_gaps = _alignment(doc, closing)
            gaps.extend(alignment_gaps)
            gaps.extend("confirmation_provenance_missing:" + sid for sid in missing_statement_events)
            for event in fills["records"]:
                if event.get("account_id") != aid:
                    continue
                target = f"execution:{event['id']}"
                if _confirmation(engine, target, event) is None:
                    gaps.append("confirmation_provenance_missing:" + target)
            for event in movements["records"]:
                if event.get("account_id") != aid:
                    continue
                target = f"cash_movements:{event['id']}"
                if _confirmation(engine, target, event) is None:
                    gaps.append("confirmation_provenance_missing:" + target)
            if closing["scope"] != "complete_account":
                gaps.append("partial_statement_scope")
            for event in fills["records"] + movements["records"]:
                if event.get("account_id") != aid:
                    continue
                if event.get("recorded_at") and utc(event["recorded_at"]) > cutoff:
                    continue
                for field in ("executed_at", "settled_at", "effective_at"):
                    if event.get(field) and utc(closing["as_of"]) < utc(event[field]) <= cutoff:
                        gaps.append("events_after_latest_statement")
            if report["status"] != "matched":
                gaps.append("reconciliation_" + report["status"])
            result["gaps"].extend(f"{aid}:{gap}" for gap in gaps)
            result["positions"].update(positions)
            result["accounts"][aid] = {
                "status": "matched" if not gaps else "incomplete", "statement_id": closing["id"],
                "statement_hash": digest(closing), "statement_as_of": closing["as_of"],
                "statement_confirmation": statement_events.get(closing["id"]),
                "cash_by_currency": {r["currency"]: {"balance": r["balance"],
                                        "available_balance": r.get("available_balance")} for r in closing["cash"]},
            }
        result["status"] = "matched" if not result["gaps"] else "incomplete"
        if any(r["status"] == "mismatch" for r in result["reports"].values()):
            result["status"] = "mismatch"
    except (ValueError, TypeError, KeyError, OSError, InvalidOperation) as exc:
        result["gaps"].append("financial_integrity_unavailable:" + str(exc))
    result["gaps"] = sorted(set(result["gaps"]))
    result["input_hash"] = digest({k: v for k, v in result.items() if k != "as_of"})
    return result
