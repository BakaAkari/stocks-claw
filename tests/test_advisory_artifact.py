"""Delivery uses genuine simulated production artifacts, not hand-made hashes."""
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.advisory_artifact import validate_advisory_artifact
from scripts.build_push_payload import build_push_payload
from scripts.smoke_advisory_lineage import run_smoke
from stocks.engine.evaluation_contract import digest


@pytest.fixture
def artifact(tmp_path):
    run_smoke(tmp_path)
    return json.loads((tmp_path / "advisory-run.json").read_text())


def reseal(artifact):
    lineage = artifact["advisory_lineage"]
    lineage.pop("content_hash", None)
    lineage["content_hash"] = digest(lineage)


def test_genuine_artifact_passes_and_guard_is_read_only(artifact):
    before = deepcopy(artifact)
    payload = build_push_payload(artifact, now=artifact["generated_at"], require_lineage=True)
    assert payload["user_view"]["instruction_card"]["actions"][0]["signal"] == "sell"
    assert artifact == before


@pytest.mark.parametrize("part", ["snapshot", "receipt", "original", "decision", "display", "prompt",
                                  "cards", "missing", "session", "duplicate_display"])
def test_corruption_or_crosswired_inputs_are_blocked(artifact, part):
    lineage = artifact["advisory_lineage"]
    if part == "snapshot":
        lineage["snapshot"]["metadata"]["financial_integrity"]["status"] = "mismatch"
        reseal(artifact)
    elif part == "receipt":
        lineage["validation_receipt"]["common_reasons"] = ["unverified"]
        reseal(artifact)
    elif part == "original":
        lineage["advisory"]["actions"][0]["size"] = "999"
        reseal(artifact)
    elif part == "decision":
        artifact["portfolio_decision"]["approved_actions"][0]["signal"] = "buy"
    elif part == "display":
        artifact["portfolio_decision"]["user_view"]["instruction_card"]["actions"][0]["signal"] = "buy"
    elif part == "prompt":
        lineage["attempts"][0]["prompt"] += " modified"
        reseal(artifact)
    elif part == "cards":
        artifact["action_cards"] = []
    elif part == "missing":
        artifact.pop("advisory_lineage")
        artifact.pop("decision_authority")
    elif part == "session":
        artifact["session"] = "us_after_close"
    else:
        actions = artifact["portfolio_decision"]["user_view"]["instruction_card"]["actions"]
        actions.append(deepcopy(actions[0]))
        lineage["deterministic_display"]["user_view"] = deepcopy(artifact["portfolio_decision"]["user_view"])
        lineage["deterministic_display"]["content_hash"] = digest(lineage["deterministic_display"]["user_view"])
        reseal(artifact)
    with pytest.raises(ValueError, match="advisory integrity"):
        build_push_payload(artifact, now=artifact["generated_at"], require_lineage=True)


@pytest.mark.parametrize("value", [None, [], {"session": "cn_after_close"},
                                    {"session": "cn_after_close", "decision_authority": "rules"}])
def test_malformed_or_legacy_input_fails_cleanly(value):
    with pytest.raises(ValueError, match="advisory integrity"):
        validate_advisory_artifact(value, require_primary=True)


@pytest.mark.parametrize("script", ["run_push_report.py", "run_llm_report.py"])
def test_actual_delivery_entrypoints_render_frozen_proposal(artifact, tmp_path, script):
    root = tmp_path / "latest"
    root.mkdir()
    (root / "cn_after_close.json").write_text(json.dumps(artifact))
    result = subprocess.run([
        sys.executable, str(Path(__file__).parents[1] / "scripts" / script),
        "--session", "cn_after_close", "--artifact-root", str(root),
        "--payload-root", str(tmp_path / "delivery"), "--now", artifact["generated_at"],
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "卖出" in result.stdout
    assert (tmp_path / "delivery" / "cn_after_close.json").exists()
