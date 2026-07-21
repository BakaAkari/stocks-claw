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

# ── Forbidden tokens for outlook/delta narrative scanning ──────────────────
# Real word boundaries (\b) - the nested \\\\b in the original was a literal
# backslash-b sequence that never matched anything.
_OUTLOOK_FORBIDDEN = re.compile(
    r"\b(?:position_id|decision_id|manual_review|approved_actions|"
    r"suppressed_actions|unresolved_conflicts|research_only|review_required|"
    r"take_profit|stop_loss|reduce|add|hedge)\b|"
    r"(?:a|us|ccb|alipay)_[A-Za-z0-9_]+"
)

# Chinese trading-action patterns that must never appear in outlook narrative
_CHINESE_TRADE_ACTION_RE = re.compile(
    "买入|卖出|减仓|加仓|清仓|止损\\d+|止盈\\d+|仓位\\s*\\d+|¥|人民币"
)


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
    if age < -1 or age > 45:
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

    # ── 中长期研判 section ──────────────────────────────────────────────────
    outlook = assistant.get("outlook") or {}
    outlook_delta = assistant.get("outlook_delta") or {}
    if outlook_delta:
        # ── 研判变化 (delta) ──────────────────────────────────────────
        lines.extend(["", "**研判变化**"])
        changes = outlook_delta.get("changes", {})
        _render_delta_changes(changes, int(outlook_delta.get("schema_version", 1)), lines)
    elif outlook and outlook.get("status") == "unavailable":
        lines.extend(["", "**中长期研判**"])
        lines.append(f"- {outlook.get('message', '研判暂不可用')}")
        for limit in (outlook.get("data_limitations") or [])[:3]:
            lines.append(f"  - {limit}")
    elif outlook and outlook.get("status") == "ok":
        lines.extend(["", "**中长期研判**"])
        lines.append(f"- 综合置信度: {_CONFIDENCE_LABELS.get(outlook.get('confidence', ''), outlook.get('confidence', ''))}")
        if outlook.get("summary"):
            lines.append(f"- 综合判断: {outlook['summary']}")

        # ── Near term (1-2w) ──────────────────────────────────────────
        near = outlook.get("near_term") or {}
        if near:
            lines.append("")
            lines.append("**未来1–2周**")
            near_dir = _DIRECTION_LABELS.get(near.get("direction", ""), near.get("direction", ""))
            near_conf = _CONFIDENCE_LABELS.get(near.get("confidence", ""), near.get("confidence", ""))
            horizon_str = f"（{near.get('horizon', '')}）" if near.get("horizon") else ""
            lines.append(f"- 方向: {near_dir}，置信度: {near_conf}{horizon_str}")

        # ── Medium term (1-3m) ────────────────────────────────────────
        medium = outlook.get("medium_term") or {}
        if medium:
            lines.append("")
            lines.append("**未来1–3个月**")
            med_dir = _DIRECTION_LABELS.get(medium.get("direction", ""), medium.get("direction", ""))
            med_conf = _CONFIDENCE_LABELS.get(medium.get("confidence", ""), medium.get("confidence", ""))
            horizon_str = f"（{medium.get('horizon', '')}）" if medium.get("horizon") else ""
            lines.append(f"- 方向: {med_dir}，置信度: {med_conf}{horizon_str}")

        # ── Asset views (top level, limit 4) ──────────────────────────
        av_list = outlook.get("asset_views") or []
        if av_list:
            lines.append("")
            lines.append("**资产类别**")
        for av in av_list[:4]:
            asset_key = av.get("asset_class", "") or av.get("asset", "")
            d = _DIRECTION_LABELS.get(av.get("direction", ""), av.get("direction", ""))
            rationale = av.get("rationale", "")
            if rationale:
                lines.append(f"- {asset_key}: {d} — {rationale}")
            else:
                lines.append(f"- {asset_key}: {d}")

        # ── Sector views (top level, limit 5) ─────────────────────────
        sv_list = outlook.get("sector_views") or []
        if sv_list:
            lines.append("")
            lines.append("**行业观察**")
        for sv in sv_list[:5]:
            sector_key = sv.get("sector", "")
            d = _DIRECTION_LABELS.get(sv.get("direction", ""), sv.get("direction", ""))
            rationale = sv.get("rationale", "")
            if rationale:
                lines.append(f"- {sector_key}行业: {d} — {rationale}")
            else:
                lines.append(f"- {sector_key}行业: {d}")

        # ── Scenarios ─────────────────────────────────────────────────
        scenarios = outlook.get("scenarios") or {}
        for key, label in (("base", "基准情景"), ("bull", "乐观情景"), ("risk", "风险情景")):
            scene = scenarios.get(key) or {}
            if not scene:
                continue
            lines.append("")
            lines.append(f"**{label}**")
            for driver in (scene.get("drivers") or [])[:3]:
                lines.append(f"- 驱动因素: {driver}")
            if scene.get("portfolio_effect"):
                lines.append(f"- 组合影响: {scene['portfolio_effect']}")
            # validation / invalidation are lists; render up to 3 items each
            validation = scene.get("validation", []) if isinstance(scene.get("validation"), list) else ([scene["validation"]] if scene.get("validation") else [])
            for item in validation[:3]:
                lines.append(f"- 验证条件: {item}")
            invalidation = scene.get("invalidation", []) if isinstance(scene.get("invalidation"), list) else ([scene["invalidation"]] if scene.get("invalidation") else [])
            for item in invalidation[:3]:
                lines.append(f"- 否定条件: {item}")

        # ── Source references (limit 5) ───────────────────────────────
        sources = outlook.get("source_refs") or []
        if sources:
            lines.append("")
            lines.append("**来源**")
            for src in sources[:5]:
                s = src.get("source", "")
                t = src.get("title", "")
                u = src.get("url", "")
                p = src.get("published_at", "")
                if s and t and u:
                    if p:
                        lines.append(f"- [{s}｜{t}]({u}) — {p}")
                    else:
                        lines.append(f"- [{s}｜{t}]({u})")

    return "\n".join(lines)


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


