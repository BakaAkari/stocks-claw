import pytest

from stocks.domain.models import (
    Account,
    Classification,
    CostBasis,
    FinancialAsset,
    Holding,
    Liquidity,
    Position,
    ReportedPerformance,
    ValuationInput,
    financial_asset_to_position_v2,
)


def test_position_v2_round_trip_market_quote() -> None:
    position = Position(
        position_id="cn_broker_510300",
        account_id="cn_broker",
        display_name="沪深300ETF",
        currency="CNY",
        classification=Classification(
            asset_class="equity",
            product_type="exchange_traded_fund",
            subtype="broad_index_etf",
            exposure_tags=["CN Equity", "csi300"],
        ),
        instrument={"instrument_key": "a:510300", "exchange": "sh"},
        holding=Holding(
            quantity=2100,
            unit="share",
            cost_basis=CostBasis(unit_cost=4.796, currency="CNY"),
        ),
        valuation_input=ValuationInput(method="market_quote"),
        liquidity=Liquidity(tradable=True, rebalance_eligible=True, tier="t0"),
        confirmed=True,
    )

    assert position.currency == "CNY"
    assert position.instrument["instrument_key"] == "a:510300"
    assert position.holding.quantity == 2100.0
    assert position.classification.exposure_tags == ["cn_equity", "csi300"]
    assert position.data_completeness["missing_fields"] == []

    restored = Position.from_dict(position.to_storage_dict())
    assert restored == position
    assert restored.to_dict() == position.to_dict()


def test_invalid_controlled_vocabularies_rejected() -> None:
    with pytest.raises(ValueError, match="asset_class"):
        Classification(asset_class="equities", product_type="stock")

    with pytest.raises(ValueError, match="product_type"):
        Classification(asset_class="equity", product_type="magic_fund")

    with pytest.raises(ValueError, match="valuation_input.method"):
        ValuationInput(method="broker_api")

    with pytest.raises(ValueError, match="liquidity.tier"):
        Liquidity(tier="tomorrow")

    with pytest.raises(ValueError, match="holding.unit"):
        Holding(quantity=1, unit="lot")


def test_insurance_policy_defaults_to_locked_non_rebalanceable() -> None:
    position = Position(
        position_id="hk_insurance_policy",
        account_id="hk_insurance",
        display_name="香港保险",
        currency="USD",
        classification=Classification(asset_class="insurance", product_type="insurance_policy"),
        valuation_input=ValuationInput(method="insurance_value", manual_amount=50000, as_of="2026-07-04"),
        liquidity=Liquidity(),
        confirmed=True,
    )

    assert position.liquidity.tradable is False
    assert position.liquidity.rebalance_eligible is False
    assert position.liquidity.tier == "locked"


def test_completeness_rules_are_machine_readable() -> None:
    market_without_cost = Position(
        position_id="us_broker_xle",
        account_id="us_broker",
        display_name="XLE",
        currency="USD",
        classification=Classification(asset_class="equity", product_type="exchange_traded_fund"),
        instrument={"instrument_key": "us:XLE"},
        holding=Holding(quantity=90, unit="share"),
        valuation_input=ValuationInput(method="market_quote"),
        liquidity=Liquidity(tradable=True, rebalance_eligible=True, tier="t1"),
        confirmed=True,
    )
    assert "cost_basis" in market_without_cost.data_completeness["missing_fields"]

    manual_without_as_of = Position(
        position_id="manual_cash_like",
        account_id="bank",
        display_name="手工理财",
        currency="CNY",
        classification=Classification(asset_class="unknown", product_type="manual_asset"),
        valuation_input=ValuationInput(method="manual_amount", manual_amount=10000),
        liquidity=Liquidity(tier="periodic_open"),
        confirmed=True,
    )
    assert "valuation_as_of" in manual_without_as_of.data_completeness["missing_fields"]
    assert "classification" in manual_without_as_of.data_completeness["missing_fields"]


@pytest.mark.parametrize(
    "payload, message",
    [
        (
            {
                "position_id": "bad_manual",
                "account_id": "bank",
                "display_name": "缺金额",
                "currency": "CNY",
                "classification": Classification(asset_class="cash", product_type="cash"),
                "valuation_input": ValuationInput(method="manual_amount", as_of="2026-07-04"),
                "liquidity": Liquidity(tier="cash"),
            },
            "manual_amount",
        ),
        (
            {
                "position_id": "bad_quote",
                "account_id": "broker",
                "display_name": "缺标的",
                "currency": "CNY",
                "classification": Classification(asset_class="equity", product_type="stock"),
                "holding": Holding(quantity=1, unit="share"),
                "valuation_input": ValuationInput(method="market_quote"),
                "liquidity": Liquidity(tier="t1"),
            },
            "instrument_key",
        ),
    ],
)
def test_position_validation_for_valuation_methods(payload: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Position(**payload)


def test_account_and_reported_performance_round_trip() -> None:
    account = Account(
        account_id="us_broker",
        display_name="IBKR",
        institution_type="brokerage",
        market_scope=["us"],
        base_currency="USD",
        default_liquidity_tier="t1",
        notes="manual alias only",
    )
    assert Account.from_dict(account.to_dict()) == account

    perf = ReportedPerformance(
        unrealized_pnl=-353.8,
        cumulative_pnl=None,
        as_of="2026-07-04",
        source="broker",
    )
    assert ReportedPerformance.from_dict(perf.to_dict()) == perf


def test_v1_position_id_generation_does_not_collide_for_chinese_names() -> None:
    first = financial_asset_to_position_v2(
        FinancialAsset(name="神秘资产", platform="其他", amount=1, asset_type="unknown")
    )
    second = financial_asset_to_position_v2(
        FinancialAsset(name="测试资产", platform="其他", amount=1, asset_type="unknown")
    )
    cash = financial_asset_to_position_v2(
        FinancialAsset(name="现金", platform="银行", amount=1, asset_type="现金")
    )
    demand = financial_asset_to_position_v2(
        FinancialAsset(name="活期", platform="银行", amount=1, asset_type="活期")
    )

    assert first.position_id != second.position_id
    assert cash.position_id != demand.position_id
    assert first.position_id.startswith("其他_神秘资产_")
    assert cash.position_id.startswith("银行_现金_")


@pytest.mark.parametrize(
    "payload, message",
    [
        ([], "position must be an object"),
        ({"classification": []}, "classification must be an object"),
        ({"valuation_input": []}, "valuation_input must be an object"),
        ({"holding": []}, "holding must be an object"),
    ],
)
def test_position_from_dict_rejects_invalid_nested_shapes(payload, message) -> None:
    with pytest.raises(ValueError, match=message):
        Position.from_dict(payload)
