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
from zoneinfo import ZoneInfo

_SESSION_LABELS = {
    "cn_post_open": "A股开盘后",
    "cn_after_close": "A股盘后复盘",
    "us_post_open": "美股开盘后",
    "us_after_close": "美股盘后复盘",
    "global_intelligence_watch": "每日全球情报",
}
_PRIMARY = frozenset({"cn_post_open", "cn_after_close", "us_post_open", "us_after_close", "global_intelligence_watch"})
_WATCH = frozenset()
_FORBIDDEN = re.compile(
    r"\b(?:manual_review|approved_actions|suppressed_actions|unresolved_conflicts|position_id|decision_id|research_only|review_required)\b|(?:a|us|ccb|alipay)_[A-Za-z0-9_]+"
)
_DIRECTION_LABELS = {
    "supportive": "\u504f\u6709\u5229",
    "neutral": "\u4e2d\u6027",
    "adverse": "\u504f\u4e0d\u5229",
    "uncertain": "\u4e0d\u786e\u5b9a",
    "mixed": "\u6df7\u5408",
}
_CONFIDENCE_LABELS = {
    "high": "\u9ad8",
    "medium": "\u4e2d",
    "low": "\u4f4e",
}
# P1-14: human labels for deterministic window_delta risk/action changes.
# P1-16: values must stay in sync with presentation.TRANSITION_LABELS /
# _RISK_LABELS / _SIGNAL_LABELS. This script deliberately avoids importing
# the stocks engine (keeps the renderer runnable without pandas), so the
# mapping is mirrored here -- keep the two in lockstep.
_RISK_LEVEL_LABELS = {
    "normal": "常态", "watch": "观察", "reduce": "降风险", "hedge": "对冲/高风险",
}
_TRANSITION_LABELS = {
    "escalated": "较上次升级", "deescalated": "较上次缓和", "stable": "与上次持平",
    "unchanged": "与上次持平", "candidate": "候选状态待确认", "expired": "已过期",
    "initial": "初始化", "reconfirmed": "再确认",
}
_ACTION_LABELS = {
    "stop_loss": "止损", "take_profit": "止盈", "reduce": "减仓",
    "add": "加仓", "hold": "持有", "wait": "等待", "accumulate": "分批布局",
}
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
    is_intel = artifact.get("market") == "intelligence"
    generated = _parse_dt(artifact.get("generated_at") or "")
    current = _parse_dt(now)
    age = (current.astimezone(generated.tzinfo) - generated).total_seconds() / 60
    if age < -1 or age > 45:
        raise ValueError(f"artifact age {age:.1f} minutes outside allowed range")
    if is_intel:
        summary = artifact.get("session_summary") or {}
        signals = artifact.get("action_signal_reviews") or []
        risk = artifact.get("risk_assessment") or {}
        cd = artifact.get("context_digest") or {}
        mkt = cd.get("market_state_summary") or {}
        return {
            "payload_version": 1,
            "session_label": _SESSION_LABELS[session],
            "market_date": str(artifact.get("market_date") or ""),
            "delivery": "send",
            "session_type": "intelligence",
            "headline": summary.get("headline", ""),
            "vix": mkt.get("vix"),
            "top_move": mkt.get("top_move"),
            "risk_level": risk.get("level"),
            "risk_triggers": risk.get("triggers") or [],
            "data_quality": artifact.get("data_quality") or {},
            "signals": signals,
        }
    view = (artifact.get("portfolio_decision") or {}).get("user_view")
    if not isinstance(view, dict):
        raise ValueError("portfolio_decision.user_view missing")
    if not isinstance(view.get("instruction_card"), dict) or not isinstance(
        view.get("assistant_brief"), dict
    ):
        raise ValueError("user_view is incomplete")
    delivery = "send"
    if (session in _WATCH or session not in _PRIMARY) and not _has_content(view):
        delivery = "silent"
    return {
        "payload_version": 1,
        "session_label": _SESSION_LABELS[session],
        "market_date": str(artifact.get("market_date") or ""),
        "delivery": delivery,
        "session_type": "trading",
        "user_view": view,
        # P1-14: deterministic window delta travels with the payload so the
        # "本窗口变化" section can report risk-state / action / conflict
        # changes even when the LLM outlook_delta is empty. Previously a
        # risk escalation (or any adjudicator change) with no outlook_delta
        # rendered as "本窗口未发现需要改变计划的新证据" -- the exact
        # contradiction observed in the 2026-08-05 us_after_close report.
        "window_delta": artifact.get("window_delta") or {},
        # R5-5: 报告生成时间(北京时间)供标题展示,用户据此判断报告时效
        # (定时 15:00 的报告 19:58 才到,必须让用户看到生成时刻)。
        "generated_at": str(artifact.get("generated_at") or ""),
    }



_IDENTITY_CODE = re.compile(r"[（(]([^（）()]+)[）)]\s*$")
_EXECUTABLE_STATUS = frozenset({"full", "adjusted_to_step"})


def _instrument_identity(display_label: str) -> str:
    """Extract the trailing (code) instrument identity from a rendered label."""
    m = _IDENTITY_CODE.search(str(display_label or ""))
    return m.group(1).strip() if m else ""


def validate_push_truth(payload: dict) -> list[str]:
    """Deterministic pre-delivery truth gate (TASK-001E1 scope item 7).

    Defense-in-depth re-check of the same invariants ``presentation.
    build_user_view`` is responsible for enforcing, applied directly to the
    built payload so a regression there cannot silently reach delivery:
    action text percentages must agree with ``final_ratio``, every action in
    ``instruction_card.actions`` must actually be executable, no instrument
    identity may appear in both actions and research, and a successful
    Outlook narrative must carry at least one source_ref.
    """
    errors: list[str] = []
    if payload.get("session_type") != "trading":
        return errors
    view = payload.get("user_view") or {}
    card = view.get("instruction_card") or {}
    assistant = view.get("assistant_brief") or {}

    action_identities: set[str] = set()
    for action in card.get("actions") or []:
        final_ratio = action.get("final_ratio")
        is_number = isinstance(final_ratio, (int, float)) and not isinstance(final_ratio, bool)
        if not is_number or final_ratio <= 0:
            errors.append(f"action has zero or missing final_ratio: {action.get('display_label')}")
        if action.get("execution_status") not in _EXECUTABLE_STATUS:
            errors.append(
                f"non-executable action present in instruction_card.actions: "
                f"{action.get('display_label')} execution_status={action.get('execution_status')}"
            )
        qty = action.get("executable_quantity")
        if qty is not None and not (isinstance(qty, (int, float)) and not isinstance(qty, bool) and qty > 0):
            errors.append(f"action has non-positive executable_quantity: {action.get('display_label')}")
        if is_number:
            expected_pct = round(final_ratio * 100)
            for m in _NUMBER.finditer(str(action.get("reason_summary") or "")):
                raw = m.group()
                if not raw.endswith("%"):
                    continue
                try:
                    value = round(float(raw.rstrip("%")))
                except ValueError:
                    continue
                if value != expected_pct:
                    errors.append(
                        f"action text percentage {raw} disagrees with final_ratio "
                        f"{final_ratio}: {action.get('display_label')}"
                    )
        identity = _instrument_identity(action.get("display_label"))
        if identity:
            action_identities.add(identity)

    # Defect 1 also covers assistant_brief.why, which is rendered as its own
    # push section. It must carry each card action's finalized sentence
    # verbatim; if the two layers ever drift apart, one of them is lying about
    # the ratio even when each is internally self-consistent.
    why_texts = [w for w in (assistant.get("why") or []) if isinstance(w, str)]
    for action in card.get("actions") or []:
        sentence = action.get("reason_summary")
        if isinstance(sentence, str) and sentence and sentence not in why_texts:
            errors.append(
                f"assistant_brief.why omits the finalized action sentence for "
                f"{action.get('display_label')}"
            )

    for research in assistant.get("research") or []:
        identity = _instrument_identity(research.get("display_label"))
        if identity and identity in action_identities:
            errors.append(f"instrument {identity} appears in both actions and research")

    outlook = assistant.get("outlook")
    if isinstance(outlook, dict) and outlook.get("status") == "ok":
        if not (outlook.get("source_refs") or []):
            errors.append("successful outlook with no source_refs")

    return errors

