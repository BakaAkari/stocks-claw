"""A1 asset intake service: NL draft → token confirmation → audited v2 write.

This module is the translation layer between the SHADOW `AssetIntakeDraft`
library and the v2 financial-memory file.  Drafts never write; applying a
draft requires the token issued at draft time and an untouched memory file
(enforced by `AssetIntakeWriter`).  All writes go through the v2 file with
a timestamped backup, and every new/updated position is validated with
`Position.from_dict` / `Account.from_dict` before persisting.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from stocks.domain.advisory_models import AssetIntakeDraft
from stocks.domain.models import Account, Position
from stocks.engine.advisory_mainline import resolve_mainline_llm_client
from stocks.engine.asset_intake_parser import parse_asset_intake
from stocks.engine.asset_intake_writer import AssetIntakeWriter
from stocks.engine.llm_asset_intake import parse_llm_asset_intake
from stocks.logging_utils import get_logger

logger = get_logger("asset_intake_service")

_CURRENCY_BY_PREFIX = {"a": "CNY", "hk": "HKD", "us": "USD", "crypto": "USD"}
_PRODUCT_TYPE_BY_PREFIX = {"a": "stock", "hk": "stock", "us": "stock", "crypto": "manual_asset"}
_CASH_ASSET_CLASSES = {"cash", "cash_equivalent"}
_CASH_PRODUCT_TYPES = {"cash", "cash_equivalent", "money_market_fund"}


class IntakeRejected(Exception):
    """A draft change cannot be translated into a safe v2 edit."""


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _backup_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _asset_path(engine: Any) -> Path:
    return engine._asset_file_path()  # noqa: SLF001 — same package family


def _load_asset_doc(engine: Any) -> dict:
    path = _asset_path(engine)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def intake_memory_hash(engine: Any) -> str:
    """Stable hash of the current v2 assets document (canonical JSON)."""
    doc = _load_asset_doc(engine)
    canonical = json.dumps(doc, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _memory_context_block(engine: Any) -> str:
    """Compact reference list of current accounts/positions for the LLM prompt.

    Ids and classifications only — enough for the LLM to reference real
    account_id / position_id values, not a data dump.
    """
    doc = _load_asset_doc(engine)
    lines = ["Current financial memory (reference ids; do not restate as facts):"]
    for account in doc.get("accounts") or []:
        lines.append(
            f"- account {account.get('account_id')}: {account.get('display_name')}"
            f" ({account.get('institution_type')}, {account.get('base_currency')})"
        )
    for position in doc.get("positions") or []:
        instrument = position.get("instrument") or {}
        classification = position.get("classification") or {}
        ikey = instrument.get("instrument_key") or "-"
        lines.append(
            f"- position {position.get('position_id')}: {position.get('display_name')}"
            f" [{ikey}] account={position.get('account_id')}"
            f" ccy={position.get('currency')} class={classification.get('asset_class')}"
            f"/{classification.get('product_type')}"
        )
    return "\n".join(lines)


def _draft_to_json(draft: AssetIntakeDraft) -> dict:
    return json.loads(json.dumps(asdict(draft), ensure_ascii=False, default=str))


def build_intake_draft(
    engine: Any,
    text: str,
    *,
    llm_client: Any = "auto",
) -> dict:
    """Build a confirmation draft from natural language.  Never writes.

    Returns {draft, confirmation_token, ambiguities, used_llm, base_memory_hash}.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("intake text must be a non-empty string")
    base_hash = intake_memory_hash(engine)

    client = llm_client
    if llm_client == "auto":
        client = resolve_mainline_llm_client(getattr(engine, "_config", None) or {})

    if client is not None:
        context_block = _memory_context_block(engine)
        prompted_text = f"{context_block}\n\nUser message: {text.strip()}"
        draft = parse_llm_asset_intake(
            prompted_text, llm_client=client, base_memory_hash=base_hash,
        )
        used_llm = True
    else:
        draft = parse_asset_intake(text.strip(), base_memory_hash=base_hash)
        used_llm = False

    writer = AssetIntakeWriter(
        get_memory_hash=lambda: intake_memory_hash(engine),
        apply_change=lambda change: None,  # token generation only; no apply here
    )
    token = writer.generate_token(draft)
    return {
        "success": True,
        "draft": _draft_to_json(draft),
        "confirmation_token": token,
        "ambiguities": [dict(a) for a in draft.ambiguities],
        "used_llm": used_llm,
        "base_memory_hash": base_hash,
        "will_write": False,
        "confirm_with": "--asset-intake-confirm --draft-json '<draft>' --token '<token>'",
    }


