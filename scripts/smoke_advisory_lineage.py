"""Confirmed simulated finances → production context/LLM gate/run → facts review.

No online LLM or market request. The explicitly injected transports return
simulated quotes and a simulated proposal; all other pipeline code is production.
"""
import argparse
import asyncio
import builtins
import io
import json
import os
import sys
from contextlib import contextmanager
from copy import deepcopy
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
from scripts.smoke_decision_review import _call, _files_hash, _isolated_adapter
from scripts.smoke_financial_reconciliation import run_smoke as finance_smoke
from stocks.domain.models import Instrument, Quote
from stocks.engine.config_loader import load_engine_config
from stocks.engine.evaluation_contract import digest
from stocks.engine.scheduled_analysis import ScheduledAnalysisRunner


class SimulatedAnalyst:
    model = "SIMULATED-response-not-an-online-model"

    def complete(self, prompt):
        self.prompt = prompt
        self.snapshot = json.loads(prompt.split("Snapshot:\n", 1)[1])
        self.response = json.dumps({
            "market_assessment": "模拟模型：只验证责任链，不判断真实市场", "portfolio_assessment": "模拟出售部分现有持仓",
            "actions": [{"action_id": "simulated-sell-100", "target": "sim-stock", "action": "sell",
                         "size": "100", "size_type": "shares", "execute_when": "now",
                         "cancel_when": "用户资金用途或可用数量变化时重新复核", "reasoning": "模拟响应：部分卖出100股供用户决定",
                         "evidence_refs": ["position:sim-stock", "finance:integrity", "constraints:portfolio"]}],
            "next_checkpoints": ["用户决定后核对实际成交"],
            "data_limitations": ["本次所有金融事实、报价和模型返回均为模拟，非投资建议"],
        }, ensure_ascii=False)
        return self.response


async def _produce(adapter, output):
    engine = adapter.engine
    smoke_now = datetime(2026, 10, 16, 7, 30, tzinfo=timezone.utc)

    async def simulated_quotes(instruments):
        del instruments
        return {"a": [Quote(Instrument("518880", "模拟标的", "a"), price=8.5,
                            source="SIMULATED_quote_transport", stale=False, currency="CNY",
                            as_of=(smoke_now - timedelta(seconds=1)).isoformat())]}

    engine.fetcher.fetch_quotes = simulated_quotes
    analyst = SimulatedAnalyst()
    config = deepcopy(engine._config)
    config.update({"artifact_dir": str(output / "scheduled_runs"), "user_timezone": "Asia/Shanghai",
                   "risk_state": {"state_path": str(output / "risk_state.json")},
                   "financial_statement_max_age_seconds": 86400,
                   "execution_rules": deepcopy(load_engine_config()["execution_rules"]),
                   "markets": {"cn": {"enabled": True, "exchange_timezone": "Asia/Shanghai", "holidays": [],
                                       "sessions": [{"id": "cn_after_close", "time": "15:30",
                                                     "intent": "after_close_review", "push": "normal"}]}}})
    config["llm"]["advisory_mainline"] = {"enabled": True}
    runner = ScheduledAnalysisRunner(engine, config=config, artifact_dir=config["artifact_dir"],
                                     repo_root=output, advisory_client=analyst)
    result = await runner.run_session("cn_after_close", now=smoke_now)
    if not result.get("paths"):
        raise RuntimeError(f"scheduled production did not run: {result}")
    artifact = json.loads(Path(result["paths"]["json_path"]).read_text())
    return artifact, analyst


@contextmanager
def _private_io_guard():
    """A standalone smoke must fail before reading/writing real private state."""
    repo = Path(__file__).resolve().parents[1]
    roots = (repo / ".local", repo / ".secret", Path("/opt/data/.secret"))
    files = (repo / "stocks/data/financial_assets.json", repo / "stocks/config/investor_profile.json")
    def guarded(original):
        def call(file, *args, **kwargs):
            if isinstance(file, (str, bytes, os.PathLike)):
                path = Path(os.fsdecode(file)).resolve()
                if path in files or any(path == base or base in path.parents for base in roots):
                    raise RuntimeError(f"smoke private IO blocked: {path}")
            return original(file, *args, **kwargs)
        return call
    with patch.object(builtins, "open", guarded(builtins.open)), \
         patch.object(io, "open", guarded(io.open)), patch.object(os, "open", guarded(os.open)):
        yield


def run_smoke(output):
    with _private_io_guard():
        return _run_smoke(output)