def render_push_payload(payload: dict) -> str:
    if payload.get("delivery") == "silent":
        return "[SILENT]"
    if payload.get("session_type") == "intelligence":
        return _render_intelligence_payload(payload)
    return _render_trading_payload(payload)


def _render_intelligence_payload(payload: dict) -> str:
    lines = [
        f"**{payload.get('session_label', '每日全球情报')} · {payload.get('market_date', '')}**",
        "",
    ]
    vix = payload.get("vix")
    top_move = payload.get("top_move")
    risk_level = payload.get("risk_level")
    risk_triggers = payload.get("risk_triggers") or []
    dq = payload.get("data_quality") or {}
    signals = payload.get("signals") or []
    if vix is not None or top_move:
        lines.append(f"VIX: {vix if vix is not None else 'N/A'}  ·  {top_move or ''}")
        lines.append("")
    if risk_level:
        lines.append(f"**风险等级: {risk_level}**")
        for t in risk_triggers[:3]:
            cond = t.get("condition", "")
            reason = t.get("reason", "")
            if cond or reason:
                lines.append(f"- {cond}{' — ' + reason if reason else ''}")
        lines.append("")
    if signals:
        # Adversarial review P1-6: the intelligence channel bypasses the
        # trading truth gate by design, so it must not speak in trade-
        # instruction vocabulary. These are watch-tlist attention hints from
        # the news analyzer, not approved actions.
        lines.append("**情报关注信号**")
        for s in signals[:5]:
            direction = {"buy": "偏多关注", "sell": "偏空回避", "hold": "中性", "watch": "观察"}.get(s.get("signal",""), s.get("signal",""))
            lines.append(f"- **{direction}** {s.get('name','') or s.get('symbol','')}: {s.get('action_hint','')}")
        lines.append("")
    article_count = dq.get("articles") or dq.get("snapshots")
    if article_count:
        lines.append(f"数据来源: {article_count} 篇新闻, {dq.get('clusters', 0)} 个信息群")
    return "\n".join(lines)


def _render_trading_payload(payload: dict) -> str:
    view = payload.get("user_view") or {}
    card = view.get("instruction_card") or {}
    assistant = view.get("assistant_brief") or {}

    sections: list[list[str]] = []

    # 1. 本窗口变化
    delta_section = _section_window_changes(assistant, payload.get("window_delta") or {})
    if delta_section:
        sections.append(delta_section)

    # 2. 走势研判 (M1: new)
    outlook_section = _section_market_outlook(assistant)
    if outlook_section:
        sections.append(outlook_section)

    # 3. 可执行动作
    action_section = _section_executable_actions(card)
    if action_section:
        sections.append(action_section)

    # 4. 提前布局 (M1: new)
    setup_section = _section_setup_candidates(assistant, card.get("next_checkpoint") or "")
    if setup_section:
        sections.append(setup_section)

    # 5. 禁止与延后
    blocked_section = _section_blocked_and_deferred(card, assistant)
    if blocked_section:
        sections.append(blocked_section)

    # 6. 组合与检查点 (M1: merged former sections 4 and 5)
    impact_section = _section_portfolio_and_checkpoint(card, assistant)
    if impact_section:
        sections.append(impact_section)

    lines: list[str] = [
        f"**{payload.get('session_label', '交易窗口')} · {payload.get('market_date', '')}**",
    ]
    # R5-5: 生成时间(北京时间)标注,用户判断报告时效。用完整 ISO 格式
    # (YYYY-MM-DD HH:MM),与 _safe_numeric_spans 的 ISO 豁免匹配,避免
    # 被 number gate 误判为未授权数字。
    gen = payload.get("generated_at") or ""
    if gen:
        try:
            gen_dt = _parse_dt(str(gen))
            gen_local = gen_dt.astimezone(ZoneInfo("Asia/Shanghai"))
            lines.append(f"*生成时间 {gen_local.strftime('%Y-%m-%d %H:%M')}*")
        except (ValueError, TypeError):
            pass
    if not sections:
        lines.append("")
        lines.append("当前无输出内容")
        return "\n".join(lines)

    for section in sections:
        lines.append("")
        lines.extend(section)

    return "\n".join(lines)


def _section_heading(title: str) -> str:
    return f"**{title}**"


def _window_delta_human_changes(window_delta: dict) -> list[str]:
    """Render deterministic risk/action/conflict changes from window_delta.

    P1-14: window_delta.changes carry risk_state.level/transition moves,
    approved/suppressed action ratio revisions, and unresolved conflict
    additions. These are objective, user-relevant changes that must appear
    even when the LLM outlook_delta is empty. Unknown raw fields are
    skipped; every emitted line is derived from a known field shape.
    """
    lines: list[str] = []
    changes = window_delta.get("changes") or []
    if not isinstance(changes, list):
        return lines
    seen: set[str] = set()
    for change in changes:
        if not isinstance(change, dict):
            continue
        field = str(change.get("field") or "")
        if field == "initial":
            continue
        old = change.get("old")
        new = change.get("new")
        if field.startswith("risk_state."):
            key = field.split(".", 1)[1]
            if key == "level":
                old_l = _RISK_LEVEL_LABELS.get(str(old or ""), str(old or "未知"))
                new_l = _RISK_LEVEL_LABELS.get(str(new or ""), str(new or "未知"))
                text = f"风险档位: {old_l} → {new_l}"
            elif key == "transition":
                text = f"风险状态变化: {_TRANSITION_LABELS.get(str(new or ''), str(new or '待确认'))}"
            elif key == "candidate_level":
                cand = _RISK_LEVEL_LABELS.get(str(new or ""), str(new or "无"))
                text = f"风险候选档位: {cand}"
            else:
                text = f"风险状态字段更新: {key}"
        elif field.startswith("approved_action."):
            if isinstance(old, dict) and isinstance(new, dict):
                sig_old = _ACTION_LABELS.get(str(old.get("signal") or ""), str(old.get("signal") or "动作"))
                sig_new = _ACTION_LABELS.get(str(new.get("signal") or ""), str(new.get("signal") or "动作"))
                if sig_old == sig_new:
                    text = f"获批动作调整: {sig_new}"
                else:
                    text = f"获批动作变化: {sig_old} → {sig_new}"
            else:
                text = "获批动作有调整"
        elif field == "portfolio_decision.unresolved_conflicts":
            old_count = len(old) if isinstance(old, list) else 0
            new_count = len(new) if isinstance(new, list) else 0
            if new_count > old_count:
                text = f"新增 {new_count - old_count} 项方向冲突，需人工确认"
            elif new_count < old_count:
                text = f"方向冲突减少 {old_count - new_count} 项"
            else:
                text = "方向冲突集合有变化"
        elif field.startswith("portfolio_decision."):
            key = field.split(".", 1)[1]
            text = f"组合决策更新: {key}"
        else:
            continue
        if text in seen:
            continue
        seen.add(text)
        lines.append(f"- {text}")
    return lines[:4]


