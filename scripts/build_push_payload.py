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
    delta_section = _section_window_changes(assistant)
    if delta_section:
        sections.append(delta_section)

    # 2. 可执行动作
    action_section = _section_executable_actions(card)
    if action_section:
        sections.append(action_section)

    # 3. 禁止与延后
    blocked_section = _section_blocked_and_deferred(card, assistant)
    if blocked_section:
        sections.append(blocked_section)

    # 4. 组合影响
    impact_section = _section_portfolio_impact(assistant)
    if impact_section:
        sections.append(impact_section)

    # 5. 下一检查点
    checkpoint_section = _section_next_checkpoint(card, assistant)
    if checkpoint_section:
        sections.append(checkpoint_section)

    lines: list[str] = [
        f"**{payload.get('session_label', '交易窗口')} · {payload.get('market_date', '')}**",
    ]
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


def _section_window_changes(assistant: dict) -> list[str]:
    lines: list[str] = [_section_heading("本窗口变化")]
    outlook_delta = assistant.get("outlook_delta") or {}
    changes = (outlook_delta.get("changes") or {}) if isinstance(outlook_delta, dict) else {}

    if changes:
        rendered = _render_delta_changes_concise(changes)
        for line in rendered[:4]:
            lines.append(f"- {line}")
        # Source refs changes (added/removed IDs)
        source_changes = changes.get("source_refs") or {}
        if isinstance(source_changes, dict):
            added = source_changes.get("added") or []
            removed = source_changes.get("removed") or []
            if added:
                lines.append(f"- 来源新增: {', '.join(str(x) for x in added[:3])}")
            if removed:
                lines.append(f"- 来源移除: {', '.join(str(x) for x in removed[:3])}")
        return lines

    outlook = assistant.get("outlook") or {}
    if isinstance(outlook, dict) and outlook.get("status") == "unavailable":
        lines.append("- 中长期研判暂不可用")
        return lines

    if isinstance(outlook, dict) and outlook.get("status") == "ok":
        if outlook.get("summary"):
            lines.append(f"- 综合判断: {outlook['summary']}")
        near = outlook.get("near_term") or {}
        medium = outlook.get("medium_term") or {}
        parts: list[str] = []
        if near:
            d = _DIRECTION_LABELS.get(near.get("direction"), near.get("direction", ""))
            c = _CONFIDENCE_LABELS.get(near.get("confidence"), near.get("confidence", ""))
            if d or c:
                parts.append(f"未来1-2周: {d}" + (f"（{c}）" if c else ""))
        if medium:
            d = _DIRECTION_LABELS.get(medium.get("direction"), medium.get("direction", ""))
            c = _CONFIDENCE_LABELS.get(medium.get("confidence"), medium.get("confidence", ""))
            if d or c:
                parts.append(f"未来1-3个月: {d}" + (f"（{c}）" if c else ""))
        for av in (outlook.get("asset_views") or [])[:4]:
            key = av.get("asset_class") or av.get("asset") or ""
            d = _DIRECTION_LABELS.get(av.get("direction"), av.get("direction", ""))
            if key and d:
                parts.append(f"{key}: {d}")
        for sv in (outlook.get("sector_views") or [])[:4]:
            key = sv.get("sector") or ""
            d = _DIRECTION_LABELS.get(sv.get("direction"), sv.get("direction", ""))
            if key and d:
                parts.append(f"{key}行业: {d}")
        for line in parts[:4]:
            lines.append(f"- {line}")
        return lines

    lines.append("- 本窗口未发现需要改变计划的新证据")
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


def _section_executable_actions(card: dict) -> list[str]:
    lines: list[str] = [_section_heading("可执行动作")]
    actions = card.get("actions") or []
    if not actions:
        no_action_reasons = card.get("no_action_reasons") or []
        status = card.get("status_label") or "等待人工确认"
        lines.append(f"- 状态: {status}")
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


def _section_blocked_and_deferred(card: dict, assistant: dict) -> list[str]:
    lines: list[str] = [_section_heading("禁止与延后")]
    collected: list[str] = []

    no_action_reasons = card.get("no_action_reasons") or []
    if no_action_reasons:
        collected.extend(no_action_reasons)

    why = assistant.get("why") or []
    action_sentences = {a.get("reason_summary") for a in (card.get("actions") or []) if a.get("reason_summary")}
    for text in why:
        if text in action_sentences:
            continue
        if text not in collected:
            collected.append(text)

    do_not_do = assistant.get("do_not_do") or []
    for text in do_not_do:
        if text and text not in collected:
            collected.append(text)

    data_notes = assistant.get("data_notes") or []
    for note in data_notes:
        if note and note not in collected:
            collected.append(note)

    risk = assistant.get("risk") or {}
    if risk.get("suspend_accumulation"):
        msg = "风险状态暂停加仓"
        if msg not in collected:
            collected.append(msg)

    if not collected:
        lines.append("- 无")

    # Sort by priority groups
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
    for text in ordered:
        lines.append(f"- {text}")

    # E2: research is always compressed to a single trailing line if present
    research = assistant.get("research") or []
    if research:
        lines.append(f"- 研究候选 {len(research)} 个，当前均不构成交易动作")

    return lines


