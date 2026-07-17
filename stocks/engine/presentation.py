"""Deterministic user-facing presentation helpers for trading reports.

Machine identifiers and internal enum values remain in the audit artifact. This
module creates stable Chinese labels for the user-facing report contract.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

_SENTINEL = object()
_no_value = _SENTINEL

_SIGNAL_LABELS = {
    "stop_loss": "止损", "take_profit": "止盈", "reduce": "减仓",
    "add": "加仓", "hold": "持有", "wait": "等待",
}
_STATUS_LABELS = {
    "approved": "已有获批动作", "suppressed": "今日无需操作",
    "review_required": "等待人工确认",
}
_RISK_LABELS = {
    "hedge": "防御状态", "reduce": "降低风险", "watch": "观察状态",
    "normal": "常态",
}
_ANOMALY_MESSAGES = {
    "single_bar_jump": ("单根行情跳变异常，可能存在拆分、复权或数据源问题", "暂停依据该段行情执行交易"),
    "mixed_adjustment_regime": ("历史行情混用了不同复权口径", "暂停技术指标交易，等待统一行情口径"),
    "price_ma20_dislocation": ("价格与20日均线偏差异常，可能存在复权或数据源口径问题", "暂停依据该指标执行交易"),
    "prev_close_mismatch": ("前收盘价在不同数据源之间不一致", "暂停执行，等待核对正确收盘价"),
    "source_regime_change": ("行情数据源或计算口径发生变化", "暂停执行，等待同口径数据恢复"),
}
_EVIDENCE_LABELS = {
    "price": "价格", "ma20": "20日均线", "prev_close": "前收盘价",
    "source_prev_close": "数据源前收盘价", "stated_prev_close": "数据源前收盘价",
    "actual_prev_close": "上一根实际收盘价", "diff_pct": "差异百分比",
    "current_close": "当前收盘价", "change_pct": "跳变幅度",
    "prev_price": "跳变前价格", "current_price": "当前价格",
    "ratio": "价格比值", "deviation_pct": "偏离百分比",
    "threshold_pct": "告警阈值", "pct_change": "涨跌幅",
}
_ESTIMATE_FRESHNESS = frozenset({"previous_close", "stale", "old", "unknown", "missing", "no_data"})
_ESTIMATE_VALUATION_METHODS = frozenset({"manual_amount", "fund_nav", "insurance_value"})


def public_instrument_code(instrument_key: str, product_type: str = "") -> str:
    """Return a public trading/fund code, never a position id."""
    del product_type
    value = str(instrument_key or "").strip()
    if not value or ":" not in value:
        return ""
    market, code = value.split(":", 1)
    if market.lower() not in {"a", "us", "hk", "fund", "crypto"}:
        return ""
    return code.strip()


def display_label(
    display_name: str,
    instrument_key: str,
    product_type: str = "",
    *,
    public_code: str = "",
    fallback: str = "",
) -> str:
    """Render real name plus public code without leaking the machine fallback id."""
    del fallback
    name = str(display_name or "").strip() or "未命名持仓"
    code = str(public_code or "").strip() or public_instrument_code(instrument_key, product_type)
    return f"{name}（{code}）" if code else name


def signal_label(signal: str) -> str:
    return _SIGNAL_LABELS.get(str(signal or ""), "待确认动作")


def status_label(status: str) -> str:
    return _STATUS_LABELS.get(str(status or ""), "等待人工确认")


def risk_label(level: str) -> str:
    return _RISK_LABELS.get(str(level or ""), "风险状态待确认")


def freshness_is_estimate(evidence: dict, valuation_method: str) -> bool:
    if str(valuation_method or "") in _ESTIMATE_VALUATION_METHODS:
        return True
    freshness = str((evidence or {}).get("price_freshness") or "unknown")
    return freshness in _ESTIMATE_FRESHNESS


def _evidence_summary(evidence: dict[str, Any]) -> str:
    parts = []
    for key in sorted(evidence):
        value = evidence[key]
        if not isinstance(value, (str, int, float, bool)) and value is not None:
            continue
        parts.append(f"{_EVIDENCE_LABELS.get(key, '相关数据')}={value}")
    return "，".join(parts)


def anomaly_display(anomaly: dict) -> dict:
    """Translate an anomaly without exposing its internal code."""
    code = str((anomaly or {}).get("code") or "")
    message, impact = _ANOMALY_MESSAGES.get(
        code, ("数据质量异常，需人工核对", "暂停相关技术动作，等待数据确认")
    )
    return {
        "display_message": message,
        "user_impact": impact,
        "evidence_summary": _evidence_summary((anomaly or {}).get("evidence") or {}),
    }


_SUPPRESSION_REASON_TEXT = {
    "research_only": "属于长期配置或研究信号，当前不形成交易动作",
    "periodic_open": "当前不在开放操作期，暂时不能交易",
    "locked": "资产当前处于锁定状态，暂时不能交易",
    "prev_close_mismatch": "前收盘价存在数据源差异，需核对后再决定",
    "source_regime_change": "行情来源或计算口径发生变化，需等待同口径数据",
    "single_bar_jump": "行情出现异常跳变，需排查拆分、复权或数据源问题",
    "mixed_adjustment_regime": "历史行情复权口径不一致，技术动作已暂停",
    "price_ma20_dislocation": "价格与20日均线关系异常，技术动作已暂停",
    "t2_plus": "资金或份额需等待结算完成后才能操作",
    "manual_fallback": "当前使用人工估值，需更新可靠行情后再决定",
}


def _safe_reason_text(reason: str) -> str:
    value = str(reason or "")
    for key, text in _SUPPRESSION_REASON_TEXT.items():
        if key in value:
            return text
    if "数据异常" in value:
        return "相关数据存在异常，需核对后再决定"
    if "流动性" in value or "不可交易" in value or "不可操作" in value:
        return "当前交易条件不满足，暂时不能操作"
    return "组合约束或风险条件尚未满足，等待下一检查点"


def suppression_reason_display(reason: str) -> str:
    """Translate suppression reason codes without leaking machine tokens."""
    return _safe_reason_text(reason)


def _position_maps(position_valuations: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    by_id = {}
    by_key = {}
    for item in position_valuations or []:
        pid = str(item.get("position_id") or "")
        key = str(item.get("instrument_key") or "")
        if pid:
            by_id[pid] = item
        if key:
            by_key[key] = item
    return by_id, by_key


def _display_for_position(item: dict) -> str:
    classification = item.get("classification") or {}
    return display_label(
        item.get("display_name", ""), item.get("instrument_key", ""),
        classification.get("product_type", ""), public_code=item.get("public_code", ""),
    )


def _cash_view(schedule: dict) -> dict:
    values = schedule or {}
    return {
        "immediate": {"label": "现在能用", "amount_cny": round(values.get("immediate_cash_cny", 0.0) or 0.0, 2)},
        "settling": {"label": "到账途中", "amount_cny": round(values.get("settling_cash_cny", 0.0) or 0.0, 2)},
        "strategic_exit": {"label": "卖出后才能用", "amount_cny": round(values.get("strategic_exit_value_cny", 0.0) or 0.0, 2)},
        "locked": {"label": "不能动", "amount_cny": round(values.get("locked_value_cny", 0.0) or 0.0, 2)},
    }


def _conflict_reason(conflict: dict, by_id: dict[str, dict]) -> str:
    item = by_id.get(str(conflict.get("position_id") or ""), {})
    label = _display_for_position(item)
    bucket = str(conflict.get("bucket") or "组合")
    ratio = conflict.get("bucket_ratio")
    ratio_text = f"{float(ratio) * 100:.1f}%" if isinstance(ratio, (int, float)) else "待确认"
    action = signal_label(conflict.get("signal", ""))
    return f"{label}：{bucket}当前占组合{ratio_text}，低于目标下限，但技术信号要求{action}；方向冲突，需人工确认"


def _position_review_map(position_reviews: list[dict]) -> dict[str, dict]:
    return {
        str(item.get("position_id") or ""): item
        for item in (position_reviews or [])
        if item.get("position_id")
    }


def _suppressed_user_text(raw: dict, by_id: dict[str, dict], reviews_by_id: dict[str, dict]) -> str:
    pid = str(raw.get("position_id") or "")
    label = _display_for_position(by_id.get(pid, {}))
    anomalies = ((reviews_by_id.get(pid, {}).get("evidence") or {}).get("data_anomalies") or [])
    if anomalies:
        display = anomaly_display(anomalies[0])
        evidence = display.get("evidence_summary")
        detail = f"；{evidence}" if evidence else ""
        return f"{label}：{display['display_message']}{detail}；{display['user_impact']}"
    return f"{label}：{_safe_reason_text(raw.get('reason', ''))}"


def _conflict_summary(conflicts: list[dict]) -> list[dict]:
    counts = Counter(signal_label(item.get("signal", "")) for item in (conflicts or []))
    order = {"止损": 0, "减仓": 1, "止盈": 2, "加仓": 3, "持有": 4, "待确认动作": 9}
    return [
        {"action_label": label, "count": count}
        for label, count in sorted(counts.items(), key=lambda pair: (order.get(pair[0], 8), pair[0]))
    ]


def _risk_reasons(risk_state: dict) -> list[str]:
    mapping = {
        "cluster:geopolitics": "地缘政治风险达到临界级别",
        "cluster:macro": "宏观风险达到临界级别",
        "cluster:liquidity": "市场流动性风险达到临界级别",
    }
    reasons = []
    for key in (risk_state or {}).get("evidence_keys") or []:
        text = mapping.get(str(key))
        if text and text not in reasons:
            reasons.append(text)
    return reasons


def _display_timestamp(value: str) -> str:
    raw = str(value or "")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw or "时间待确认"
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


def _data_notes(data_boundaries: dict) -> list[str]:
    quality = (data_boundaries or {}).get("data_quality") or {}
    notes = []
    by_market = ((quality.get("quotes") or {}).get("by_market") or {})
    names = {"a": "A股", "us": "美股", "crypto": "加密资产"}
    for market in ("a", "us", "crypto"):
        item = by_market.get(market) or {}
        freshness = str(item.get("freshness") or "")
        if freshness in {"stale", "old"}:
            notes.append(f"{names[market]}行情数据已过时（截止 {_display_timestamp(item.get('as_of'))}）")
        elif freshness in {"missing", "unknown"} and item:
            notes.append(f"{names[market]}行情数据缺失或时间未知")
    macro = quality.get("macro") or {}
    if str(macro.get("freshness") or "") in {"old", "stale"}:
        notes.append(f"宏观数据较旧（截止 {_display_timestamp(macro.get('as_of'))}）")
    return notes


_OUTLOOK_ALLOWED_TOP = frozenset({
    "status", "generated_at", "message", "data_limitations",
    "summary", "confidence", "near_term", "medium_term",
    "asset_views", "sector_views", "scenarios", "source_refs",
})
_OUTLOOK_HORIZON_ALLOWED = frozenset({"horizon", "direction", "confidence"})
_OUTLOOK_VIEW_ALLOWED = frozenset({"asset_class", "asset", "sector", "direction", "rationale"})
_OUTLOOK_SCENARIO_ALLOWED = frozenset({
    "label", "drivers", "portfolio_effect", "validation", "invalidation",
})
_OUTLOOK_SOURCE_ALLOWED = frozenset({"source", "title", "url", "published_at", "id"})
_DELTA_ALLOWED_TOP = frozenset({"schema_version", "changes"})
_DELTA_CHANGE_SCALAR = frozenset({"summary", "confidence"})
_DELTA_SCENARIO_NAMES = frozenset({"base", "bull", "risk"})
_DELTA_SCENARIO_FIELDS = frozenset({"label", "validation", "invalidation"})
_DELTA_HORIZON_KEYS = frozenset({"direction", "confidence", "horizon"})
_DELTA_SOURCE_KEYS = frozenset({"added", "removed"})


def _str(val: Any) -> str | None:
    """Return *val* only when it is already a string."""
    if isinstance(val, str):
        return val
    return None


def _str_list(val: Any, max_items: int = 0) -> list[str]:
    """Return list of str values; cap at max_items (0 = unlimited)."""
    if not isinstance(val, list):
        return []
    items = [v for v in val if isinstance(v, str)]
    return items[:max_items] if max_items > 0 else items


def project_outlook_for_display(outlook: dict | None) -> dict:
    """Public helper: project only whitelisted outlook fields with type enforcement.

    Strips unknown/internal keys, enforces scalar types, limits list lengths,
    and restricts scenario names to base/bull/risk only.  Call before writing
    an outlook into the user-facing assistant_brief.
    """
    if not isinstance(outlook, dict):
        return {}
    result: dict = {}
    for key in outlook:
        if key not in _OUTLOOK_ALLOWED_TOP:
            continue
        val = outlook[key]
        # ---- scalar string fields ----
        if key in ("status", "generated_at", "message", "summary", "confidence"):
            s = _str(val)
            if s is not None:
                result[key] = s
        # ---- data_limitations: str list, max 3 ----
        elif key == "data_limitations":
            items = _str_list(val, max_items=3)
            if items:
                result[key] = items
        # ---- near_term / medium_term: dict with str-only horizon/direction/confidence ----
        elif key in ("near_term", "medium_term"):
            if isinstance(val, dict):
                projected = {}
                for hk in _OUTLOOK_HORIZON_ALLOWED:
                    hv = val.get(hk)
                    s = _str(hv)
                    if s is not None:
                        projected[hk] = s
                if projected:
                    result[key] = projected
        # ---- asset_views / sector_views: list of dicts, str-only fields, max 5/4 ----
        elif key in ("asset_views", "sector_views"):
            if isinstance(val, list):
                max_items = 4 if key == "asset_views" else 5
                items = []
                for item in val:
                    if not isinstance(item, dict):
                        continue
                    projected = {}
                    for vk in _OUTLOOK_VIEW_ALLOWED:
                        vv = item.get(vk)
                        s = _str(vv)
                        if s is not None:
                            projected[vk] = s
                    if projected:
                        items.append(projected)
                    if len(items) >= max_items:
                        break
                if items:
                    result[key] = items
        # ---- scenarios: only base/bull/risk, each scene str/list[str] with caps ----
        elif key == "scenarios":
            if isinstance(val, dict):
                projected = {}
                for sname in ("base", "bull", "risk"):
                    scene = val.get(sname)
                    if not isinstance(scene, dict):
                        continue
                    scene_proj: dict = {}
                    # label / portfolio_effect -> str
                    for sk in ("label", "portfolio_effect"):
                        sv = scene.get(sk)
                        s = _str(sv)
                        if s is not None:
                            scene_proj[sk] = s
                    # drivers / validation / invalidation -> str list, max 3 each
                    for sk in ("drivers", "validation", "invalidation"):
                        items = _str_list(scene.get(sk), max_items=3)
                        if items:
                            scene_proj[sk] = items
                    if scene_proj:
                        projected[sname] = scene_proj
                if projected:
                    result[key] = projected
        # ---- source_refs: list of dicts, str-only fields, max 5 ----
        elif key == "source_refs":
            if isinstance(val, list):
                items = []
                for item in val:
                    if not isinstance(item, dict):
                        continue
                    projected = {}
                    for sk in _OUTLOOK_SOURCE_ALLOWED:
                        sv = item.get(sk)
                        s = _str(sv)
                        if s is not None:
                            projected[sk] = s
                    if projected:
                        items.append(projected)
                    if len(items) >= 5:
                        break
                if items:
                    result[key] = items
    return result


def project_outlook_delta_for_display(delta: dict | None) -> dict:
    """Public helper: project only whitelisted outlook delta fields.

    Strips unknown/internal keys and enforces type constraints on from/to
    values (scalar str or list[str] by field).  Call before writing a delta
    into the user-facing assistant_brief.
    """
    if not isinstance(delta, dict):
        return {}
    result: dict = {}
    for key in delta:
        if key not in _DELTA_ALLOWED_TOP:
            continue
        if key == "changes":
            val = delta[key]
            if not isinstance(val, dict):
                continue
            projected = _project_delta_changes(val)
            if projected:
                result["changes"] = projected
        elif key == "schema_version" and isinstance(delta[key], int) and not isinstance(delta[key], bool):
            result[key] = delta[key]
    return result


def _project_delta_changes(changes: dict) -> dict:
    """Recursive whitelist for delta changes; strip unknown/nested/internal keys
    and enforce type constraints."""
    projected: dict = {}
    for ckey, cval in changes.items():
        if not isinstance(cval, dict):
            continue
        if ckey in _DELTA_CHANGE_SCALAR:
            # summary/confidence: from/to scalar str
            scalar = {}
            for dk in ("from", "to"):
                dv = cval.get(dk)
                s = _str(dv)
                if s is not None:
                    scalar[dk] = s
            if scalar:
                projected[ckey] = scalar
        elif ckey in ("near_term", "medium_term"):
            # direction/confidence/horizon: from/to scalar str
            h_proj = {}
            for hk in _DELTA_HORIZON_KEYS:
                hv = cval.get(hk)
                if isinstance(hv, dict):
                    h_sub = {}
                    for dk in ("from", "to"):
                        dv = hv.get(dk)
                        s = _str(dv)
                        if s is not None:
                            h_sub[dk] = s
                    if h_sub:
                        h_proj[hk] = h_sub
            if h_proj:
                projected[ckey] = h_proj
        elif ckey in ("sector_views", "asset_views"):
            # direction: only scalar str from/to for each named view
            v_proj = {}
            for vname, vval in cval.items():
                if not isinstance(vval, dict):
                    continue
                dir_val = vval.get("direction")
                if isinstance(dir_val, dict):
                    dir_proj = {}
                    for dk in ("from", "to"):
                        dv = dir_val.get(dk)
                        s = _str(dv)
                        if s is not None:
                            dir_proj[dk] = s
                    if dir_proj:
                        v_proj[vname] = {"direction": dir_proj}
            if v_proj:
                projected[ckey] = v_proj
        elif ckey == "scenarios":
            # label: scalar str; validation/invalidation: list[str]
            s_proj = {}
            for sname in _DELTA_SCENARIO_NAMES:
                scene = cval.get(sname)
                if not isinstance(scene, dict):
                    continue
                scene_proj = {}
                for sf in _DELTA_SCENARIO_FIELDS:
                    sfv = scene.get(sf)
                    if sf == "label":
                        if isinstance(sfv, dict):
                            sub = {}
                            for dk in ("from", "to"):
                                dv = sfv.get(dk)
                                s = _str(dv)
                                if s is not None:
                                    sub[dk] = s
                            if sub:
                                scene_proj[sf] = sub
                    else:
                        # validation/invalidation: list[str] from/to
                        if isinstance(sfv, dict):
                            sub = {}
                            for dk in ("from", "to"):
                                dv = sfv.get(dk)
                                if isinstance(dv, list):
                                    lst = [x for x in dv if isinstance(x, str)]
                                    if lst:
                                        sub[dk] = lst
                                elif isinstance(dv, str):
                                    sub[dk] = dv
                            if sub:
                                scene_proj[sf] = sub
                if scene_proj:
                    s_proj[sname] = scene_proj
            if s_proj:
                projected[ckey] = s_proj
        elif ckey == "source_refs":
            src_proj = {}
            for sk in _DELTA_SOURCE_KEYS:
                sv = cval.get(sk)
                if isinstance(sv, list):
                    lst = [str(x) for x in sv if isinstance(x, (str, int, float, bool))]
                    if lst:
                        src_proj[sk] = lst
            if src_proj:
                projected[ckey] = src_proj
    return projected


# Backward-compatible private aliases used internally by build_user_view
_project_outlook = project_outlook_for_display
_project_outlook_delta = project_outlook_delta_for_display


def build_user_view(
    portfolio_decision: dict,
    position_valuations: list[dict],
    position_reviews: list[dict],
    research_candidates: list[dict],
    risk_state: dict,
    *,
    data_boundaries: dict | None = None,
    session_id: str,
    session_intent: str,
    structured_outlook: dict | None = None,
    outlook_delta: dict | None = None,
) -> dict:
    """Build the deterministic trade-card and assistant presentation contract."""
    decision = portfolio_decision or {}
    by_id, by_key = _position_maps(position_valuations)
    reviews_by_id = _position_review_map(position_reviews)
    actions = []
    for raw in (decision.get("approved_actions") or [])[:3]:
        item = by_id.get(str(raw.get("position_id") or ""), {})
        market_value = item.get("market_value_cny")
        ratio = float(raw.get("ratio") or 0.0)
        amount = round(float(market_value) * ratio, 2) if market_value is not None else None
        actions.append({
            "display_label": _display_for_position(item),
            "action_label": signal_label(raw.get("signal", "")),
            "ratio": ratio,
            "estimated_amount_cny": amount,
            "amount_is_estimate": freshness_is_estimate(
                item.get("evidence") or {}, item.get("valuation_method", "")
            ) if item else True,
            "reason_summary": str(raw.get("action_description") or raw.get("reason") or "按获批条件执行"),
            "cancel_condition": str(raw.get("cancel_condition") or "触发条件不再成立时取消"),
            "settlement_display": str(raw.get("settlement_timing") or "到账时间待确认"),
            "next_checkpoint": str(raw.get("next_checkpoint") or "下一交易窗口复核"),
        })

    no_action_reasons = []
    for conflict in decision.get("unresolved_conflicts") or []:
        reason = _conflict_reason(conflict, by_id)
        if reason not in no_action_reasons:
            no_action_reasons.append(reason)
        if len(no_action_reasons) == 2:
            break
    for raw in decision.get("suppressed_actions") or []:
        if len(no_action_reasons) == 2:
            break
        reason = _suppressed_user_text(raw, by_id, reviews_by_id)
        if reason not in no_action_reasons:
            no_action_reasons.append(reason)
    if not no_action_reasons and not actions:
        no_action_reasons.append("当前没有满足执行条件的获批动作")

    raw_status = str(decision.get("status") or "")
    if actions:
        card_status, card_label = "action_required", "需要操作"
    elif raw_status == "review_required" and decision.get("unresolved_conflicts"):
        card_status, card_label = "manual_review", "等待人工确认"
    else:
        card_status, card_label = "no_action", "今日无需操作"

    research = []
    for candidate in (research_candidates or [])[:8]:
        symbol = str(candidate.get("symbol") or "")
        item = by_key.get(symbol, {})
        name = candidate.get("name") or item.get("display_name") or "未命名标的"
        reassess_after = str(candidate.get("reassess_after") or "下一交易窗口复核")
        if "当前状态:" in reassess_after:
            reassess_after = "风险解除后再评估"
        research.append({
            "display_label": display_label(name, symbol),
            "action_hint": str(candidate.get("action_hint") or "仅供观察，不形成交易动作"),
            "reassess_after": reassess_after,
        })

    level = str((risk_state or {}).get("level") or "normal")
    transition = str((risk_state or {}).get("transition") or "stable")
    transition_text = {
        "escalated": "风险升级", "deescalated": "风险缓和", "stable": "状态未变",
        "unchanged": "状态未变", "candidate": "候选状态待确认",
    }.get(transition, "状态待确认")
    assistant = {
        "why": [a["reason_summary"] for a in actions] or no_action_reasons[:2],
        "conflict_summary": _conflict_summary(decision.get("unresolved_conflicts") or []),
        "do_not_do": [
            _suppressed_user_text(x, by_id, reviews_by_id)
            for x in (decision.get("suppressed_actions") or [])[:5]
        ],
        "cash": _cash_view(decision.get("cash_schedule") or {}),
        "risk": {
            "label": risk_label(level),
            "transition": transition_text,
            "suspend_accumulation": bool((risk_state or {}).get("suspend_accumulation")),
            "reasons": _risk_reasons(risk_state or {}),
            "release_condition": str((risk_state or {}).get("release_condition") or "等待风险状态满足解除条件"),
        },
        "data_notes": _data_notes(data_boundaries or {}),
        "research": research,
        "outlook": _project_outlook(structured_outlook) if structured_outlook is not None else _no_value,
        "outlook_delta": _project_outlook_delta(outlook_delta) if outlook_delta is not None else _no_value,
    }
    # Strip sentinel keys that were never set
    assistant = {k: v for k, v in assistant.items() if v is not _no_value}
    return {
        "instruction_card": {
            "status": card_status,
            "status_label": card_label,
            "actions": actions,
            "no_action_reasons": no_action_reasons[:2] if not actions else [],
            "next_checkpoint": actions[0]["next_checkpoint"] if actions else _session_checkpoint(session_id, session_intent),
        },
        "assistant_brief": assistant,
    }


def _session_checkpoint(session_id: str, session_intent: str) -> str:
    checkpoints = {
        "cn_pre_open": "A股开盘观察窗口复核",
        "cn_open_watch": "A股收盘前窗口复核",
        "cn_pre_close": "A股盘后复盘",
        "cn_after_close": "下一交易日盘前复核",
        "us_pre_open": "美股开盘观察窗口复核",
        "us_open_watch": "美股收盘前窗口复核",
        "us_pre_close": "美股盘后复盘",
        "us_after_close": "下一交易日盘前复核",
    }
    return checkpoints.get(session_id, "下一交易窗口复核")
