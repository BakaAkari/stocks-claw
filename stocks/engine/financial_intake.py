"""Pure account capture preview. No storage, confirmation or order effects."""
from copy import deepcopy
from dataclasses import fields
from datetime import datetime, timezone

from stocks.domain.models import ExecutionRecord
from stocks.engine.evaluation_contract import digest, utc
from stocks.engine.financial_reconciliation import (
    reconcile,
    validate_cash_movement,
    validate_statement,
)

_FIELDS = {"schema_version", "account_id", "market", "opening_statement", "closing_statement",
           "executions", "cash_movements"}


def intake_template(market):
    """Unknown values are explicit nulls; template is intentionally not valid."""
    if market not in {"a", "us"}:
        raise ValueError("supported_intake_markets:a,us")
    currency = "CNY" if market == "a" else "USD"
    statement = {"schema_version": 1, "id": None, "account_id": None, "as_of": None,
                 "source": None, "cash_basis": "settled", "position_basis": "trade_date",
                 "scope": "complete_account",
                 "cash": [{"currency": currency, "balance": None, "available_balance": None}],
                 "positions": [{"instrument_id": None, "unit": "share", "quantity": None,
                                "available_quantity": None}]}
    return {"schema_version": 1, "account_id": None, "market": market,
            "opening_statement": deepcopy(statement), "closing_statement": deepcopy(statement),
            "executions": [], "cash_movements": []}


def preview_intake(bundle, *, as_of=None):
    """Validate and reconcile proposed records in memory; never promote to facts.

    Missing recorded_at defaults to preview time (capture metadata), never to a
    fabricated broker observation time. Broker as_of/executed_at are required.
    One account and two independently sourced statements per preview.
    """
    now = utc(as_of) if as_of is not None else datetime.now(timezone.utc)
    errors = []

    def issue(path, reason):
        errors.append({"path": path, "reason": reason})

    if not isinstance(bundle, dict):
        raise ValueError("intake_bundle_must_be_object")
    # Reject NaN/non-JSON values even in otherwise unused fields.
    source_hash = digest(bundle)
    if set(bundle) - _FIELDS:
        issue("bundle", "unsupported_fields:" + ",".join(sorted(set(bundle) - _FIELDS)))
    if type(bundle.get("schema_version")) is not int or bundle["schema_version"] != 1:
        issue("schema_version", "must_be_1")
    account = bundle.get("account_id")
    if not isinstance(account, str) or not account.strip() or account != account.strip():
        issue("account_id", "explicit_account_id_required")
    market = bundle.get("market")
    if market not in {"a", "us"}:
        issue("market", "supported_intake_markets:a,us")

    def normalize(row, path, validator):
        if not isinstance(row, dict):
            issue(path, "record_must_be_object")
            return None
        row = deepcopy(row)
        row.setdefault("recorded_at", now.isoformat())
        try:
            row = validator(row)
            if row.get("account_id") != account:
                raise ValueError("record_account_mismatch")
            if utc(row.get("recorded_at")) > now:
                raise ValueError("record_not_visible_at_preview_time")
            for time_field in ("as_of", "executed_at", "settled_at", "effective_at"):
                if row.get(time_field) and utc(row[time_field]) > now:
                    raise ValueError("future_evidence:" + time_field)
            instruments = [r["instrument_id"] for r in row.get("positions", [])]
            if row.get("target"):
                instruments.append(row["target"])
            if any(not instrument.startswith(str(market) + ":") for instrument in instruments):
                raise ValueError("instrument_market_mismatch")
        except (TypeError, ValueError, KeyError) as exc:
            issue(path, str(exc))
            return None
        return row

    def execution(row):
        allowed = {f.name for f in fields(ExecutionRecord)}
        if set(row) - allowed:
            raise ValueError("unsupported_execution_fields")
        if row.get("schema_version") != 3 or row.get("record_origin") != "manual":
            raise ValueError("intake_requires_schema3_manual_fill")
        # Existing fills do not acquire a fabricated advice or decision link.
        row.setdefault("action", {"buy": "add", "sell": "reduce"}.get(row.get("side")))
        if row.get("action") != {"buy": "add", "sell": "reduce"}.get(row.get("side")):
            raise ValueError("execution_action_side_mismatch")
        return ExecutionRecord.from_dict(row).to_dict()

    normalized = {}
    for name in ("opening_statement", "closing_statement"):
        normalized[name] = normalize(bundle.get(name), name, validate_statement)
    for name, validator in (("executions", execution), ("cash_movements", validate_cash_movement)):
        rows = bundle.get(name)
        if not isinstance(rows, list):
            issue(name, "explicit_list_required")
            rows = []
        normalized[name] = []
        ids = set()
        for index, row in enumerate(rows):
            value = normalize(row, f"{name}[{index}]", validator)
            if value is not None:
                if value["id"] in ids:
                    issue(f"{name}[{index}].id", "duplicate_event_id")
                ids.add(value["id"])
                normalized[name].append(value)

    report = None
    if not errors:
        opening, closing = normalized["opening_statement"], normalized["closing_statement"]
        if opening["id"] == closing["id"]:
            issue("closing_statement.id", "distinct_statement_ids_required")
        if any(s["scope"] != "complete_account" for s in (opening, closing)):
            issue("statements.scope", "complete_account_scope_required")
        # The entire supplied interval must be accounted for, not silently
        # excluded by the reconciliation engine's historical-query semantics.
        for name, time_field in (("executions", "executed_at"), ("cash_movements", "effective_at")):
            for i, event in enumerate(normalized[name]):
                try:
                    event_time = utc(event.get(time_field))
                    if event_time > now:
                        raise ValueError("future_event")
                    # Older executions may settle inside this interval.
                    settlement = utc(event["settled_at"]) if event.get("settled_at") else None
                    in_interval = utc(opening["as_of"]) < event_time <= utc(closing["as_of"])
                    settlement_in_interval = settlement is not None and utc(opening["as_of"]) < settlement <= utc(closing["as_of"])
                    if not in_interval and not settlement_in_interval:
                        raise ValueError("event_outside_statement_interval")
                except (TypeError, ValueError) as exc:
                    issue(f"{name}[{i}]", str(exc))
        if not errors:
            try:
                report = reconcile(opening, closing, normalized["executions"], normalized["cash_movements"], as_of=now)
            except (TypeError, ValueError, KeyError) as exc:
                issue("reconciliation", str(exc))
    review_notes = []
    closing = normalized["closing_statement"]
    if closing is not None:
        for row in closing["cash"]:
            if row.get("available_balance") is None:
                review_notes.append("available_cash_unknown:" + row["currency"])
        for row in closing["positions"]:
            if row.get("available_quantity") is None:
                review_notes.append("available_quantity_unknown:" + row["instrument_id"])
    ready = not errors and report is not None and report["status"] == "matched"
    return {"schema_version": 1, "status": "ready_for_user_review" if ready else "needs_correction",
            "read_only": True, "requires_confirmation": True, "advisory_ready": False,
            "account_id": account, "market": market, "previewed_at": now.isoformat(),
            "input_hash": source_hash, "errors": errors, "reconciliation": report,
            "review_notes": review_notes,
            "proposed_records": normalized,
            "confirmation_state": "unconfirmed_draft", "financial_memory_written": False,
            "next_steps": ["review_record_differences_and_gaps", "confirm_asset_ledger_and_account_route",
                           "explicitly_confirm_records_via_existing_write_apis", "check_current_account_readiness"]}