def _section_portfolio_impact(assistant: dict) -> list[str]:
    lines: list[str] = [_section_heading("组合影响")]
    risk = assistant.get("risk") or {}
    label = risk.get("label") or "风险状态待确认"
    transition = risk.get("transition") or "状态未变"
    lines.append(f"- 风险状态: {label}（{transition}）")
    reasons = risk.get("reasons") or []
    for reason in reasons[:2]:
        lines.append(f"- 触发原因: {reason}")
    if not reasons and risk.get("level") in ("hedge", "reduce"):
        lines.append("- 触发原因: 风险等级判定缺少可读证据，已转人工复核")

    cash = assistant.get("cash") or {}
    cash_parts = []
    for key, label_text in (
        ("available_now", "现在能用"),
        ("confirmed_settling", "到账途中"),
        ("planned_release", "计划内到期释放"),
    ):
        item = cash.get(key) or {}
        if item.get("amount_cny") is not None:
            cash_parts.append(f"{label_text} ¥{float(item.get('amount_cny') or 0):,.0f}")
    locked = cash.get("locked") or {}
    locked_amount = float(locked.get("amount_cny") or 0)
    if locked_amount > 0:
        cash_parts.append(f"不能动 ¥{locked_amount:,.0f}")

    strategic = cash.get("strategic_exit") or {}
    strategic_amount = float(strategic.get("amount_cny") or 0)
    if strategic_amount > 0:
        cash_parts.append(f"卖出后可释放 ¥{strategic_amount:,.0f}")

    safety = cash.get("safety_buffer") or {}
    safety_amount = float(safety.get("amount_cny") or 0)
    if safety_amount > 0:
        cash_parts.append(f"安全垫 ¥{safety_amount:,.0f}（不计入可用）")

    if cash_parts:
        lines.append(f"- 资金: {'；'.join(cash_parts)}")
    return lines


def _section_next_checkpoint(card: dict, assistant: dict) -> list[str]:
    lines: list[str] = [_section_heading("下一检查点")]
    checkpoint = card.get("next_checkpoint") or "下一交易窗口复核"
    lines.append(f"- {checkpoint}")

    actions = card.get("actions") or []
    if actions:
        cancel = actions[0].get("cancel_condition") or "触发条件不再成立时取消"
        lines.append(f"- 条件: {cancel}")
        return lines

    risk = assistant.get("risk") or {}
    release = risk.get("release_condition")
    if release:
        lines.append(f"- 条件: {release}")
        return lines

    data_notes = assistant.get("data_notes") or []
    if data_notes:
        lines.append(f"- 注意: {data_notes[0]}")
    return lines



_SUBKEY_LABELS = {
    "direction": "方向", "confidence": "置信度", "horizon": "时间范围",
}


