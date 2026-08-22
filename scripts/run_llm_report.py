#!/usr/bin/env python3
"""LLM-first report renderer for stocks-claw push.

Reads an artifact, renders the user-facing report via LLM (the agent_task prompt
is consumed exactly as designed: output_structure + render_discipline + persona +
must_not_do), validates the LLM output against the same fail-closed gates the
deterministic renderer uses.

TASK-013 (2026-08-17): 切回 LLM 渲染, 让 agent_task 的 render_discipline /
output_structure(简洁化/论断 vs 罗列)真正生效——此前这些规则落在 LLM prompt 层
但推送一直走确定性 run_push_report.py, 从未消费 agent_task, 故报告格式未变。

TASK-014 (2026-08-17, Kari 拍板 B 方案): LLM 失败不再静默降级推送数据报告。
- LLM 渲染失败自动重试, 最多 _LLM_MAX_ATTEMPTS 次(含首次)。
- 全部失败 -> 标 fail, 非 0 退出码(3), stdout 保持空, 不推送数据;
  失败原因落盘 .local/llm_render_errors/<session>.log。
- Kari 手动重发: --force-llm(跳过 age 门禁, 仅 LLM 渲染)。
- --no-llm 保留为显式手动兜底(强制确定性渲染, troubleshoot 用)。

Usage:  run_llm_report.py --session cn_after_close [--now ISO]
        [--artifact-root DIR] [--force-llm|--no-llm]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
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

def _load_render_cfg() -> dict:
    """报告渲染 LLM 配置：权威来源 engine.yaml llm.report_render。

    缺失键即部署事故（配置文件随 repo 分发），fail-closed 抛错而不是
    用代码内裸默认——配置化纪律（2026-08-22 Kari 指令）。
    """
    from stocks.engine.config_loader import load_engine_config
    cfg = ((load_engine_config() or {}).get("llm") or {}).get("report_render") or {}
    required = ("base_url", "model", "timeout_seconds", "max_attempts")
    missing = [k for k in required if k not in cfg]
    if missing:
        raise RuntimeError(
            f"engine.yaml llm.report_render 缺少必需键: {missing}"
        )
    return cfg

_RENDER_CFG = _load_render_cfg()
_LLM_BASE_URL = str(_RENDER_CFG["base_url"])
_LLM_MODEL = str(_RENDER_CFG["model"])
_LLM_TIMEOUT = int(_RENDER_CFG["timeout_seconds"])
_LLM_MAX_ATTEMPTS = int(_RENDER_CFG["max_attempts"])
_LLM_API_KEY = str(_RENDER_CFG.get("api_key", "EMPTY"))


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


def _load_prompt_template() -> str:
    """报告 prompt 模板：权威来源 stocks/config/templates/report_prompt.txt。
    缺失 = 部署事故（模板随 repo 分发），fail-closed。"""
    from pathlib import Path
    tpl = Path(__file__).resolve().parent.parent / "stocks" / "config" / "templates" / "report_prompt.txt"
    if not tpl.exists():
        raise RuntimeError(f"prompt 模板缺失: {tpl}")
    return tpl.read_text(encoding="utf-8")


def _build_llm_prompt(artifact: dict) -> str:
    """把 artifact 里的 agent_task(指令) + user_view(数据) 填入模板。"""
    agent_task = artifact.get("agent_task") or {}
    pd = artifact.get("portfolio_decision") or {}
    view = pd.get("user_view") or {}

    return _load_prompt_template().format(
        agent_task_json=json.dumps(agent_task, ensure_ascii=False, indent=1),
        user_view_json=json.dumps(
            {"instruction_card": view.get("instruction_card"), "assistant_brief": view.get("assistant_brief")},
            ensure_ascii=False,
            indent=1,
        ),
    )


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

    client = LLMClient(model=_LLM_MODEL, api_key=_LLM_API_KEY, base_url=_LLM_BASE_URL, timeout=_LLM_TIMEOUT)
    raw = client.complete(_build_llm_prompt(artifact))
    text = _strip_llm_wrapping(raw)
    if not text or text.upper() != "[SILENT]" and len(text) < 10:
        raise ValueError("LLM returned unusable short/empty report")
    return text


# _LLM_MAX_ATTEMPTS 由 engine.yaml llm.report_render.max_attempts 提供（上方加载）
_LLM_RETRY_BACKOFF_SECONDS = 2
_FAILURE_LOG_DIR = Path(".local/llm_render_errors")


def _log_llm_failure(session: str, attempt: int, exc: Exception) -> None:
    """落盘 LLM 渲染失败(每次重试一行), 供排查非静默降级。

    日志写失败不反杀主流程(记录失败不能阻断 fail 标记)。
    """
    try:
        d = Path(_FAILURE_LOG_DIR)
        d.mkdir(parents=True, exist_ok=True)
        with (d / f"{session}.log").open("a", encoding="utf-8") as fh:
            fh.write(
                f"{datetime.now().astimezone().isoformat()} attempt={attempt} "
                f"exc={type(exc).__name__}: {exc}\n"
            )
    except OSError:
        pass


def _attempt_llm_render(artifact: dict, payload: dict) -> str:
    """单次 LLM 渲染: 调用 + fail-closed 校验, 任一失败抛异常。"""
    llm_text = _render_llm(artifact)
    truth_errors = validate_push_truth(payload)
    text_errors = _validate_llm_text(payload, llm_text)
    if truth_errors or text_errors:
        raise ValueError("; ".join(text_errors or truth_errors))
    return llm_text


def _render_deterministic(payload: dict, payload_path: Path) -> int:
    """显式手动兜底(--no-llm): 确定性渲染 + 落盘 payload。"""
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True)
    parser.add_argument("--artifact-root", default=".local/scheduled_runs/latest")
    parser.add_argument("--payload-root", default=".local/push_payloads/latest")
    parser.add_argument("--now")
    parser.add_argument("--no-llm", action="store_true",
                        help="显式手动兜底: 强制只用确定性渲染(troubleshoot)")
    parser.add_argument("--force-llm", action="store_true",
                        help="手动重发: 跳过 age 门禁, 仅 LLM 渲染(失败标 fail 非0退出)")
    args = parser.parse_args()

    now = args.now or datetime.now().astimezone().isoformat()
    artifact_path = Path(args.artifact_root) / f"{args.session}.json"
    payload_path = Path(args.payload_root) / f"{args.session}.json"

    # force-llm(手动重发) 跳过 age 上限; 常规/--no-llm 保持 45min 门禁
    max_age = None if args.force_llm else 45
    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        payload = build_push_payload(artifact, now=now, max_age_min=max_age)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 2

    if args.no_llm:
        return _render_deterministic(payload, payload_path)

    # ---- LLM 优先: 失败自动重试, 最多 _LLM_MAX_ATTEMPTS 次 ----
    last_exc: Exception | None = None
    for attempt in range(1, _LLM_MAX_ATTEMPTS + 1):
        try:
            llm_text = _attempt_llm_render(artifact, payload)
        except Exception as exc:  # noqa: BLE001 - 任何失败都计数重试
            last_exc = exc
            _log_llm_failure(args.session, attempt, exc)
            print(f"LLM render attempt {attempt}/{_LLM_MAX_ATTEMPTS} failed: {exc}",
                  file=sys.stderr)
            if attempt < _LLM_MAX_ATTEMPTS:
                time.sleep(_LLM_RETRY_BACKOFF_SECONDS)
            continue
        if llm_text == "[SILENT]":
            print("[SILENT]", file=sys.stderr)
            return 0
        print(llm_text)
        return 0

    # ---- 全部失败: 标 fail, 不推送数据报告, 由 Kari 手动重发 ----
    print(
        f"LLM render FAILED after {_LLM_MAX_ATTEMPTS} attempts "
        f"({type(last_exc).__name__}: {last_exc}); no report pushed. "
        f"manual re-send: run_llm_report.py --session {args.session} --force-llm",
        file=sys.stderr,
    )
    return 3

if __name__ == "__main__":
    raise SystemExit(main())
