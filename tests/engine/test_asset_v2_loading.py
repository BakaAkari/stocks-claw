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
def snapshot_document():
    return {
        "schema_version": 2,
        "base_currency": "CNY",
        "accounts": [{"account_id": "bank", "display_name": "Test bank",
                      "institution_type": "bank", "base_currency": "CNY"}],
        "positions": [
            {"position_id": "cash", "account_id": "bank", "display_name": "Cash",
             "currency": "CNY", "classification": {"asset_class": "cash", "product_type": "cash"},
             "valuation_input": {"method": "manual_amount", "manual_amount": 100,
                                 "as_of": "2026-01-01"}, "confirmed": True},
            {"position_id": "bank_total", "account_id": "bank", "display_name": "Account total",
             "currency": "CNY", "classification": {
                 "asset_class": "account_snapshot", "product_type": "account_snapshot",
                 "exposure_tags": ["non_additive_snapshot"]},
             "valuation_input": {"method": "manual_amount", "manual_amount": 150},
             "liquidity": {"tradable": False, "rebalance_eligible": False, "tier": "unknown"},
             "confirmed": True, "notes": "Approximate total; source date unavailable."},
        ],
    }


def test_snapshot_separation_preserves_source_and_excludes_double_count(engine, tmp_path, snapshot_document):
    from stocks.adapters.mcp import MCPAdapter

    path = tmp_path / "financial_assets.json"
    original = json.dumps(snapshot_document)
    path.write_text(original)
    engine._assets = engine._load_assets_from_file()
    assert engine._asset_load_warning is None
    assert len(engine._asset_positions_v2) == len(engine.load_assets()) == 1
    snapshot = engine.account_snapshots()[0]
    assert snapshot["record"] == snapshot_document["positions"][1]
    assert snapshot["as_of"] is None
    assert snapshot["freshness"] == "unknown"
    assert snapshot["included_in_position_totals"] is False
    summary = MCPAdapter(engine)._get_portfolio_summary({})
    assert summary["success"] is True
    assert summary["data"]["total_value"] == 100
    assert summary["data"]["asset_count"] == 1
    assert summary["data"]["account_snapshots"] == [snapshot]
    assert summary["data"]["total_value_scope"] == "position_values_only_not_account_net_worth"
    assert engine.financial_integrity()["asset_confirmation"] is None
    assert path.read_text() == original
    snapshot["record"]["notes"] = "mutated caller copy"
    assert engine.account_snapshots()[0]["record"]["notes"] != "mutated caller copy"
    path.write_text(json.dumps({**snapshot_document, "positions": []}))
    engine._load_assets_from_file()
    assert engine.account_snapshots() == []


@pytest.mark.parametrize("field,value", [
    ("classification", {"asset_class": "account_snapshot", "product_type": "cash"}),
    ("account_id", "unknown"),
    ("position_id", "cash"),
    ("currency", None),
    ("valuation_input", {"method": "manual_amount", "manual_amount": True}),
    ("valuation_input", {"method": "manual_amount", "manual_amount": float("nan")}),
    ("valuation_input", {"method": "market_quote", "manual_amount": 150}),
    ("holding", {"quantity": 1, "unit": "share"}),
    ("instrument", {"instrument_key": "us:TEST"}),
    ("liquidity", {"tradable": True, "rebalance_eligible": False}),
])
def test_invalid_snapshot_is_not_silently_skipped(engine, tmp_path, snapshot_document, field, value):
    snapshot_document["positions"][1][field] = value
    (tmp_path / "financial_assets.json").write_text(json.dumps(snapshot_document))
    assert engine._load_assets_from_file() == []
    assert engine._asset_load_warning == "asset_file_invalid_v2_record"
    assert engine.account_snapshots() == []


@pytest.mark.asyncio
async def test_context_carries_non_additive_snapshot_without_promoting_confirmation(
    engine, tmp_path, snapshot_document,
):
    from stocks.domain.models import AnalysisContext

    (tmp_path / "financial_assets.json").write_text(json.dumps(snapshot_document))
    engine._assets = engine._load_assets_from_file()
    engine._watchlist = []
    engine._sector_scan = []
    engine.context_builder.macro_provider = None
    context = await engine.build_context(include_news=False, include_quotes=False, include_history=False)
    payload = context.to_dict()
    assert payload["asset_count"] == 1
    assert len(payload["asset_positions"]) == 1
    assert payload["account_snapshots"][0]["record"] == snapshot_document["positions"][1]
    assert AnalysisContext.from_dict(payload).account_snapshots == payload["account_snapshots"]
    assert AnalysisContext.from_dict({"account_snapshots": payload["account_snapshots"]}).to_dict()[
        "account_snapshots"
    ] == payload["account_snapshots"]
    assert context.financial_integrity["asset_confirmation"] is None
    payload["account_snapshots"][0]["record"]["notes"] = "caller mutation"
    assert context.account_snapshots[0]["record"]["notes"] != "caller mutation"


