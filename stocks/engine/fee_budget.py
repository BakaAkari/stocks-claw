"""User-confirmed, bounded per-order fee reserves. No inferred broker rates."""
import json
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext

from stocks.engine.confirmation_provenance import ConfirmationStore, confirmation_hash
from stocks.engine.evaluation_contract import digest, utc

_FIELDS = {"id", "account_id", "instrument_id", "market", "currency", "valid_from", "valid_until",
           "max_quantity", "max_notional", "fee_upper_bound", "source"}


def amount(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("finite_nonnegative_amount_required")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("finite_nonnegative_amount_required") from None
    # Bound precision/scale so arithmetic remains exact under the local context.
    if not number.is_finite() or number < 0 or number > Decimal("1e18") or number.as_tuple().exponent < -8:
        raise ValueError("amount_outside_supported_precision")
    return number


def validate_document(document):
    if not isinstance(document, dict) or set(document) != {"schema_version", "budgets"}:
        raise ValueError("fee_budget_document_fields_invalid")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ValueError("fee_budget_schema_must_be_1")
    if not isinstance(document["budgets"], list):
        raise ValueError("fee_budgets_must_be_list")
    result = deepcopy(document)
    identities, scopes = set(), set()
    for row in result["budgets"]:
        if not isinstance(row, dict) or set(row) != _FIELDS:
            raise ValueError("fee_budget_fields_invalid")
        for key in _FIELDS - {"max_quantity", "max_notional", "fee_upper_bound"}:
            if not isinstance(row[key], str) or not row[key].strip() or row[key] != row[key].strip():
                raise ValueError("fee_budget_nonempty_string_required:" + key)
        market = row["market"]
        if market not in {"a", "us"} or row["currency"] != {"a": "CNY", "us": "USD"}.get(market):
            raise ValueError("fee_budget_market_currency_unsupported")
        from stocks.engine.entry_snapshot import canonical_identity
        if canonical_identity(row["instrument_id"]) != row["instrument_id"] or not row["instrument_id"].startswith(market + ":"):
            raise ValueError("fee_budget_instrument_market_mismatch")
        if utc(row["valid_until"]) <= utc(row["valid_from"]):
            raise ValueError("fee_budget_invalid_validity_interval")
        for key in ("max_quantity", "max_notional", "fee_upper_bound"):
            number = amount(row[key])
            if number <= 0:
                raise ValueError("fee_budget_positive_bound_required:" + key)
            row[key] = format(number, "f")
        scope = (row["account_id"], row["instrument_id"], row["currency"])
        if row["id"] in identities or scope in scopes:
            raise ValueError("fee_budget_duplicate_id_or_scope")
        identities.add(row["id"])
        scopes.add(scope)
    return result


def preview(document):
    result = validate_document(document)
    return {"document": result, "content_hash": confirmation_hash(result, target="fee_budgets"),
            "read_only": True, "requires_confirmation": True, "advisory_ready": False,
            "scope": "per_order_all_in_fee_upper_bound_not_broker_rate"}


def replace_budgets(engine, document, *, confirmed=False, channel=None):
    if confirmed is not True or channel not in {"cli", "mcp"}:
        raise ValueError("fee_budget_explicit_user_confirmation_required")
    result = validate_document(document)
    from stocks.engine.financial_service import _account
    for row in result["budgets"]:
        _account(engine, row["account_id"])
    path = engine._local_data_dir / "fee_budgets.json"
    # A failed confirmation leaves an unverified document, never an implicit grant.
    path.parent.mkdir(parents=True, exist_ok=True)
    engine.persistence._atomic_json(path, result)
    ConfirmationStore(engine._local_data_dir).record("fee_budgets", result, channel=channel, source_path=str(path))
    return deepcopy(result)


def load_state(engine):
    path = engine._local_data_dir / "fee_budgets.json"
    if not path.exists():
        return {"status": "unavailable", "reason": "confirmed_fee_budget_unavailable"}
    try:
        document = validate_document(json.loads(path.read_text(encoding="utf-8")))
        event = ConfirmationStore(engine._local_data_dir).find("fee_budgets", document)
        return {"status": "confirmed" if event else "unverified", "document": document,
                "content_hash": confirmation_hash(document, target="fee_budgets"), "confirmation": event}
    except (OSError, TypeError, ValueError, KeyError) as exc:
        return {"status": "unavailable", "reason": "fee_budget_read_error:" + str(exc)}


def check_purchase(state, *, account_id, instrument_id, currency, quantity, price, available_cash, as_of=None):
    """Mechanical reserve check only, independent of suitability/portfolio checks."""
    result = {"status": "review_required", "reasons": [], "scope": "single_order_confirmed_cash_only"}
    try:
        if not state or state.get("status") != "confirmed":
            raise ValueError("confirmed_fee_budget_unavailable")
        document = validate_document(state.get("document"))
        content_hash = confirmation_hash(document, target="fee_budgets")
        event = state.get("confirmation") or {}
        if (state.get("content_hash") != content_hash or event.get("content_hash") != content_hash
                or event.get("target") != "fee_budgets" or event.get("source_kind") != "user_confirmed"
                or event.get("actor") != "user" or event.get("channel") not in {"cli", "mcp"}
                or event.get("event_integrity") != digest({k: v for k, v in event.items() if k != "event_integrity"})):
            raise ValueError("fee_budget_confirmation_mismatch")
        now = utc(as_of) if as_of is not None else datetime.now(timezone.utc)
        if utc(event.get("confirmed_at")) > now:
            raise ValueError("fee_budget_confirmation_after_snapshot")
        matches = [r for r in document["budgets"] if r["account_id"] == account_id
                   and r["instrument_id"] == instrument_id and r["currency"] == currency]
        if len(matches) != 1:
            raise ValueError("fee_budget_scope_missing_or_ambiguous")
        row = matches[0]
        if not utc(row["valid_from"]) <= now < utc(row["valid_until"]):
            raise ValueError("fee_budget_outside_validity")
        qty, px, cash = amount(quantity), amount(price), amount(available_cash)
        if qty <= 0 or px <= 0:
            raise ValueError("positive_purchase_quantity_and_price_required")
        with localcontext() as ctx:
            ctx.prec = 80
            notional = qty * px
            fee = amount(row["fee_upper_bound"])
            total = notional + fee
        result.update({"budget_id": row["id"], "budget_content_hash": content_hash,
                       "confirmation_event_id": event["event_id"], "currency": currency,
                       "valid_from": row["valid_from"], "valid_until": row["valid_until"],
                       "notional": format(notional, "f"), "fee_upper_bound": format(fee, "f"),
                       "total_cash_required": format(total, "f"), "available_cash": format(cash, "f")})
        if qty > amount(row["max_quantity"]) or notional > amount(row["max_notional"]):
            raise ValueError("purchase_exceeds_confirmed_fee_scope")
        if total > cash:
            raise ValueError("available_cash_including_fee_exceeded")
        result["status"] = "covered"
    except (TypeError, ValueError, KeyError) as exc:
        result["reasons"].append(str(exc))
    return result
