"""Separate legacy non-additive account totals from investable positions.

This is a read-time view; the original document remains the confirmation and
storage authority. A snapshot is neither a holding nor a broker statement.
"""

import math
import re
from copy import deepcopy


def split_asset_records(accounts: list[dict], records: list[dict]) -> tuple[list[dict], list[dict]]:
    positions, snapshots = [], []
    for row in records:
        if not isinstance(row, dict):
            raise ValueError("invalid_asset_record")
        classification = row.get("classification") or {}
        if not isinstance(classification, dict):
            raise ValueError("invalid_asset_classification")
        tags = classification.get("exposure_tags", [])
        markers = (
            classification.get("asset_class") == "account_snapshot",
            classification.get("product_type") == "account_snapshot",
            isinstance(tags, list) and "non_additive_snapshot" in tags,
        )
        if not any(markers):
            positions.append(row)
            continue
        if not all(markers):
            raise ValueError("ambiguous_account_snapshot")
        pid = row.get("position_id")
        aid = row.get("account_id")
        if (not isinstance(pid, str) or not pid.strip()
                or sum(r.get("position_id") == pid for r in records if isinstance(r, dict)) != 1
                or not isinstance(aid, str) or not aid.strip()
                or sum(a.get("account_id") == aid for a in accounts) != 1):
            raise ValueError("invalid_account_snapshot_identity")
        currency = row.get("currency")
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError("invalid_account_snapshot_currency")
        value = row.get("valuation_input")
        if not isinstance(value, dict) or value.get("method") != "manual_amount":
            raise ValueError("invalid_account_snapshot_valuation")
        amount = value.get("manual_amount")
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount):
            raise ValueError("invalid_account_snapshot_amount")
        liquidity = row.get("liquidity")
        if (row.get("instrument") is not None or row.get("holding") is not None
                or not isinstance(liquidity, dict)
                or liquidity.get("tradable") is not False
                or liquidity.get("rebalance_eligible") is not False):
            raise ValueError("account_snapshot_must_not_be_tradable")
        snapshots.append(deepcopy(row))
    return positions, snapshots
