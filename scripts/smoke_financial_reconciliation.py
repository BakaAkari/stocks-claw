"""Exercise confirmed finance APIs with explicit simulated statements and trades."""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.smoke_decision_review import _call, _files_hash, _isolated_adapter
from stocks.domain.models import Account, Position


def fixture_document(*, share_scale=1):
    """Simulated current memory, independently consistent with the closing statement."""
    account = Account("simulation-broker", "模拟账户", "brokerage", base_currency="CNY")
    stock = Position.from_dict({
        "position_id": "sim-stock", "account_id": account.account_id, "display_name": "模拟标的",
        "currency": "CNY", "classification": {"asset_class": "equity", "product_type": "stock"},
        "instrument": {"instrument_key": "a:518880"}, "holding": {"quantity": 70 * share_scale, "unit": "share"},
        "valuation_input": {"method": "market_quote"}, "liquidity": {"tier": "t1", "tradable": True},
        "confirmed": True, "notes": "SIMULATED fixture, not user financial memory",
    })
    cash = Position.from_dict({
        "position_id": "sim-cash", "account_id": account.account_id, "display_name": "模拟现金",
        "currency": "CNY", "classification": {"asset_class": "cash", "product_type": "cash"},
        "valuation_input": {"method": "manual_amount", "manual_amount": 1048 + 350 * share_scale, "as_of": "2026-10-09"},
        "liquidity": {"tier": "cash", "tradable": True}, "confirmed": True,
        "notes": "SIMULATED fixture, not user financial memory",
    })
    return {"schema_version": 2, "base_currency": "CNY", "accounts": [account.to_dict()],
            "positions": [stock.to_dict(), cash.to_dict()]}


def sample_facts(*, share_scale=1):
    account = "simulation-broker"
    base = {"schema_version": 1, "account_id": account, "source": "SIMULATED explicit statement",
            "cash_basis": "settled", "position_basis": "trade_date", "scope": "complete_account"}
    opening = {**base, "id": "opening", "as_of": "2026-10-01T00:00:00Z",
               "cash": [{"currency": "CNY", "balance": "1000", "available_balance": "1000"}],
               "positions": [{"instrument_id": "a:518880", "unit": "share", "quantity": str(100 * share_scale), "available_quantity": str(100 * share_scale)}]}
    closing = {**base, "id": "closing", "as_of": "2026-10-09T00:00:00Z",
               "cash": [{"currency": "CNY", "balance": str(1048 + 350 * share_scale), "available_balance": str(1048 + 350 * share_scale)}],
               "positions": [{"instrument_id": "a:518880", "unit": "share", "quantity": str(70 * share_scale), "available_quantity": str(70 * share_scale)}]}
    fills = [{"schema_version": 3, "record_origin": "manual", "source": "SIMULATED broker execution",
              "id": f"fill-{i}", "account_id": account, "status": "executed", "side": "sell",
              "target": "a:518880", "unit": "share", "quantity": qty * share_scale, "price": price,
              "currency": "CNY", "fee_amount": 1, "fee_currency": "CNY", "fee_booking": "cash",
              "executed_at": f"2026-10-0{i+1}T07:00:00Z", "settled_at": f"2026-10-0{i+2}T07:00:00Z"}
             for i, qty, price in ((1, 10, 11), (2, 20, 12))]
    movement = {"schema_version": 1, "id": "deposit", "account_id": account, "currency": "CNY",
                "amount": "50", "kind": "deposit", "effective_at": "2026-10-06T00:00:00Z",
                "source": "SIMULATED independent bank deposit"}
    mandate = {"goals": "模拟场景：长期积累，保留生活现金", "horizon": "三年",
               "loss_capacity": {"amount": 500, "currency": "CNY"},
               "cash_needs": [{"account_id": account, "currency": "CNY", "amount": 500,
                               "needed_at": "2027-01-01T00:00:00Z"}],
               "operation_frequency": "每周复核", "style_preferences": ["模拟偏好：分批操作"]}
    return opening, closing, fills, movement, mandate


def run_smoke(output, *, share_scale=1):
    if type(share_scale) is not int or share_scale < 1:
        raise ValueError("share_scale must be a positive integer")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    adapter = _isolated_adapter(output)
    doc = fixture_document(share_scale=share_scale)
    (output / ".local" / "financial_assets.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    adapter.engine._assets = adapter.engine._load_assets_from_file()
    opening, closing, fills, movement, mandate = sample_facts(share_scale=share_scale)
    # This fixture explicitly asserts the simulated closing facts as of a
    # recent time. It is not a timestamp refresh of any real broker statement.
    closing["as_of"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    requests = [("profile_update", {"profile": {"mandate": mandate}}),
                ("constraints_replace", {"constraints": {}}),
                ("statement_save", {"statement": opening}),
                *[("execution_save", {"execution": fill}) for fill in fills],
                ("cash_movement_save", {"movement": movement}),
                ("statement_save", {"statement": closing})]
    before = _files_hash(output / ".local")
    for method, params in requests:
        denied = adapter.handle_request({"method": method, "params": params})
        if denied.get("success"):
            raise RuntimeError("unconfirmed financial write accepted")
    if before != _files_hash(output / ".local"):
        raise RuntimeError("unconfirmed writes changed files")
    for method, params in requests:
        _call(adapter, method, {**params, "confirmed": True})
    before = _files_hash(output / ".local")
    report = _call(adapter, "financial_reconcile", {"opening_id": "opening", "closing_id": "closing"})
    integrity = _call(adapter, "financial_integrity", {})
    if report["status"] != "matched" or integrity["status"] != "matched":
        raise RuntimeError(f"reconciliation incomplete: {report} {integrity}")
    expected_cash = str(1000 + (10 * 11 + 20 * 12) * share_scale - 2 + 50)
    if report["cash_by_currency"]["CNY"]["expected"] != expected_cash:
        raise RuntimeError("independent arithmetic mismatch: 1000 + 110 - 1 + 240 - 1 + 50")
    if before != _files_hash(output / ".local"):
        raise RuntimeError("read-only review changed memory")
    # Profile update has an intentional updated_at; immutable financial events do not.
    for method, params in requests[1:]:
        _call(adapter, method, {**params, "confirmed": True})
    if before != _files_hash(output / ".local"):
        raise RuntimeError("duplicate financial events changed files")
    for name, value in (("reconciliation.json", report), ("financial-integrity.json", integrity),
                        ("personal-mandate.json", _call(adapter, "personal_mandate", {}))):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"status": "ok", "real_user_records": False, "real_market_prices": False,
               "origin": "simulated_financial_facts", "unconfirmed_writes": 0,
               "duplicate_events_added": 0, "read_only": True, "closing_quantity": str(70 * share_scale),
               "closing_cash_CNY": expected_cash, "scope": "record_consistency_not_broker_verification",
               "output_dir": str(output)}
    (output / "smoke-result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    repo = Path(__file__).resolve().parents[1]
    if output.exists() or repo == output or repo in output.parents:
        parser.error("use a new output directory outside the repository")
    print(json.dumps(run_smoke(output), ensure_ascii=False))


if __name__ == "__main__":
    main()