def _section_window_changes(assistant: dict, window_delta: dict | None = None) -> list[str]:
    """§1 本窗口变化 — outlook_delta 或确定性 window_delta 或"无新证据"。

    The LLM outlook_delta is preferred when present; when it is empty,
    deterministic window_delta changes (risk moves, action revisions,
    conflict additions) are rendered instead. Only when both are empty do
    we claim "无新证据" -- otherwise the report can contradict itself
    (P1-14: 2026-08-05 us_after_close said "未发现新证据" while risk had
    just escalated).
    """
    lines: list[str] = [_section_heading("本窗口变化")]
    outlook_delta = assistant.get("outlook_delta") or {}
    changes = (outlook_delta.get("changes") or {}) if isinstance(outlook_delta, dict) else {}

    if changes:
        rendered = _render_delta_changes_concise(changes)
        for line in rendered[:3]:
            lines.append(f"- {line}")
        source_changes = changes.get("source_refs") or {}
        if isinstance(source_changes, dict):
            added = source_changes.get("added") or []
            removed = source_changes.get("removed") or []
            if added:
                lines.append(f"- 来源新增: {', '.join(str(x) for x in added[:3])}")
            if removed:
                lines.append(f"- 来源移除: {', '.join(str(x) for x in removed[:3])}")
        return lines

    deterministic = _window_delta_human_changes(window_delta or {})
    # P1-14 follow-up: on the first run of a session (window_delta carries
    # only an "initial" record), the risk state can still have just
    # escalated/deescalated relative to the previous session's run. Surface
    # that move from assistant.risk so "本窗口未发现新证据" never coexists
    # with "风险状态: 降风险（较上次升级）".
    if not deterministic:
        risk = assistant.get("risk") or {}
        # P1-16: branch on the raw enum (transition_key) rather than the
        # rendered Chinese text, which would silently break if wording
        # changes in presentation.TRANSITION_LABELS.
        risk_key = str(risk.get("transition_key") or "")
        if risk_key in ("escalated", "deescalated"):
            risk_label = str(risk.get("label") or "风险状态")
            risk_transition = str(risk.get("transition") or "")
            deterministic = [f"- 风险档位变化: {risk_label}（{risk_transition}）"]
        # R5-7: transition=unchanged 但仍处于高风险(suspend_accumulation,
        # 即 hedge/reduce 档)时,持续高风险是用户必须知道的事实,"本窗口
        # 未发现新证据"会误导用户以为风险解除。
        elif (
            risk_key == "unchanged"
            and bool(risk.get("suspend_accumulation"))
        ):
            risk_label = str(risk.get("label") or "风险状态")
            deterministic = [
                f"- 风险状态持续: {risk_label}（无档位变化，触发原因见组合与检查点）"
            ]
    if deterministic:
        lines.extend(deterministic)
        return lines

    lines.append("- 本窗口未发现需要改变计划的新证据")
    return lines


def _section_market_outlook(assistant: dict) -> list[str]:
    """§2 走势研判 — 短期 + 中期方向、驱动、证伪线、组合影响。

    Outlook 未就绪时给一句诚实的"暂不可用"降级，不泄漏内部错误。
    M2 的 evidence 扩展让这段实际填充上短中期研判正文；此处的渲染
    结构已按最终形态实现，无需 M2 再改渲染层。
    """
    lines: list[str] = [_section_heading("走势研判")]
    outlook = assistant.get("outlook") or {}

    if not isinstance(outlook, dict) or not outlook:
        lines.append("- 中长期研判暂不可用（研判待复核）")
        return lines

    status = outlook.get("status")
    if status == "unavailable":
        # 不泄漏 disable/未配置类内部字符串。outlook.message 是上游 sanitize
        # 过的中文降级文案（如"本期研判未通过数据完整性校验，暂不输出"），
        # 可直接转述；缺 message 时退回 M1 的占位降级。
        message = str(outlook.get("message") or "").strip()
        if message:
            lines.append(f"- 中长期研判：{message}")
        else:
            lines.append("- 中长期研判暂不可用（研判待复核）")
        return lines

    if status != "ok":
        lines.append("- 中长期研判暂不可用（研判待复核）")
        return lines

    summary = outlook.get("summary") or ""
    if summary:
        lines.append(f"- 综合判断: {summary}")

    for hkey, hlabel in (("near_term", "短期(1-2周)"), ("medium_term", "中期(1-3月)")):
        h = outlook.get(hkey) or {}
        if not isinstance(h, dict) or not h:
            continue
        # M2 advisory outlooks carry their own horizon label (e.g. "3-7天");
        # prefer it over the legacy default when present.
        hlabel = str(h.get("horizon") or hlabel)
        direction_key = str(h.get("direction") or "")
        confidence_key = str(h.get("confidence") or "")
        rationale = str(h.get("rationale") or "")
        d = _DIRECTION_LABELS.get(direction_key, direction_key)
        c = _CONFIDENCE_LABELS.get(confidence_key, confidence_key)
        if d or c:
            piece = f"{hlabel}: {d}" if d else hlabel
            if c:
                piece += f"（置信 {c}）"
            if rationale:
                piece += f" — {rationale}"
            lines.append(f"- {piece}")
        validation = str(h.get("validation") or "")
        falsification = str(h.get("falsification") or "")
        if validation:
            lines.append(f"  验证：{validation}")
        if falsification:
            lines.append(f"  证伪：{falsification}")

    shown_lines = 0
    for sv in (outlook.get("sector_views") or [])[:3]:
        sector = sv.get("sector") or ""
        direction_key = str(sv.get("direction") or "")
        d = _DIRECTION_LABELS.get(direction_key, direction_key)
        rationale = str(sv.get("rationale") or "")
        if sector and d:
            piece = f"- {sector}: {d}"
            if rationale:
                piece += f" — {rationale}"
            lines.append(piece)
            shown_lines += 1
        if shown_lines >= 3:
            break

    for av in (outlook.get("asset_views") or [])[:2]:
        asset_class = av.get("asset_class") or av.get("asset") or ""
        direction_key = str(av.get("direction") or "")
        d = _DIRECTION_LABELS.get(direction_key, direction_key)
        rationale = str(av.get("rationale") or "")
        if asset_class and d:
            piece = f"- {asset_class}: {d}"
            if rationale:
                piece += f" — {rationale}"
            lines.append(piece)

    return lines


