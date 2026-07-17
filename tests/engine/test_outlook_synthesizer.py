"""Tests for constrained OpenAI-compatible outlook synthesizer and cache."""
from __future__ import annotations

import json
import time

import pytest

NOW = "2026-07-17T14:30:00+00:00"


@pytest.fixture
def evidence() -> dict:
    """Minimal evidence for synthesizer tests."""
    return {
        "version": 1,
        "generated_at": NOW,
        "session": "cn_after_close",
        "market": "a",
        "intelligence_events": [
            {
                "event_id": "cluster-oil",
                "theme": "油价波动",
                "summary": "中东局势推高油价",
                "sources": [
                    {
                        "source": "Reuters",
                        "title": "Oil rises as shipping risk increases",
                        "url": "https://example.test/reuters-oil",
                        "published_at": "2026-07-17T07:30:00+00:00",
                    },
                ],
                "urgency": "high",
                "sentiment": "bearish",
            },
        ],
        "authorized_instruments": [
            {
                "display_label": "创业板ETF（159915）",
                "instrument_key": "a:159915",
                "asset_class": "equity",
                "product_type": "exchange_traded_fund",
                "exposure_tags": ["cn_equity", "tech"],
            },
        ],
        "confidence_cap": "high",
        "confidence_reasons": ["数据均在有效期内"],
        "portfolio_snapshot": {"total_value_cny": 1000000.0, "focus_positions": []},
        "asset_class_snapshot": {"equity": 1000000.0},
        "sector_snapshot": {},
        "technical_evidence": [],
        "rotation_evidence": [],
        "directional_intelligence": {"signal_count": 1, "signals": []},
        "macro_evidence": {},
        "upcoming_events": [],
        "risk_context": {},
        "data_boundaries": {},
    }


def _valid_outlook_dict() -> dict:
    """A minimal outlook that passes validation."""
    return {
        "status": "ok",
        "generated_at": NOW,
        "summary": "组合整体研判偏正面",
        "near_term": {"horizon": "1-2w", "direction": "supportive", "confidence": "high"},
        "medium_term": {"horizon": "1-3m", "direction": "supportive", "confidence": "medium"},
        "scenarios": {
            "base": {
                "label": "基准情景",
                "drivers": ["经济数据温和增长"],
                "portfolio_effect": "组合预计小幅上涨",
                "validation": ["GDP数据符合预期"],
                "invalidation": ["通胀超预期上行"],
                "probability": 0.5,
            },
            "bull": {
                "label": "乐观情景",
                "drivers": ["政策刺激超预期"],
                "portfolio_effect": "组合预计明显上涨",
                "validation": ["社融数据大幅超预期"],
                "invalidation": ["地缘风险突然升级"],
                "probability": 0.3,
            },
            "risk": {
                "label": "风险情景",
                "drivers": ["地缘冲突升级"],
                "portfolio_effect": "组合预计下跌",
                "validation": ["VIX指数持续高于25"],
                "invalidation": ["政策强力干预"],
                "probability": 0.2,
            },
        },
        "sector_views": [],
        "asset_views": [],
        "source_refs": [
            {
                "id": "src-reuters-oil",
                "source": "Reuters",
                "title": "Oil rises as shipping risk increases",
                "url": "https://example.test/reuters-oil",
                "published_at": "2026-07-17T07:30:00+00:00",
            },
        ],
        "confidence": "high",
        "forecast_candidates": [],
    }


def _chat_response(content: str) -> dict:
    """Build an OpenAI-compatible chat completion response."""
    return {
        "choices": [
            {
                "message": {
                    "content": content,
                }
            }
        ]
    }


def _valid_cfg(cache_dir: str = "/tmp/stocks_test_outlook_cache") -> dict:
    """Minimal config with outlook section."""
    return {
        "paths": {
            "secret_env_file": None,
        },
        "llm": {
            "outlook": {
                "enabled": True,
                "model": "deepseek-v4-pro",
                "api_key_env": "OPENAI_COMPATIBLE_API_KEY",
                "base_url_env": "OPENAI_COMPATIBLE_BASE_URL",
                "fallback_base_url": "http://100.121.167.1:8317/v1",
                "timeout_seconds": 120,
                "temperature": 0.2,
                "max_tokens": 3000,
                "cache_dir": cache_dir,
            }
        },
    }


def test_generate_returns_ok_for_valid_response(evidence, monkeypatch, tmp_path):
    """Standard JSON content from transport yields status=ok."""
    from stocks.engine.outlook_synthesizer import OutlookSynthesizer

    captured = {}

    def transport(req: dict) -> dict:
        captured.update(req)
        return _chat_response(json.dumps(_valid_outlook_dict(), ensure_ascii=False))

    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "sk-test-key")
    synth = OutlookSynthesizer(_valid_cfg(cache_dir=str(tmp_path)), transport=transport)
    result = synth.generate(evidence, now=NOW)
    assert result["status"] == "ok"
    assert result["generated_at"] == NOW