def _run_smoke(output):
    output = Path(output)
    finance = finance_smoke(output, share_scale=10)
    adapter = _isolated_adapter(output)
    artifact, analyst = asyncio.run(_produce(adapter, output))
    validate_advisory_artifact(artifact, require_primary=True)
    payload = build_push_payload(artifact, now=artifact["generated_at"], require_lineage=True)
    report = render_push_payload(payload)
    delivery_errors = validate_push_truth(payload) + validate_payload_text(payload, report)
    if delivery_errors:
        raise RuntimeError(f"simulated report failed delivery validation: {delivery_errors}")
    (output / "advisory-report.txt").write_text(report, encoding="utf-8")
    (output / "advisory-run.json").write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    decision = artifact["portfolio_decision"]
    if decision["status"] != "approved" or len(decision["approved_actions"]) != 1:
        raise RuntimeError(f"simulated proposal did not pass production gate: {decision}")
    action = decision["approved_actions"][0]
    lineage = artifact["advisory_lineage"]
    if (lineage["attempts"][0]["prompt"] != analyst.prompt
            or lineage["attempts"][0]["raw_response"] != analyst.response
            or lineage["snapshot"]["metadata"]["financial_integrity"]["status"] != "matched"):
        raise RuntimeError("model/input lineage incomplete")
    view = decision["user_view"]
    display = view["instruction_card"]["actions"][0]
    for key in ("decision_id", "source_advisory_id", "source_action_id", "validation_receipt_hash", "input_hash"):
        if display.get(key) != action[key]:
            raise RuntimeError(f"display lost source: {key}")
    if display["signal"] != "sell" or lineage["deterministic_display"]["content_hash"] != digest(view):
        raise RuntimeError("display changed model direction or content hash")
    did, aid = action["decision_id"], "advice:" + action["decision_id"]
    now = datetime.now(timezone.utc)
    user_decision = {"id": "simulated-user-accept", "advice_id": aid, "decision_id": did,
                     "status": "accepted", "decided_at": now.isoformat(), "note": "SIMULATED user decision, not Kari"}
    fill = {"schema_version": 3, "record_origin": "advice_linked", "source": "SIMULATED broker fill",
            "id": "simulated-linked-fill", "advice_id": aid, "decision_id": did,
            "user_decision_id": user_decision["id"], "account_id": "simulation-broker",
            "target": "a:518880", "unit": "share", "status": "executed", "side": "sell",
            "quantity": 100, "price": 8.5, "currency": "CNY", "fee_amount": 1, "fee_currency": "CNY",
            "fee_booking": "cash", "executed_at": (now + timedelta(microseconds=1)).isoformat(),
            "settled_at": None, "note": "SIMULATED execution; not a real trade"}
    requests = [("advice_save", {"advice": {"source_decision_id": did}}),
                ("decision_save", {"decision": user_decision}), ("execution_save", {"execution": fill})]
    before = _files_hash(output / ".local")
    for method, params in requests:
        if adapter.handle_request({"method": method, "params": params}).get("success"):
            raise RuntimeError("unconfirmed write accepted")
    if before != _files_hash(output / ".local"):
        raise RuntimeError("unconfirmed write modified financial files")
    for method, params in requests:
        _call(adapter, method, {**params, "confirmed": True})
    before = _files_hash(output / ".local")
    for method, params in requests:
        _call(adapter, method, {**params, "confirmed": True})
    if before != _files_hash(output / ".local"):
        raise RuntimeError("duplicate facts were appended")
    review = _call(adapter, "decision_review", {"decision_id": did})
    row = review["by_decision"][did]
    if row["execution_status"] != "full" or row["cashflow_by_currency"]["CNY"]["cash_flow"] != 849:
        raise RuntimeError(f"review mismatch: {row['execution_status']}, {row['cashflow_by_currency']}")
    if before != _files_hash(output / ".local"):
        raise RuntimeError("read-only review changed financial files")
    (output / "advisory-review.json").write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"status": "ok", "origin": "simulated_transport_and_financial_facts",
               "real_llm_call": False, "real_market_prices": False, "real_user_records": False,
               "finance_smoke": finance, "decision_id": did, "source_advisory_id": action["source_advisory_id"],
               "snapshot_hash": lineage["snapshot_hash"], "validation_receipt_hash": action["validation_receipt_hash"],
               "private_io_guard": True, "prompt_and_raw_preserved": True, "display_linked": True, "unconfirmed_writes": 0,
               "deterministic_delivery_validated": True,
               "duplicates_added": 0, "review_read_only": True, "cashflow_CNY": 849,
               "cashflow_is_profit": False, "future_market_observation": "not_requested",
               "final_llm_report_verified": False, "output_dir": str(output)}
    (output / "advisory-smoke-result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    repo = Path(__file__).resolve().parents[1]
    if output.exists() or repo == output or repo in output.parents:
        parser.error("use a new directory outside the repository")
    print(json.dumps(run_smoke(output), ensure_ascii=False))


if __name__ == "__main__":
    main()