# 信号到渲染标签
_SETUP_SIGNAL_LABELS = {
    "left_bottom_candidate": "左侧超跌",
    "accumulate_candidate": "趋势布局",
    "rotation_candidate": "轮动贴轨",
    "wait_for_pullback": "等回踎",
    "reduce_risk": "趋势转弱",
    "avoid_catching_falling_knife": "下跌未止",
    "neutral_hold": "维持现状",
    "no_data": "缺数据",
}


# 合体可能的时候提取较为干净的条件/止损几乎语
_SETUP_TRIGGER_HINTS = {
    "left_bottom_candidate": ["轻仓试", "超跌", "止损", "等待加仓"],
    "accumulate_candidate": ["分批", "布局", "回踎", "趋势"],
    "rotation_candidate": ["轮动", "相对强势", "贴轨"],
    "wait_for_pullback": ["回踎", "过热", "等回踎"],
}


def _setup_candidate_tag(item: dict) -> str:
    """Return a short decision tag from candidate metadata."""
    tag = str(item.get("setup_tag") or "")
    if tag:
        return tag
    signal = str(item.get("signal") or "")
    return _SETUP_SIGNAL_LABELS.get(signal, "观察")


def _setup_candidate_tail(item: dict) -> str | None:
    """Return one-line trigger/condition tail (≤60 chars), or None."""
    reasons = [str(r) for r in (item.get("reasons") or []) if r]
    if reasons:
        return "；".join(reasons[:2])[:60]
    hint = str(item.get("action_hint") or "")
    # strip obvious generic phrases
    for stale in ("仅供观察", "不形成交易", "可分批", "深跌超卖"):
        hint = hint.replace(stale, "")
    # extract price/threshold-looking sentences if any
    for key in ("价格", "回踎", "等待", "止损", "MA", "RSI", "放量"):
        if key in hint:
            # crude: take the first sentence-like chunk containing key
            idx = hint.find(key)
            start = max(0, idx - 20)
            end = min(len(hint), idx + 40)
            return hint[start:end].strip("，。 ")[:60]
    cleaned = hint.strip("，。 ")
    return cleaned[:60] if cleaned else None


def _section_setup_candidates(assistant: dict, next_checkpoint: str = "") -> list[str]:
    """§4 提前布局 — 从 research 提升到主段，展示 top 2-3 候选。"""
    lines: list[str] = [_section_heading("提前布局")]
    research = assistant.get("research") or []
    if not research:
        lines.append("- 本窗口无值得提前布局的候选")
        return lines

    def _score(item: dict) -> float:
        for key in ("score", "composite_score"):
            s = item.get(key)
            if isinstance(s, (int, float)) and not isinstance(s, bool):
                return float(s)
        return -1.0

    sorted_items = sorted(research, key=_score, reverse=True)
    top = sorted_items[:3]
    checkpoint_ref = str(next_checkpoint or "") or "下一交易窗口复核"

    for item in top:
        label = item.get("display_label") or "未命名候选"
        tag = _setup_candidate_tag(item)
        tail = _setup_candidate_tail(item)
        score = item.get("score")
        score_text = ""
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            score_text = f" 综合得分 {score:.2f}"
        sizing_hint = str(item.get("sizing_hint") or "")
        if tail:
            lines.append(f"- **{label}**（{tag}{score_text}）: {tail}")
        else:
            lines.append(f"- **{label}**（{tag}{score_text}）")
        if sizing_hint:
            lines.append(f"  仓位/止损: {sizing_hint}")
        reassess = item.get("reassess_after")
        if reassess and reassess != checkpoint_ref:
            lines.append(f"  复核: {reassess}")

    overflow = len(research) - len(top)
    if overflow > 0:
        # P5-5: 给出候选名单,不空泛说"详情待筛选"——用户可直接看到
        # 剩余候选是谁,再决定是否人工进一步查看。
        rest_labels = [
            str((item.get("display_label") or item.get("label") or "候选"))
            for item in sorted_items[len(top):]
        ]
        rest_text = "、".join(rest_labels[:6])
        if len(rest_labels) > 6:
            rest_text += f" 等 {overflow} 个"
        lines.append(f"- 另有 {overflow} 个候选: {rest_text}（详情待人工进一步筛选）")
    return lines


def _render_delta_changes_concise(changes: dict) -> list[str]:
    rendered: list[str] = []
    summary = changes.get("summary")
    if isinstance(summary, dict):
        from_val = summary.get("from")
        to_val = summary.get("to")
        if from_val and to_val:
            rendered.append(f"综合判断: {from_val} → {to_val}")
        elif to_val:
            rendered.append(f"综合判断更新为: {to_val}")

    confidence = changes.get("confidence")
    if isinstance(confidence, dict):
        from_c = _CONFIDENCE_LABELS.get(confidence.get("from"), confidence.get("from"))
        to_c = _CONFIDENCE_LABELS.get(confidence.get("to"), confidence.get("to"))
        if from_c and to_c:
            rendered.append(f"置信度: {from_c} → {to_c}")

    for hkey, hlabel in (("near_term", "未来1-2周"), ("medium_term", "未来1-3个月")):
        hc = changes.get(hkey)
        if not isinstance(hc, dict):
            continue
        parts = []
        for subkey, sublabel in (("direction", "方向"), ("confidence", "置信度"), ("horizon", "时间范围")):
            sub = hc.get(subkey)
            if not isinstance(sub, dict):
                continue
            from_v = sub.get("from")
            to_v = sub.get("to")
            if from_v is None and to_v is None:
                continue
            if subkey == "direction":
                from_v = _DIRECTION_LABELS.get(from_v, from_v)
                to_v = _DIRECTION_LABELS.get(to_v, to_v)
            if from_v and to_v:
                parts.append(f"{sublabel}: {from_v} → {to_v}")
            elif to_v:
                parts.append(f"{sublabel}: 新→ {to_v}")
        if parts:
            rendered.append(f"{hlabel}: {'；'.join(parts)}")

    sector_changes = changes.get("sector_views") or {}
    if isinstance(sector_changes, dict):
        for sector, sc in list(sector_changes.items())[:3]:
            if not isinstance(sc, dict):
                continue
            direction = sc.get("direction")
            if isinstance(direction, dict):
                d_from = _DIRECTION_LABELS.get(direction.get("from"), direction.get("from"))
                d_to = _DIRECTION_LABELS.get(direction.get("to"), direction.get("to"))
                if d_from and d_to:
                    rendered.append(f"{sector}行业: {d_from} → {d_to}")

    asset_changes = changes.get("asset_views") or {}
    if isinstance(asset_changes, dict):
        for asset, ac in list(asset_changes.items())[:3]:
            if not isinstance(ac, dict):
                continue
            direction = ac.get("direction")
            if isinstance(direction, dict):
                d_from = _DIRECTION_LABELS.get(direction.get("from"), direction.get("from"))
                d_to = _DIRECTION_LABELS.get(direction.get("to"), direction.get("to"))
                if d_from and d_to:
                    rendered.append(f"{asset}: {d_from} → {d_to}")

    scenario_changes = changes.get("scenarios") or {}
    if isinstance(scenario_changes, dict):
        for sname, slabel in (("base", "基准情景"), ("bull", "乐观情景"), ("risk", "风险情景")):
            scene = scenario_changes.get(sname)
            if not isinstance(scene, dict):
                continue
            scene_parts = []
            for sf, sf_label in (("label", "研判"), ("validation", "验证条件"), ("invalidation", "否定条件")):
                sfv = scene.get(sf)
                if isinstance(sfv, dict):
                    from_v = sfv.get("from")
                    to_v = sfv.get("to")
                    if from_v and to_v:
                        scene_parts.append(f"{sf_label}: {from_v} → {to_v}")
            if scene_parts:
                rendered.append(f"{slabel}: {'；'.join(scene_parts[:2])}")

    return rendered[:8]


