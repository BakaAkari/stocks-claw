"""CLI/MCP 资产记忆写入接口测试。"""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from unittest.mock import patch

import pandas as pd
import pytest

from stocks.adapters.cli import CLIAdapter
from stocks.adapters.mcp import MCPAdapter
from stocks.domain.models import (
    Classification,
    CostBasis,
    FinancialAsset,
    ForecastRecord,
    Holding,
    Instrument,
    Liquidity,
    Position,
    ValuationInput,
)
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


def test_cli_and_mcp_asset_migration_v2(adapter_engine, tmp_path, capsys):
    path = tmp_path / "financial_assets.json"
    path.write_text(
        json.dumps([
            {
                "name": "现金",
                "platform": "银行",
                "amount": 1000,
                "asset_type": "现金",
                "currency": "CNY",
            }
        ], ensure_ascii=False),
        encoding="utf-8",
    )
    adapter_engine._assets = adapter_engine._load_assets_from_file()
    cli = CLIAdapter(adapter_engine)
    mcp = MCPAdapter(adapter_engine)

    cli.run(["--asset-migrate-v2"])
    preview = json.loads(capsys.readouterr().out)
    assert preview["success"] is True
    assert preview["will_write"] is False
    assert json.loads(path.read_text(encoding="utf-8"))[0]["name"] == "现金"

    mcp_preview = mcp.handle_request({
        "method": "asset_migrate_v2",
        "params": {"confirmed": False},
    })
    assert mcp_preview["success"] is True
    assert mcp_preview["target_schema_version"] == 2

    cli.run(["--asset-migrate-v2", "--confirmed"])
    migrated = json.loads(capsys.readouterr().out)
    assert migrated["success"] is True
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 2
    assert (tmp_path / "financial_assets.v1.bak.json").exists()


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
    assert "【复盘】" in context_result["data"]["raw_prompt_input"]
    assert "1. 上期建议 actions" in context_result["data"]["raw_prompt_input"]
    assert "a:588000 | increase | 5%~8% | short" in context_result["data"]["raw_prompt_input"]


def test_mcp_advice_rejects_mutable_actions_on_locked_positions(adapter_engine):
    adapter_engine._asset_schema_version = 2
    adapter_engine._asset_positions_v2 = [
        Position(
            position_id="policy_usd",
            account_id="insurance",
            display_name="保险",
            currency="USD",
            classification=Classification(
                asset_class="insurance",
                product_type="insurance_policy",
            ),
            valuation_input=ValuationInput(
                method="insurance_value",
                manual_amount=50000,
                as_of="2026-07-04",
            ),
            liquidity=Liquidity(tradable=False, rebalance_eligible=False, tier="locked"),
        )
    ]
    adapter = MCPAdapter(adapter_engine)

    rejected = adapter.handle_request({
        "method": "advice_save",
        "params": {
            "advice": _advice_payload() | {
                "actions": [_advice_action("policy_usd", "一成")]
            },
            "confirmed": True,
        },
    })

    assert rejected["success"] is False
    assert rejected["errors"][0]["field"] == "target"
    assert "rebalance_eligible=false" in rejected["errors"][0]["message"]


def test_mcp_pnl_trigger_requires_cost_basis(adapter_engine):
    adapter_engine._asset_schema_version = 2
    adapter_engine._asset_positions_v2 = [
        Position(
            position_id="broker_588000",
            account_id="broker",
            display_name="科创50ETF",
            currency="CNY",
            classification=Classification(
                asset_class="equity",
                product_type="exchange_traded_fund",
            ),
            instrument={"instrument_key": "a:588000"},
            holding=Holding(quantity=1800, unit="share"),
            valuation_input=ValuationInput(method="market_quote"),
            liquidity=Liquidity(tradable=True, rebalance_eligible=True, tier="t1"),
        )
    ]
    adapter = MCPAdapter(adapter_engine)
    trigger = {
        "instrument": "a:588000",
        "type": "pnl_pct_above",
        "level": 20.0,
        "action": "浮盈达到 20% 后减仓一半",
    }

    rejected = adapter.handle_request({
        "method": "advice_save",
        "params": {
            "advice": _advice_payload() | {"triggers": [trigger]},
            "confirmed": True,
        },
    })

    assert rejected["success"] is False
    assert rejected["errors"][0]["field"] == "triggers"
    assert "cost_basis" in rejected["errors"][0]["message"]

    adapter_engine._asset_positions_v2 = [
        Position(
            position_id="broker_588000",
            account_id="broker",
            display_name="科创50ETF",
            currency="CNY",
            classification=Classification(
                asset_class="equity",
                product_type="exchange_traded_fund",
            ),
            instrument={"instrument_key": "a:588000"},
            holding=Holding(
                quantity=1800,
                unit="share",
                cost_basis=CostBasis(unit_cost=1.0, currency="CNY"),
            ),
            valuation_input=ValuationInput(method="market_quote"),
            liquidity=Liquidity(tradable=True, rebalance_eligible=True, tier="t1"),
        )
    ]

    saved = adapter.handle_request({
        "method": "advice_save",
        "params": {
            "advice": _advice_payload() | {"triggers": [trigger]},
            "confirmed": True,
        },
    })
    assert saved["success"] is True
    assert saved["data"]["triggers"][0]["type"] == "pnl_pct_above"


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
    assert "3. 执行对照" in prompt
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