# ── v2 translation helpers ────────────────────────────────────────────────


def _slugify(value: str) -> str:
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", value.strip().lower()).strip("_")
    return slug or "position"


def _position_id_for(item: dict, existing_ids: set[str]) -> str:
    explicit = str(item.get("position_id") or "").strip()
    if explicit:
        pid = explicit
    else:
        ikey = str(item.get("instrument_key") or "").strip()
        if ikey and ":" in ikey:
            prefix, code = ikey.split(":", 1)
            pid = f"{_slugify(prefix)}_{_slugify(code)}"
        else:
            pid = _slugify(str(item.get("display_name") or item.get("name") or ""))
    if pid in existing_ids:
        raise IntakeRejected(f"position_id '{pid}' 已存在，应使用 update 而非 add")
    return pid


def _resolve_account_id(item: dict, accounts: list[dict], currency: str) -> str:
    explicit = str(item.get("account_id") or "").strip()
    known = {a.get("account_id") for a in accounts}
    if explicit:
        if explicit not in known:
            raise IntakeRejected(f"account_id '{explicit}' 不在现有账户中")
        return explicit
    candidates = [
        a for a in accounts
        if str(a.get("base_currency") or "").upper() == currency.upper()
    ]
    if len(candidates) == 1:
        return str(candidates[0].get("account_id"))
    raise IntakeRejected(
        f"无法唯一确定入账账户（币种 {currency} 匹配到 {len(candidates)} 个账户），"
        "请在草稿中显式给出 account_id"
    )


def _build_position_dict(item: dict, accounts: list[dict], existing_ids: set[str]) -> dict:
    ikey = str(item.get("instrument_key") or "").strip()
    prefix = ikey.split(":", 1)[0] if ":" in ikey else ""
    currency = str(item.get("currency") or _CURRENCY_BY_PREFIX.get(prefix, "CNY")).upper()
    display_name = str(item.get("display_name") or item.get("name") or ikey or "").strip()
    if not display_name:
        raise IntakeRejected("add_position 缺少 display_name / instrument_key")

    quantity = item.get("quantity")
    amount = item.get("amount", item.get("amount_cny"))
    unit_cost = item.get("cost_basis", item.get("unit_cost"))
    if quantity is None and amount is None:
        raise IntakeRejected(f"{display_name}: 缺少 quantity 或 amount")

    is_cash = str(item.get("asset_class") or "") in _CASH_ASSET_CLASSES
    classification = {
        "asset_class": item.get("asset_class") or ("equity" if ikey else "unknown"),
        "product_type": item.get("product_type")
        or ("cash" if is_cash else _PRODUCT_TYPE_BY_PREFIX.get(prefix, "manual_asset")),
        "subtype": item.get("subtype") or display_name,
        "exposure_tags": list(item.get("exposure_tags") or []),
    }
    if ikey:
        valuation_input = {"method": "market_quote", "manual_amount": None, "as_of": None}
        liquidity = {"tradable": True, "rebalance_eligible": True, "tier": "t1"}
    else:
        valuation_input = {
            "method": "manual_amount",
            "manual_amount": float(amount) if amount is not None else None,
            "as_of": _today(),
        }
        liquidity = {
            "tradable": True,
            "rebalance_eligible": True,
            "tier": "cash" if is_cash else "unknown",
        }

    holding = None
    if quantity is not None:
        cost_basis = None
        if unit_cost is not None:
            cost_basis = {
                "method": "average",
                "unit_cost": float(unit_cost),
                "cost_amount": round(float(unit_cost) * float(quantity), 2),
                "currency": currency,
            }
        elif amount is not None:
            cost_basis = {
                "method": "average",
                "unit_cost": None,
                "cost_amount": float(amount),
                "currency": currency,
            }
        holding = {"quantity": float(quantity), "unit": "share", "cost_basis": cost_basis}

    position = {
        "position_id": _position_id_for(item, existing_ids),
        "account_id": _resolve_account_id(item, accounts, currency),
        "display_name": display_name,
        "currency": currency,
        "classification": classification,
        "valuation_input": valuation_input,
        "liquidity": liquidity,
        "instrument": (
            {
                "instrument_key": ikey,
                "ticker": ikey.split(":", 1)[1].upper(),
                "source_market": prefix.upper(),
            }
            if ikey
            else None
        ),
        "holding": holding,
        "role": item.get("role"),
        "reported_performance": None,
        "data_completeness": {"source": "nl_intake", "confidence": item.get("confidence", "low")},
        "confirmed": True,
        "notes": item.get("notes"),
    }
    # Validate before it ever touches the file.
    return Position.from_dict(position).to_storage_dict()