def _conflict_type(reason: str) -> str:
    """Classify a no_action_reason into conflict type for decision branching."""
    r = str(reason or "").lower()
    if "数据" in r or "行情" in r or "过时" in r or "缺失" in r or "复权" in r:
        return "数据问题"
    if "冲突" in r or "人工确认" in r or "方向" in r or "权益" in r or "低于" in r or "但" in r:
        return "决策冲突"
    if "锁定" in r or "不能交易" in r or "锁定状态" in r:
        return "锁定"
    if "开放期" in r or "周期" in r or "不在开放" in r:
        return "非开放期"
    if "资金" in r or "结算" in r or "缺口" in r:
        return "资金/结算"
    return "其他"


def _conflict_decision_branch(reason: str) -> str:
    """Return a default branch for manual review conflicts."""
    t = _conflict_type(reason)
    if t == "数据问题":
        return "默认：等待数据恢复后重新评估"
    if t == "决策冲突":
        return "默认：维持现状，等待人工确认方向"
    if t == "锁定":
        return "默认：等待锁定解除"
    if t == "非开放期":
        return "默认：等待开放期"
    if t == "资金/结算":
        return "默认：等待资金结算方式确认"
    return "默认：维持现状"


def _no_action_conflict_details(reason: str) -> dict[str, Any]:
    """Parse a no_action_reason into structured conflict details for display."""
    text = str(reason or "")
    # Extract instrument label and code from the leading 'label(code): ...' form.
    label = text
    code = ""
    m = re.match(r"(.+?)（([\dA-Za-z\.]+)）：", text)
    if not m:
        m = re.match(r"(.+?)\(([\dA-Za-z\.]+)\)\s*:", text)
    if m:
        label = m.group(1).strip()
        code = m.group(2).strip()
    return {
        "type": _conflict_type(text),
        "label": label,
        "code": code,
        "reason": text,
        "branch": _conflict_decision_branch(text),
    }


def _find_reference_for_conflict(detail: dict, references: list[dict]) -> dict | None:
    """Match a parsed manual-review conflict to its rule-driven reference entry.

    The conflict reason text starts with ``{display_label}：…``; the parser
    splits label/code apart, so we re-match by containment against the
    reference's full display_label.
    """
    label = str(detail.get("label") or "")
    code = str(detail.get("code") or "")
    for ref in references:
        ref_label = str(ref.get("display_label") or "")
        if not ref_label:
            continue
        if code and code in ref_label:
            return ref
        if label and label in ref_label:
            return ref
    return None


def _format_reference_line(ref: dict, *, with_label: bool = False) -> str:
    """Render one truth-gate audit-trail line:
    ``参考: <signal_type> <ratio>%<, 参考数量 Q><, 参考金额 ¥A>``.
    """
    head: list[str] = []
    label = str(ref.get("display_label") or "")
    signal = str(ref.get("signal_type") or ref.get("action_type") or "")
    ratio = ref.get("ratio")
    if with_label and label:
        head.append(label)
    if signal:
        head.append(signal)
    if isinstance(ratio, (int, float)) and not isinstance(ratio, bool) and ratio > 0:
        head.append(f"{float(ratio) * 100:.0f}%")
    if not head:
        return ""
    tail: list[str] = []
    qty = ref.get("executable_quantity")
    if isinstance(qty, (int, float)) and not isinstance(qty, bool) and qty > 0:
        tail.append(f"参考数量 {int(qty)}")
    # P1-12: when the gate rejected the action because quotes were
    # stale/missing, the estimated amount computed from that stale valuation
    # must not be quoted as a reference figure. Surface the block reason
    # instead so the user knows the amount is unavailable, not forgotten.
    blocked_reason = str(ref.get("amount_blocked_reason") or "")
    amount = ref.get("estimated_amount_cny")
    if blocked_reason:
        tail.append(blocked_reason)
    elif isinstance(amount, (int, float)) and not isinstance(amount, bool) and amount > 0:
        tail.append(f"参考金额 ¥{float(amount):,.0f}")
    line = f"参考: {' '.join(head)}"
    if tail:
        line += f"，{'，'.join(tail)}"
    return line


