"""Run one end-to-end shadow advisory pipeline without touching production.

Usage:
    .venv/bin/python scripts/run_shadow_advisory.py --session cn_after_close

The script auto-detects the latest AnalysisContext (context.json) and report
artifact in .local/scheduled_runs/latest/.

Steps:
1. Loads the AnalysisContext;
2. Builds a UnifiedAnalysisSnapshot;
3. Synthesizes an InvestmentAdvisory via the LLM analyst;
4. Validates the advisory;
5. Saves to .local/advisory_shadow/;
6. Optionally compares with the production report artifact.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from stocks.domain.models import AnalysisContext
from stocks.engine.advisory_contract import validate_advisory
from stocks.engine.advisory_shadow_store import AdvisoryShadowStore
from stocks.engine.advisory_synthesizer import synthesize_advisory
from stocks.engine.unified_snapshot import build_unified_snapshot
from stocks.logging_utils import get_logger

logger = get_logger("run_shadow_advisory")

_ARTIFACT_DIR = Path(".local/scheduled_runs/latest")
_CONTEXT_FILE = _ARTIFACT_DIR / "context.json"


def _resolve_context_path() -> Path:
    if _CONTEXT_FILE.exists():
        return _CONTEXT_FILE
    raise FileNotFoundError(
        f"No context file found at {_CONTEXT_FILE}. "
        "Wait for the next scheduled run to generate it, "
        "or pass --context explicitly."
    )


def _resolve_report_path(session: str) -> Path:
    report = _ARTIFACT_DIR / f"{session}.json"
    if report.exists():
        return report
    raise FileNotFoundError(f"No report artifact found: {report}")


def _load_context(path: str) -> AnalysisContext:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return AnalysisContext.from_dict(data)


def _make_llm_client() -> Any | None:
    try:
        from stocks.providers.openai_client import get_llm_client
        return get_llm_client()
    except Exception:
        logger.info("LLM client not available; synthesizer will use fallback")
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Run end-to-end shadow advisory pipeline")
    parser.add_argument(
        "--session", required=True,
        help="Session label, e.g. cn_after_close or us_after_close"
    )
    parser.add_argument(
        "--context",
        help="Path to AnalysisContext JSON (auto-detected if omitted)"
    )
    parser.add_argument(
        "--market", default="cn",
        help="Market scope, e.g. cn or us"
    )
    parser.add_argument(
        "--compare", action="store_true",
        help="Compare shadow advisory with production report artifact"
    )
    parser.add_argument(
        "--no-llm", action="store_true",
        help="Skip LLM and use fallback hold advisory"
    )
    args = parser.parse_args()

    try:
        if args.context:
            context_path = Path(args.context)
        else:
            context_path = _resolve_context_path()
    except FileNotFoundError as e:
        print(json.dumps({"status": "error", "reason": str(e)}, ensure_ascii=False))
        return 1

    context = _load_context(str(context_path))
    logger.info(
        "loaded context: %d assets, %d quotes, %d valuations",
        len(context.assets),
        sum(len(v) for v in context.quotes.values()),
        len(context.position_valuations),
    )

    snapshot = build_unified_snapshot(
        context,
        trigger="shadow",
        session=args.session,
        market_scope=args.market,
    )
    logger.info(
        "snapshot built: %d facts",
        len(snapshot.all_facts()),
    )

    llm_client = None if args.no_llm else _make_llm_client()
    advisory = synthesize_advisory(snapshot, llm_client=llm_client)
    receipt = validate_advisory(
        advisory,
        snapshot_hash=snapshot.snapshot_id,
        prompt_contract_hash="advisory_synthesizer:v1",
    )

    store = AdvisoryShadowStore()
    run_id = f"{args.session}-{snapshot.generated_at.replace(':', '').replace('+', 'z')[:17]}"

    report_path = None
    try:
        report_path = _resolve_report_path(args.session)
    except FileNotFoundError:
        pass

    manifest = store.save(
        run_id,
        snapshot,
        advisory,
        receipt,
        production_decision_id=args.session,
        production_artifact_path=str(report_path.resolve()) if report_path else "",
    )

    result = {
        "status": "ok",
        "run_id": run_id,
        "snapshot_id": snapshot.snapshot_id,
        "advisory_id": advisory.advisory_id,
        "receipt_status": receipt.status,
        "shadow_dir": str(Path(".local/advisory_shadow") / run_id),
        "actions": len(advisory.actions),
        "hold_decisions": len(advisory.hold_decisions) if advisory.hold_decisions else 0,
        "scenarios": len(advisory.scenarios),
        "do_not_do": len(advisory.do_not_do),
        "data_limitations": len(advisory.data_limitations),
        "manifest": manifest,
    }

    if args.compare and report_path:
        from scripts.compare_advisory_paths import _compare, _load_production_user_view
        try:
            production = _load_production_user_view(args.session, str(report_path))
            comparison = _compare(run_id, store, production)
            result["comparison"] = comparison
        except Exception as e:
            result["comparison"] = {"status": "error", "reason": str(e)}

    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0 if receipt.status in {"ok", "warnings"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