def test_request_has_json_object_response_format(evidence, monkeypatch, tmp_path):
    from stocks.engine.outlook_synthesizer import OutlookSynthesizer
    captured = {}

    def transport(req: dict) -> dict:
        captured.update(req)
        return _chat_response(json.dumps(_valid_outlook_dict(), ensure_ascii=False))

    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "sk-test-key")
    synth = OutlookSynthesizer(_valid_cfg(cache_dir=str(tmp_path)), transport=transport)
    synth.generate(evidence, now=NOW)
    assert captured["response_format"]["type"] == "json_object"


def test_request_contains_no_position_id(evidence, monkeypatch, tmp_path):
    from stocks.engine.outlook_synthesizer import OutlookSynthesizer
    captured = {}

    def transport(req: dict) -> dict:
        captured.update(req)
        return _chat_response(json.dumps(_valid_outlook_dict(), ensure_ascii=False))

    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "sk-test-key")
    synth = OutlookSynthesizer(_valid_cfg(cache_dir=str(tmp_path)), transport=transport)
    synth.generate(evidence, now=NOW)
    # Check only the user message (evidence), not the system prompt
    for msg in captured.get("messages", []):
        if msg.get("role") == "user":
            assert "position_id" not in msg.get("content", "")
            break


def test_fenced_json_extraction(evidence, monkeypatch, tmp_path):
    from stocks.engine.outlook_synthesizer import OutlookSynthesizer

    fenced = "```json\n" + json.dumps(_valid_outlook_dict(), ensure_ascii=False) + "\n```"

    def transport(req: dict) -> dict:
        return _chat_response(fenced)

    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "sk-test-key")
    synth = OutlookSynthesizer(_valid_cfg(cache_dir=str(tmp_path)), transport=transport)
    result = synth.generate(evidence, now=NOW)
    assert result["status"] == "ok"


def test_reasoning_content_fallback(evidence, monkeypatch, tmp_path):
    from stocks.engine.outlook_synthesizer import OutlookSynthesizer

    def transport(req: dict) -> dict:
        return {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "reasoning_content": json.dumps(_valid_outlook_dict(), ensure_ascii=False),
                    }
                }
            ]
        }

    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "sk-test-key")
    synth = OutlookSynthesizer(_valid_cfg(cache_dir=str(tmp_path)), transport=transport)
    result = synth.generate(evidence, now=NOW)
    assert result["status"] == "ok"


def test_invalid_json_returns_unavailable(evidence, monkeypatch, tmp_path):
    from stocks.engine.outlook_synthesizer import OutlookSynthesizer

    def transport(req: dict) -> dict:
        return _chat_response("not valid json at all")

    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "sk-test-key")
    synth = OutlookSynthesizer(_valid_cfg(cache_dir=str(tmp_path)), transport=transport)
    result = synth.generate(evidence, now=NOW)
    assert result["status"] == "unavailable"


def test_transport_exception_returns_unavailable(evidence, monkeypatch, tmp_path):
    from stocks.engine.outlook_synthesizer import OutlookSynthesizer

    def transport(req: dict) -> dict:
        raise ConnectionError("API unreachable")

    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "sk-test-key")
    synth = OutlookSynthesizer(_valid_cfg(cache_dir=str(tmp_path)), transport=transport)
    result = synth.generate(evidence, now=NOW)
    assert result["status"] == "unavailable"


def test_validator_failure_returns_unavailable(evidence, monkeypatch, tmp_path):
    from stocks.engine.outlook_synthesizer import OutlookSynthesizer

    bad = _valid_outlook_dict()
    del bad["scenarios"]

    def transport(req: dict) -> dict:
        return _chat_response(json.dumps(bad, ensure_ascii=False))

    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "sk-test-key")
    synth = OutlookSynthesizer(_valid_cfg(cache_dir=str(tmp_path)), transport=transport)
    result = synth.generate(evidence, now=NOW)
    assert result["status"] == "unavailable"
    assert "data_limitations" in result


