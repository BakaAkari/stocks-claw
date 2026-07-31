"""Tests for the A1 asset intake service (draft → token → audited v2 write).

The real `.local/financial_assets.json` is never touched: every test runs
against a sandbox v2 file under tmp_path.
"""
from __future__ import annotations

import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from stocks.engine import StocksEngine
from stocks.engine.asset_intake_service import (
    apply_intake_draft,
    build_intake_draft,
    intake_memory_hash,
)
from tests.engine.test_engine import MINIMAL_CONFIG


def _v2_doc() -> dict:
    return {
        "schema_version": 2,
        "base_currency": "CNY",
        "accounts": [
            {
                "account_id": "cn_broker",
                "display_name": "A股账户",
                "institution_type": "brokerage",
                "base_currency": "CNY",
            },
            {
                "account_id": "ibkr_usd",
                "display_name": "IBKR 美元账户",
                "institution_type": "brokerage",
                "base_currency": "USD",
            },
        ],
        "positions": [
            {
                "position_id": "ibkr_usd_cash",
                "account_id": "ibkr_usd",
                "display_name": "IBKR 美元现金",
                "currency": "USD",
                "classification": {
                    "asset_class": "cash",
                    "product_type": "cash",
                    "subtype": "美元现金",
                    "exposure_tags": ["cash_like"],
                },
                "valuation_input": {
                    "method": "manual_amount",
                    "manual_amount": 10000.0,
                    "as_of": "2026-07-01",
                },
                "liquidity": {"tradable": True, "rebalance_eligible": True, "tier": "cash"},
                "instrument": None,
                "holding": None,
                "confirmed": True,
                "notes": None,
            },
        ],
    }


@pytest.fixture
def engine(tmp_path):
    (tmp_path / "financial_assets.json").write_text(
        json.dumps(_v2_doc(), ensure_ascii=False), encoding="utf-8",
    )
    config = deepcopy(MINIMAL_CONFIG)
    config["paths"]["local_data_dir"] = str(tmp_path)
    with patch("stocks.engine.load_engine_config", return_value=config):
        instance = StocksEngine()
    return instance


def _read_doc(engine, tmp_path) -> dict:
    return json.loads((tmp_path / "financial_assets.json").read_text("utf-8"))


class _FakeClient:
    """Fake LLM returning a canned intake diff; records the prompt."""

    def __init__(self, payload: dict):
        self.payload = payload
        self.prompts: list[str] = []

    def complete(self, prompt: str):
        self.prompts.append(prompt)
        return json.dumps(self.payload, ensure_ascii=False)


def _buy_payload() -> dict:
    return {
        "positions_to_add": [
            {
                "instrument_key": "us:AAPL",
                "display_name": "Apple Inc.",
                "account_id": "ibkr_usd",
                "currency": "USD",
                "quantity": 5,
                "cost_basis": 301.4,
                "product_type": "stock",
                "notes": "财报后大跌买入",
                "confidence": "low",
            }
        ],
        "positions_to_update": [
            {
                "position_id": "ibkr_usd_cash",
                "delta_amount": -1507.0,
                "notes": "买入 AAPL 5 股扣减",
            }
        ],
    }


class TestBuildIntakeDraft:
    def test_draft_with_llm_writes_nothing_and_injects_context(self, engine, tmp_path) -> None:
        client = _FakeClient(_buy_payload())
        before = intake_memory_hash(engine)

        result = build_intake_draft(engine, "买入5股AAPL，成本301.4", llm_client=client)

        assert result["success"] is True
        assert result["used_llm"] is True
        assert result["confirmation_token"]
        assert result["draft"]["positions_to_add"]
        assert result["draft"]["base_memory_hash"] == before
        # 记忆上下文注入 prompt（引用真实 account/position id）
        prompt = client.prompts[0]
        assert "ibkr_usd_cash" in prompt
        assert "cn_broker" in prompt
        # 未写文件
        assert intake_memory_hash(engine) == before

    def test_draft_without_client_falls_back_to_ambiguity(self, engine, tmp_path) -> None:
        before = intake_memory_hash(engine)
        result = build_intake_draft(engine, "随便说点什么没有代码也没有金额", llm_client=None)
        assert result["used_llm"] is False
        assert result["ambiguities"]
        assert intake_memory_hash(engine) == before

    def test_empty_text_rejected(self, engine) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            build_intake_draft(engine, "  ", llm_client=None)


