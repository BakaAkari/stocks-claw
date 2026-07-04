from __future__ import annotations

import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from stocks.domain.models import (
    FinancialAsset,
    classification_from_v1_asset_type,
    financial_asset_to_position_v2,
)
from stocks.engine import StocksEngine
from tests.engine.test_engine import MINIMAL_CONFIG


@pytest.fixture
def engine(tmp_path):
    config = deepcopy(MINIMAL_CONFIG)
    config["paths"]["local_data_dir"] = str(tmp_path)
    with patch("stocks.engine.load_engine_config", return_value=config):
        instance = StocksEngine()
    return instance


def test_v1_loads_and_maps_to_v2_in_memory_without_writing(engine, tmp_path):
    path = tmp_path / "financial_assets.json"
    original = json.dumps([
        {
            "name": "纳指QDII",
            "platform": "支付宝",
            "amount": 12000,
            "asset_type": "QDII",
            "currency": "CNY",
        },
        {
            "name": "神秘资产",
            "platform": "其他",
            "amount": 1,
            "asset_type": "无法识别",
            "currency": "CNY",
        },
    ], ensure_ascii=False)
    path.write_text(original, encoding="utf-8")

    loaded = engine._load_assets_from_file()

    assert path.read_text(encoding="utf-8") == original
    assert engine._asset_schema_version == 1
    assert engine._asset_load_warning == "v1_format_migration_recommended"
    assert len(loaded) == 2
    assert len(engine._asset_accounts_v2) == 2
    assert len(engine._asset_positions_v2) == 2
    assert engine._asset_positions_v2[0].classification.product_type == "qdii_fund"
    assert engine._asset_positions_v2[0].data_completeness["missing_fields"] == ["valuation_as_of"]
    assert engine._asset_positions_v2[1].classification.asset_class == "unknown"
    assert "classification" in engine._asset_positions_v2[1].data_completeness["missing_fields"]


def test_v2_loads_accounts_positions_and_legacy_assets(engine, tmp_path):
    path = tmp_path / "financial_assets.json"
    path.write_text(json.dumps({
        "schema_version": 2,
        "base_currency": "CNY",
        "accounts": [
            {
                "account_id": "cn_broker",
                "display_name": "A股账户",
                "institution_type": "brokerage",
                "market_scope": ["a"],
                "base_currency": "CNY",
            }
        ],
        "positions": [
            {
                "position_id": "cn_broker_588000",
                "account_id": "cn_broker",
                "display_name": "科创50ETF",
                "currency": "CNY",
                "classification": {
                    "asset_class": "equity",
                    "product_type": "exchange_traded_fund",
                    "exposure_tags": ["star50"],
                },
                "instrument": {"instrument_key": "a:588000"},
                "holding": {"quantity": 1800, "unit": "share"},
                "valuation_input": {"method": "market_quote"},
                "liquidity": {"tradable": True, "rebalance_eligible": True, "tier": "t1"},
                "confirmed": True,
            }
        ],
    }, ensure_ascii=False), encoding="utf-8")

    loaded = engine._load_assets_from_file()

    assert engine._asset_schema_version == 2
    assert engine._asset_base_currency == "CNY"
    assert engine._asset_load_warning is None
    assert engine._asset_accounts_v2[0].account_id == "cn_broker"
    assert engine._asset_positions_v2[0].instrument_key == "a:588000"
    assert loaded[0].name == "科创50ETF"
    assert loaded[0].instrument_key == "a:588000"
    assert loaded[0].quantity == 1800.0


def test_invalid_top_level_is_structured_error(engine, tmp_path):
    (tmp_path / "financial_assets.json").write_text('"bad"', encoding="utf-8")
    assert engine._load_assets_from_file() == []
    assert engine._asset_load_warning == "asset_file_invalid_top_level"


def test_v1_to_v2_mapping_covers_new_keywords_and_unknown() -> None:
    assert classification_from_v1_asset_type("贵金属").asset_class == "commodity"
    assert classification_from_v1_asset_type("保险").product_type == "insurance_policy"
    assert classification_from_v1_asset_type("固收+").product_type == "fixed_income_plus_fund"
    assert classification_from_v1_asset_type("货基").product_type == "money_market_fund"
    assert classification_from_v1_asset_type("not-known").asset_class == "unknown"

    position = financial_asset_to_position_v2(
        FinancialAsset(
            name="科创50ETF",
            platform="券商",
            amount=3000,
            asset_type="股票ETF",
            instrument_key="a:588000",
            quantity=1800,
            tradable=True,
        )
    )
    assert position.valuation_input.method == "market_quote"
    assert position.holding is not None
    assert position.holding.quantity == 1800.0
    assert "cost_basis" in position.data_completeness["missing_fields"]
