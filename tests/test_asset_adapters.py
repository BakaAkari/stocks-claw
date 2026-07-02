"""CLI/MCP 资产记忆写入接口测试。"""

from __future__ import annotations

import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from stocks.adapters.cli import CLIAdapter
from stocks.adapters.mcp import MCPAdapter
from stocks.engine import StocksEngine
from tests.engine.test_engine import MINIMAL_CONFIG


@pytest.fixture
def adapter_engine(tmp_path):
    config = deepcopy(MINIMAL_CONFIG)
    config["paths"]["local_data_dir"] = str(tmp_path)
    with patch("stocks.engine.load_engine_config", return_value=config):
        engine = StocksEngine()
    engine._assets = []
    return engine


def test_mcp_asset_crud_round_trip(adapter_engine, tmp_path):
    adapter = MCPAdapter(adapter_engine)

    denied = adapter.handle_request({
        "method": "asset_add",
        "params": {"name": "现金", "platform": "银行", "amount": 1000},
    })
    assert denied["success"] is False
    assert adapter_engine.load_assets() == []

    added = adapter.handle_request({
        "method": "asset_add",
        "params": {
            "name": "现金",
            "platform": "银行",
            "amount": 1000,
            "asset_type": "现金",
            "currency": "CNY",
            "confirmed": True,
        },
    })
    assert added["success"] is True

    updated = adapter.handle_request({
        "method": "asset_update",
        "params": {
            "name": "现金",
            "changes": {"amount": 1200},
            "confirmed": True,
        },
    })
    assert updated["success"] is True
    assert adapter.handle_request({"method": "assets_list"})["data"][0]["amount"] == 1200

    removed = adapter.handle_request({
        "method": "asset_remove",
        "params": {"name": "现金", "confirmed": True},
    })
    assert removed["success"] is True
    assert json.loads((tmp_path / "financial_assets.json").read_text()) == []


def test_cli_asset_crud_round_trip(adapter_engine, tmp_path, capsys):
    adapter = CLIAdapter(adapter_engine)

    adapter.run([
        "--asset-add",
        json.dumps({"name": "黄金", "platform": "券商", "amount": 500}),
    ])
    denied = json.loads(capsys.readouterr().out)
    assert denied["success"] is False

    adapter.run([
        "--asset-add",
        json.dumps({"name": "黄金", "platform": "券商", "amount": 500}),
        "--confirmed",
    ])
    assert json.loads(capsys.readouterr().out)["success"] is True

    adapter.run([
        "--asset-update",
        json.dumps({"name": "黄金", "changes": {"amount": 600}}),
        "--confirmed",
    ])
    assert json.loads(capsys.readouterr().out)["success"] is True

    adapter.run(["--asset-remove", "黄金", "--confirmed"])
    assert json.loads(capsys.readouterr().out)["success"] is True
    assert json.loads((tmp_path / "financial_assets.json").read_text()) == []


def test_profile_update_requires_confirmation_and_persists(adapter_engine, tmp_path):
    adapter = MCPAdapter(adapter_engine)
    denied = adapter.handle_request({
        "method": "profile_update",
        "params": {"profile": {"risk_tolerance": "moderate"}},
    })
    assert denied["success"] is False
    assert not (tmp_path / "investor_profile.json").exists()

    updated = adapter.handle_request({
        "method": "profile_update",
        "params": {
            "profile": {
                "risk_tolerance": "moderate",
                "investment_horizon": "long_term",
                "preferences": ["低费率"],
                "constraints": {"prohibited_assets": ["高杠杆"]},
            },
            "confirmed": True,
        },
    })

    assert updated["success"] is True
    stored = json.loads((tmp_path / "investor_profile.json").read_text())
    assert stored["risk_tolerance"] == "moderate"
    assert stored["updated_at"]
    assert adapter.handle_request({"method": "profile_get"})["data"] == stored
