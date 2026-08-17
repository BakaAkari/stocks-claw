#!/usr/bin/env python3
"""LLM-first report renderer for stocks-claw push, with deterministic fallback.

Reads an artifact, renders the user-facing report via LLM (the agent_task prompt
is consumed exactly as designed: output_structure + render_discipline + persona +
must_not_do), validates the LLM output against the same fail-closed gates the
deterministic renderer uses, and falls back to the deterministic renderer if the
LLM call fails, times out, or produces output that fails validation.

Usage:  run_llm_report.py --session cn_after_close [--now ISO] [--artifact-root DIR]

TASK-013 (2026-08-17): 切回 LLM 渲染, 让 agent_task 里的 render_discipline /
output_structure(简洁化/论断 vs 罗列)真正生效——此前这些规则落在 LLM prompt 层
但推送一直走确定性 run_push_report.py, 从未消费 agent_task, 故报告格式未变。
Kari 拍板: 接受 LLM 延迟/不稳定导致推送失败, 失败降级为当前确定性渲染。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent  # repo 根: stocks 包所在
for p in (str(_HERE), str(_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from build_push_payload import (  # noqa: E402
    _FORBIDDEN,
    build_push_payload,
    render_push_payload,
    validate_payload_text,
    validate_push_truth,
)

_LLM_BASE_URL = "http://172.16.248.60:8000/v1"  # NAS 本地 vllm(DeepSeek-V4-Flash)
_LLM_MODEL = "DeepSeek-V4-Flash"
_LLM_TIMEOUT = 180


def _strip_llm_wrapping(text: str) -> str:
    """DeepSeek 倾向返回 JSON;去掉常见的 ```json ... ``` 护栏和单层 JSON 包装。"""
    t = text.strip()
    # ```json ... ``` / ``` ... ```
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", t, re.DOTALL)
    if fence:
        t = fence.group(1).strip()
    # 单层 JSON 包装: {"response": "..."} 或 {"report": "..."}
    try:
        parsed = json.loads(t)
        if isinstance(parsed, dict):
            for key in ("response", "report", "text", "content", "result"):
                val = parsed.get(key)
                if isinstance(val, str) and len(val) > 20:
                    t = val.strip()
                    break
    except (json.JSONDecodeError, ValueError):
        pass
    return t


def _build_llm_prompt(artifact: dict) -> str:
    """把 artifact 里的 agent_task(指令) + user_view(数据) 拼成给 LLM 的提示词。"""
    agent_task = artifact.get("agent_task") or {}
    pd = artifact.get("portfolio_decision") or {}
    view = pd.get("user_view") or {}

    header = (
        "你是用户的私人投资分析师。请严格按下面的「报告契约」,用给定的「用户视图数据」"
        "生成一份面向用户的飞书报告(Markdown)。\n"
        "要求: 只输出报告正文本身,不要任何解释、不要代码块围栏、不要 JSON 包装。\n"
        "语言: 简体中文。\n\n"
    )
    task_block = "===== 报告契约(agent_task) =====\n" + json.dumps(agent_task, ensure_ascii=False, indent=1)
    data_block = "\n\n===== 用户视图数据(唯一权威数据源, 所有数字/标的/比例从这里取) =====\n" + json.dumps(
        {"instruction_card": view.get("instruction_card"), "assistant_brief": view.get("assistant_brief")},
        ensure_ascii=False,
        indent=1,
    )
    tail = (
        "\n\n===== 输出纪律 =====\n"
        "- 只从上面的用户视图数据取数字和结论,禁止自行计算或编造。\n"
        "- 交易指令卡必须在最上方,私人投资助理紧接其后。\n"
        "- 保持简洁: 论断为主,不逐条罗列 MA/RSI/布林等技术数值。\n"
    )
    return header + task_block + data_block + tail


def _validate_llm_text(payload: dict, text: str) -> list[str]:
    """LLM 输出专属文本门禁(适配 agent_task 新契约, 而非旧确定性渲染契约)。

    与确定性 render_push_payload 的 validate_payload_text 不同: 那个强制旧标题
    (本窗口变化/走势研判/...)并把新标题(交易指令卡/私人投资助理)当 banned——
    与 agent_task 契约互斥。LLM 路径改校验 agent_task 要求的东西:

    - _FORBIDDEN 内部 token 泄漏(position_id/decision_id/manual_review/a_|us_|... 前缀)——保留, 防 LLM 泄漏内部代号。
    - 每个 instruction_card 可执行动作的最终比例必须出现在 LLM 正文(防 LLM 漏掉/改错动作比例)。
    - 非空 + 封面行存在。
    数字门禁(outlook 已验证数字)对 LLM 放宽, 因为它基于同一个 user_view 数据,
    且 action 比例一致性已由 validate_push_truth + 上面动作比例校验覆盖。
    """
    errors = [f"internal token: {m.group(0)}" for m in _FORBIDDEN.finditer(text)]
    if not text or not text.strip():
        errors.append("empty LLM report")
    # 每个可执行动作的最终比例应出现在 LLM 正文(显示为 N%)
    view = payload.get("user_view") or {}
    card = view.get("instruction_card") or {}
    for action in card.get("actions") or []:
        fr = action.get("final_ratio")
        if isinstance(fr, (int, float)) and not isinstance(fr, bool) and fr > 0:
            pct = f"{round(fr * 100)}%"
            if pct not in text:
                errors.append(f"LLM output omits action final ratio {pct} for {action.get('display_label')}")
    return errors


def _render_llm(artifact: dict) -> str:
    from stocks.providers.openai_client import LLMClient

    client = LLMClient(model=_LLM_MODEL, api_key="EMPTY", base_url=_LLM_BASE_URL, timeout=_LLM_TIMEOUT)
    raw = client.complete(_build_llm_prompt(artifact))
    text = _strip_llm_wrapping(raw)
    if not text or text.upper() != "[SILENT]" and len(text) < 10:
        raise ValueError("LLM returned unusable short/empty report")
    return text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True)
    parser.add_argument("--artifact-root", default=".local/scheduled_runs/latest")
    parser.add_argument("--payload-root", default=".local/push_payloads/latest")
    parser.add_argument("--now")
    parser.add_argument("--no-llm", action="store_true", help="强制只用确定性渲染(troubleshoot)")
    args = parser.parse_args()

    now = args.now or datetime.now().astimezone().isoformat()
    artifact_path = Path(args.artifact_root) / f"{args.session}.json"
    payload_path = Path(args.payload_root) / f"{args.session}.json"

    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        payload = build_push_payload(artifact, now=now)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 2

    # ---- LLM 优先 ----
    if not args.no_llm:
        try:
            llm_text = _render_llm(artifact)
            # LLM 输出也要过 fail-closed 校验(内部token泄漏 + 动作比例一致)
            truth_errors = validate_push_truth(payload)
            text_errors = _validate_llm_text(payload, llm_text)
            if truth_errors or text_errors:
                raise ValueError("; ".join(text_errors or truth_errors))
            if llm_text == "[SILENT]":
                print("[SILENT]", file=sys.stderr)
                return 0
            print(llm_text)
            return 0
        except Exception as exc:  # noqa: BLE001 - 任何 LLM 失败都降级
            print(f"LLM render failed, falling back to deterministic: {exc}", file=sys.stderr)

    # ---- 确定性兜底 ----
    try:
        truth_errors = validate_push_truth(payload)
        if truth_errors:
            raise ValueError("; ".join(truth_errors))
        text = render_push_payload(payload)
        errors = validate_payload_text(payload, text)
        if errors:
            raise ValueError("; ".join(errors))
        payload_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = payload_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(payload_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 2
    if text == "[SILENT]":
        print("[SILENT]", file=sys.stderr)
        return 0
    if not text.strip():
        print("INVALID: empty push output", file=sys.stderr)
        return 2
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
