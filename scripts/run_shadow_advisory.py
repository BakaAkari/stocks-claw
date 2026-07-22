"""Run one end-to-end shadow advisory pipeline without touching production.

The script assumes the project root is on PYTHONPATH. If invoked directly,
add the project root to sys.path so that `stocks` is importable.

Usage:
    .venv/bin/python scripts/run_shadow_advisory.py --artifact .local/scheduled_runs/latest/cn_after_close.json --session cn_after_close --market cn

The script:
1. Loads a production AnalysisContext artifact;
2. Builds a UnifiedAnalysisSnapshot;
3. Synthesizes an InvestmentAdvisory via the LLM analyst;
4. Validates the advisory and produces a receipt;
5. Saves the shadow run to .local/advisory_shadow/;
6. Optionally compares with the original production artifact.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
import json
from pathlib import Path
from typing import Any

from stocks.domain.models import AnalysisContext
from stocks.engine.advisory_contract import validate_advisory
from stocks.engine.advisory_shadow_store import AdvisoryShadowStore
from stocks.engine.advisory_synthesizer import synthesize_advisory
from stocks.engine.unified_snapshot import build_unified_snapshot
from stocks.logging_utils import get_logger

logger = get_logger("run_shadow_advisory")


def _load_context(path: str) -> AnalysisContext:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return AnalysisContext.from_dict(data)


def _make_llm_client() -> Any | None:
    """Return a minimal LLM client using the existing OpenAI-compatible provider.

    If the provider is not configured, return None and the synthesizer will fall
    back to a safe hold advisory.
    """
    try:
        from stocks.providers.openai_client import get_llm_client
        return get_llm_client()
    except Exception:
        logger.info("LLM client not available; synthesizer will use fallback")
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Run end-to-end shadow advisory pipeline")
    parser.add_argument("--artifact", required=True, help="Path to AnalysisContext JSON artifact")
    parser.add_argument("--session", default="unknown", help="Session label, e.g. cn_after_close")
    parser.add_argument("--market", default="cn", help="Market scope, e.g. cn or us")
    parser.add_argument("--compare", action="store_true", help="Also compare with production artifact")
    args = parser.parse_args()

    artifact_path = Path(args.artifact)
    if not artifact_path.exists():
        print(json.dumps({"status": "error", "reason": f"artifact not found: {args.artifact}"}, ensure_ascii=False))
        return 1

    context = _load_context(args.artifact)
    snapshot = build_unified_snapshot(
        context,
        trigger="shadow",
        session=args.session,
        market_scope=args.market,
    )
    llm_client = _make_llm_client()
    advisory = synthesize_advisory(snapshot, llm_client=llm_client)
    receipt = validate_advisory(
        advisory,
        snapshot_hash=snapshot.snapshot_id,
        prompt_contract_hash="advisory_synthesizer:v1",
    )

    store = AdvisoryShadowStore()
    run_id = f"{args.session}-{snapshot.generated_at.replace(':', '').replace('+', 'z')[:17]}"
    manifest = store.save(
        run_id,
        snapshot,
        advisory,
        receipt,
        production_decision_id=Path(args.artifact).stem,
        production_artifact_path=str(artifact_path.resolve()),
    )

    result = {
        "status": "ok",
        "run_id": run_id,
        "snapshot_id": snapshot.snapshot_id,
        "advisory_id": advisory.advisory_id,
        "receipt_status": receipt.status,
        "shadow_dir": str(Path(".local/advisory_shadow") / run_id),
        "manifest": manifest,
    }

    if args.compare:
        from scripts.compare_advisory_paths import _compare, _load_production_user_view
        production = _load_production_user_view(Path(args.artifact).stem, str(artifact_path))
        comparison = _compare(run_id, store, production)
        result["comparison"] = comparison

    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0 if receipt.status in {"ok", "warnings"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