def test_reconciliation_does_not_treat_snapshot_as_cash_or_a_statement(snapshot_document):
    from stocks.engine.financial_service import _alignment

    statement = {"account_id": "bank", "positions": [],
                 "cash": [{"currency": "CNY", "balance": 100}]}
    positions, gaps = _alignment(snapshot_document, statement)
    assert positions == {}
    assert gaps == []
    statement["cash"][0]["balance"] = 150
    assert "asset_memory_cash_mismatch:CNY" in _alignment(snapshot_document, statement)[1]


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


def test_invalid_v2_nested_record_is_structured_error(engine, tmp_path):
    (tmp_path / "financial_assets.json").write_text(json.dumps({
        "schema_version": 2,
        "base_currency": "CNY",
        "accounts": [],
        "positions": [
            {
                "position_id": "bad",
                "account_id": "manual",
                "display_name": "坏记录",
                "currency": "CNY",
                "classification": [],
                "valuation_input": {"method": "manual_amount", "manual_amount": 1},
                "liquidity": {},
            }
        ],
    }, ensure_ascii=False), encoding="utf-8")

    assert engine._load_assets_from_file() == []
    assert engine._asset_load_warning == "asset_file_invalid_v2_record"


def test_legacy_crud_refuses_to_overwrite_v2_file(engine, tmp_path):
    path = tmp_path / "financial_assets.json"
    original = json.dumps({
        "schema_version": 2,
        "base_currency": "CNY",
        "accounts": [],
        "positions": [],
    }, ensure_ascii=False)
    path.write_text(original, encoding="utf-8")
    assert engine._load_assets_from_file() == []
    assert engine._asset_schema_version == 2

    with pytest.raises(ValueError, match="schema_version=2"):
        engine.add_asset(FinancialAsset(name="现金", platform="银行", amount=1000))
    with pytest.raises(ValueError, match="schema_version=2"):
        engine.update_asset("现金", amount=2000)
    with pytest.raises(ValueError, match="schema_version=2"):
        engine.remove_asset("现金")

    assert path.read_text(encoding="utf-8") == original


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


def test_asset_migration_preview_and_confirmed_write(engine, tmp_path):
    path = tmp_path / "financial_assets.json"
    original = json.dumps([
        {
            "name": "现金",
            "platform": "银行",
            "amount": 1000,
            "asset_type": "现金",
            "currency": "CNY",
        },
        {
            "name": "科创50ETF",
            "platform": "券商",
            "amount": 3000,
            "asset_type": "股票ETF",
            "currency": "CNY",
            "instrument_key": "a:588000",
            "quantity": 1800,
            "tradable": True,
        },
    ], ensure_ascii=False)
    path.write_text(original, encoding="utf-8")
    engine._assets = engine._load_assets_from_file()

    preview = engine.migrate_assets_v2(confirmed=False)

    assert preview["success"] is True
    assert preview["will_write"] is False
    assert json.loads(path.read_text(encoding="utf-8"))[0]["name"] == "现金"
    assert preview["target_schema_version"] == 2
    assert preview["missing_fields_summary"]["valuation_as_of"] == 1
    assert preview["missing_fields_summary"]["cost_basis"] == 1
    assert preview["missing_fields_priority"][0]["field"] == "cost_basis"
    assert preview["positions"][0]["position_id"] != preview["positions"][1]["position_id"]

    migrated = engine.migrate_assets_v2(confirmed=True)

    assert migrated["success"] is True
    assert migrated["will_write"] is True
    assert (tmp_path / "financial_assets.v1.bak.json").read_text(encoding="utf-8") == original
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["schema_version"] == 2
    assert len(stored["accounts"]) == 2
    assert len(stored["positions"]) == 2
    assert engine._asset_schema_version == 2

    rejected = engine.migrate_assets_v2(confirmed=True)
    assert rejected["success"] is False
    assert rejected["error"] == "financial_assets.json is already schema_version=2"
