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


def _advice_payload() -> dict:
    return {
        "instruments": [{"market": "a", "code": "000001", "name": "平安银行"}],
        "direction": {"a:000001": "watch"},
        "rationale_summary": "现金占比较高，银行股继续观察。",
        "based_on": ["quotes", "portfolio", "profile"],
        "boundary": [
            {"type": "fact", "text": "现金占比较高"},
            {"type": "inference", "text": "银行股继续观察"},
        ],
    }


def test_mcp_advice_save_requires_confirmation_and_lists(adapter_engine, tmp_path):
    adapter = MCPAdapter(adapter_engine)
    denied = adapter.handle_request({
        "method": "advice_save",
        "params": {"advice": _advice_payload()},
    })
    assert denied["success"] is False
    assert not (tmp_path / "advice").exists()

    saved = adapter.handle_request({
        "method": "advice_save",
        "params": {"advice": _advice_payload(), "confirmed": True},
    })

    assert saved["success"] is True
    assert saved["data"]["created_at"]
    assert saved["data"]["direction"]["a:000001"] == "watch"
    listed = adapter.handle_request({"method": "advice_list"})
    assert listed["data"][0]["rationale_summary"] == "现金占比较高，银行股继续观察。"


def test_cli_advice_save_requires_confirmation_and_lists(adapter_engine, capsys):
    adapter = CLIAdapter(adapter_engine)

    adapter.run(["--advice-save", json.dumps(_advice_payload(), ensure_ascii=False)])
    denied = json.loads(capsys.readouterr().out)
    assert denied["success"] is False

    adapter.run([
        "--advice-save",
        json.dumps(_advice_payload(), ensure_ascii=False),
        "--confirmed",
    ])
    assert json.loads(capsys.readouterr().out)["success"] is True

    adapter.run(["--advice-list"])
    listed = json.loads(capsys.readouterr().out)
    assert listed["data"][0]["direction"]["a:000001"] == "watch"


def test_mcp_confirmed_memory_updates_feed_personal_advice_context(adapter_engine):
    """Agent 可经 MCP 改持仓、改偏好，并立即取得个人建议上下文。"""
    adapter_engine._watchlist = []
    adapter = MCPAdapter(adapter_engine)

    asset_result = adapter.handle_request({
        "method": "asset_add",
        "params": {
            "name": "应急现金",
            "platform": "银行",
            "amount": 12345,
            "asset_type": "现金",
            "currency": "CNY",
            "confirmed": True,
        },
    })
    profile_result = adapter.handle_request({
        "method": "profile_update",
        "params": {
            "profile": {
                "risk_tolerance": "conservative",
                "preferences": ["保留应急流动性"],
            },
            "confirmed": True,
        },
    })
    context_result = adapter.handle_request({
        "method": "get_analysis_context",
        "params": {
            "include_news": False,
            "include_quotes": False,
            "include_history": False,
        },
    })

    assert asset_result["success"] is True
    assert profile_result["success"] is True
    assert context_result["success"] is True
    context = context_result["data"]
    assert context["assets"][0]["amount"] == 12345
    assert context["portfolio_profile"]["risk_tolerance"] == "conservative"
    assert "risk_tolerance: conservative" in context["raw_prompt_input"]
    assert "保留应急流动性" in context["raw_prompt_input"]
    assert "12,345" not in context["raw_prompt_input"]
    assert "请基于以上上下文给出投资组合分析和建议" in context["raw_prompt_input"]