def _forecast_payload(
    *,
    deadline: str = "2999-01-01",
    target: str | None = "a:588000",
    level: float | None = 1.0,
) -> dict:
    payload = {
        "statement": "科创50ETF 到期收盘高于 1.0",
        "target": target,
        "metric": "close",
        "comparator": "above",
        "level": level,
        "deadline": deadline,
        "confidence": "medium",
    }
    return {key: value for key, value in payload.items() if value is not None}


def test_mcp_forecast_save_requires_confirmation_and_lists(adapter_engine, tmp_path):
    adapter = MCPAdapter(adapter_engine)
    denied = adapter.handle_request({
        "method": "forecast_save",
        "params": {"forecast": _forecast_payload()},
    })
    assert denied["success"] is False
    assert not (tmp_path / "forecasts").exists()

    saved = adapter.handle_request({
        "method": "forecast_save",
        "params": {"forecast": _forecast_payload(), "confirmed": True},
    })
    assert saved["success"] is True
    assert saved["data"]["status"] == "open"

    manual = adapter.handle_request({
        "method": "forecast_save",
        "params": {
            "forecast": _forecast_payload(target=None, level=None),
            "confirmed": True,
        },
    })
    assert manual["success"] is True
    assert manual["data"]["status"] == "manual"

    listed = adapter.handle_request({"method": "forecast_list"})
    assert len(listed["data"]) == 2
    assert {item["status"] for item in listed["data"]} == {"open", "manual"}


def test_cli_forecast_save_and_list(adapter_engine, capsys):
    adapter = CLIAdapter(adapter_engine)

    adapter.run(["--forecast-save", json.dumps(_forecast_payload(), ensure_ascii=False)])
    denied = json.loads(capsys.readouterr().out)
    assert denied["success"] is False

    adapter.run([
        "--forecast-save",
        json.dumps(_forecast_payload(), ensure_ascii=False),
        "--confirmed",
    ])
    assert json.loads(capsys.readouterr().out)["success"] is True

    adapter.run(["--forecast-list"])
    listed = json.loads(capsys.readouterr().out)
    assert listed["data"][0]["status"] == "open"
    assert listed["data"][0]["metric"] == "close"


def test_due_forecast_settles_and_feeds_context(adapter_engine):
    instrument = Instrument("588000", "科创50ETF", "a")
    adapter_engine._watchlist = [instrument]
    history = pd.DataFrame([
        {
            "timestamp": pd.Timestamp("2026-07-02", tz="UTC"),
            "code": "588000",
            "name": "科创50ETF",
            "market": "a",
            "price": 1.2,
            "open_price": 1.2,
            "high": 1.2,
            "low": 1.2,
            "prev_close": 1.0,
            "volume_lot": 100,
            "data_source": "provider",
        }
    ])
    asyncio.run(adapter_engine.history_cache.warm(instrument, history))
    adapter_engine.persistence.save_forecast(ForecastRecord(
        id="fixture-hit",
        created_at="2026-07-01T00:00:00+00:00",
        statement="科创50ETF 到期收盘高于 1.0",
        target="a:588000",
        metric="close",
        comparator="above",
        level=1.0,
        deadline="2026-07-02",
        confidence="medium",
        status="open",
    ))

    adapter = MCPAdapter(adapter_engine)
    context_result = adapter.handle_request({
        "method": "get_analysis_context",
        "params": {
            "include_news": False,
            "include_quotes": False,
            "include_history": True,
        },
    })

    assert context_result["success"] is True
    summary = context_result["data"]["forecast_summary"]
    assert summary["open_count"] == 0
    assert summary["recent_settlements"][0]["id"] == "fixture-hit"
    assert summary["recent_settlements"][0]["status"] == "hit"
    prompt = context_result["data"]["raw_prompt_input"]
    assert "【复盘】" in prompt
    assert "4. 到期预测结算" in prompt
    assert "fixture-hit" not in prompt
    assert "a:588000 | hit | 科创50ETF 到期收盘高于 1.0" in prompt
    assert "样本不足" in prompt
    assert "累计命中率" not in prompt
    assert "胜率" not in prompt
    assert "概率" not in prompt
    assert adapter_engine.persistence.list_forecasts()[0]["status"] == "hit"


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
    assert "12,345.00 CNY" in context["raw_prompt_input"]
    assert "按 personal_advice_prompt 的决策导向契约输出" in context["raw_prompt_input"]
    assert "带触发条件的调仓清单与下一个机会提名" in context["raw_prompt_input"]
