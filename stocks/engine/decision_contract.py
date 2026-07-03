"""DecisionEnvelope 的唯一机器可校验契约。"""

from __future__ import annotations

from typing import Any

from stocks.domain.models import DecisionEnvelope

DECISION_STATUSES = {
    "ok",
    "degraded",
    "setup_required",
    "validation_failed",
    "failed",
}
DECISION_MODES = {"internal_llm", "agent_delegate", "deterministic_only"}

DECISION_ENVELOPE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://stocks-claw.local/schema/decision-envelope-v1.json",
    "title": "DecisionEnvelope",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "status",
        "mode_requested",
        "mode_used",
        "decision_plan",
        "agent_task",
        "setup_required",
        "quality",
        "errors",
        "final_analysis_instructions",
    ],
    "properties": {
        "status": {"type": "string", "enum": sorted(DECISION_STATUSES)},
        "mode_requested": {"type": "string", "minLength": 1},
        "mode_used": {"type": "string", "enum": sorted(DECISION_MODES)},
        "decision_plan": {"type": ["object", "null"]},
        "agent_task": {"type": ["object", "null"]},
        "setup_required": {"type": ["object", "null"]},
        "quality": {"type": "object"},
        "errors": {"type": "array", "items": {"type": "string"}},
        "final_analysis_instructions": {"type": "string", "minLength": 1},
    },
}


def validate_decision_envelope(value: Any) -> list[str]:
    """不依赖外部 jsonschema 包的等价顶层校验器。"""
    if isinstance(value, DecisionEnvelope):
        value = value.to_dict()
    if not isinstance(value, dict):
        return ["envelope 必须是 object"]

    required = set(DECISION_ENVELOPE_SCHEMA["required"])
    errors = [f"缺少字段: {key}" for key in sorted(required - set(value))]
    unknown = set(value) - required
    errors.extend(f"未知字段: {key}" for key in sorted(unknown))
    if errors:
        return errors

    if value["status"] not in DECISION_STATUSES:
        errors.append("status 非法")
    if not isinstance(value["mode_requested"], str) or not value[
        "mode_requested"
    ].strip():
        errors.append("mode_requested 必须是非空字符串")
    if value["mode_used"] not in DECISION_MODES:
        errors.append("mode_used 非法")
    for key in ("decision_plan", "agent_task", "setup_required"):
        if value[key] is not None and not isinstance(value[key], dict):
            errors.append(f"{key} 必须是 object 或 null")
    if not isinstance(value["quality"], dict):
        errors.append("quality 必须是 object")
    if not isinstance(value["errors"], list) or not all(
        isinstance(item, str) for item in value["errors"]
    ):
        errors.append("errors 必须是字符串数组")
    if not isinstance(value["final_analysis_instructions"], str) or not value[
        "final_analysis_instructions"
    ].strip():
        errors.append("final_analysis_instructions 必须是非空字符串")
    if value["status"] == "setup_required" and value["setup_required"] is None:
        errors.append("setup_required 状态必须提供 setup_required")
    return errors
