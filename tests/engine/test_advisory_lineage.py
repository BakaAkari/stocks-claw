"""Injected transport, genuine production snapshot/parser/gate/freeze pipeline.

All accounts, quotes and responses here are fixtures, never user financial facts
or evidence of a successful online model call.
"""
import json
from copy import deepcopy
from dataclasses import replace

import pytest

from stocks.domain.models import AnalysisContext, Instrument, MarketState, PortfolioMapping, Quote
from stocks.engine.advisory_mainline import build_advisory_outlook
from stocks.engine.evaluation_contract import digest
from stocks.engine.outcome_attribution import save_portfolio_snapshots
from stocks.engine.scheduled_analysis import apply_advisory_authority
from stocks.engine.unified_snapshot import build_unified_snapshot, snapshot_content_hash

NOW = "2026-10-15T07:00:00+00:00"
EXECUTION_RULES = {
    "settlement_rules": [{"id": "a-share", "match": {"market": "a"}, "settlement_rule": "T+1"}],
    "quantity_rules": [{"id": "a-step", "match": {"market": "a", "holding_unit": "shares"},
                        "quantity_step": 1}],
}


def context_fixture():
    fields = {"goals": "保留未来支出资金", "horizon": "一年以上", "operation_frequency": "每周",
              "style_preferences": ["不做日内交易"], "loss_capacity": {"amount": 1000, "currency": "CNY"},
              "cash_needs": []}
    mandate = {"fields": {k: {"value": v, "status": "confirmed", "source_field": k}
                          for k, v in fields.items()},
               "constraint_snapshot": {"value": {}, "status": "confirmed"},
               "sources": {"profile": {"kind": "local_confirmed"},
                           "constraints": {"kind": "local_confirmed"}}, "content_hash": "fixture-mandate"}
    pv = {"position_id": "fixture-position", "instrument_key": "a:518880",
          "account_id": "fixture-account", "display_name": "模拟持仓", "institution_type": "brokerage",
          "holding": {"quantity": 1000, "unit": "share"},
          "classification": {"product_type": "etf"},
          "liquidity": {"tradable": True, "rebalance_eligible": True}, "market_value_cny": 8500}
    integrity = {"schema_version": 1, "status": "matched", "input_hash": "fixture-reconciliation",
                 "as_of": NOW, "gaps": [],
                 "positions": {"fixture-position": {"account_id": "fixture-account", "instrument_id": "a:518880",
                                                     "unit": "share", "quantity": "1000", "available_quantity": "1000"}},
                 "accounts": {"fixture-account": {"status": "matched", "statement_id": "fixture-statement",
                                                   "statement_hash": "fixture-hash", "statement_as_of": NOW,
                                                   "cash_by_currency": {"CNY": {"balance": "10000", "available_balance": "10000"}}}}}
    return AnalysisContext(
        generated_at=NOW, assets=[], asset_count=0, portfolio_constraints={}, portfolio_profile={},
        quotes={"a": [Quote(Instrument(code="518880", name="模拟黄金ETF", market="a"), price=8.5,
                             source="fixture-provider", as_of=NOW, stale=False, currency="CNY")]},
        news=[], news_count=0, market_state=MarketState(), portfolio_mapping=PortfolioMapping(),
        drift_checks=[], recent_snapshots=[], raw_prompt_input="fixture", position_valuations=[pv],
        personal_mandate=mandate, financial_integrity=integrity,
        data_quality={"quotes": {"by_market": {"a": {"status": "ok", "freshness": "fresh"}}}},
    )


class InjectedClient:
    model = "fixture-transport-not-online-llm"

    def __init__(self, action_changes=None):
        self.prompt = None
        self.action_changes = action_changes or {}

    def complete(self, prompt):
        self.prompt = prompt
        self.input = json.loads(prompt.split("Snapshot:\n", 1)[1])
        proposal = {"action_id": "fixture-proposal", "target": "fixture-position", "action": "sell",
                    "size": "100", "size_type": "shares", "reasoning": "模拟模型根据资金需求提出卖出",
                    "evidence_refs": ["position:fixture-position"], "execute_when": "now",
                    "cancel_when": "资金用途变更时复核"}
        proposal.update(self.action_changes)
        self.response = json.dumps({"market_assessment": "模拟市场判断", "portfolio_assessment": "模拟组合判断",
                                    "actions": [proposal], "next_checkpoints": ["下周复核"]}, ensure_ascii=False)
        return self.response