def account_readiness(integrity, mandate, *, as_of=None, max_age_seconds=86400):
    """Current fact readiness, deliberately not action approval or L2 acceptance."""
    now = utc(as_of) if as_of is not None else datetime.now(timezone.utc)
    policy_ok = type(max_age_seconds) is int and max_age_seconds > 0
    global_gaps = list(integrity.get("gaps") or [])
    if integrity.get("status") != "matched":
        global_gaps.append("financial_integrity_not_matched")
    if not policy_ok:
        global_gaps.append("invalid_statement_freshness_policy")
    for source in ("profile", "constraints"):
        if ((mandate.get("sources") or {}).get(source) or {}).get("kind") != "local_confirmed":
            global_gaps.append(source + "_not_user_confirmed")
    for name in ("loss_capacity", "cash_needs"):
        if ((mandate.get("fields") or {}).get(name) or {}).get("status") != "confirmed":
            global_gaps.append("personal_mandate_unknown:" + name)
    if (mandate.get("constraint_snapshot") or {}).get("status") != "confirmed":
        global_gaps.append("portfolio_constraints_not_user_confirmed")
    accounts = {}
    for aid, account in (integrity.get("accounts") or {}).items():
        gaps = []
        if account.get("status") != "matched":
            gaps.append("account_not_reconciled")
        try:
            age = (now - utc(account.get("statement_as_of"))).total_seconds()
            if age < 0:
                gaps.append("statement_in_future")
            elif policy_ok and age > max_age_seconds:
                gaps.append("statement_stale")
        except (TypeError, ValueError):
            gaps.append("statement_time_unknown")
        for currency, cash in (account.get("cash_by_currency") or {}).items():
            if cash.get("available_balance") is None:
                gaps.append("available_cash_unknown:" + currency)
        for pid, position in (integrity.get("positions") or {}).items():
            if position.get("account_id") == aid and position.get("available_quantity") is None:
                gaps.append("available_quantity_unknown:" + pid)
        accounts[aid] = {"status": "current_facts_available" if not gaps else "review_required",
                         "statement_id": account.get("statement_id"), "statement_as_of": account.get("statement_as_of"),
                         "blockers": gaps}
    if not accounts:
        global_gaps.append("account_evidence_missing")
    ready = bool(accounts) and not global_gaps and all(not row["blockers"] for row in accounts.values())
    return {"schema_version": 1, "read_only": True, "as_of": now.isoformat(),
            "status": "current_facts_available" if ready else "review_required",
            "max_statement_age_seconds": max_age_seconds, "accounts": accounts,
            "blockers": sorted(set(global_gaps)), "advisory_ready": False,
            "scope": "financial_facts_only_not_action_or_deployment_approval"}
