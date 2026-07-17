#!/usr/bin/env python3
"""Build and render a sanitized push payload from a scheduled-run artifact."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SESSION_LABELS = {
    "cn_pre_open": "A股盘前",
    "cn_open_watch": "A股开盘观察",
    "cn_morning_close": "A股午间检查",
    "cn_afternoon_open": "A股午后检查",
    "cn_pre_close": "A股收盘前",
    "cn_after_close": "A股盘后复盘",
    "us_pre_open": "美股盘前",
    "us_open_watch": "美股开盘观察",
    "us_mid_session": "美股盘中检查",
    "us_pre_close": "美股收盘前",
    "us_after_close": "美股盘后复盘",
}
_PRIMARY = frozenset({"cn_pre_open", "cn_after_close", "us_pre_open", "us_after_close"})
_WATCH = frozenset({"cn_open_watch", "cn_pre_close", "us_open_watch", "us_pre_close"})
_FORBIDDEN = re.compile(
    r"\b(?:manual_review|approved_actions|suppressed_actions|unresolved_conflicts|position_id|decision_id|research_only|review_required|take_profit|stop_loss|reduce|hedge)\b|(?:a|us|ccb|alipay)_[A-Za-z0-9_]+"
)
_NUMBER = re.compile(r"(?<![A-Za-z0-9_])-?\d+(?:\.\d+)?%?")


def _parse_dt(value: str) -> datetime:
    p = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return p if p.tzinfo else p.replace(tzinfo=timezone.utc)


def _number_values(obj: Any) -> set[float]:
    values = set()
    if isinstance(obj, dict):
        for value in obj.values():
            values.update(_number_values(value))
    elif isinstance(obj, list):
        for value in obj:
            values.update(_number_values(value))
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        number = float(obj)
        values.add(round(number, 4))
        values.add(round(number * 100, 4))
        values.add(round(number))
        values.add(round(number * 100))
    else:
        for raw in _NUMBER.findall(str(obj or "")):
            try:
                values.add(round(float(raw.rstrip("%")), 4))
            except ValueError:
                pass
    return values


def _has_content(view: dict) -> bool:
    card = view.get("instruction_card") or {}
    return bool(card.get("actions") or card.get("status") == "manual_review")


def build_push_payload(artifact: dict, *, now: str) -> dict:
    session = str(artifact.get("session") or "")
    if session not in _SESSION_LABELS:
        raise ValueError(f"unsupported session: {session}")
    if ((artifact.get("agent_task") or {}).get("task_version")) != 5:
        raise ValueError("task_version must be 5")
    view = (artifact.get("portfolio_decision") or {}).get("user_view")
    if not isinstance(view, dict):
        raise ValueError("portfolio_decision.user_view missing")
    if not isinstance(view.get("instruction_card"), dict) or not isinstance(
        view.get("assistant_brief"), dict
    ):
        raise ValueError("user_view is incomplete")
    generated = _parse_dt(artifact.get("generated_at") or "")
    current = _parse_dt(now)
    age = (current.astimezone(generated.tzinfo) - generated).total_seconds() / 60
    if age < -1 or age > 30:
        raise ValueError(f"artifact age {age:.1f} minutes outside allowed range")
    delivery = "send"
    if (session in _WATCH or session not in _PRIMARY) and not _has_content(view):
        delivery = "silent"
    return {
        "payload_version": 1,
        "session_label": _SESSION_LABELS[session],
        "market_date": str(artifact.get("market_date") or ""),
        "delivery": delivery,
        "user_view": view,
    }


def render_push_payload(payload: dict) -> str:
    if payload.get("delivery") == "silent":
        return "[SILENT]"
    view = payload.get("user_view") or {}
    card = view.get("instruction_card") or {}
    assistant = view.get("assistant_brief") or {}
    lines = [
        f"**{payload.get('session_label', '交易窗口')} · {payload.get('market_date', '')}**",
        "",
        "**交易指令卡**",
        f"- **{card.get('status_label', '等待人工确认')}**",
    ]
    actions = card.get("actions") or []
    if actions:
        for action in actions[:3]:
            lines.append(
                f"- **{action.get('action_label', '待确认动作')}｜{action.get('display_label', '未命名持仓')}**"
            )
            lines.append(f"  - 比例: {float(action.get('ratio') or 0) * 100:.0f}%")
            amount = action.get("estimated_amount_cny")
            amount_text = "金额待确认" if amount is None else f"¥{float(amount):,.0f}"
            if amount is not None and action.get("amount_is_estimate"):
                amount_text += "（估算）"
            lines.extend(
                [
                    f"  - 预计金额: {amount_text}",
                    f"  - 取消条件: {action.get('cancel_condition', '条件不再成立时取消')}",
                    f"  - 到账: {action.get('settlement_display', '到账时间待确认')}",
                    f"  - 下次检查: {action.get('next_checkpoint', '下一交易窗口复核')}",
                ]
            )
    else:
        for reason in (card.get("no_action_reasons") or ["当前没有可直接执行的获批动作"])[:2]:
            lines.append(f"- 原因: {reason}")
        lines.append(f"- 下次检查: {card.get('next_checkpoint', '下一交易窗口复核')}")
    lines.extend(["", "**私人投资助理**", "", "**为什么这样安排**"])
    for reason in (assistant.get("why") or ["当前决策以组合裁决结果为准"])[:5]:
        lines.append(f"- {reason}")
    conflicts = assistant.get("conflict_summary") or []
    if conflicts:
        lines.extend(["", "**待人工确认的信号分类**"])
        for item in conflicts:
            lines.append(
                f"- {item.get('action_label', '待确认动作')}: {int(item.get('count') or 0)} 项"
            )
    lines.extend(["", "**现在不要做什么**"])
    for item in (assistant.get("do_not_do") or ["无额外禁止事项"])[:5]:
        lines.append(f"- {item}")
    lines.extend(["", "**资金状态**"])
    for key in ("immediate", "settling", "strategic_exit", "locked"):
        item = (assistant.get("cash") or {}).get(key) or {}
        lines.append(
            f"- {item.get('label', '资金待确认')}: ¥{float(item.get('amount_cny') or 0):,.0f}"
        )
    risk = assistant.get("risk") or {}
    lines.extend(
        [
            "",
            "**组合与风险**",
            f"- 当前状态: {risk.get('label', '风险状态待确认')}（{risk.get('transition', '状态待确认')}）",
        ]
    )
    if risk.get("suspend_accumulation"):
        lines.append("- 当前暂停加仓")
    for reason in risk.get("reasons") or []:
        lines.append(f"- 触发原因: {reason}")
    lines.append(f"- 解除条件: {risk.get('release_condition', '等待风险条件明确')}")
    notes = assistant.get("data_notes") or []
    if notes:
        lines.extend(["", "**数据说明**"])
        lines.extend(f"- {note}" for note in notes)
    lines.extend(["", "**仅供观察**"])
    research = assistant.get("research") or []
    if research:
        for item in research[:8]:
            lines.append(
                f"- **{item.get('display_label', '未命名标的')}**: {item.get('action_hint', '仅供观察')}"
            )
            if item.get("reassess_after"):
                lines.append(f"  - 再评估: {item['reassess_after']}")
    else:
        lines.append("- 暂无需要重点跟踪的研究候选")
    return "\n".join(lines)


def validate_payload_text(payload: dict, text: str) -> list[str]:
    errors = [f"internal token: {m.group(0)}" for m in _FORBIDDEN.finditer(text)]
    allowed = _number_values(payload)
    numeric_text = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", text)
    for raw in _NUMBER.findall(numeric_text):
        try:
            value = round(float(raw.rstrip("%")), 4)
        except ValueError:
            continue
        if value not in allowed:
            errors.append(f"unauthorized number: {raw}")
    if text != "[SILENT]":
        if "**交易指令卡**" not in text or "**私人投资助理**" not in text:
            errors.append("missing two-layer headings")
        elif text.index("**交易指令卡**") > text.index("**私人投资助理**"):
            errors.append("wrong section order")
    return errors


def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as h:
            h.write(data)
            h.flush()
            os.fsync(h.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--now", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    args = parser.parse_args()
    try:
        artifact = json.loads(Path(args.artifact).read_text(encoding="utf-8"))
        if artifact.get("session") != args.session:
            raise ValueError("session mismatch")
        payload = build_push_payload(artifact, now=args.now)
        payload_text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        _atomic_write(Path(args.output), payload_text)
        output = payload_text if args.format == "json" else render_push_payload(payload)
        errors = validate_payload_text(payload, output) if args.format == "markdown" else []
        if errors:
            raise ValueError("; ".join(errors))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 2
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
