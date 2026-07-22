"""Integration tests for the end-to-end shadow advisory pipeline.

These tests use a synthetic AnalysisContext to verify the full pipeline:
context -> snapshot -> advisory -> receipt -> shadow store -> comparison.
"""
from __future__ import annotations

import json
from pathlib import Path

from stocks.domain.models import AnalysisContext, MarketState, PortfolioMapping
from stocks.engine.advisory_shadow_store import AdvisoryShadowStore


def _write_context(tmp_path: Path) -> Path:
    context = AnalysisContext(
        generated_at="2026-07-22T10:00:00+00:00",
        assets=[],
        asset_count=0,
        portfolio_constraints={},
        portfolio_profile={"risk_preference": "left_bottom"},
        quotes={},
        news=[],
        news_count=0,
        market_state=MarketState(),
        portfolio_mapping=PortfolioMapping(),
        drift_checks=[],
        recent_snapshots=[],
        raw_prompt_input="test",
    )
    path = tmp_path / "context.json"
    path.write_text(json.dumps(context.to_dict(), ensure_ascii=False), encoding="utf-8")
    return path


class TestRunShadowAdvisory:
    def test_end_to_end_pipeline_saves_shadow_run(self, tmp_path: Path) -> None:
        context_path = _write_context(tmp_path)
        import sys
        sys.argv = [
            "run_shadow_advisory.py",
            "--artifact", str(context_path),
            "--session", "test",
            "--market", "cn",
        ]
        from scripts.run_shadow_advisory import main
        assert main() == 0
        # Verify shadow run was created
        store = AdvisoryShadowStore()
        runs = store.list_runs()
        assert runs
        run_id = runs[-1]
        manifest = store.load_manifest(run_id)
        assert manifest is not None
        assert manifest["receipt_status"] in {"ok", "warnings"}

    def test_pipeline_with_compare(self, tmp_path: Path) -> None:
        context_path = _write_context(tmp_path)
        import sys
        sys.argv = [
            "run_shadow_advisory.py",
            "--artifact", str(context_path),
            "--session", "test",
            "--market", "cn",
            "--compare",
        ]
        from scripts.run_shadow_advisory import main
        assert main() == 0