def _traverse_outlook_text(obj: Any) -> str:
    """Concatenate all string values from outlook/outlook_delta sub-tree."""
    parts: list[str] = []
    _walk_outlook_strings(obj, parts)
    return " ".join(parts)


def _walk_outlook_strings(obj: Any, parts: list[str]) -> None:
    """Walk all string values for token/number scanning.

    Does NOT skip any key — horizon, published_at, and generated_at values are
    scanned for internal tokens; the caller's span-based safe-numeric check
    handles ISO dates and legit horizon patterns.
    """
    if isinstance(obj, dict):
        for v in obj.values():
            _walk_outlook_strings(v, parts)
    elif isinstance(obj, list):
        for item in obj:
            _walk_outlook_strings(item, parts)
    elif isinstance(obj, str):
        parts.append(obj)


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

    return spans


def _is_span_safe(pos: int, num_str: str, safe_spans: set[tuple[int, int]]) -> bool:
    """Return True if the number at *pos* of length *len(num_str)* overlaps any safe span."""
    num_end = pos + len(num_str)
    for start, end in safe_spans:
        if pos < end and num_end > start:
            return True
    return False


def validate_payload_text(payload: dict, text: str) -> list[str]:
    errors = [f"internal token: {m.group(0)}" for m in _FORBIDDEN.finditer(text)]

    # Base allowed numbers: payload MINUS outlook/outlook_delta (no self-authorization)
    clean_payload = _strip_outlook_from_payload(payload)
    allowed = _number_values(clean_payload)

    numeric_text = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", text)
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

    # Also scan outlook / outlook_delta sub-fields inside user_view
    view = payload.get("user_view") or {}
    assistant = view.get("assistant_brief") or {}
    for field in ("outlook", "outlook_delta"):
        outlook_data = assistant.get(field) or {}
        if outlook_data:
            outlook_text = _traverse_outlook_text(outlook_data)
            errors.extend(
                f"internal token in outlook: {m.group(0)}"
                for m in _OUTLOOK_FORBIDDEN.finditer(outlook_text)
            )
            # Chinese trading action scanning in outlook narrative
            for m in _CHINESE_TRADE_ACTION_RE.finditer(outlook_text):
                errors.append(f"trade instruction in outlook: {m.group(0)}")
            # Number scanning on outlook text — skip ISO dates, URLs, horizon
            outlook_numeric = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", outlook_text)
            outlook_safe = _safe_numeric_spans(outlook_numeric)
            for m in _NUMBER.finditer(outlook_numeric):
                raw = m.group()
                pos = m.start()
                try:
                    value = round(float(raw.rstrip("%")), 4)
                except ValueError:
                    continue
                # Skip safe numeric spans in outlook text
                if _is_span_safe(pos, raw, outlook_safe):
                    continue
                if value not in allowed:
                    errors.append(f"unauthorized number in outlook: {raw}")

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
