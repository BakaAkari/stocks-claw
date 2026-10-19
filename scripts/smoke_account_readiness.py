"""Isolated account preview/readiness replay; no real accounts or external IO."""
import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.smoke_advisory_lineage import _private_io_guard
from scripts.smoke_financial_reconciliation import (
    _files_hash,
    _isolated_adapter,
    sample_facts,
)
from scripts.smoke_financial_reconciliation import (
    run_smoke as finance_smoke,
)


def run_smoke(output):
    output = Path(output)
    with _private_io_guard():
        finance_smoke(output)
        adapter = _isolated_adapter(output)
        now = datetime.now(timezone.utc)
        before = _files_hash(output / ".local")

        def call(method, params):
            result = adapter.handle_request({"method": method, "params": params})
            if not result.get("success"):
                raise RuntimeError(f"{method} failed: {result}")
            return result["data"]

        opening, closing, fills, movement, _ = sample_facts()
        bundle = {"schema_version": 1, "account_id": opening["account_id"], "market": "a",
                  "opening_statement": opening, "closing_statement": closing,
                  "executions": fills, "cash_movements": [movement]}
        previews = {}
        for market in ("a", "us"):
            proposed = deepcopy(bundle)
            if market == "us":
                proposed = json.loads(json.dumps(proposed).replace("a:518880", "us:SPY").replace("CNY", "USD"))
                proposed["market"] = "us"
            preview = call("financial_intake_preview", {"bundle": proposed, "as_of": now.isoformat()})
            if preview["status"] != "ready_for_user_review" or preview["advisory_ready"]:
                raise RuntimeError(f"preview boundary failed: {preview}")
            previews[market] = preview
        current = call("financial_readiness", {"as_of": now.isoformat()})
        stale = call("financial_readiness", {"as_of": (now + timedelta(days=2)).isoformat()})
        if current["status"] != "current_facts_available" or stale["status"] != "review_required":
            raise RuntimeError(f"fresh/stale readiness boundary failed: {current}, {stale}")
        bad = deepcopy(bundle)
        bad["cash_movements"][0]["account_id"] = "different-account"
        rejected = call("financial_intake_preview", {"bundle": bad, "as_of": now.isoformat()})
        if rejected["status"] != "needs_correction":
            raise RuntimeError("cross-account evidence was accepted")
        if before != _files_hash(output / ".local"):
            raise RuntimeError("preview/readiness modified financial memory")
        summary = {"status": "ok", "real_user_records": False, "real_market_prices": False,
                   "private_io_guard": True, "preview_markets": list(previews),
                   "financial_memory_unchanged": True, "stale_account_blocked": True,
                   "cross_account_rejected": True, "advisory_ready": False,
                   "output_dir": str(output)}
        (output / "account-preview.json").write_text(json.dumps(previews, ensure_ascii=False, indent=2))
        (output / "account-readiness-smoke.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    repo = Path(__file__).resolve().parents[1]
    if output.exists() or output == repo or repo in output.parents:
        parser.error("use a new directory outside the repository")
    print(json.dumps(run_smoke(output), ensure_ascii=False))


if __name__ == "__main__":
    main()