def _section_executable_actions(card: dict) -> list[str]:
    """§3 可执行动作 — manual_review 时列出待决冲突和参考值，并给出决策分支。"""
    lines: list[str] = [_section_heading("可执行动作")]
    actions = card.get("actions") or []
    status_raw = str(card.get("status") or "").strip().lower()
    status_label = card.get("status_label") or ("等待人工确认" if status_raw == "manual_review" else "")

    if not actions:
        # 无 approved actions；若为 manual_review，把冲突理由摆出来带参考值和默认分支。
        no_action_reasons = card.get("no_action_reasons") or []
        if status_raw == "manual_review" and no_action_reasons:
            lines.append(f"- 状态: {status_label or '等待人工确认'}")
            lines.append("- 以下冲突需你判断:")
            suppressed = [
                ref for ref in (card.get("suppressed_actions_reference") or [])
                if isinstance(ref, dict)
            ]
            matched: set[int] = set()
            for reason in no_action_reasons[:3]:
                detail = _no_action_conflict_details(reason)
                if detail["label"] and detail["code"]:
                    lines.append(f"  · **{detail['label']}（{detail['code']}）** [{detail['type']}] {detail['reason'].split('：', 1)[-1] if '：' in detail['reason'] else detail['reason']}")
                else:
                    lines.append(f"  · [{detail['type']}] {detail['reason']}")
                lines.append(f"    分支: {detail['branch']}")
                # M1 truth-gate 审计线：该冲突对应的 rule-driven 参考值随冲突展示
                ref = _find_reference_for_conflict(detail, suppressed)
                if ref is not None:
                    matched.add(id(ref))
                    ref_line = _format_reference_line(ref)
                    if ref_line:
                        lines.append(f"    {ref_line}")
            for ref in suppressed[:3]:
                if id(ref) in matched:
                    continue
                ref_line = _format_reference_line(ref, with_label=True)
                if ref_line:
                    lines.append(f"  {ref_line}")
            return lines

        lines.append(f"- 状态: {status_label or '当前无需操作'}")
        for reason in (no_action_reasons or ["当前没有满足执行条件的获批动作"])[:2]:
            lines.append(f"- {reason}")
        return lines

    for action in actions[:3]:
        label = action.get("display_label") or action.get("action_label") or "未命名持仓"
        signal = action.get("action_label") or "动作"
        ratio = action.get("final_ratio")
        pct_text = "比例待确认"
        if isinstance(ratio, (int, float)) and not isinstance(ratio, bool):
            pct_text = f"{float(ratio) * 100:.0f}%"
        qty = action.get("executable_quantity")
        qty_text = ""
        if isinstance(qty, (int, float)) and not isinstance(qty, bool) and qty > 0:
            qty_text = f"，{int(qty)} 单位"
        amount = action.get("estimated_amount_cny")
        amount_text = ""
        if amount is not None:
            amount_text = f"，约 ¥{float(amount):,.0f}"
            if action.get("amount_is_estimate"):
                amount_text += "（估算）"
        platform = action.get("platform") or ""
        settlement = action.get("settlement_display") or ""
        platform_line = " | ".join(x for x in [platform, settlement] if x) or "平台待确认"
        cancel = action.get("cancel_condition") or "触发条件不再成立时取消"
        lines.append(f"- **{signal}｜{label}**：{pct_text}{qty_text}{amount_text}")
        lines.append(f"  通道: {platform_line}；取消条件: {cancel}")
    overflow = card.get("actions_overflow")
    if isinstance(overflow, int) and not isinstance(overflow, bool) and overflow > 0:
        lines.append(f"- 另有 {overflow} 个获批动作超出展示上限，详见当日审计产物")
    return lines


# 带金额的 data_notes 类型判断：当做“待决事项”
_PENDING_TYPES = frozenset({"数据问题", "决策冲突", "锁定", "非开放期", "资金/结算"})


def _pending_item_type(text: str) -> str:
    """Classify a blocked/deferred note into pending type for grouping."""
    r = str(text or "").lower()
    if "开放期" in r or "周期" in r or "不在开放" in r:
        return "非开放期"
    if "锁定" in r or "不能交易" in r or "锁定状态" in r:
        return "锁定"
    if "数据" in r or "行情" in r or "过时" in r or "缺失" in r or "复权" in r:
        return "数据"
    if "资金" in r or "结算" in r or "缺口" in r:
        return "资金"
    if "风险" in r or "暂停加仓" in r or "停止" in r:
        return "风险"
    if "长期配置" in r or "仅供观察" in r or "研究" in r:
        return "观察"
    return "其他"


def _section_blocked_and_deferred(card: dict, assistant: dict) -> list[str]:
    """§5 禁止与延后 — manual_review 冲突已在 §3 呈现时不重复；
    带金额的 data_notes 归到 §6 组合与检查点的“待决事项”。

    P5-2: 估值过期类提示("手工估值超过 N 天")单独聚合展示——它们
    数量大(8/6 实测 14 条)且同质,逐条挤占前 4 上限会让用户看不到
    "半数持仓估值过期"这个整体事实。聚合为一条带计数,避免被截断。
    """
    lines: list[str] = [_section_heading("禁止与延后")]
    collected: list[str] = []
    valuation_stale_count = 0
    valuation_stale_seen: set[str] = set()

    status_raw = str(card.get("status") or "").strip().lower()
    actions = card.get("actions") or []
    manual_review_only = status_raw == "manual_review" and not actions

    # manual_review 状态下 §3 已列出 no_action_reasons，此处跳过
    no_action_reasons = card.get("no_action_reasons") or []
    if no_action_reasons and not manual_review_only:
        collected.extend(no_action_reasons)

    why = assistant.get("why") or []
    action_sentences = {a.get("reason_summary") for a in actions if a.get("reason_summary")}
    # R5-10: manual_review 时 §3 已展示 no_action_reasons 全文,§5 不得
    # 再通过 why/do_not_do 重复同一标的的抑制/延后原因(8/6 实测广发纳指
    # 在 §3 和 §5 各出现一次)。
    # 注意 §3 只渲染前 3 条(no_action_reasons[:3]),§5 只应跳过这前 3 条
    # ——若 reasons > 3,第 4+ 条必须保留在 §5,否则用户完全看不到
    # (R5-10 审查修正)。
    already_shown = set(no_action_reasons[:3]) if manual_review_only else set()
    for text in why:
        if text in action_sentences:
            continue
        if text in already_shown:
            continue
        if text not in collected:
            collected.append(text)

    do_not_do = assistant.get("do_not_do") or []
    for text in do_not_do:
        # R5-10: 已在 §3 展示的文本(或其前缀)跳过;do_not_do 与 why
        # 可能带不同后缀,做前缀匹配更稳。
        if text in already_shown:
            continue
        if any(text.startswith(s[:12]) or s.startswith(text[:12]) for s in already_shown):
            continue
        if text and text not in collected:
            collected.append(text)

    # 只把非资金缺口类的 data_notes 放这里；含金额的 data_notes 归到 §6
    data_notes = assistant.get("data_notes") or []
    for note in data_notes:
        if note and note not in collected and not _has_currency_amount(note):
            # P5-2: 估值过期类单独计数聚合,不逐条占位
            if "手工估值超过" in note or "估值超过" in note:
                norm = note.split(" 手工估值")[0].split(" 估值")[0]
                if norm not in valuation_stale_seen:
                    valuation_stale_seen.add(norm)
                valuation_stale_count += 1
                continue
            collected.append(note)

    risk = assistant.get("risk") or {}
    if risk.get("suspend_accumulation"):
        msg = "风险状态暂停加仓"
        if msg not in collected:
            collected.append(msg)

    priority0 = []
    priority1 = []
    priority2 = []
    priority3 = []
    for text in collected[:8]:
        lowered = text.lower()
        if any(k in lowered for k in ["行情数据过时", "数据异常", "暂缓", "需人工", "等待人工", "冲突", "最小交易单位", "review", "锁定"]):
            priority0.append(text)
        elif any(k in lowered for k in ["风险", "暂停加仓", "suspend"]):
            priority1.append(text)
        elif any(k in lowered for k in ["长期配置", "仅供观察", "研究候选", "研究", "观察"]):
            priority3.append(text)
        else:
            priority2.append(text)

    ordered = (priority0 + priority1 + priority2 + priority3)[:4]
    if not ordered:
        lines.append("- 无")
    for text in ordered:
        lines.append(f"- {text}")

    # P5-2: 估值过期聚合行(不占前 4 上限)
    if valuation_stale_count:
        lines.append(f"- {valuation_stale_count} 项持仓为手工估值（超过 30 天未更新），"
                     f"精确调仓前需先更新金额（{', '.join(list(valuation_stale_seen)[:3])} 等）")

    return lines


