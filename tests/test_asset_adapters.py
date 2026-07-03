"""CLI/MCP 资产记忆写入接口测试。"""

from __future__ import annotations

import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from stocks.adapters.cli import CLIAdapter
from stocks.adapters.mcp import MCPAdapter
from stocks.domain.models import FinancialAsset
from stocks.engine import StocksEngine
from tests.engine.test_engine import MINIMAL_CONFIG


@pytest.fixture
def adapter_engine(tmp_path):
    config = deepcopy(MINIMAL_CONFIG)
    config["paths"]["local_data_dir"] = str(tmp_path)
    with patch("stocks.engine.load_engine_config", return_value=config):
        engine = StocksEngine()
    engine._assets = []
    engine._sector_scan = []
    engine._history_warmed = True
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


def test_asset_instrument_mapping_cli_and_mcp_validation(adapter_engine, capsys):
    mcp = MCPAdapter(adapter_engine)
    cli = CLIAdapter(adapter_engine)

    invalid_mcp = mcp.handle_request({
        "method": "asset_add",
        "params": {
            "name": "未知标的",
            "platform": "券商",
            "amount": 1000,
            "instrument_key": "bad-key",
            "confirmed": True,
        },
    })
    assert invalid_mcp["success"] is False
    assert "instrument_key" in invalid_mcp["error"]
    assert adapter_engine.load_assets() == []

    added = mcp.handle_request({
        "method": "asset_add",
        "params": {
            "name": "科创50ETF",
            "platform": "券商",
            "amount": 3000,
            "asset_type": "股票ETF",
            "currency": "CNY",
            "instrument_key": "a:588000",
            "quantity": 1800,
            "tradable": True,
            "confirmed": True,
        },
    })
    assert added["success"] is True
    asset = adapter_engine.load_assets()[0]
    assert asset.instrument_key == "a:588000"
    assert asset.quantity == 1800.0
    assert asset.tradable is True

    invalid_cli_payload = {
        "name": "科创50ETF",
        "changes": {"instrument_key": "hk:2800"},
    }
    cli.run(["--asset-update", json.dumps(invalid_cli_payload), "--confirmed"])
    invalid_cli = json.loads(capsys.readouterr().out)
    assert invalid_cli["success"] is False
    assert "instrument_key market" in invalid_cli["error"]

    valid_cli_payload = {
        "name": "科创50ETF",
        "changes": {"instrument_key": "us:QCOM", "quantity": 3, "tradable": True},
    }
    cli.run(["--asset-update", json.dumps(valid_cli_payload), "--confirmed"])
    assert json.loads(capsys.readouterr().out)["success"] is True
    updated = adapter_engine.load_assets()[0]
    assert updated.instrument_key == "us:QCOM"
    assert updated.quantity == 3.0
    assert updated.tradable is True


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


