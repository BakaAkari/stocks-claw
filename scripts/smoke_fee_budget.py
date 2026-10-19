"""Synthetic confirmed fee budget through the real scheduled proposal/report path."""
import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.advisory_artifact import validate_advisory_artifact
from scripts.build_push_payload import (
    build_push_payload,
    render_push_payload,
    validate_payload_text,
    validate_push_truth,
)
from scripts.smoke_advisory_lineage import SimulatedAnalyst, _private_io_guard, _produce
from scripts.smoke_financial_reconciliation import _files_hash, _isolated_adapter
from scripts.smoke_financial_reconciliation import run_smoke as finance_smoke


class SimulatedBuyer(SimulatedAnalyst):
    def complete(self, prompt):
        data = json.loads(super().complete(prompt))
        data["portfolio_assessment"] = "模拟加仓，仅验证费用和现金门禁"
        data["actions"][0].update({"action": "add", "action_id": "simulated-add-100",
                                   "reasoning": "模拟加仓100股，非真实建议"})
        self.response = json.dumps(data, ensure_ascii=False)
        return self.response


def run_smoke(output):
    output = Path(output)
    with _private_io_guard():
        finance_smoke(output, share_scale=10)
        adapter = _isolated_adapter(output)
        now = datetime.now(timezone.utc)
        document = {"schema_version": 1, "budgets": [{
            "id": "synthetic-fee-budget", "account_id": "simulation-broker", "instrument_id": "a:518880",
            "market": "a", "currency": "CNY", "valid_from": (now - timedelta(days=1)).isoformat(),
            "valid_until": (now + timedelta(days=1)).isoformat(), "max_quantity": "100",
            "max_notional": "1000", "fee_upper_bound": "5", "source": "SIMULATED all-in reserve"}]}
        before = _files_hash(output / ".local")
        adapter.engine.fee_budget_preview(document)
        denied = adapter.handle_request({"method": "fee_budget_replace", "params": {"document": document}})
        if denied["success"] or before != _files_hash(output / ".local"):
            raise RuntimeError("unconfirmed write or preview modified financial facts")
        outcomes = {}
        for label, fee in (("covered", "5"), ("insufficient_cash", "10000"), ("revoked", None)):
            document["budgets"][0]["fee_upper_bound"] = fee or "5"
            value = document if fee is not None else {"schema_version": 1, "budgets": []}
            saved = adapter.handle_request({"method": "fee_budget_replace", "params": {"document": value, "confirmed": True}})
            if not saved["success"]:
                raise RuntimeError(str(saved))
            before = _files_hash(output / ".local")
            with patch("scripts.smoke_advisory_lineage.SimulatedAnalyst", SimulatedBuyer):
                artifact, _ = asyncio.run(_produce(adapter, output / label))
            validate_advisory_artifact(artifact, require_primary=True)
            payload = build_push_payload(artifact, now=artifact["generated_at"], require_lineage=True)
            errors = validate_push_truth(payload) + validate_payload_text(payload, render_push_payload(payload))
            if errors:
                raise RuntimeError(str(errors))
            # Context may write analysis caches; confirmed financial documents must not change.
            after = _files_hash(output / ".local")
            protected = ("fee_budgets", "confirmation_events", "financial_assets", "investor_profile",
                         "portfolio_constraints", "account_statements", "cash_movements", "executions", "advice", "forecasts")
            def select(rows):
                return {k: v for k, v in rows.items() if any(token in k for token in protected)}
            if select(before) != select(after):
                raise RuntimeError("analysis modified financial facts")
            decision = artifact["portfolio_decision"]
            if bool(decision["approved_actions"]) != (label == "covered"):
                raise RuntimeError(f"unexpected {label}: {decision}")
            if label == "covered":
                if "费用预留上界 CNY 5" not in render_push_payload(payload):
                    raise RuntimeError("fee reserve missing from user report")
                try:
                    build_push_payload(artifact, now=document["budgets"][0]["valid_until"],
                                       max_age_min=None, require_lineage=True)
                except ValueError as exc:
                    if "fee budget" not in str(exc):
                        raise
                else:
                    raise RuntimeError("expired fee budget was delivered")
            outcomes[label] = decision["status"]
        result = {"status": "ok", "real_user_records": False, "real_llm_call": False,
                  "real_market_prices": False, "unconfirmed_writes": 0,
                  "financial_memory_unchanged_by_analysis": True, "outcomes": outcomes,
                  "deterministic_delivery_validated": True, "output_dir": str(output)}
        (output / "fee-budget-smoke.json").write_text(json.dumps(result, indent=2))
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    output = parser.parse_args().output_dir.resolve()
    repo = Path(__file__).resolve().parents[1]
    if output.exists() or output == repo or repo in output.parents:
        parser.error("use a new directory outside the repository")
    print(json.dumps(run_smoke(output)))


if __name__ == "__main__":
    main()