def run(context=None, client=None):
    return build_advisory_outlook(context or context_fixture(), session_id="cn_after_close", market="cn",
                                  config={"execution_rules": EXECUTION_RULES},
                                  llm_client=client or InjectedClient(), now=NOW)


def test_snapshot_hash_covers_finance_mandate_and_values_and_is_detached():
    context = context_fixture()
    first = build_unified_snapshot(context)
    assert first.snapshot_id == snapshot_content_hash(first)
    assert first.snapshot_id == build_unified_snapshot(context).snapshot_id
    context.financial_integrity["positions"]["fixture-position"]["available_quantity"] = "5"
    assert build_unified_snapshot(context).snapshot_id != first.snapshot_id
    assert first.metadata["financial_integrity"]["positions"]["fixture-position"]["available_quantity"] == "1000"
    other = replace(context_fixture(), personal_mandate={})
    assert build_unified_snapshot(other).snapshot_id != first.snapshot_id


def test_original_llm_sell_is_final_and_every_stage_is_frozen(tmp_path):
    context, client = context_fixture(), InjectedClient()
    outlook = run(context, client)
    lineage = outlook["advisory_lineage"]
    pd = lineage["portfolio_decision"]
    assert pd["status"] == "approved", pd
    assert client.input["metadata"]["personal_mandate"] == context.personal_mandate
    assert client.input["metadata"]["financial_integrity"] == context.financial_integrity
    assert "User trading style (left-side" not in client.prompt
    trace = lineage["attempts"][0]
    assert trace["prompt"] == client.prompt and trace["raw_response"] == client.response
    assert trace["model"] == "fixture-transport-not-online-llm"
    assert trace["prompt_hash"] == digest(client.prompt)
    action = pd["approved_actions"][0]
    assert action["signal"] == "sell" and action["executable_quantity"] == 100
    assert action["original_proposal"] == lineage["advisory"]["actions"][0]
    assert action["validation_receipt_hash"] == digest(lineage["validation_receipt"])
    paths = save_portfolio_snapshots(pd, generated_at=NOW, snaps_dir=tmp_path / "decisions",
                                    run_id="fixture-run", position_valuations=context.position_valuations,
                                    quotes=context.to_dict()["quotes"], context={"advisory_lineage": lineage})
    frozen = json.loads(paths[0].read_text())
    assert frozen["action"]["signal"] == "sell"
    assert frozen["context"]["advisory_lineage"]["attempts"][0]["raw_response"] == client.response
    source_hash = frozen.pop("source_hash")
    assert source_hash == digest(frozen)


@pytest.mark.parametrize("change, expected", [
    ({"size": "1001"}, "available_quantity_missing_or_exceeded"),
    ({"size": "nan"}, "exact_positive_share_quantity_required"),
    ({"size": "1e9999"}, "exact_positive_share_quantity_required"),
    ({"size": "10%", "size_type": "ratio"}, "exact_positive_share_quantity_required"),
    ({"execute_when": "价格上涨时"}, "conditional_proposal_requires_manual_review"),
    ({"target": "a:999999"}, "existing_position_not_unique"),
    ({"evidence_refs": ["invented-reference"]}, "advisory_contract_invalid"),
    ({"action": "buy"}, "confirmed_fee_budget_unavailable"),
])
def test_unsupported_or_unverified_proposal_remains_original(change, expected):
    result = run(client=InjectedClient(change))["advisory_lineage"]
    decision = result["portfolio_decision"]
    assert not decision["approved_actions"]
    item = decision["suppressed_actions"][0]
    assert expected in item["validation_reasons"]
    for key, value in change.items():
        actual = item["original_proposal"][key]
        assert list(actual) == value if isinstance(value, list) else actual == value