def _has_currency_amount(text: str) -> bool:
    """Detect whether a data_notes entry mentions a currency amount."""
    if not isinstance(text, str):
        return False
    return bool(re.search(r"[¥$€]\s*-?\d|CNY\s*-?\d|-?\d[\d,]*\s*元", text))


def _section_portfolio_and_checkpoint(card: dict, assistant: dict) -> list[str]:
    """§6 组合与检查点 — 风险状态 + 现金 + 待决事项 + 执行后 + 下一检查点。"""
    lines: list[str] = [_section_heading("组合与检查点")]
    risk = assistant.get("risk") or {}
    label = risk.get("label") or "风险状态待确认"
    transition = risk.get("transition") or "状态未变"
    lines.append(f"- 风险状态: {label}（{transition}）")
    reasons = risk.get("reasons") or []
    for reason in reasons[:2]:
        lines.append(f"- 触发原因: {reason}")
    if not reasons and risk.get("level") in ("hedge", "reduce"):
        lines.append("- 触发原因: 风险等级判定缺少可读证据，已转人工复核")

    # —— 资金折叠 ——
    cash = assistant.get("cash") or {}
    cash_parts: list[str] = []

    def _amount(key: str) -> float:
        item = cash.get(key) or {}
        try:
            return float(item.get("amount_cny") or 0)
        except (TypeError, ValueError):
            return 0.0

    available_now = _amount("available_now")
    confirmed_settling = _amount("confirmed_settling")
    planned_release = _amount("planned_release")
    locked = _amount("locked")
    strategic_exit = _amount("strategic_exit")
    safety_buffer = _amount("safety_buffer")
    unresolved_settlement = _amount("unresolved_settlement")

    cash_parts.append(f"现在能用 ¥{available_now:,.0f}")
    if confirmed_settling > 0:
        cash_parts.append(f"到账途中 ¥{confirmed_settling:,.0f}")
    if planned_release > 0:
        cash_parts.append(f"计划内到期释放 ¥{planned_release:,.0f}")
    if locked > 0:
        cash_parts.append(f"不能动 ¥{locked:,.0f}")

    # strategic_exit 仅当存在可执行的 sell 类动作，或存在"已获批但等待人工
    # 复核"的卖出（presentation 在 cash.pending_sell 置位）时展示
    actions = card.get("actions") or []
    has_sell_action = any(
        _is_sell_action(a) for a in actions
    ) or bool(cash.get("pending_sell"))
    if has_sell_action and strategic_exit > 0:
        cash_parts.append(f"卖出后可释放 ¥{strategic_exit:,.0f}")

    if safety_buffer > 0:
        cash_parts.append(f"安全垫 ¥{safety_buffer:,.0f}（不计入可用）")

    if cash_parts:
        lines.append(f"- 资金: {'；'.join(cash_parts)}")

    # R5-4: 总资产 —— 交易分析师需要知道资金桶的相对规模,否则 6 个数字
    # 无法快速判断"现在可用仅占总资产 X%"。数字来自 presentation 的
    # cash.total_assets_cny(与各桶同源,validator 已授权);占比为派生
    # 百分比,与各桶同源可复核,不新增 payload 数字。
    total_assets = float(cash.get("total_assets_cny") or 0.0)
    if total_assets > 0:
        lines.append(f"- 资产合计: ¥{total_assets:,.0f}（各资金桶加总，含安全垫与待决）")

    # —— 待决事项统一段（带金额的 data_notes + 部分延后理由）——
    pending_items: list[tuple[str, str]] = []
    for note in assistant.get("data_notes") or []:
        if _has_currency_amount(note):
            pending_items.append(("资金", note))
    if unresolved_settlement > 0 and not any(
        t == "资金" for t, _ in pending_items
    ):
        pending_items.append(("资金",
            f"¥{unresolved_settlement:,.0f} 的卖出资金结算方式待确认，"
            "未计入“现在能用”或“到账途中”"))

    do_not_do = assistant.get("do_not_do") or []
    for text in do_not_do:
        t = _pending_item_type(text)
        if t in ("锁定", "非开放期", "数据", "资金"):
            pending_items.append((t, text))

    if pending_items:
        lines.append("- 待决事项:")
        for ptype, text in pending_items[:3]:
            lines.append(f"  · [{ptype}] {text}")

    # —— 执行后估算 ——
    post_trade = assistant.get("post_trade_projection") or {}
    if actions and isinstance(post_trade, dict) and post_trade:
        available_before = post_trade.get("available_now_before_cny")
        available_after = post_trade.get("available_now_after_cny")
        equity_before = post_trade.get("equity_ratio_before")
        equity_after = post_trade.get("equity_ratio_after")
        # 只有所有字段都齐备时才输出，避免半截信息
        if (
            isinstance(available_before, (int, float)) and not isinstance(available_before, bool)
            and isinstance(available_after, (int, float)) and not isinstance(available_after, bool)
            and isinstance(equity_before, (int, float)) and not isinstance(equity_before, bool)
            and isinstance(equity_after, (int, float)) and not isinstance(equity_after, bool)
        ):
            lines.append(
                f"- 执行后估算: 可用 ¥{float(available_before):,.0f} → "
                f"¥{float(available_after):,.0f}, 权益比例 "
                f"{float(equity_before)*100:.0f}% → {float(equity_after)*100:.0f}%"
            )

    # —— 下一检查点 ——
    checkpoint = card.get("next_checkpoint") or "下一交易窗口复核"
    lines.append(f"- 下一检查点: {checkpoint}")

    if actions:
        cancel = actions[0].get("cancel_condition") or "触发条件不再成立时取消"
        lines.append(f"- 条件: {cancel}")
    else:
        release = risk.get("release_condition")
        if release:
            lines.append(f"- 条件: {release}")
        else:
            # 找一个非资金缺口的 data_note
            for note in assistant.get("data_notes") or []:
                if not _has_currency_amount(note):
                    lines.append(f"- 注意: {note}")
                    break

    # —— 明日计划 (C1-WP3: 确定性清单,非 LLM 创作) ——
    plan = assistant.get("tomorrow_plan") or []
    if plan:
        lines.append(_section_heading("明日计划"))
        for item in plan[:6]:
            action_text = str(item.get("action") or "").strip()
            if not action_text:
                continue
            prio = str(item.get("priority") or "low")
            marker = {"high": "①", "medium": "②", "low": "③"}.get(prio, "·")
            lines.append(f"- {marker} {action_text}")

    return lines