def _find_position(doc: dict, item: dict) -> Optional[dict]:
    pid = str(item.get("position_id") or "").strip()
    ikey = str(item.get("instrument_key") or "").strip()
    for position in doc.get("positions") or []:
        if pid and position.get("position_id") == pid:
            return position
        if ikey and (position.get("instrument") or {}).get("instrument_key") == ikey:
            return position
    return None


def _is_cash_position(position: dict) -> bool:
    classification = position.get("classification") or {}
    return (
        classification.get("asset_class") in _CASH_ASSET_CLASSES
        or classification.get("product_type") in _CASH_PRODUCT_TYPES
    )


def _apply_position_update(doc: dict, item: dict) -> str:
    position = _find_position(doc, item)
    if position is None:
        raise IntakeRejected(
            f"update_position 未找到持仓: {item.get('position_id') or item.get('instrument_key')}"
        )
    delta_amount = item.get("delta_amount", item.get("delta_amount_cny"))
    if delta_amount is not None:
        if not _is_cash_position(position):
            raise IntakeRejected(
                f"{position.get('position_id')}: delta_amount 仅支持现金类持仓"
            )
        valuation = position.setdefault("valuation_input", {})
        current = float(valuation.get("manual_amount") or 0.0)
        new_amount = round(current + float(delta_amount), 2)
        if new_amount < 0:
            raise IntakeRejected(
                f"{position.get('position_id')}: delta_amount {float(delta_amount)} "
                f"导致现金为负（当前 {current}），请核实金额"
            )
        valuation["method"] = "manual_amount"
        valuation["manual_amount"] = new_amount
        valuation["as_of"] = _today()

    delta_quantity = item.get("delta_quantity")
    if delta_quantity is not None:
        holding = position.get("holding")
        if not holding:
            raise IntakeRejected(f"{position.get('position_id')}: 无 holding，无法应用 delta_quantity")
        new_quantity = float(holding.get("quantity") or 0.0) + float(delta_quantity)
        if new_quantity < 0:
            raise IntakeRejected(f"{position.get('position_id')}: delta_quantity 导致数量为负")
        holding["quantity"] = round(new_quantity, 6)

    if item.get("quantity") is not None:
        holding = position.setdefault("holding", {"quantity": 0.0, "unit": "share", "cost_basis": None})
        holding["quantity"] = float(item["quantity"])

    unit_cost = item.get("cost_basis", item.get("unit_cost"))
    if unit_cost is not None:
        holding = position.get("holding")
        if not holding:
            raise IntakeRejected(f"{position.get('position_id')}: 无 holding，无法设置成本")
        quantity = float(holding.get("quantity") or 0.0)
        holding["cost_basis"] = {
            "method": "average",
            "unit_cost": float(unit_cost),
            "cost_amount": round(float(unit_cost) * quantity, 2),
            "currency": position.get("currency") or "CNY",
        }

    if item.get("notes"):
        position["notes"] = str(item["notes"])

    # Re-validate the merged position.
    validated = Position.from_dict(position).to_storage_dict()
    position.clear()
    position.update(validated)
    return str(position.get("position_id"))


# ── Apply path ────────────────────────────────────────────────────────────


def _rehydrate_draft(draft_dict: dict) -> AssetIntakeDraft:
    if not isinstance(draft_dict, dict):
        raise ValueError("draft must be a JSON object")
    data = dict(draft_dict)
    data.setdefault("accounts_to_add", ())
    data.setdefault("positions_to_add", ())
    data.setdefault("positions_to_update", ())
    data.setdefault("positions_to_remove", ())
    data.setdefault("profile_updates", ())
    data.setdefault("ambiguities", ())
    data.setdefault("source_quotes", ())
    data.setdefault("draft_hash", "")
    data["requires_confirmation"] = True
    for required in ("draft_id", "base_memory_hash", "generated_at"):
        if not data.get(required):
            raise ValueError(f"draft missing required field: {required}")
    return AssetIntakeDraft(**data)