@pytest.mark.parametrize("change, expected", [
    ("mismatch", "financial_integrity_not_matched"),
    ("no-available", "available_quantity_missing_or_exceeded"),
    ("other-account", "confirmed_position_mismatch"),
    ("default-profile", "personal_profile_not_user_confirmed"),
    ("unknown-loss", "personal_mandate_unknown:loss_capacity"),
    ("hard-cap", "allocation_constraints_require_portfolio_projection"),
])
def test_unknown_or_conflicting_user_facts_block(change, expected):
    context = context_fixture()
    if change == "mismatch":
        context.financial_integrity["status"] = "mismatch"
    elif change == "no-available":
        context.financial_integrity["positions"]["fixture-position"]["available_quantity"] = None
    elif change == "other-account":
        context.financial_integrity["positions"]["fixture-position"]["account_id"] = "different-account"
    elif change == "default-profile":
        context.personal_mandate["sources"]["profile"]["kind"] = "reference_default"
    elif change == "unknown-loss":
        context.personal_mandate["fields"]["loss_capacity"]["status"] = "unknown"
    else:
        context.personal_mandate["constraint_snapshot"]["value"] = {"gold": {"min": 0.1, "max": 0.2}}
    decision = run(context)["advisory_lineage"]["portfolio_decision"]
    assert not decision["approved_actions"]
    assert expected in decision["suppressed_actions"][0]["validation_reasons"]


def test_no_llm_never_falls_back_to_approved_rule_candidate():
    context = context_fixture()
    outlook = build_advisory_outlook(context, session_id="cn_after_close", market="cn",
                                    llm_client=None, now=NOW)
    old = {"approved_actions": [{"signal": "add", "position_id": "fixture-position"}]}
    run_value = {"structured_outlook": outlook, "portfolio_decision": deepcopy(old)}
    apply_advisory_authority(run_value)
    assert run_value["rule_evidence"]["portfolio_decision"] == old
    assert run_value["portfolio_decision"]["approved_actions"] == []
    assert run_value["portfolio_decision"]["status"] == "review_required"
    assert run_value["action_cards"] == []


def test_opposite_rule_does_not_revote_llm_direction():
    run_value = {"structured_outlook": run(), "portfolio_decision": {"approved_actions": [{"signal": "add"}]}}
    apply_advisory_authority(run_value)
    assert run_value["portfolio_decision"]["approved_actions"][0]["signal"] == "sell"
    assert run_value["rule_evidence"]["portfolio_decision"]["approved_actions"][0]["signal"] == "add"


def test_old_or_future_statement_does_not_authorize_available_quantity():
    for as_of in ("2026-01-01T00:00:00Z", "2027-01-01T00:00:00Z"):
        context = context_fixture()
        context.financial_integrity["accounts"]["fixture-account"]["statement_as_of"] = as_of
        lineage = run(context)["advisory_lineage"]
        assert lineage["validation_receipt"]["validation_policy"]["financial_statement_max_age_seconds"] == 86400
        decision = lineage["portfolio_decision"]
        assert not decision["approved_actions"]
        assert "available_statement_outside_freshness_policy" in decision["suppressed_actions"][0]["validation_reasons"]


def test_unknown_style_is_context_gap_not_automatic_sell_veto():
    context = context_fixture()
    context.personal_mandate["fields"]["style_preferences"] = {"status": "unknown", "value": None}
    lineage = run(context)["advisory_lineage"]
    assert lineage["portfolio_decision"]["status"] == "approved"
    assert "style_preferences" in lineage["validation_receipt"]["personal_mandate_scope"]["unknown_preferences"]


def test_malformed_action_is_archived_not_silently_dropped():
    class MalformedClient:
        model = "fixture"
        def complete(self, prompt):
            return '{"actions": ["sell something"]}'
    lineage = run(client=MalformedClient())["advisory_lineage"]
    assert not lineage["portfolio_decision"]["approved_actions"]
    assert lineage["attempts"][0]["raw_response"] == '{"actions": ["sell something"]}'
    assert lineage["attempts"][0]["status"] == "failed"


def test_confirmed_apis_normal_context_and_scheduled_producer_to_review(tmp_path):
    from scripts.smoke_advisory_lineage import run_smoke
    summary = run_smoke(tmp_path)
    assert summary["status"] == "ok"
    assert summary["real_llm_call"] is False and summary["real_user_records"] is False
    assert summary["display_linked"] is True and summary["review_read_only"] is True
    assert summary["cashflow_CNY"] == 849 and summary["cashflow_is_profit"] is False