def _is_sell_action(action: dict) -> bool:
    """Detect whether an action is a sell/reduce/hedge type."""
    if not isinstance(action, dict):
        return False
    label = (action.get("action_label") or "").strip()
    signal = (action.get("signal_type") or "").strip().lower()
    if "卖" in label or "减" in label or "止损" in label or "对冲" in label:
        return True
    if signal in {"sell", "reduce", "hedge", "trim"}:
        return True
    return False


_SUBKEY_LABELS = {
    "direction": "方向", "confidence": "置信度", "horizon": "时间范围",
}


def _strip_outlook_from_payload(payload: dict) -> dict:
    """Return payload copy with outlook/outlook_delta removed for number scanning."""
    p = json.loads(json.dumps(payload))
    view = p.get("user_view") or {}
    brief = view.get("assistant_brief") or {}
    brief.pop("outlook", None)
    brief.pop("outlook_delta", None)
    return p


def _safe_numeric_spans(text: str) -> set[tuple[int, int]]:
    """Precompute (start, end) spans in *text* where numbers are contextually safe.

    Safe contexts:
    - Inside an ISO date (e.g. ``2026-07-17``)
    - Inside a URL (from ``http[s]://`` to the next whitespace / closing paren)
    - Horizon patterns (e.g. ``1-2w``, ``1-3个月``)
    - Version strings (e.g. ``v3.1.4``)
    """
    spans: set[tuple[int, int]] = set()

    # 1. ISO date/time spans — exact match only. Including the complete
    # timestamp exempts its time and timezone tokens, but not adjacent prose.
    iso_pattern = r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?"
    for m in re.finditer(iso_pattern, text):
        spans.add((m.start(), m.end()))

    # 2. URL spans
    for m in re.finditer(r"https?://[^\s\)>]+", text):
        spans.add((m.start(), m.end()))

    # 3. Horizon spans (e.g. "1-2w", "1-3m", "1-2周", "1-3个月")
    for m in re.finditer(r"\d[wmdy周个月天]|\d-\d+[wmdy周个月天]", text):
        spans.add((m.start(), m.end()))

    # 4. Version strings (e.g. "v3.1.4")
    for m in re.finditer(r"v\d+(?:\.\d+)+", text):
        spans.add((m.start(), m.end()))

    # 5. Instrument codes inside parentheses (e.g. "沪深300ETF（510300）")
    for m in re.finditer(r"[（\(]\d{6}[）\)]", text):
        spans.add((m.start(), m.end()))

    # 6. Array index notation (e.g. "sector_views[2]")
    for m in re.finditer(r"\[\d+\]", text):
        spans.add((m.start(), m.end()))
    return spans


def _is_span_safe(pos: int, num_str: str, safe_spans: set[tuple[int, int]]) -> bool:
    """Return True if the number at *pos* of length *len(num_str)* overlaps any safe span."""
    num_end = pos + len(num_str)
    for start, end in safe_spans:
        if pos < end and num_end > start:
            return True
    return False


_E2_REQUIRED_HEADINGS = ("本窗口变化", "走势研判", "可执行动作", "提前布局", "禁止与延后", "组合与检查点")


def _remove_outlook_sections(text: str) -> str:
    """Remove outlook-bearing section(s) from the rendered text.

    In the M1 six-section layout, both 本窗口变化 (delta narrative) and
    走势研判 (outlook body) may quote LLM-produced numbers (macro levels,
    horizons). These are validated upstream by outlook_validation.py; the
    render-layer number scan must not double-check them. All other
    sections must trace every number back to the payload.
    """
    stripped = text
    for heading in ("本窗口变化", "走势研判"):
        marker = f"**{heading}**"
        start = stripped.find(marker)
        if start < 0:
            continue
        end = len(stripped)
        for later in _E2_REQUIRED_HEADINGS:
            if later == heading:
                continue
            later_marker = f"**{later}**"
            idx = stripped.find(later_marker, start + len(marker))
            if idx >= 0:
                end = min(end, idx)
        stripped = stripped[:start] + stripped[end:]
    # Legacy layouts appended outlook sections at the end; keep cutting there.
    for marker in ("**中长期研判**", "**研判变化**"):
        idx = stripped.find(marker)
        if idx >= 0:
            stripped = stripped[:idx]
            break
    return stripped


def validate_payload_text(payload: dict, text: str) -> list[str]:
    errors = [f"internal token: {m.group(0)}" for m in _FORBIDDEN.finditer(text)]

    # Number validation: only scan deterministic rendered sections.
    # Outlook/delta have their own upstream validator (outlook_validation.py);
    # forcing deterministic-number authorization on narrative macro numbers
    # (VIX levels, yield thresholds, price targets) is an unbounded whitelist problem.
    # Remove only the outlook-bearing section(s) before number scanning.
    deterministic_text = _remove_outlook_sections(text)
    allowed = _number_values(_strip_outlook_from_payload(payload))
    # Render-time computed counts that are deterministic given the payload
    # but not stored as values inside it (trading payloads only). In M1 the
    # 提前布局 section may render "另有 N 个候选" where N == len(research) -
    # displayed_count; both len(research) and any subset count are safe to
    # authorize because they derive purely from the payload's research list.
    if payload.get("session_type") == "trading":
        research = ((payload.get("user_view") or {}).get("assistant_brief") or {}).get("research") or []
        for i in range(len(research) + 1):
            allowed.add(round(float(i), 4))

    numeric_text = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", deterministic_text)
    safe_spans = _safe_numeric_spans(numeric_text)
    for m in _NUMBER.finditer(numeric_text):
        raw = m.group()
        pos = m.start()
        try:
            value = round(float(raw.rstrip("%")), 4)
        except ValueError:
            continue
        # Skip safe numeric spans (date, URL, horizon)
        if _is_span_safe(pos, raw, safe_spans):
            continue
        if value not in allowed:
            errors.append(f"unauthorized number: {raw}")

    if text == "[SILENT]":
        return errors
    if payload.get("session_type") == "trading":
        # M1: six ordered sections (本窗口变化 / 走势研判 / 可执行动作 /
        # 提前布局 / 禁止与延后 / 组合与检查点).
        positions = []
        for heading in _E2_REQUIRED_HEADINGS:
            marker = f"**{heading}**"
            if marker not in text:
                errors.append(f"missing required section: {heading}")
            else:
                positions.append(text.index(marker))
        if len(positions) == len(_E2_REQUIRED_HEADINGS) and positions != sorted(positions):
            errors.append("wrong section order")
        for banned in (
            "交易指令卡", "私人投资助理", "为什么这样安排", "待人工确认的信号分类",
            "仅供观察", "中长期研判", "资产类别", "行业观察", "基准情景", "乐观情景", "风险情景",
            # M1: banned legacy 5-section headings
            "组合影响", "下一检查点",
        ):
            if f"**{banned}**" in text:
                errors.append(f"banned legacy heading: {banned}")
        # Conflict count lines are rendered in the old schema only.
        if re.search(r"[:：]\s*\d+\s*项\s*$", text, re.MULTILINE):
            errors.append("legacy conflict count line present")
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
        truth_errors = validate_push_truth(payload)
        if truth_errors:
            raise ValueError("; ".join(truth_errors))
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