class TestApplyIntakeDraft:
    def _draft_and_token(self, engine, payload: dict) -> tuple[dict, str]:
        client = _FakeClient(payload)
        result = build_intake_draft(engine, "买入5股AAPL，成本301.4", llm_client=client)
        return result["draft"], result["confirmation_token"]

    def test_full_round_trip_buy_with_cash_delta(self, engine, tmp_path) -> None:
        draft, token = self._draft_and_token(engine, _buy_payload())

        result = apply_intake_draft(engine, draft, token)

        assert result["success"] is True
        assert result["status"] == "applied"
        assert (tmp_path / result["backup_path"].split("/")[-1]).exists()

        doc = _read_doc(engine, tmp_path)
        positions = {p["position_id"]: p for p in doc["positions"]}
        aapl = positions.get("us_aapl")
        assert aapl is not None
        assert aapl["account_id"] == "ibkr_usd"
        assert aapl["currency"] == "USD"
        assert aapl["instrument"]["instrument_key"] == "us:AAPL"
        assert aapl["holding"]["quantity"] == 5.0
        assert aapl["holding"]["cost_basis"]["unit_cost"] == 301.4
        assert aapl["holding"]["cost_basis"]["cost_amount"] == 1507.0
        assert aapl["classification"]["asset_class"] == "equity"
        assert aapl["valuation_input"]["method"] == "market_quote"
        assert aapl["confirmed"] is True
        # 现金扣减
        cash = positions["ibkr_usd_cash"]
        assert cash["valuation_input"]["manual_amount"] == 10000.0 - 1507.0
        # engine 内存态已刷新
        assert any(p.position_id == "us_aapl" for p in engine._asset_positions_v2)

    def test_token_replay_rejected(self, engine, tmp_path) -> None:
        draft, token = self._draft_and_token(engine, _buy_payload())
        first = apply_intake_draft(engine, draft, token)
        assert first["status"] == "applied"

        replay = apply_intake_draft(engine, draft, token)
        assert replay["status"] == "rejected"
        # 文件未被二次修改：AAPL 仍只有一条
        doc = _read_doc(engine, tmp_path)
        assert sum(1 for p in doc["positions"] if p["position_id"] == "us_aapl") == 1

    def test_stale_memory_rejected(self, engine, tmp_path) -> None:
        draft, token = self._draft_and_token(engine, _buy_payload())
        # draft 之后文件被外部改动
        doc = _read_doc(engine, tmp_path)
        doc["positions"][0]["valuation_input"]["manual_amount"] = 9000.0
        (tmp_path / "financial_assets.json").write_text(
            json.dumps(doc, ensure_ascii=False), encoding="utf-8",
        )

        result = apply_intake_draft(engine, draft, token)
        assert result["status"] == "rejected"
        assert "stale" in result["reason"] or "changed" in result["reason"]
        # 外部改动未被覆盖
        doc2 = _read_doc(engine, tmp_path)
        assert doc2["positions"][0]["valuation_input"]["manual_amount"] == 9000.0

    def test_ambiguous_draft_rejected(self, engine, tmp_path) -> None:
        payload = {"ambiguities": [{"field": "amount", "reason": "missing"}]}
        draft, token = self._draft_and_token(engine, payload)
        before = intake_memory_hash(engine)

        result = apply_intake_draft(engine, draft, token)
        assert result["status"] == "rejected"
        assert "ambiguities" in result["reason"]
        assert intake_memory_hash(engine) == before

    def test_unknown_account_rejected_nothing_written(self, engine, tmp_path) -> None:
        payload = {
            "positions_to_add": [
                {
                    "instrument_key": "us:AAPL",
                    "display_name": "Apple",
                    "account_id": "no_such_account",
                    "quantity": 1,
                    "cost_basis": 300,
                }
            ]
        }
        draft, token = self._draft_and_token(engine, payload)
        before = intake_memory_hash(engine)

        result = apply_intake_draft(engine, draft, token)
        assert result["status"] == "rejected"
        assert "no_such_account" in result["reason"]
        assert intake_memory_hash(engine) == before
        assert not list(tmp_path.glob("financial_assets.intake-*.bak.json"))

    def test_account_inferred_by_currency(self, engine, tmp_path) -> None:
        payload = {
            "positions_to_add": [
                {
                    "instrument_key": "us:QQQ",
                    "display_name": "纳指ETF",
                    "quantity": 2,
                    "cost_basis": 500,
                    "product_type": "exchange_traded_fund",
                }
            ]
        }
        draft, token = self._draft_and_token(engine, payload)
        result = apply_intake_draft(engine, draft, token)
        assert result["status"] == "applied"
        doc = _read_doc(engine, tmp_path)
        qqq = next(p for p in doc["positions"] if p["position_id"] == "us_qqq")
        assert qqq["account_id"] == "ibkr_usd"  # 唯一 USD 账户
        assert qqq["classification"]["product_type"] == "exchange_traded_fund"

    def test_update_nonexistent_position_rejected(self, engine, tmp_path) -> None:
        payload = {"positions_to_update": [{"position_id": "ghost", "delta_amount": -100}]}
        draft, token = self._draft_and_token(engine, payload)
        before = intake_memory_hash(engine)

        result = apply_intake_draft(engine, draft, token)
        assert result["status"] == "rejected"
        assert "ghost" in result["reason"]
        assert intake_memory_hash(engine) == before

    def test_negative_cash_delta_rejected_nothing_written(self, engine, tmp_path) -> None:
        payload = {
            "positions_to_update": [
                {"position_id": "ibkr_usd_cash", "delta_amount": -99999.0}
            ]
        }
        draft, token = self._draft_and_token(engine, payload)
        before = intake_memory_hash(engine)

        result = apply_intake_draft(engine, draft, token)
        assert result["status"] == "rejected"
        assert "现金为负" in result["reason"]
        assert intake_memory_hash(engine) == before
        assert not list(tmp_path.glob("financial_assets.intake-*.bak.json"))

    def test_v1_file_rejected_with_migrate_hint(self, engine, tmp_path) -> None:
        (tmp_path / "financial_assets.json").write_text(
            json.dumps([{"name": "x", "platform": "y", "amount": 1, "asset_type": "现金"}]),
            encoding="utf-8",
        )
        engine._assets = engine._load_assets_from_file()
        result = apply_intake_draft(engine, {"draft_id": "d", "base_memory_hash": "h",
                                             "generated_at": "t"}, "token")
        assert result["status"] == "rejected"
        assert "migrate-v2" in result["reason"]


class TestCLI:
    def test_cli_draft_then_confirm(self, engine, tmp_path, capsys) -> None:
        from stocks.adapters.cli import CLIAdapter

        adapter = CLIAdapter(engine)
        client = _FakeClient(_buy_payload())

        with patch("stocks.engine.asset_intake_service.resolve_mainline_llm_client",
                   return_value=client):
            adapter.run(["--asset-intake", "买入5股AAPL，成本301.4"])
        out = json.loads(capsys.readouterr().out)
        assert out["success"] is True
        assert out["will_write"] is False
        assert out["confirmation_token"]
        # draft 阶段不写文件
        assert not list(tmp_path.glob("financial_assets.intake-*.bak.json"))

        adapter.run([
            "--asset-intake-confirm",
            "--draft-json", json.dumps(out["draft"], ensure_ascii=False),
            "--token", out["confirmation_token"],
        ])
        confirm = json.loads(capsys.readouterr().out)
        assert confirm["status"] == "applied"

        doc = _read_doc(engine, tmp_path)
        assert any(p["position_id"] == "us_aapl" for p in doc["positions"])
