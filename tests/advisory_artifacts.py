"""Explicitly simulated artifacts for delivery-boundary tests."""
from copy import deepcopy

from stocks.engine.advisory_lineage import empty_decision
from stocks.engine.evaluation_contract import digest


def blocked_artifact(artifact):
    """Attach an unavailable-model envelope to a renderer-only, no-action fixture."""
    artifact = deepcopy(artifact)
    view = artifact["portfolio_decision"]["user_view"]
    decision = empty_decision("Simulated unavailable model")
    artifact["portfolio_decision"] = {**decision, "user_view": view}
    artifact["decision_authority"] = "llm_proposal_with_deterministic_validation"
    artifact["action_cards"] = []
    lineage = {"schema_version": 1, "author": "llm", "status": "unavailable",
               "snapshot": None, "advisory": None, "attempts": [],
               "portfolio_decision": decision,
               "deterministic_display": {"user_view": deepcopy(view), "content_hash": digest(view),
                                         "portfolio_decision_id": decision["decision_id"]}}
    lineage["content_hash"] = digest(lineage)
    artifact["advisory_lineage"] = lineage
    return artifact