def _render_delta_changes(changes: dict, schema_version: int, lines: list[str]) -> None:
    """Render deterministic delta changes as Chinese text."""
    _ = schema_version  # reserved for future schema migration

    # Summary change
    summary = changes.get("summary", {})
    if isinstance(summary, dict) and ("from" in summary or "to" in summary):
        from_val = summary.get("from", "")
        to_val = summary.get("to", "")
        if from_val and to_val:
            lines.append(f"- 综合判断: {from_val} → {to_val}")
        elif to_val:
            lines.append(f"- 综合判断: 新→ {to_val}")

    # Confidence change
    confidence = changes.get("confidence", {})
    if isinstance(confidence, dict):
        from_c = confidence.get("from", "")
        to_c = confidence.get("to", "")
        if from_c and to_c:
            from_label = _CONFIDENCE_LABELS.get(from_c, from_c)
            to_label = _CONFIDENCE_LABELS.get(to_c, to_c)
            lines.append(f"- 置信度: {from_label} → {to_label}")

    # Sector view changes
    sector_changes = changes.get("sector_views", {})
    if isinstance(sector_changes, dict):
        for sector_name in list(sector_changes.keys())[:5]:
            sc = sector_changes[sector_name]
            if not isinstance(sc, dict):
                continue
            direction = sc.get("direction", {})
            if isinstance(direction, dict):
                d_from = _DIRECTION_LABELS.get(direction.get("from", ""), direction.get("from", ""))
                d_to = _DIRECTION_LABELS.get(direction.get("to", ""), direction.get("to", ""))
                if d_from and d_to:
                    lines.append(f"- {sector_name}行业: {d_from} → {d_to}")

    # Asset view changes
    asset_changes = changes.get("asset_views", {})
    if isinstance(asset_changes, dict):
        for asset_name in list(asset_changes.keys())[:4]:
            ac = asset_changes[asset_name]
            if not isinstance(ac, dict):
                continue
            direction = ac.get("direction", {})
            if isinstance(direction, dict):
                d_from = _DIRECTION_LABELS.get(direction.get("from", ""), direction.get("from", ""))
                d_to = _DIRECTION_LABELS.get(direction.get("to", ""), direction.get("to", ""))
                if d_from and d_to:
                    lines.append(f"- {asset_name}: {d_from} → {d_to}")

    # Near/medium term changes
    for hkey, hlabel in (("near_term", "未来1-2周"), ("medium_term", "未来1-3个月")):
        hc = changes.get(hkey, {})
        if isinstance(hc, dict) and hc:
            parts = []
            for subkey in ("direction", "confidence", "horizon"):
                sub = hc.get(subkey, {})
                if isinstance(sub, dict) and ("from" in sub or "to" in sub):
                    from_v = sub.get("from", "")
                    to_v = sub.get("to", "")
                    # Translate direction/confidence values
                    if subkey == "direction":
                        from_v = _DIRECTION_LABELS.get(from_v, from_v)
                        to_v = _DIRECTION_LABELS.get(to_v, to_v)
                    elif subkey == "confidence" or subkey == "horizon":
                        pass  # keep raw values
                    label = _SUBKEY_LABELS.get(subkey, subkey)
                    parts.append(f"{label}: {from_v} → {to_v}")
            if parts:
                lines.append(f"- {hlabel}: {'; '.join(parts)}")

    # Scenario changes (only base/bull/risk, each: label/validation/invalidation)
    scenario_changes = changes.get("scenarios", {})
    if isinstance(scenario_changes, dict):
        for sname, slabel in (("base", "基准情景"), ("bull", "乐观情景"), ("risk", "风险情景")):
            scene = scenario_changes.get(sname)
            if not isinstance(scene, dict):
                continue
            scene_parts = []
            for sf, sf_label in (("label", "研判"), ("validation", "验证条件"), ("invalidation", "否定条件")):
                sfv = scene.get(sf)
                if isinstance(sfv, dict) and ("from" in sfv or "to" in sfv):
                    from_v = sfv.get("from", "")
                    to_v = sfv.get("to", "")
                    if from_v and to_v:
                        scene_parts.append(f"{sf_label}: {from_v} → {to_v}")
            if scene_parts:
                for part in scene_parts[:3]:
                    lines.append(f"- {slabel}: {part}")

    # Source refs changes (added/removed IDs, max 5 total)
    source_changes = changes.get("source_refs", {})
    if isinstance(source_changes, dict):
        src_shown = 0
        added = source_changes.get("added", [])
        if isinstance(added, list):
            for sid in added[:5]:
                if src_shown >= 5:
                    break
                lines.append(f"- 来源新增: {sid}")
                src_shown += 1
        removed = source_changes.get("removed", [])
        if isinstance(removed, list):
            for sid in removed[:5]:
                if src_shown >= 5:
                    break
                lines.append(f"- 来源移除: {sid}")
                src_shown += 1


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


_E2_REQUIRED_HEADINGS = ("本窗口变化", "可执行动作", "禁止与延后", "组合影响", "下一检查点")


def _remove_outlook_sections(text: str) -> str:
    """Remove only the outlook-bearing section(s) from the rendered text.

    The number-authorization scan must cover every deterministic section
    (可执行动作 / 禁止与延后 / 组合影响 / 下一检查点) — amounts, quantities
    and percentages there must trace back to the payload. The E2 layout puts
    the outlook narrative inside 本窗口变化, so only that one section is
    removed (outlook numbers have their own upstream validator,
    outlook_validation.py). The pre-E2 implementation truncated the text at
    the first section heading, which silently disabled the scan for every
    section in the E2 layout (adversarial review P0-2).
    """
    start = text.find("**本窗口变化**")
    if start < 0:
        stripped = text
    else:
        end = len(text)
        for marker in ("**可执行动作**", "**禁止与延后**", "**组合影响**", "**下一检查点**"):
            idx = text.find(marker, start)
            if idx >= 0:
                end = min(end, idx)
        stripped = text[:start] + text[end:]
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
    # Render-time computed counts that are deterministic given the payload but
    # not stored as values inside it (trading payloads only — the research
    # list lives under user_view).
    if payload.get("session_type") == "trading":
        research = ((payload.get("user_view") or {}).get("assistant_brief") or {}).get("research") or []
        allowed.add(round(float(len(research)), 4))

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
        # Concise report schema (TASK-001E2): exactly five ordered sections.
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