def apply_intake_draft(engine: Any, draft_dict: dict, token: str) -> dict:
    """Validate the token and apply the draft to the v2 assets file.

    Returns the AssetIntakeWriter result plus write details.  Rejections
    never touch the file.
    """
    if getattr(engine, "_asset_schema_version", None) != 2:
        return {
            "success": False,
            "status": "rejected",
            "reason": "financial_assets.json 不是 schema_version=2；"
                      "请先运行 --asset-migrate-v2 --confirmed",
        }

    try:
        draft = _rehydrate_draft(draft_dict)
    except (TypeError, ValueError) as exc:
        return {"success": False, "status": "rejected", "reason": f"invalid draft: {exc}"}

    doc = _load_asset_doc(engine)
    if doc.get("schema_version") != 2:
        return {
            "success": False,
            "status": "rejected",
            "reason": "financial_assets.json 不是 schema_version=2；"
                      "请先运行 --asset-migrate-v2 --confirmed",
        }
    work = json.loads(json.dumps(doc))  # deep copy for staging
    staged: list[dict[str, Any]] = []

    def apply_change(change: dict[str, Any]) -> None:
        change_type = change.get("type")
        value = change.get("value") or {}
        if change_type == "add_position":
            accounts = work.get("accounts") or []
            existing_ids = {p.get("position_id") for p in work.get("positions") or []}
            position = _build_position_dict(value, accounts, existing_ids)
            work.setdefault("positions", []).append(position)
            staged.append({"type": change_type, "position_id": position["position_id"]})
        elif change_type == "update_position":
            pid = _apply_position_update(work, value)
            staged.append({"type": change_type, "position_id": pid})
        elif change_type == "remove_position":
            pid = str(value.get("position_id") or "").strip()
            positions = work.get("positions") or []
            remaining = [p for p in positions if p.get("position_id") != pid]
            if len(remaining) == len(positions):
                raise IntakeRejected(f"remove_position 未找到持仓: {pid}")
            work["positions"] = remaining
            staged.append({"type": change_type, "position_id": pid})
        elif change_type == "add_account":
            account = Account.from_dict(value).to_dict()
            known = {a.get("account_id") for a in work.get("accounts") or []}
            if account["account_id"] in known:
                raise IntakeRejected(f"account_id '{account['account_id']}' 已存在")
            work.setdefault("accounts", []).append(account)
            staged.append({"type": change_type, "account_id": account["account_id"]})
        elif change_type == "update_profile":
            staged.append({"type": change_type, "value": dict(value)})
        else:
            raise IntakeRejected(f"unsupported change type: {change_type}")

    writer = AssetIntakeWriter(
        get_memory_hash=lambda: intake_memory_hash(engine),
        apply_change=apply_change,
    )
    try:
        result = writer.apply(draft, token)
    except IntakeRejected as exc:
        return {"success": False, "status": "rejected", "reason": str(exc)}
    except (ValueError, TypeError) as exc:
        # Model-level validation failure (e.g. negative amounts): reject
        # cleanly; staged edits are never persisted.
        return {
            "success": False,
            "status": "rejected",
            "reason": f"draft change failed validation: {exc}",
        }
    if result.get("status") != "applied":
        return {"success": False, **result}

    # Persist: timestamped backup + atomic-ish write, then reload engine state.
    path = _asset_path(engine)
    backup_path = path.with_name(f"financial_assets.intake-{_backup_stamp()}.bak.json")
    shutil.copy2(path, backup_path)
    path.write_text(json.dumps(work, ensure_ascii=False, indent=2), "utf-8")

    for change in staged:
        if change["type"] == "update_profile":
            engine.update_profile(change["value"])
    engine._assets = engine._load_assets_from_file()  # noqa: SLF001

    return {
        "success": True,
        **result,
        "staged": staged,
        "written_path": str(path),
        "backup_path": str(backup_path),
        "new_memory_hash": intake_memory_hash(engine),
    }