def confirmed_fee_context(tmp_path):
    from stocks.engine.confirmation_provenance import ConfirmationStore
    from stocks.engine.fee_budget import validate_document
    context = context_fixture()
    doc = validate_document({"schema_version": 1, "budgets": [{
        "id": "fixture-fee", "account_id": "fixture-account", "instrument_id": "a:518880",
        "market": "a", "currency": "CNY", "valid_from": "2026-10-01T00:00:00Z",
        "valid_until": "2026-11-01T00:00:00Z", "max_quantity": "1000",
        "max_notional": "10000", "fee_upper_bound": "5", "source": "SIMULATED fee upper bound"}]})
    event = ConfirmationStore(tmp_path).record("fee_budgets", doc, channel="mcp", confirmed_at=NOW)
    return replace(context, fee_budgets={"status": "confirmed", "document": doc,
                                       "content_hash": event["content_hash"], "confirmation": event})


def test_confirmed_fee_allows_existing_add_only_and_freezes_evidence(tmp_path):
    context = confirmed_fee_context(tmp_path)
    result = run(context, InjectedClient({"action": "add"}))["advisory_lineage"]
    approved = result["portfolio_decision"]["approved_actions"]
    assert len(approved) == 1, result["portfolio_decision"]
    assert approved[0]["fee_budget_check"]["total_cash_required"] == "855.0"
    assert result["snapshot"]["metadata"]["fee_budgets"] == context.fee_budgets
    roundtrip = AnalysisContext.from_dict(context.to_dict())
    assert roundtrip.fee_budgets == context.fee_budgets
    assert build_unified_snapshot(context).snapshot_id != build_unified_snapshot(context_fixture()).snapshot_id
    buy = run(context, InjectedClient({"action": "buy"}))["advisory_lineage"]["portfolio_decision"]
    assert not buy["approved_actions"]
    assert "new_position_buy_requires_explicit_account_route" in buy["suppressed_actions"][0]["validation_reasons"]


def test_fee_cannot_bypass_cash_or_allocation_limits(tmp_path):
    context = confirmed_fee_context(tmp_path)
    context.financial_integrity["accounts"]["fixture-account"]["cash_by_currency"]["CNY"]["available_balance"] = "850"
    result = run(context, InjectedClient({"action": "add"}))["advisory_lineage"]["portfolio_decision"]
    assert "available_cash_including_fee_exceeded" in result["suppressed_actions"][0]["validation_reasons"]
    context = confirmed_fee_context(tmp_path)
    context.personal_mandate["constraint_snapshot"]["value"] = {"hard_caps": {"max_position_weight": 0.3}}
    result = run(context, InjectedClient({"action": "add"}))["advisory_lineage"]["portfolio_decision"]
    assert not result["approved_actions"]


def test_multiple_purchases_never_spend_same_cash_twice(tmp_path):
    from stocks.domain.advisory_models import AdvisoryValidationReceipt, InvestmentAdvisory
    from stocks.engine.advisory_lineage import build_advisory_lineage
    context = confirmed_fee_context(tmp_path)
    first = run(context, InjectedClient({"action": "add"}))["advisory_lineage"]
    # Two distinct holdings share the same account cash. Neither alone exceeds
    # available cash; approval of both would double-spend it.
    peer = deepcopy(context.position_valuations[0])
    peer["position_id"] = "other-position"
    context.position_valuations.append(peer)
    context.financial_integrity["positions"]["other-position"] = deepcopy(context.financial_integrity["positions"]["fixture-position"])
    context.financial_integrity["accounts"]["fixture-account"]["cash_by_currency"]["CNY"]["available_balance"] = "855"
    snapshot = build_unified_snapshot(context, execution_rules=EXECUTION_RULES)
    from stocks.domain.advisory_models import AdvisoryAction
    action = AdvisoryAction(**first["advisory"]["actions"][0])
    advisory = InvestmentAdvisory(**{**first["advisory"], "actions": (action, replace(action, action_id="other", target="other-position", evidence_refs=("position:other-position",)))})
    receipt = AdvisoryValidationReceipt(**first["validation_receipt"]["contract_receipt"])
    result = build_advisory_lineage(snapshot, advisory, receipt, first["attempts"])["portfolio_decision"]
    assert not result["approved_actions"]
    assert all(row["validation_reasons"] == ["multiple_purchases_require_cash_projection"]
               for row in result["suppressed_actions"])
