"""DecisionEnvelope 顶层协议测试。"""

from __future__ import annotations

from stocks.domain.models import DecisionEnvelope
from stocks.engine.decision_contract import (
    DECISION_ENVELOPE_SCHEMA,
    validate_decision_envelope,
)

FINAL_REVIEW = "审查数据质量，明确采纳或推翻项，再结合用户当前意图输出最终分析。"


def _envelope(mode: str) -> DecisionEnvelope:
    return DecisionEnvelope(
        status="ok",
        mode_requested=mode,
        mode_used=mode,
        decision_plan={"schema_version": 1},
        agent_task={"contract": "self-contained"} if mode == "agent_delegate" else None,
        quality={"context": "mock"},
        final_analysis_instructions=FINAL_REVIEW,
    )


def test_internal_and_delegate_envelopes_have_identical_shape():
    internal = _envelope("internal_llm").to_dict()
    delegated = _envelope("agent_delegate").to_dict()

    assert set(internal) == set(delegated)
    assert set(internal) == set(DECISION_ENVELOPE_SCHEMA["required"])
    assert validate_decision_envelope(internal) == []
    assert validate_decision_envelope(delegated) == []


def test_setup_required_requires_structured_setup_payload():
    envelope = _envelope("internal_llm").to_dict()
    envelope["status"] = "setup_required"

    assert validate_decision_envelope(envelope) == [
        "setup_required 状态必须提供 setup_required"
    ]


def test_missing_and_unknown_fields_are_rejected():
    envelope = _envelope("internal_llm").to_dict()
    envelope.pop("quality")
    envelope["extra"] = True

    assert validate_decision_envelope(envelope) == [
        "缺少字段: quality",
        "未知字段: extra",
    ]
