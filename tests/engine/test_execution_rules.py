from pathlib import Path

import yaml

from stocks.engine.execution_rules import resolve_execution


def _production_config():
    return yaml.safe_load(Path("stocks/config/engine.yaml").read_text())["execution_rules"]


def _evidence(*, key="a:588000", quantity=1000, unit="share", tier="t1",
              product_type="exchange_traded_fund", tradable=True,
              rebalance_eligible=True, redemption_rule=None):
    return {
        "instrument_key": key,
        "holding": {} if quantity is None else {"quantity": quantity, "unit": unit},
        "classification": {"product_type": product_type},
        "liquidity": {
            "tier": tier, "tradable": tradable,
            "rebalance_eligible": rebalance_eligible,
            "redemption_rule": redemption_rule,
        },
    }


def _card(institution="brokerage", product_type="exchange_traded_fund"):
    return {"institution_type": institution, "product_type": product_type}


def test_production_a_share_add_uses_configured_lot():
    r = resolve_execution(evidence=_evidence(), card=_card(), side="add", ratio=0.25,
                          config=_production_config())
    assert r.settlement_rule == "T+1"
    assert r.quantity_step == 100
    assert r.executable_quantity == 200
    assert r.final_ratio == 0.2
    assert r.execution_status == "adjusted_to_step"


def test_production_a_share_small_add_is_deferred():
    r = resolve_execution(evidence=_evidence(quantity=300), card=_card(), side="add", ratio=0.2,
                          config=_production_config())
    assert r.executable_quantity == 0
    assert r.final_ratio == 0
    assert r.execution_status == "deferred_min_unit"


def test_production_us_is_conservative_whole_share():
    r = resolve_execution(evidence=_evidence(key="us:NVDA", quantity=13), card=_card(),
                          side="reduce", ratio=0.2, config=_production_config())
    assert r.quantity_step == 1
    assert r.executable_quantity == 2
    assert r.execution_status == "adjusted_to_step"


def test_fund_platform_t2_and_fractional_step_are_explicit():
    r = resolve_execution(
        evidence=_evidence(key="us:QQQ", quantity=123.45, tier="t2_plus", product_type="qdii_fund"),
        card=_card("fund_platform", "qdii_fund"), side="reduce", ratio=0.1,
        config=_production_config(),
    )
    assert r.settlement_rule == "T+2"
    assert r.quantity_step == 0.01
    assert r.executable_quantity == 12.34


def test_periodic_open_and_locked_fail_closed():
    periodic = resolve_execution(
        evidence=_evidence(key="", quantity=100, tier="periodic_open", product_type="bank_wealth_management"),
        card=_card("bank", "bank_wealth_management"), side="reduce", ratio=0.1,
        config=_production_config(),
    )
    assert periodic.settlement_rule == "periodic_open"
    assert periodic.execution_status == "review_required"
    locked = resolve_execution(
        evidence=_evidence(key="", quantity=None, tier="locked", product_type="insurance_policy",
                           tradable=False, rebalance_eligible=False),
        card=_card("insurance", "insurance_policy"), side="reduce", ratio=1,
        config=_production_config(),
    )
    assert locked.settlement_rule == "locked"
    assert locked.execution_status == "review_required"


def test_unknown_rule_and_missing_quantity_fail_closed():
    unknown = resolve_execution(
        evidence=_evidence(key="crypto:BTC", quantity=1, tier="unknown", product_type="manual_asset"),
        card=_card("manual", "manual_asset"), side="reduce", ratio=0.1,
        config=_production_config(),
    )
    assert unknown.execution_status == "review_required"
    assert unknown.rule_id is None
    missing = resolve_execution(
        evidence=_evidence(quantity=None), card=_card(), side="add", ratio=0.1,
        config=_production_config(),
    )
    assert missing.execution_status == "review_required"
    assert missing.executable_quantity is None


def test_unmapped_redemption_rule_overrides_config_and_fails_closed():
    r = resolve_execution(
        evidence=_evidence(redemption_rule="平台自定义规则"), card=_card(),
        side="reduce", ratio=0.1, config=_production_config(),
    )
    assert r.execution_status == "review_required"
    assert "unmapped" in r.reason


def test_replacement_portfolio_ratio_never_claims_quantity():
    r = resolve_execution(
        evidence=_evidence(key="us:NVDA", quantity=10), card=_card(),
        side="add", ratio=0.05, ratio_basis="portfolio", config=_production_config(),
    )
    assert r.settlement_rule == "T+1"
    assert r.execution_status == "review_required"
    assert r.executable_quantity is None


def test_bank_gram_uses_product_type_not_holding_unit():
    """Bank precious-metal accounts round to a 0.01 gram step, keyed off
    product_type (a fact reliably plumbed end-to-end) rather than
    holding_unit (which context_builder never serializes today)."""
    r = resolve_execution(
        evidence=_evidence(key="bank:ccb_gold", quantity=139.2733, tier="cash",
                           product_type="precious_metal_account"),
        card=_card("bank", "precious_metal_account"), side="reduce", ratio=0.1,
        config=_production_config(),
    )
    assert r.quantity_step == 0.01
    assert r.executable_quantity == 13.92
    assert r.execution_status == "adjusted_to_step"


def test_bank_non_gold_product_type_does_not_inherit_gram_step():
    """A bank money-market position must not silently borrow the gram step
    just because it shares institution_type=bank with the gold account."""
    r = resolve_execution(
        evidence=_evidence(key="bank:ccb_mm", quantity=202969.43, tier="t0",
                           product_type="money_market_fund"),
        card=_card("bank", "money_market_fund"), side="reduce", ratio=0.1,
        config=_production_config(),
    )
    assert r.settlement_rule == "T+0"
    assert r.execution_status == "review_required"
    assert r.executable_quantity is None
    assert "no quantity-step rule matched" in r.reason