def _advice_action(target: str = "a:588000", size_hint: str = "一成") -> dict:
    return {
        "target": target,
        "action": "increase",
        "size_hint": size_hint,
        "trigger": "回踩20日线后重新转强",
        "invalidation": "跌破前低",
        "horizon": "short",
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


def test_mcp_advice_actions_validate_target_and_context_echo(adapter_engine):
    adapter_engine._assets = [
        FinancialAsset(
            name="科创50ETF华夏",
            platform="券商",
            amount=3000,
            asset_type="股票ETF",
            instrument_key="a:588000",
            quantity=1800,
            tradable=True,
        )
    ]
    adapter = MCPAdapter(adapter_engine)
    payload = _advice_payload() | {
        "actions": [
            _advice_action("a:588000", "5%~8%"),
            _advice_action("权益", "一成"),
        ]
    }
    adapter_engine._constraints = {"权益": {"min": 0.25, "max": 0.65}}

    saved = adapter.handle_request({
        "method": "advice_save",
        "params": {"advice": payload, "confirmed": True},
    })
    assert saved["success"] is True
    assert len(saved["data"]["actions"]) == 2

    fake_target = adapter.handle_request({
        "method": "advice_save",
        "params": {
            "advice": _advice_payload() | {"actions": [_advice_action("a:FAKE")]},
            "confirmed": True,
        },
    })
    assert fake_target["success"] is False
    assert fake_target["errors"][0]["field"] == "target"

    exact_amount = adapter.handle_request({
        "method": "advice_save",
        "params": {
            "advice": _advice_payload() | {"actions": [_advice_action(size_hint="¥12,000")]},
            "confirmed": True,
        },
    })
    assert exact_amount["success"] is False
    assert exact_amount["errors"][0]["field"] == "actions"

    context_result = adapter.handle_request({
        "method": "get_analysis_context",
        "params": {
            "include_news": False,
            "include_quotes": False,
            "include_history": True,
        },
    })
    assert context_result["success"] is True
    advice = context_result["data"]["recent_advice"][0]
    assert advice["actions"] == payload["actions"]
    assert "结构化动作:" in context_result["data"]["raw_prompt_input"]
    assert "a:588000 | increase | 5%~8% | short" in context_result["data"]["raw_prompt_input"]


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


def test_cli_advice_actions_passthrough(adapter_engine, capsys):
    adapter_engine._constraints = {"权益": {"min": 0.25, "max": 0.65}}
    adapter = CLIAdapter(adapter_engine)
    payload = _advice_payload() | {"actions": [_advice_action("权益", "一成")]}

    adapter.run(["--advice-save", json.dumps(payload, ensure_ascii=False), "--confirmed"])
    assert json.loads(capsys.readouterr().out)["success"] is True

    adapter.run(["--advice-list"])
    listed = json.loads(capsys.readouterr().out)
    assert listed["data"][0]["actions"] == payload["actions"]


def _execution_payload(
    *,
    advice_id: str | None,
    target: str,
    action: str = "increase",
    extent: str | None = "full",
    note: str = "执行记录",
) -> dict:
    payload = {
        "advice_id": advice_id,
        "target": target,
        "action": action,
        "note": note,
        "executed_at": "2026-07-03T12:00:00+08:00",
    }
    if extent is not None:
        payload["extent"] = extent
    return payload


def test_mcp_execution_save_requires_confirmation_and_review_four_states(adapter_engine):
    adapter_engine._assets = [
        FinancialAsset(
            name="科创50ETF华夏",
            platform="券商",
            amount=3000,
            asset_type="股票ETF",
            instrument_key="a:588000",
        )
    ]
    adapter_engine._constraints = {
        "权益": {"min": 0.25, "max": 0.65},
        "现金": {"min": 0.05, "max": 0.30},
        "固收": {"min": 0.15, "max": 0.50},
        "黄金": {"min": 0.0, "max": 0.15},
    }
    adapter = MCPAdapter(adapter_engine)
    advice_payload = _advice_payload() | {
        "actions": [
            _advice_action("a:588000", "5%~8%"),
            _advice_action("现金", "一成"),
            _advice_action("固收", "保持"),
            _advice_action("黄金", "观察"),
        ]
    }
    saved_advice = adapter.handle_request({
        "method": "advice_save",
        "params": {"advice": advice_payload, "confirmed": True},
    })
    advice_id = saved_advice["data"]["created_at"]

    denied = adapter.handle_request({
        "method": "execution_save",
        "params": {"execution": _execution_payload(advice_id=advice_id, target="a:588000")},
    })
    assert denied["success"] is False

    for execution in [
        _execution_payload(advice_id=advice_id, target="a:588000", extent="full"),
        _execution_payload(advice_id=advice_id, target="现金", action="reduce", extent="partial"),
        _execution_payload(
            advice_id=advice_id,
            target="固收",
            action="none",
            extent=None,
            note="明确未执行",
        ),
        _execution_payload(advice_id=None, target="a:588000", extent="partial"),
    ]:
        saved = adapter.handle_request({
            "method": "execution_save",
            "params": {"execution": execution, "confirmed": True},
        })
        assert saved["success"] is True

    listed = adapter.handle_request({"method": "execution_list"})
    assert len(listed["data"]) == 4

    context_result = adapter.handle_request({
        "method": "get_analysis_context",
        "params": {
            "include_news": False,
            "include_quotes": False,
            "include_history": True,
        },
    })
    assert context_result["success"] is True
    review = context_result["data"]["recent_advice"][0]["execution_review"]
    statuses = {item["target"]: item["status"] for item in review}
    assert statuses == {
        "a:588000": "executed",
        "现金": "partial",
        "固收": "not_executed",
        "黄金": "unknown",
    }
    prompt = context_result["data"]["raw_prompt_input"]
    assert "建议 vs 执行:" in prompt
    assert "a:588000 | 建议 increase → executed" in prompt
    assert "固收 | 建议 increase → not_executed | 记录 none" in prompt


def test_cli_execution_save_and_list(adapter_engine, capsys):
    adapter = CLIAdapter(adapter_engine)

    adapter.run([
        "--execution-save",
        json.dumps(
            _execution_payload(
                advice_id="advice-1",
                target="a:588000",
                action="increase",
                extent="partial",
            ),
            ensure_ascii=False,
        ),
    ])
    denied = json.loads(capsys.readouterr().out)
    assert denied["success"] is False

    adapter.run([
        "--execution-save",
        json.dumps(
            _execution_payload(
                advice_id="advice-1",
                target="a:588000",
                action="increase",
                extent="partial",
            ),
            ensure_ascii=False,
        ),
        "--confirmed",
    ])
    assert json.loads(capsys.readouterr().out)["success"] is True

    adapter.run(["--execution-list"])
    listed = json.loads(capsys.readouterr().out)
    assert listed["data"][0]["target"] == "a:588000"
    assert listed["data"][0]["extent"] == "partial"


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
    assert "按 personal_advice_prompt 的决策导向契约输出" in context["raw_prompt_input"]
    assert "带触发条件的调仓清单与下一个机会提名" in context["raw_prompt_input"]