def test_cache_hit_skips_transport(evidence, monkeypatch, tmp_path):
    from stocks.engine.outlook_synthesizer import OutlookSynthesizer

    cfg = _valid_cfg()
    cfg["llm"]["outlook"]["cache_dir"] = str(tmp_path)
    call_count = 0

    def transport(req: dict) -> dict:
        nonlocal call_count
        call_count += 1
        return _chat_response(json.dumps(_valid_outlook_dict(), ensure_ascii=False))

    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "sk-test-key")
    synth = OutlookSynthesizer(cfg, transport=transport)

    result1 = synth.generate(evidence, now=NOW)
    assert result1["status"] == "ok"
    assert call_count == 1

    result2 = synth.generate(evidence, now=NOW)
    assert result2["status"] == "ok"
    assert call_count == 1


def test_cache_expiry_after_24h(evidence, monkeypatch, tmp_path):
    from stocks.engine.outlook_synthesizer import OutlookCache, OutlookSynthesizer

    cache = OutlookCache(tmp_path)
    old = _valid_outlook_dict()
    old["_cached_at"] = time.time() - 86401
    old["_evidence_hash"] = "stale-hash"
    cache.save("cn_after_close", "stale-hash", old)

    cfg = _valid_cfg()
    cfg["llm"]["outlook"]["cache_dir"] = str(tmp_path)
    call_count = 0

    def transport(req: dict) -> dict:
        nonlocal call_count
        call_count += 1
        return _chat_response(json.dumps(_valid_outlook_dict(), ensure_ascii=False))

    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "sk-test-key")
    synth = OutlookSynthesizer(cfg, transport=transport)
    result = synth.generate(evidence, now=NOW)
    assert result["status"] == "ok"
    assert call_count == 1


def test_disabled_returns_unavailable(evidence, monkeypatch, tmp_path):
    from stocks.engine.outlook_synthesizer import OutlookSynthesizer

    cfg = _valid_cfg()
    cfg["llm"]["outlook"]["enabled"] = False
    called = False

    def transport(req: dict) -> dict:
        nonlocal called
        called = True
        return _chat_response(json.dumps(_valid_outlook_dict(), ensure_ascii=False))

    synth = OutlookSynthesizer(cfg, transport=transport)
    result = synth.generate(evidence, now=NOW)
    assert result["status"] == "unavailable"
    assert not called


def test_no_api_key_returns_unavailable(evidence, monkeypatch, tmp_path):
    from stocks.engine.outlook_synthesizer import OutlookSynthesizer

    # Ensure env var is absent
    monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)

    cfg = _valid_cfg()

    def transport(req: dict) -> dict:
        return _chat_response(json.dumps(_valid_outlook_dict(), ensure_ascii=False))

    synth = OutlookSynthesizer(cfg, transport=transport)
    result = synth.generate(evidence, now=NOW)
    assert result["status"] == "unavailable"


def test_retry_temperature_once_on_error(evidence, monkeypatch, tmp_path):
    from stocks.engine.outlook_synthesizer import OutlookSynthesizer

    attempts = []

    def transport(req: dict) -> dict:
        attempts.append(req.get("temperature"))
        if len(attempts) == 1:
            return {"error": {"message": "The model deepseek-v4-pro only supports temperature=1"}}
        return _chat_response(json.dumps(_valid_outlook_dict(), ensure_ascii=False))

    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "sk-test-key")
    synth = OutlookSynthesizer(_valid_cfg(cache_dir=str(tmp_path)), transport=transport)
    result = synth.generate(evidence, now=NOW)
    assert result["status"] == "ok"
    assert len(attempts) == 2
    assert attempts[0] == 0.2
    assert attempts[1] == 1.0


def test_request_only_evidence_no_position_id(evidence, monkeypatch, tmp_path):
    from stocks.engine.outlook_synthesizer import OutlookSynthesizer
    captured = {}

    def transport(req: dict) -> dict:
        captured.update(req)
        return _chat_response(json.dumps(_valid_outlook_dict(), ensure_ascii=False))

    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "sk-test-key")
    synth = OutlookSynthesizer(_valid_cfg(cache_dir=str(tmp_path)), transport=transport)
    synth.generate(evidence, now=NOW)
    user_msg = captured.get("messages", [{}])[-1].get("content", "")
    parsed = json.loads(user_msg)
    assert "intelligence_events" in parsed
    assert "position_id" not in parsed


def test_outlook_cache_save_load(tmp_path):
    from stocks.engine.outlook_synthesizer import OutlookCache

    cache = OutlookCache(tmp_path)
    outlook = {"status": "ok", "summary": "test"}
    cache.save("test_session", "hash123", outlook)
    loaded = cache.load("test_session")
    assert loaded is not None
    assert loaded["status"] == "ok"
    assert loaded["summary"] == "test"


def test_outlook_cache_miss_returns_none(tmp_path):
    from stocks.engine.outlook_synthesizer import OutlookCache

    cache = OutlookCache(tmp_path)
    result = cache.load("nonexistent")
    assert result is None
