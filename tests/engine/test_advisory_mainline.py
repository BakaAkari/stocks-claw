"""Tests for the M2 advisory mainline (advisory-driven structured_outlook).

The mainline orchestrates snapshot → LLM synthesis → contract validation →
projection for primary sessions.  Every failure path must degrade to an
honest 研判待复核 unavailable outlook without calling the LLM when the
freshness gate blocks, and without fabricating a judgment.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from stocks.domain.models import AnalysisContext, MarketState, PortfolioMapping
from stocks.engine.advisory_mainline import (
    build_advisory_outlook,
    resolve_mainline_llm_client,
)
from stocks.engine.outlook_delta import compute_outlook_delta

NOW = "2026-07-30T15:00:00+08:00"


def _context(*, freshness: str = "fresh", generated_at: str = NOW) -> AnalysisContext:
    return AnalysisContext(
        generated_at=generated_at,
        assets=[],
        asset_count=0,
        portfolio_constraints={},
        portfolio_profile={},
        quotes={},
        news=[],
        news_count=0,
        market_state=MarketState(),
        portfolio_mapping=PortfolioMapping(),
        drift_checks=[],
        recent_snapshots=[],
        raw_prompt_input="test",
        data_quality={
            "quotes": {
                "by_market": {
                    "a": {"status": "ok", "freshness": freshness},
                    "us": {"status": "ok", "freshness": freshness},
                },
            },
        },
    )


def _valid_advisory_payload() -> dict:
    outlook = {
        "direction": "supportive",
        "confidence": "medium",
        "rationale": "流动性边际宽松，指数站上关键均线",
        "drivers": ["央行公开市场净投放", "成交回暖"],
        "validation": "未来一周成交额维持在万亿以上",
        "falsification": "成交额连续三日萎缩至 8000 亿以下",
        "source_refs": ["fact:a:000001:close", "src-macro-1"],
    }
    return {
        "market_assessment": "市场处于温和修复阶段，结构性机会占优",
        "portfolio_assessment": "维持中性仓位",
        "short_term": outlook,
        "medium_term": {
            **outlook,
            "direction": "neutral",
            "rationale": "盈利修复尚需数据确认",
            "falsification": "季度盈利增速连续两期下修",
        },
        "scenarios": [
            {
                "name": "base",
                "description": "震荡修复",
                "trigger": "成交额持续放大",
                "invalidation": "跌破年线",
                "evidence_refs": ["src-macro-1"],
                "confidence": "medium",
            },
            {
                "name": "risk",
                "description": "外部冲击",
                "trigger": "美债利率快速上行",
                "invalidation": "外部流动性转松",
                "evidence_refs": ["src-macro-2"],
                "confidence": "low",
            },
        ],
        "sector_opportunities": [
            {
                "action_id": "sec1",
                "target": "半导体",
                "action": "add",
                "size": "info_only",
                "size_type": "defer",
                "reasoning": "国产替代订单改善",
                "evidence_refs": ["src-sector-1"],
            }
        ],
        "asset_class_opportunities": [
            {
                "action_id": "ac1",
                "target": "黄金",
                "action": "reduce",
                "size": "info_only",
                "size_type": "defer",
                "reasoning": "避险溢价回落",
                "evidence_refs": ["src-macro-2"],
            }
        ],
        "forecast_candidates": [
            {
                "forecast_id": "fc1",
                "statement": "沪深300 在 2026-08-31 前高于 4200",
                "target": "a:000300",
                "metric": "close",
                "comparator": "above",
                "level": "4200",
                "deadline": "2026-08-31",
                "confidence": "low",
                "evidence_refs": ["fact:a:000001:close"],
            },
            {
                "forecast_id": "fc2",
                "statement": "不可量化(level 非数值)应被跳过",
                "target": "a:000300",
                "metric": "close",
                "comparator": "above",
                "level": "很高",
                "deadline": "2026-08-31",
                "confidence": "low",
                "evidence_refs": ["fact:a:000001:close"],
            },
        ],
        "next_checkpoints": ["下周复查成交额"],
        "data_limitations": ["宏观数据滞后一周"],
    }


class _FakeClient:
    """Fake LLM client returning a fixed advisory payload; records calls."""

    def __init__(self, payload: dict | None = None):
        self.payload = payload if payload is not None else _valid_advisory_payload()
        self.calls = 0

    def complete(self, prompt: str):
        self.calls += 1
        return json.dumps(self.payload, ensure_ascii=False)


class TestBuildAdvisoryOutlook:
    def test_ok_with_fake_client(self) -> None:
        client = _FakeClient()
        outlook = build_advisory_outlook(
            _context(), session_id="cn_after_close", market="cn",
            llm_client=client, now=NOW,
        )
        assert client.calls == 1
        assert outlook["status"] == "ok"
        assert outlook["summary"].startswith("市场处于温和修复")

        near = outlook["near_term"]
        assert near["horizon"] == "3-7天"
        assert near["direction"] == "supportive"
        assert near["confidence"] == "medium"
        assert near["rationale"]
        assert near["validation"]
        assert near["falsification"]

        medium = outlook["medium_term"]
        assert medium["horizon"] == "1-3个月"
        assert medium["direction"] == "neutral"

        scenarios = outlook["scenarios"]
        assert set(scenarios) == {"base", "risk"}
        assert scenarios["base"]["validation"] == ["成交额持续放大"]
        assert scenarios["base"]["invalidation"] == ["跌破年线"]

        source_ids = [r["id"] for r in outlook["source_refs"]]
        assert "fact:a:000001:close" in source_ids
        assert len(source_ids) == len(set(source_ids))

        assert outlook["sector_views"][0]["sector"] == "半导体"
        assert outlook["sector_views"][0]["direction"] == "supportive"
        assert outlook["asset_views"][0]["asset_class"] == "黄金"
        assert outlook["asset_views"][0]["direction"] == "adverse"

        # level 转 float；不可转换的候选被跳过
        candidates = outlook["forecast_candidates"]
        assert len(candidates) == 1
        assert candidates[0]["level"] == 4200.0
        assert candidates[0]["source_ref_ids"] == ["fact:a:000001:close"]

        receipt = outlook["advisory_receipt"]
        assert receipt["status"] in {"ok", "warnings"}
        assert not receipt["errors"]

    def test_no_client_returns_unavailable(self) -> None:
        outlook = build_advisory_outlook(
            _context(), session_id="cn_after_close", market="cn",
            llm_client=None, now=NOW,
        )
        assert outlook["status"] == "unavailable"
        assert outlook["message"] == "研判待复核：LLM 分析端未配置"

    def test_auto_resolution_without_config_returns_unavailable(self, monkeypatch, tmp_path) -> None:
        monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_COMPATIBLE_BASE_URL", raising=False)
        monkeypatch.chdir(tmp_path)  # 隔离 repo .secret/
        outlook = build_advisory_outlook(
            _context(), session_id="cn_after_close", market="cn",
            config={"llm": {"outlook": {}}}, llm_client="auto", now=NOW,
        )
        assert outlook["status"] == "unavailable"
        assert outlook["message"] == "研判待复核：LLM 分析端未配置"

    def test_stale_quotes_blocks_before_llm(self) -> None:
        client = _FakeClient()
        outlook = build_advisory_outlook(
            _context(freshness="stale"), session_id="cn_after_close", market="cn",
            llm_client=client, now=NOW,
        )
        assert outlook["status"] == "unavailable"
        assert outlook["message"] == "研判待复核：目标市场行情数据过旧或缺失"
        assert client.calls == 0

    def test_missing_market_entry_blocks(self) -> None:
        context = _context()
        context.data_quality["quotes"]["by_market"] = {}
        client = _FakeClient()
        outlook = build_advisory_outlook(
            context, session_id="cn_after_close", market="cn",
            llm_client=client, now=NOW,
        )
        assert outlook["status"] == "unavailable"
        assert client.calls == 0

    def test_old_snapshot_blocks_before_llm(self) -> None:
        old = (
            datetime.fromisoformat(NOW) - timedelta(minutes=120)
        ).isoformat()
        client = _FakeClient()
        outlook = build_advisory_outlook(
            _context(generated_at=old), session_id="cn_after_close", market="cn",
            llm_client=client, now=NOW,
        )
        assert outlook["status"] == "unavailable"
        assert outlook["message"] == "研判待复核：数据快照过旧"
        assert client.calls == 0

    def test_missing_falsification_fails_validation(self) -> None:
        payload = _valid_advisory_payload()
        payload["short_term"] = {**payload["short_term"], "falsification": ""}
        client = _FakeClient(payload)
        outlook = build_advisory_outlook(
            _context(), session_id="cn_after_close", market="cn",
            llm_client=client, now=NOW,
        )
        assert client.calls == 1
        assert outlook["status"] == "unavailable"
        assert outlook["message"] == "研判待复核：本期研判未通过校验"

    def test_llm_error_fallback_returns_unavailable(self) -> None:
        class BadClient:
            def complete(self, prompt: str) -> str:
                return "not valid json"

        outlook = build_advisory_outlook(
            _context(), session_id="cn_after_close", market="cn",
            llm_client=BadClient(), now=NOW,
        )
        assert outlook["status"] == "unavailable"
        assert outlook["message"] == "研判待复核：LLM 分析暂不可用，下期重试"

    def test_two_outlooks_feed_outlook_delta(self) -> None:
        earlier = build_advisory_outlook(
            _context(), session_id="cn_after_close", market="cn",
            llm_client=_FakeClient(), now="2026-07-29T15:00:00+08:00",
        )
        later_payload = _valid_advisory_payload()
        later_payload["market_assessment"] = "市场转为谨慎，防御为先"
        later_payload["short_term"] = {
            **later_payload["short_term"], "direction": "adverse",
        }
        later = build_advisory_outlook(
            _context(), session_id="cn_after_close", market="cn",
            llm_client=_FakeClient(later_payload), now=NOW,
        )
        assert earlier["status"] == later["status"] == "ok"

        delta = compute_outlook_delta(
            {"structured_outlook": earlier, "session": "cn_after_close",
             "generated_at": "2026-07-29T15:00:00+08:00", "market": "cn"},
            {"structured_outlook": later, "session": "cn_after_close",
             "generated_at": NOW, "market": "cn"},
        )
        assert delta
        changes = delta["changes"]
        assert changes["summary"]["to"] == "市场转为谨慎，防御为先"
        assert changes["near_term"]["direction"] == {"from": "supportive", "to": "adverse"}


class TestResolveMainlineLLMClient:
    def test_missing_env_and_secret_returns_none(self, monkeypatch, tmp_path) -> None:
        monkeypatch.delenv("TEST_M2_KEY", raising=False)
        monkeypatch.delenv("TEST_M2_URL", raising=False)
        monkeypatch.chdir(tmp_path)
        config = {"llm": {"outlook": {
            "model": "deepseek-v4-pro",
            "api_key_env": "TEST_M2_KEY",
            "base_url_env": "TEST_M2_URL",
        }}}
        assert resolve_mainline_llm_client(config) is None

    def test_env_credentials_build_client(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("TEST_M2_KEY", "sk-test")
        monkeypatch.setenv("TEST_M2_URL", "https://llm.internal/v1")
        monkeypatch.chdir(tmp_path)
        config = {"llm": {"outlook": {
            "model": "deepseek-v4-pro",
            "api_key_env": "TEST_M2_KEY",
            "base_url_env": "TEST_M2_URL",
            "timeout_seconds": 60,
        }}}
        client = resolve_mainline_llm_client(config)
        assert client is not None
        assert client.model == "deepseek-v4-pro"
        assert client.api_key == "sk-test"
        assert client.base_url == "https://llm.internal/v1"
        assert client.timeout == 60

    def test_secret_file_fallback(self, monkeypatch, tmp_path) -> None:
        monkeypatch.delenv("TEST_M2_KEY", raising=False)
        monkeypatch.delenv("TEST_M2_URL", raising=False)
        monkeypatch.chdir(tmp_path)
        secret = tmp_path / ".secret"
        secret.mkdir()
        (secret / "openai-key.md").write_text("sk-from-secret\n", encoding="utf-8")
        (secret / "openai-base-url.md").write_text("https://llm.secret/v1\n", encoding="utf-8")
        config = {"llm": {"outlook": {
            "api_key_env": "TEST_M2_KEY",
            "base_url_env": "TEST_M2_URL",
        }}}
        client = resolve_mainline_llm_client(config)
        assert client is not None
        assert client.api_key == "sk-from-secret"
        assert client.base_url == "https://llm.secret/v1"


class TestScheduledAnalysisWiring:
    """run_occurrence consumes build_advisory_outlook for primary sessions."""

    def _config(self, tmp_path, *, mainline_enabled=True) -> dict:
        return {
            "schema_version": 1,
            "user_timezone": "Asia/Shanghai",
            "artifact_dir": str(tmp_path / "scheduled_runs"),
            "default_duplicate_window_minutes": 90,
            "quiet_hours": {"enabled": True, "start": "00:00", "end": "07:30",
                            "timezone": "Asia/Shanghai", "allow_critical": True},
            "llm": {"advisory_mainline": {"enabled": mainline_enabled}},
            "markets": {
                "cn": {
                    "enabled": True, "exchange_timezone": "Asia/Shanghai",
                    "holidays": [],
                    "sessions": [
                        {"id": "cn_after_close", "time": "15:30",
                         "intent": "after_close_review", "push": "normal"},
                    ],
                },
            },
        }

    def test_primary_session_uses_mainline(self, tmp_path, monkeypatch) -> None:
        import stocks.engine.scheduled_analysis as scheduled_module
        from stocks.engine.scheduled_analysis import ScheduledAnalysisRunner
        from tests.engine.test_scheduled_analysis import FakeEngine, _context, _run

        outlook = {
            "status": "ok",
            "generated_at": NOW,
            "summary": "组合研判",
            "near_term": {"horizon": "3-7天", "direction": "supportive",
                          "confidence": "medium", "rationale": "r",
                          "validation": "v", "falsification": "f"},
            "advisory_receipt": {"status": "ok", "errors": [], "warnings": []},
            "forecast_candidates": [],
        }
        calls = []

        def fake_mainline(context, *, session_id, market, config=None, now=""):
            calls.append({"session_id": session_id, "market": market})
            return dict(outlook)

        monkeypatch.setattr(scheduled_module, "build_advisory_outlook", fake_mainline)

        config = self._config(tmp_path)
        runner = ScheduledAnalysisRunner(
            FakeEngine(_context()), config=config, artifact_dir=config["artifact_dir"],
        )
        result = _run(runner.run_session(
            "cn_after_close", now=datetime.fromisoformat("2026-07-06T15:00:00+08:00"),
        ))
        artifact = json.loads(Path(result["paths"]["json_path"]).read_text())

        assert calls == [{"session_id": "cn_after_close", "market": "cn"}]
        assert artifact["structured_outlook"]["status"] == "ok"
        assert artifact["structured_outlook"]["near_term"]["direction"] == "supportive"
        assert artifact["advisory_receipt"]["status"] == "ok"
        brief = artifact["portfolio_decision"]["user_view"]["assistant_brief"]
        assert brief["outlook"]["summary"] == "组合研判"
        assert brief["outlook"]["near_term"]["falsification"] == "f"

    def test_mainline_unavailable_degrades_report(self, tmp_path, monkeypatch) -> None:
        import stocks.engine.scheduled_analysis as scheduled_module
        from stocks.engine.scheduled_analysis import ScheduledAnalysisRunner
        from tests.engine.test_scheduled_analysis import FakeEngine, _context, _run

        def fake_mainline(context, *, session_id, market, config=None, now=""):
            return {
                "status": "unavailable",
                "generated_at": NOW,
                "message": "研判待复核：LLM 分析端未配置",
                "data_limitations": [],
            }

        monkeypatch.setattr(scheduled_module, "build_advisory_outlook", fake_mainline)

        config = self._config(tmp_path)
        runner = ScheduledAnalysisRunner(
            FakeEngine(_context()), config=config, artifact_dir=config["artifact_dir"],
        )
        result = _run(runner.run_session(
            "cn_after_close", now=datetime.fromisoformat("2026-07-06T15:00:00+08:00"),
        ))
        artifact = json.loads(Path(result["paths"]["json_path"]).read_text())

        assert artifact["structured_outlook"]["status"] == "unavailable"
        assert artifact["forecast_candidates"] == []
        assert "advisory_receipt" not in artifact
        assert "portfolio_decision" in artifact

    def test_mainline_disabled_uses_legacy_synthesizer(self, tmp_path) -> None:
        from stocks.engine.scheduled_analysis import ScheduledAnalysisRunner
        from tests.engine.test_scheduled_analysis import (
            FakeEngine,
            FakeSynth,
            _context,
            _run,
        )

        config = self._config(tmp_path, mainline_enabled=False)
        runner = ScheduledAnalysisRunner(
            FakeEngine(_context()), config=config, artifact_dir=config["artifact_dir"],
        )
        fake = FakeSynth()
        runner.outlook_synthesizer = fake

        result = _run(runner.run_session(
            "cn_after_close", now=datetime.fromisoformat("2026-07-06T15:00:00+08:00"),
        ))
        artifact = json.loads(Path(result["paths"]["json_path"]).read_text())

        assert fake.calls == 1
        assert artifact["structured_outlook"]["status"] == "ok"
        assert "outlook_evidence_meta" in artifact
