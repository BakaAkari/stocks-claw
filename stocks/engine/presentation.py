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

# P1-13: risk transition is a *relative* direction from the previous window.
# Single source of truth for transition phrasing, shared by presentation and
# the push-payload renderer so "降风险（较上次升级）" never drifts into a
# self-contradictory "降风险（风险升级）" or a mismatched "升级" in another
# renderer.
TRANSITION_LABELS = {
    "escalated": "较上次升级", "deescalated": "较上次缓和", "stable": "与上次持平",
    "unchanged": "与上次持平", "candidate": "候选状态待确认",
    "expired": "已过期", "initial": "初始化", "reconfirmed": "再确认",
}

# P2-4: 风险档位英文枚举 → 中文(与 risk_label 的 label 值对齐,用于
# window_delta 迁移文本)。
_RISK_LEVEL_LABELS = {
    "normal": "常态", "watch": "观察", "reduce": "降风险", "hedge": "对冲/高风险",
}


def _window_level_change_text(window_delta: dict | None) -> str:
    """P2-4: 本窗口风险档位迁移文本(窗口级基准),用于与观察级
    transition_key 消歧。window_delta.changes 里若有 risk_state.level
    变化,返回 \"(本窗口: 降风险 → 对冲/高风险)\";否则返回空串。

    与 user_view.risk.transition(观察级\"与上次持平\")并存时,渲染层
    可据此向用户说明两个基准,避免\"持平\"与\"迁移\"并排的误读。
    """
    if not window_delta:
        return ""
    changes = (window_delta.get("changes") or [])
    for ch in changes:
        if str(ch.get("field")) == "risk_state.level":
            old = _RISK_LEVEL_LABELS.get(str(ch.get("old") or ""), str(ch.get("old") or "?"))
            new = _RISK_LEVEL_LABELS.get(str(ch.get("new") or ""), str(ch.get("new") or "?"))
            return f"（本窗口: {old} → {new}）"
    return ""

# P1-15: per-market quote freshness values that render as "stale". Single
# source of truth shared by the research-candidate gate (scheduled_analysis)
# and the executable-action gate (presentation).
STALE_FRESHNESS = frozenset({"stale", "old", "missing", "no_data", "unknown", ""})

# ── Phase 2: 平台/操作通道显示 ──
_PLATFORM_NAME = {
    "brokerage": "证券账户",
    "fund_platform": "支付宝",
    "bank": "银行理财",
    "insurance": "保险账户",
}

_OPERATION_HINT = {
    ("fund_platform", "alipay"): "打开支付宝 → 理财 → 按名称/代码搜索",
    ("bank", "ccb"): "打开建行 APP → 理财/基金 → 查看开放期",
    ("insurance", "boc_life"): "联系香港中银人寿顾问或登录中银人寿 APP",
    ("brokerage", "a_stock"): "通过东方财富/华泰等中信建投交易软件",
    ("brokerage", "ibkr"): "登录 Interactive Brokers (IBKR) 账户",
}

_INSTITUTION_FROM_ACCOUNT = {
    "a_stock": "brokerage",
    "ibkr": "brokerage",
    "alipay": "fund_platform",
    "ccb": "bank",
    "boc_life": "insurance",
}
_STATUS_LABELS = {
    "approved": "已有获批动作", "suppressed": "今日无需操作",
    "review_required": "等待人工确认",
}
_RISK_LABELS = {
    "hedge": "对冲/高风险", "reduce": "降风险", "watch": "观察",
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


def _operation_hint_for(card: dict, default: str = "") -> str:
    """Return a human-readable operation channel hint for an action card."""
    it = (card or {}).get("institution_type", "")
    aid = (card or {}).get("account_id", "")
    if not it and aid:
        it = _INSTITUTION_FROM_ACCOUNT.get(aid, "")
    routing = (card or {}).get("routing", "")
    if routing in ("info_only", "skip"):
        if it == "insurance":
            return _OPERATION_HINT.get(("insurance", "boc_life"), "联系对应机构顾问")
        return ""
    hint = _OPERATION_HINT.get((it, aid))
    if hint:
        return hint
    if it == "brokerage":
        return "登录证券账户执行"
    if it == "fund_platform":
        return "打开支付宝 → 理财 → 按名称/代码搜索"
    if it == "bank":
        return "打开建行 APP → 理财/基金"
    return default


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
    """Project the adjudicator's canonical cash-schedule fields verbatim.

    Sources every amount from CashSchedule.to_dict()'s canonical fields
    (available_now/confirmed_settling/planned_release/strategic_exit/locked)
    rather than the pre-canonical duplicate fields, with no recomputation.
    The user_view keys match the canonical field names exactly.

    unresolved_settlement is surfaced here as an amount field so downstream
    render layers can quote the figure (M1: 组合与检查点 §6 renders the
    ¥N,NNN gap when data_notes flags it). It still is not a spendable bucket
    — the six-bucket cash-part rendering explicitly skips it.
    """
    values = schedule or {}
    return {
        "available_now": {"label": "现在能用", "amount_cny": round(values.get("available_now", 0.0) or 0.0, 2)},
        "confirmed_settling": {"label": "到账途中", "amount_cny": round(values.get("confirmed_settling", 0.0) or 0.0, 2)},
        "planned_release": {"label": "计划内到期释放", "amount_cny": round(values.get("planned_release", 0.0) or 0.0, 2)},
        "strategic_exit": {"label": "卖出后才能用", "amount_cny": round(values.get("strategic_exit", 0.0) or 0.0, 2)},
        "locked": {"label": "不能动", "amount_cny": round(values.get("locked", 0.0) or 0.0, 2)},
        # The safety buffer is carved out of available_now by the adjudicator
        # (5% of portfolio). It must be visible, or "现在能用" looks silently
        # discounted and the buckets don't add up (adversarial review P1-8).
        "safety_buffer": {"label": "预留安全垫（不计入可用）", "amount_cny": round(values.get("safety_buffer_cny", 0.0) or 0.0, 2)},
        # Not spendable — surfaced only so the render layer can report the
        # awaiting-clearing-rule gap (M1: 资金缺口 line in §6).
        "unresolved_settlement": {"label": "结算方式待确认", "amount_cny": round(values.get("unresolved_settlement", 0.0) or 0.0, 2)},
        # R5-4: 资产合计(各桶加总)。渲染层用它在资金行后展示"资产合计
        # ¥N + 占比",交易分析师可一眼判断现在可用/锁定/卖出可释放的比例。
        # 与各桶同源(均来自 schedule),保证加总可审计。
        "total_assets_cny": round(
            float(values.get("available_now", 0.0) or 0.0)
            + float(values.get("confirmed_settling", 0.0) or 0.0)
            + float(values.get("planned_release", 0.0) or 0.0)
            + float(values.get("strategic_exit", 0.0) or 0.0)
            + float(values.get("locked", 0.0) or 0.0)
            + float(values.get("safety_buffer_cny", 0.0) or 0.0)
            + float(values.get("unresolved_settlement", 0.0) or 0.0),
            2,
        ),
    }


def _conflict_reason(conflict: dict, by_id: dict[str, dict]) -> str:
    item = by_id.get(str(conflict.get("position_id") or ""), {})
    label = _display_for_position(item)
    bucket = str(conflict.get("bucket") or "组合")
    ratio = conflict.get("bucket_ratio")
    ratio_text = f"{float(ratio) * 100:.1f}%" if isinstance(ratio, (int, float)) else "待确认"
    action = signal_label(conflict.get("signal", ""))
    bucket_min = conflict.get("bucket_min")
    bucket_min_text = f"{float(bucket_min) * 100:.0f}%" if isinstance(bucket_min, (int, float)) else "目标下限"
    # P1-11: bucket_ratio is the *asset-class* weight, never the instrument's
    # own weight. The old phrasing "{label}:{bucket}当前占组合{ratio}%" read
    # as if the instrument held that much of the portfolio (e.g. "NVDA 当前占
    # 组合 12.7%" when NVDA was 0.5% and 12.7% was the equity bucket).
    # Keep the leading "label（code）：" form so _no_action_conflict_details
    # in build_push_payload can still split label/code, but make the body
    # name the instrument and the bucket separately so the two quantities
    # cannot be conflated.
    return (
        f"{label}：触发{action}信号，但{bucket}大类当前占组合{ratio_text}"
        f"（低于下限{bucket_min_text}），方向冲突，需人工确认"
    )


def _conflict_tilt(conflict: dict, item: dict) -> tuple[str, str]:
    """C1-WP1: 确定性冲突倾向(不依赖 LLM)。

    规则(与裁决器语义对齐,硬止损在裁决器已批准,冲突仅为复核提示):
    - stop_loss            → action:  硬止损不受约束限制,倾向执行
    - reduce/take_profit 且 bucket 低于下限 → constraint: 低配区再降加深偏离
    - 加仓类且 bucket 高于上限 → constraint: 高配区不宜再加
    - 其余                 → manual:  需人工裁定
    """
    signal = str(conflict.get("signal") or "")
    bucket_ratio = conflict.get("bucket_ratio")
    bucket_min = conflict.get("bucket_min")
    bucket_max = conflict.get("bucket_max")
    ratio = float(bucket_ratio) if isinstance(bucket_ratio, (int, float)) else None
    min_ = float(bucket_min) if isinstance(bucket_min, (int, float)) else None
    max_ = float(bucket_max) if isinstance(bucket_max, (int, float)) else None

    if signal == "stop_loss":
        return "action", "硬止损不受组合约束限制，倾向按止损执行（冲突仅为复核提示）"

    if signal in {"reduce", "take_profit"} and ratio is not None and min_ is not None:
        if ratio < min_:
            # "标醒目":distinguish 获利兑现(take_profit) from 风控离场(reduce).
            # Both are held from executing by the under-weight constraint, but
            # they have different intent — Kari must see whether he is being
            # stopped from banking a profit vs cutting a broken trend, so the
            # constraint reads as a trade-off the user weighs, not a hidden rule.
            motive = "获利兑现被组合低配约束暂缓——可权衡是否仍坚持落袋" if signal == "take_profit" else "风控减仓被组合低配约束暂缓——可权衡是否仍执行止损纪律"
            return (
                "constraint",
                f"权益（或该大类）当前占比 {ratio*100:.1f}% 已低于下限 {min_*100:.0f}%，"
                f"再减仓会加深低配偏离，倾向维持现状、等回补后再说（{motive}）",
            )

    if signal in {"buy", "accumulate", "add"} and ratio is not None and max_ is not None:
        if ratio > max_:
            return (
                "constraint",
                f"该大类当前占比 {ratio*100:.1f}% 已高于上限 {max_*100:.0f}%，"
                "不宜再加仓，倾向维持现状",
            )

    return "manual", "方向性冲突，需人工根据最新资金与行情裁定"


def _tomorrow_plan(
    decision: dict,
    conflict_details: list[dict],
    by_id: dict[str, dict],
    *,
    by_market: dict,
    data_notes: list[str],
    risk_state: dict,
    structured_outlook: dict | None,
) -> list[dict]:
    """C1-WP3: 确定性明日计划(非 LLM 创作)。

    输入全部来自已确认的决策结构,每一条可追溯到来源:
    - approved_actions      → 已获批动作,优先级高
    - conflict_details tilt → constraint(维持)与 manual(需裁定)
    - data_notes            → 待决资金等提示项
    - risk_state            → 风险档位(升/降级提示)
    - structured_outlook    → 研判(降级则标注低可信)

    P5-1: 与 instruction_card 的 gate 对齐 —— 只有通过 _is_executable
    (行情新鲜 + 可执行数量)的动作才列为 high 执行;被 gate 的降级为
    medium 复核,绝不与指令卡的"暂缓执行"矛盾。

    不承诺收益、不下单,只给人工确认清单。无操作时输出观察项。
    """
    plan: list[dict] = []

    # 1. 已获批动作(可执行/待复核)
    for action in (decision.get("approved_actions") or [])[:4]:
        if not isinstance(action, dict):
            continue
        pid = str(action.get("position_id") or "")
        item = by_id.get(pid, {})
        label = _display_for_position(item) or pid
        desc = str(action.get("action_description") or "")[:80]
        ratio = action.get("final_ratio") if action.get("final_ratio") is not None else action.get("ratio")
        amount_hint = ""
        if isinstance(ratio, (int, float)) and ratio > 0:
            amount_hint = f"（按 {ratio*100:.0f}% 比例）"
        # P5-1: 与指令卡同 gate。可执行 → high;被 gate(行情过时等) → medium 复核
        if _is_executable(action, item, by_market):
            plan.append({
                "action": f"{label}：{desc}{amount_hint}",
                # 用户面不暴露内部 position_id,用公开 code/名称
                "position": public_instrument_code(item.get("instrument_key", ""), "") or label,
                "priority": "high",
                "source": "approved_action",
            })
        else:
            reason = _deferred_action_text(action, item, by_market)
            # P5-7: _deferred_action_text 已含 label("XXX：暂缓执行..."),
            # 不再重复拼接 label,否则出现"XXX：XXX：暂缓执行"。
            plan.append({
                "action": reason,
                "position": public_instrument_code(item.get("instrument_key", ""), "") or label,
                "priority": "medium",
                "source": "approved_action_review",
            })

    # 2. 冲突倾向(constraint → 维持观察;manual → 人工裁定)
    for detail in (conflict_details or [])[:3]:
        tilt = str(detail.get("tilt") or "manual")
        label = str(detail.get("label") or detail.get("code") or "")
        reason = str(detail.get("tilt_reason") or "")[:80]
        if tilt == "constraint":
            plan.append({
                "action": f"{label}：维持现状（{reason}）",
                "position": str(detail.get("code") or ""),
                "priority": "medium",
                "source": "conflict_tilt",
            })
        elif tilt == "manual":
            plan.append({
                "action": f"{label}：冲突需人工裁定（{reason}）",
                "position": str(detail.get("code") or ""),
                "priority": "medium",
                "source": "conflict_tilt",
            })

    # 3. 数据/资金提示(待决资金、行情过时)
    for note in (data_notes or [])[:2]:
        plan.append({
            "action": f"核对：{note}",
            "position": "",
            "priority": "low",
            "source": "data_note",
        })

    # 4. 风险状态变化提示
    level = str((risk_state or {}).get("level") or "normal")
    transition = str((risk_state or {}).get("transition") or "stable")
    if level != "normal" or transition in {"escalated", "deescalated"}:
        plan.append({
            "action": f"风险档位 {level}（{transition}），按当前档位纪律执行，不临时加仓/加杠杆",
            "position": "",
            "priority": "medium",
            "source": "risk_state",
        })

    # 5. 研判低可信提示(结构化 outlook 降级后)
    if structured_outlook:
        nt = (structured_outlook.get("near_term") or {})
        if str(nt.get("confidence") or "") == "low":
            plan.append({
                "action": "研判基于滞后数据(置信度已降级)，明日以人工盯盘与数据恢复后的复核为准",
                "position": "",
                "priority": "medium",
                "source": "outlook_confidence",
            })

    if not plan:
        plan.append({
            "action": "观察：明日无新增动作，维持当前仓位",
            "position": "",
            "priority": "low",
            "source": "no_action",
        })
    return plan


def _conflict_detail(conflict: dict, by_id: dict[str, dict]) -> dict[str, Any]:
    """Structured conflict for render layers and future programmatic handling."""
    item = by_id.get(str(conflict.get("position_id") or ""), {})
    label = _display_for_position(item)
    code = public_instrument_code(item.get("instrument_key", ""), "")
    bucket = str(conflict.get("bucket") or "组合")
    ratio = conflict.get("bucket_ratio")
    bucket_min = conflict.get("bucket_min")
    action = signal_label(conflict.get("signal", ""))
    tilt, tilt_reason = _conflict_tilt(conflict, item)
    return {
        "label": label,
        "code": code,
        "type": "方向冲突",
        "bucket": bucket,
        "bucket_ratio": ratio,
        "bucket_min": bucket_min,
        "action": action,
        "reason": _conflict_reason(conflict, by_id),
        "branch": "默认：维持现状，等待人工确认方向",
        # C1-WP1: 确定性倾向(非 LLM)——供交易员/下游直接消费
        "tilt": tilt,
        "tilt_reason": tilt_reason,
    }


def _position_review_map(position_reviews: list[dict]) -> dict[str, dict]:
    return {
        str(item.get("position_id") or ""): item
        for item in (position_reviews or [])
        if item.get("position_id")
    }


_RESEARCH_SIGNAL_LABELS = {
    "left_bottom_candidate": "左侧超跌",
    "accumulate_candidate": "趋势布局",
    "rotation_candidate": "轮动候选",
    "wait_for_pullback": "等回踩",
    "reduce_risk": "趋势转弱",
    "avoid_catching_falling_knife": "下跌未止",
}


def _research_signal_label(signal: str) -> str:
    """Human-safe research signal label; never expose internal enum tokens."""
    return _RESEARCH_SIGNAL_LABELS.get(str(signal or ""), "观察")


def _suppressed_user_text(raw: dict, by_id: dict[str, dict], reviews_by_id: dict[str, dict]) -> str:
    pid = str(raw.get("position_id") or "")
    item = by_id.get(pid, {})
    label = _display_for_position(item)
    platform = str(raw.get("platform_display") or _PLATFORM_NAME.get(raw.get("institution_type", ""), ""))
    op_hint = _operation_hint_for(raw)
    platform_hint = f"（在 {platform} 操作：{op_hint}）" if platform and op_hint else ""
    anomalies = ((reviews_by_id.get(pid, {}).get("evidence") or {}).get("data_anomalies") or [])
    if anomalies:
        display = anomaly_display(anomalies[0])
        evidence = display.get("evidence_summary")
        detail = f"；{evidence}" if evidence else ""
        return f"{label}：{display['display_message']}{detail}；{display['user_impact']}{platform_hint}"
    return f"{label}：{_safe_reason_text(raw.get('reason', ''))}{platform_hint}"


def _conflict_summary(conflicts: list[dict]) -> list[dict]:
    counts = Counter(signal_label(item.get("signal", "")) for item in (conflicts or []))
    order = {"止损": 0, "减仓": 1, "止盈": 2, "加仓": 3, "持有": 4, "待确认动作": 9}
    return [
        {"action_label": label, "count": count}
        for label, count in sorted(counts.items(), key=lambda pair: (order.get(pair[0], 8), pair[0]))
    ]


# P5-3: risk_warning 的 trigger condition 是英文内部枚举,直接透传用户面
# 会产生"Critical cluster: 1 critical"这类机器语言。这里做确定性翻译,
# 保留数值部分(VIX=xx、-x.x%),只翻译英文触发词。
_RISK_TRIGGER_TRANSLATIONS = {
    "Critical cluster": "关键集群事件",
    "Broad negative": "广泛负面事件",
    "Geopolitical crisis": "地缘政治危机",
    "Severe drawdown": "严重回撤",
    "Drawdown warning": "回撤预警",
    "critical": "个",  # "1 critical" 是"N个critical集群", 不是"关键级别"
    "geopolitics critical": "地缘局势关键",
    "clusters": "个集群",
    "elevated": "偏高",
}


def _risk_trigger_text(condition: str, value: str) -> str:
    """Translate an English risk-trigger condition/value pair to Chinese.

    Condition is usually a short enum like ``Critical cluster``; value is
    usually a numeric fragment (``1 critical``, ``VIX=36.2``, ``-12.3%``)
    or empty.  Translates known enum tokens, preserves numbers verbatim,
    falls back to the raw text when a token is unknown (never fabricates).

    Longest token first: ``geopolitics critical`` must match before the
    shorter ``critical`` substring inside it (P5-3 regression).
    """
    cond = str(condition or "").strip()
    val = str(value or "").strip()
    ordered = sorted(
        _RISK_TRIGGER_TRANSLATIONS.items(),
        key=lambda kv: len(kv[0]),
        reverse=True,
    )
    for en, zh in ordered:
        if cond == en:
            cond = zh
            break
    # value 里的英文枚举 token 也翻译(长 token 优先)
    for en, zh in ordered:
        if en in val:
            val = val.replace(en, zh)
    return f"{cond}：{val}" if val else cond




def _release_condition_text(risk_state: dict) -> str:
    """把"等待风险状态满足解除条件"的泛文本补成具体、可预期的解除说明。

    基于 risk_state 已有的 expires_at(状态重估时间) 与 triggers(触发集群),
    明确告知用户:当前是被什么触发、系统何时会重估、解除的前提是什么。
    绝不编造系统没有的条件——只用 expires_at 与 trigger 名这些真实字段。
    """
    explicit = str((risk_state or {}).get("release_condition") or "").strip()
    if explicit:
        return explicit
    parts: list[str] = []
    # 直接翻译 risk_state.triggers(结构化、可靠),得到"关键集群事件：1 关键级别"
    # 这类中文,与"组合与检查点"风险段同源。避免透传英文枚举(Critical cluster),
    # 也不用 _risk_reasons 在 evidence 缺失时的模糊回退。
    triggers = (risk_state or {}).get("triggers") or []
    trig_zh = [
        _risk_trigger_text(str(t.get("name") or ""), str(t.get("value") or ""))
        for t in triggers
        if isinstance(t, dict) and t.get("name")
    ]
    if trig_zh:
        parts.append("当前由" + "、".join(dict.fromkeys(trig_zh)) + "触发")
    exp = (risk_state or {}).get("expires_at") or ""
    if exp:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
            local = dt.astimezone()
            parts.append(f"预计 {local.strftime('%m-%d %H:%M')} 重估风险状态")
        except (ValueError, TypeError):
            parts.append(f"预计 {exp} 重估风险状态")
    if parts:
        return "；".join(parts) + "；解除需上述风险集群降级后再评估加仓"
    return "等待风险状态满足解除条件"


def _risk_reasons(risk_state: dict) -> list[str]:
    """Build human-readable risk reasons from actual risk triggers.

    ``risk_warning.assess_risk`` already renders each trigger's ``condition``/
    ``value`` as plain, non-machine-ID text (e.g. "VIX > 35" / "VIX=36.2"), so
    this reads that evidence directly instead of matching a static, easily
    incomplete evidence-key vocabulary (TASK-001E1 defect 6). ``hedge``/
    ``reduce`` must never render with an empty reasons list; when the risk
    state carries no derivable trigger evidence, that state is itself
    invalid, so this fails closed to an explicit review message rather than
    silently returning nothing.
    """
    reasons: list[str] = []
    for trigger in (risk_state or {}).get("triggers") or []:
        if not isinstance(trigger, dict):
            continue
        condition = str(trigger.get("condition") or "").strip()
        if not condition:
            continue
        value = str(trigger.get("value") or "").strip()
        # P5-3: 英文触发枚举翻译为用户可读中文
        text = _risk_trigger_text(condition, value)
        if text not in reasons:
            reasons.append(text)
    level = str((risk_state or {}).get("level") or "")
    if level in ("hedge", "reduce") and not reasons:
        return ["风险等级判定缺少可读证据，已转人工复核"]
    return reasons


def _display_timestamp(value: str) -> str:
    raw = str(value or "")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw or "时间待确认"
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


_EXECUTABLE_STATUS = frozenset({"full", "adjusted_to_step"})


def _instrument_market(instrument_key: str) -> str:
    value = str(instrument_key or "")
    if ":" not in value:
        return ""
    market, _, _ = value.partition(":")
    return market.strip().lower()


def _market_is_primary(market: str, primary_market: str) -> bool:
    """判断 instrument 市场前缀是否属于 session 主市场。"""
    if primary_market == "us":
        return market == "us"
    if primary_market == "cn":
        return market == "a"
    return False


def _sort_approved_by_primary_market(actions: list[dict], by_id: dict, primary_market: str) -> list[dict]:
    """主市场动作优先（stable sort，同市场保持裁决器原序）。"""
    if not primary_market or not actions:
        return list(actions)

    def _key(action: dict) -> int:
        item = by_id.get(str(action.get("position_id") or ""), {})
        key = str(item.get("instrument_key") or "")
        return 0 if _market_is_primary(_instrument_market(key), primary_market) else 1

    return sorted(actions, key=_key)


def _quote_by_market(data_boundaries: dict) -> dict:
    quality = (data_boundaries or {}).get("data_quality") or {}
    return ((quality.get("quotes") or {}).get("by_market") or {})


def _market_quote_stale(market: str, by_market: dict) -> bool:
    """Fail closed: an unknown market, a missing quote entry, or any
    non-fresh freshness value all count as stale (TASK-001E1 defect 3)."""
    if not market:
        return True
    item = by_market.get(market)
    if not isinstance(item, dict):
        return True
    return str(item.get("freshness") or "") in STALE_FRESHNESS


def _is_executable(raw: dict, item: dict, by_market: dict) -> bool:
    """Executable iff execution_rules approved it AND its market's quotes are
    fresh. Folds scope item 2 (execution_status/final_ratio/executable_
    quantity) and scope item 3 (per-market freshness fail-closed, including
    cross-market positions) into a single gate: only actions passing both
    may enter instruction_card.actions or drive card_status=action_required.
    """
    if raw.get("execution_status") not in _EXECUTABLE_STATUS:
        return False
    ratio = raw.get("final_ratio")
    if not isinstance(ratio, (int, float)) or isinstance(ratio, bool) or ratio <= 0:
        return False
    qty = raw.get("executable_quantity")
    if qty is not None and not (isinstance(qty, (int, float)) and not isinstance(qty, bool) and qty > 0):
        return False
    market = _instrument_market(item.get("instrument_key", ""))
    return not _market_quote_stale(market, by_market)


def _action_sentence(raw: dict, label: str) -> str:
    """Generate the action sentence entirely from finalized fields.

    Never reuses ``action_description``/``reason`` text: those originate
    from the raw pre-adjudication signal card and can embed a percentage
    that execution_rules has since revised (TASK-001E1 defect 1). The
    displayed percentage always equals ``final_ratio``.
    """
    signal = signal_label(raw.get("signal", ""))
    ratio = raw.get("final_ratio")
    pct = f"{float(ratio) * 100:.0f}%" if isinstance(ratio, (int, float)) and not isinstance(ratio, bool) else "比例待确认"
    suffix = "（已按最小交易单位调整）" if raw.get("execution_status") == "adjusted_to_step" else ""
    return f"{label}：{signal} {pct}{suffix}"


def _deferred_action_text(raw: dict, item: dict, by_market: dict) -> str:
    """Concise no-action/manual-review text for a finalized action that
    failed the executable gate (TASK-001E1 defect 2/3) -- it must never be
    rendered as an executable card."""
    label = _display_for_position(item)
    # Replacement-chain buy legs are deliberately review_required (their
    # portfolio-basis quantity is resolved only after the sale settles), not
    # "constraints unmet" — say so explicitly (adversarial review P1-4).
    if raw.get("alternative_position_id"):
        return f"{label}：换仓买入腿——等待卖出资金到账后执行，维持权益敞口"
    market = _instrument_market(item.get("instrument_key", ""))
    if _market_quote_stale(market, by_market):
        return f"{label}：目标市场行情数据过时或缺失，暂缓执行，等待数据恢复"
    status = raw.get("execution_status")
    if status == "deferred_min_unit":
        return f"{label}：获批比例低于最小交易单位，暂不构成可执行动作"
    if status in ("review_required", "locked"):
        # Preserve the adjudicator's concrete reason; do not fold every review
        # into the generic "constraints unmet" placeholder (M1: the reason may
        # carry bucket_min / direction conflict / settlement specific details).
        reason_text = raw.get("decision_reason") or raw.get("reason") or ""
        if reason_text:
            return f"{label}：{reason_text}"
        return f"{label}：当前不满足可执行条件，等待人工复核"
    return f"{label}：当前不满足可执行条件，等待人工复核"


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
    # P1-5 fix: 宏观 composite as_of 取 field_sources 中最旧字段(通常是月频 CPI/失业率/
    # 利率), 而 freshness 按"官方统计发布周期内即最新一期"判为 fresh——两者并存会让用户
    # 困惑("as_of 7-01 却标 fresh")。诚实澄清: 区分月频官方字段与日频市场类宏观字段。
    macro_fs = macro.get("field_sources") or {}
    _monthly_macro = {"official_stats.cpi_yoy", "official_stats.us_unemployment",
                      "official_stats.fed_funds_rate"}
    _monthly_dates = [
        str(md.get("as_of") or "")[:10]
        for k, md in macro_fs.items() if k in _monthly_macro and isinstance(md, dict)
    ]
    if _monthly_dates and str(macro.get("freshness") or "") == "fresh":
        notes.append(
            f"宏观官方统计字段（CPI/失业率/利率）数据点至 {_monthly_dates[0]}，"
            "属月度发布周期；市场类宏观（VIX/汇率/利率/金油）为最新行情"
        )
    # P2-3/P3-4: asset_completeness 的 blocked/degraded issue 必须呈现,
    # 不能只存在 data_quality 节点里(8/6 实测: HKD 汇率失败 blocked,
    # 支付宝基金 7/31 估值混入资金数字,data_notes 均无提示)。
    completeness = quality.get("asset_completeness") or {}
    seen: set[str] = set()
    for issue in (completeness.get("issues") or []):
        msg = str(issue.get("message") or "").strip()
        if not msg or msg in seen:
            continue
        seen.add(msg)
        capability = str(issue.get("capability") or "")
        severity = str(issue.get("severity") or "")
        # blocked(汇率失败等)必须呈现;degraded 只呈现估值时效类
        # (valuation_age/valuation),避免噪音淹没关键提示。
        if severity == "blocked" or capability in {"valuation_age", "valuation"}:
            notes.append(msg)
    return notes


def _unresolved_settlement_note(schedule: dict) -> str | None:
    """Surface unresolved sale-settlement proceeds as a review/data boundary.

    Unresolved settlement is never available_now or confirmed_settling cash
    (fail-closed by design); it also does not get a sixth cash bucket. It is
    instead surfaced as a plain data note with the aggregate CNY amount only
    (no position IDs).
    """
    amount = round(float((schedule or {}).get("unresolved_settlement") or 0.0), 2)
    if amount <= 0:
        return None
    return f"¥{amount:,.0f} 的卖出资金结算方式待确认，未计入“现在能用”或“到账途中”"


_OUTLOOK_ALLOWED_TOP = frozenset({
    "status", "generated_at", "message", "data_limitations",
    "summary", "confidence", "near_term", "medium_term",
    "asset_views", "sector_views", "scenarios", "source_refs",
})
_OUTLOOK_HORIZON_ALLOWED = frozenset({
    "horizon", "direction", "confidence", "rationale", "validation", "falsification",
})
_OUTLOOK_VIEW_ALLOWED = frozenset({"asset_class", "asset", "sector", "direction", "rationale", "affected_positions"})
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
                        if vk == "affected_positions":
                            lst = _str_list(vv, max_items=6)
                            if lst:
                                projected[vk] = lst
                        else:
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
    primary_market: str = "",
    structured_outlook: dict | None = None,
    outlook_delta: dict | None = None,
    window_delta: dict | None = None,
) -> dict:
    """Build the deterministic trade-card and assistant presentation contract."""
    decision = portfolio_decision or {}
    by_id, by_key = _position_maps(position_valuations)
    reviews_by_id = _position_review_map(position_reviews)
    by_market = _quote_by_market(data_boundaries or {})
    all_approved = decision.get("approved_actions") or []
    # P-market-focus: 按主市场优先排序 approved_actions（stable），
    # 避免跨市场动作（如 A股止盈）占用本 session 主市场动作的展示名额。
    if primary_market:
        all_approved = _sort_approved_by_primary_market(all_approved, by_id, primary_market)
        decision = {**decision, "approved_actions": all_approved}
    approved_cards = all_approved[:3]

    # TASK-001E1 defect 4: the research-dedup identity set covers *every*
    # finalized action -- executable, deferred, and the ones beyond the
    # display cap -- not just the three that become cards. An instrument the
    # engine has already adjudicated is not a research idea, regardless of
    # whether the card had room to show it.
    approved_keys: set[str] = set()
    for raw in all_approved:
        item = by_id.get(str(raw.get("position_id") or ""), {})
        key = str(item.get("instrument_key") or "")
        if key:
            approved_keys.add(key)

    actions = []
    deferred_reasons = []
    suppressed_reference: list[dict] = []
    gate_rejected_sell = False
    for raw in approved_cards:
        item = by_id.get(str(raw.get("position_id") or ""), {})
        # TASK-001E1 defect 2/3: only executable actions (execution_status
        # full/adjusted_to_step, final_ratio>0, executable_quantity>0, and a
        # fresh quote for the instrument's own market) may become a card
        # action. Everything else is a concise deferred/review reason, never
        # a silently dropped or contradictorily-labelled action_required card.
        if not _is_executable(raw, item, by_market):
            text = _deferred_action_text(raw, item, by_market)
            if text not in deferred_reasons:
                deferred_reasons.append(text)
            # M1: an approved sell stopped at the gate is still a pending
            # sell — its proceeds belong in 卖出后可释放 (strategic_exit).
            if str(raw.get("signal") or "") in {"stop_loss", "take_profit", "reduce"}:
                gate_rejected_sell = True
            # M1 truth-gate audit trail: keep the rule-driven pre-gate
            # proposal (ratio / quantity / amount) as structured reference
            # data so the report can show it next to the manual-review
            # conflict the user has to resolve.
            # P1-12: a gate rejection caused by stale/missing quotes must not
            # carry a precise estimated amount -- that number was computed
            # from the same stale valuation that made the action non-
            # executable. Render layers then know not to quote it as a
            # reference amount.
            ratio = raw.get("final_ratio")
            if isinstance(ratio, (int, float)) and not isinstance(ratio, bool) and ratio > 0:
                market = _instrument_market(item.get("instrument_key", ""))
                quote_stale = _market_quote_stale(market, by_market)
                suppressed_reference.append({
                    "display_label": _display_for_position(item),
                    "signal_type": signal_label(raw.get("signal", "")),
                    "ratio": raw.get("final_ratio"),
                    "executable_quantity": raw.get("executable_quantity"),
                    "estimated_amount_cny": None if quote_stale else raw.get("estimated_amount_cny"),
                    "amount_blocked_reason": "行情数据过时，金额待数据恢复后确认" if quote_stale else None,
                })
            continue
        label = _display_for_position(item)
        ratio = float(raw.get("final_ratio") or 0.0)
        platform = str(raw.get("platform_display") or _PLATFORM_NAME.get(raw.get("institution_type", ""), ""))
        op_hint = _operation_hint_for(raw)
        # TASK-001D: amount, estimate-flag, ratio provenance, and execution
        # facts are computed once by the adjudicator (_finalize_approved_action)
        # and projected here verbatim -- no valuation x ratio recomputation,
        # no freshness re-derivation, no fallback to a display-time default.
        actions.append({
            "display_label": label,
            "action_label": signal_label(raw.get("signal", "")),
            "ratio": ratio,
            "final_ratio": raw.get("final_ratio"),
            "original_ratio": raw.get("original_ratio"),
            "estimated_amount_cny": raw.get("estimated_amount_cny"),
            "amount_is_estimate": raw.get("amount_is_estimate"),
            "reason_summary": _action_sentence(raw, label),
            "decision_reason": raw.get("decision_reason"),
            "evidence_summary": raw.get("evidence_summary"),
            "cancel_condition": str(raw.get("cancel_condition") or "触发条件不再成立时取消"),
            "settlement_display": str(raw.get("settlement_timing") or "到账时间待确认"),
            "settlement_rule": raw.get("settlement_rule"),
            "executable_quantity": raw.get("executable_quantity"),
            "execution_status": raw.get("execution_status"),
            "next_checkpoint": str(raw.get("next_checkpoint") or "下一交易窗口复核"),
            "platform": platform,
            "operation_channel": op_hint,
        })

    # P0-3 fix: approved_cards=all_approved[:3] 把超名额但可执行的获批卖出动作
    # 直接排除在展示之外，导致场外基金止盈(无实时行情被gate拒)+被截断的可执行动作
    # 对用户完全不可见。遍历 all_approved，凡未成为卡片的获批卖出(reduce/止盈/止损)
    # 都纳入 suppressed reference，让渲染层能呈现"还有N项获批动作"。
    shown_pids = {str(raw.get("position_id") or "") for raw in approved_cards}
    for raw in all_approved:
        pid = str(raw.get("position_id") or "")
        if pid in shown_pids:
            continue
        if str(raw.get("signal") or "") not in {"reduce", "take_profit", "stop_loss"}:
            continue
        item = by_id.get(pid, {})
        ratio = raw.get("final_ratio")
        if not (isinstance(ratio, (int, float)) and not isinstance(ratio, bool) and ratio > 0):
            continue
        label = _display_for_position(item)
        already = {r.get("display_label") for r in suppressed_reference}
        if label in already:
            continue
        market = _instrument_market(item.get("instrument_key", ""))
        quote_stale = _market_quote_stale(market, by_market)
        suppressed_reference.append({
            "display_label": label,
            "signal_type": signal_label(raw.get("signal", "")),
            "ratio": raw.get("final_ratio"),
            "executable_quantity": raw.get("executable_quantity"),
            "estimated_amount_cny": None if quote_stale else raw.get("estimated_amount_cny"),
            "amount_blocked_reason": "行情数据过时，金额待数据恢复后确认" if quote_stale else None,
        })

    no_action_reasons = list(deferred_reasons)
    approved_pids = {str(raw.get("position_id") or "") for raw in approved_cards}
    for conflict in decision.get("unresolved_conflicts") or []:
        if len(no_action_reasons) >= 2:
            break
        pid = str(conflict.get("position_id") or "")
        if pid in approved_pids:
            continue
        reason = _conflict_reason(conflict, by_id)
        if reason not in no_action_reasons:
            no_action_reasons.append(reason)
    for raw in decision.get("suppressed_actions") or []:
        if len(no_action_reasons) >= 2:
            break
        pid = str(raw.get("position_id") or "")
        if pid in approved_pids:
            continue
        reason = _suppressed_user_text(raw, by_id, reviews_by_id)
        if reason not in no_action_reasons:
            no_action_reasons.append(reason)
    if not no_action_reasons and not actions:
        no_action_reasons.append("当前没有满足执行条件的获批动作")
    no_action_reasons = no_action_reasons[:2]

    # TASK-001E2 display cap is 3 action cards, but approved actions beyond
    # the cap must never vanish silently (adversarial review P0-3): count the
    # executable ones that did not fit and surface the count on the card so
    # the user knows the report is a subset.
    executable_total = 0
    for raw in all_approved:
        item = by_id.get(str(raw.get("position_id") or ""), {})
        if _is_executable(raw, item, by_market):
            executable_total += 1
    actions_overflow = max(0, executable_total - len(actions))

    raw_status = str(decision.get("status") or "")
    if actions:
        card_status, card_label = "action_required", "需要操作"
    elif deferred_reasons or (raw_status == "review_required" and decision.get("unresolved_conflicts")):
        card_status, card_label = "manual_review", "等待人工确认"
    else:
        card_status, card_label = "no_action", "今日无需操作"

    # TASK-001E1 defect 4: an instrument that is already a finalized approved
    # or deferred action must never also appear as a research candidate.
    # Dedup by the authoritative instrument_key before capping at 8, so a
    # duplicate never displaces a genuinely distinct candidate.
    research = []
    seen_research_keys: set[str] = set()
    for candidate in research_candidates or []:
        symbol = str(candidate.get("symbol") or "")
        if symbol and symbol in approved_keys:
            continue
        if symbol and symbol in seen_research_keys:
            continue
        if symbol:
            seen_research_keys.add(symbol)
        item = by_key.get(symbol, {})
        name = candidate.get("name") or item.get("display_name") or "未命名标的"
        reassess_after = str(candidate.get("reassess_after") or "下一交易窗口复核")
        if "当前状态:" in reassess_after:
            reassess_after = "风险解除后再评估"
        # Round the score once, here, to the exact precision the report
        # renders (2 decimals). The push number gate authorizes only values
        # present in the payload — rendering a 4-decimal raw score as 0.39
        # would trip the gate with an unauthorized rounded value.
        raw_score = candidate.get("score")
        if not isinstance(raw_score, (int, float)) or isinstance(raw_score, bool):
            raw_score = candidate.get("composite_score")
        score = (
            round(float(raw_score), 2)
            if isinstance(raw_score, (int, float)) and not isinstance(raw_score, bool)
            else None
        )
        # P1-15: freshness-gated research candidates carry quote_stale (set
        # by _build_research_candidates when the market's quotes are
        # stale/missing). Render them as pure observation: no setup tag, no
        # reasons that quote precise prices, and the sizing hint already
        # replaced by the data-boundary note upstream. Also honor the
        # risk_suspend_accumulation condition by downgrading any remaining
        # setup tag to observation so "暂停加仓" never coexists with a
        # "趋势布局" claim.
        # 激进方案(2026-08-13): 唯一豁免 left_bottom_candidate —— 危机时
        # 左侧超跌保留"左侧超跌"标签(不降级为观察),sizing_hint 已降为
        # "危机试仓 1%"。数据过时(quote_stale)仍硬降级(无数据不试仓)。
        condition = str(candidate.get("condition") or "")
        is_left_bottom = str(candidate.get("signal") or "") == "left_bottom_candidate"
        if candidate.get("quote_stale") or (
            condition == "risk_suspend_accumulation" and not is_left_bottom
        ):
            setup_tag = "观察"
        else:
            setup_tag = _research_signal_label(str(candidate.get("signal") or ""))
        research.append({
            "display_label": display_label(name, symbol),
            "action_hint": str(candidate.get("action_hint") or "仅供观察，不形成交易动作"),
            "reassess_after": reassess_after,
            "category": str(candidate.get("category") or ""),
            "pool": str(candidate.get("pool") or ""),
            "setup_tag": setup_tag,
            "reasons": [str(r) for r in (candidate.get("reasons") or []) if r][:2],
            "score": score,
            "sizing_hint": str(candidate.get("sizing_hint") or ""),
            # P0(左侧): 透传左侧位置指标(布林位置/RSI/量比),渲染层据此呈现位置卡。
            "price_position": candidate.get("price_position"),
            "rsi_14": candidate.get("rsi_14"),
            "volume_ratio": candidate.get("volume_ratio"),
            # P1(左侧): 技术位供分批档位表(MA20/布林下轨/MA60)。
            "ma_20": candidate.get("ma_20"),
            "ma_60": candidate.get("ma_60"),
            "bollinger_lower": candidate.get("bollinger_lower"),
            # P1(左侧): 现价,用于过滤"价格下方的支撑位"(价格上方是阻力,不是左侧接的档位)。
            "price": candidate.get("price"),
        })
        if len(research) >= 8:
            break

    # Structured conflicts for programmatic render layers.
    conflict_details = [
        _conflict_detail(c, by_id)
        for c in (decision.get("unresolved_conflicts") or [])
    ]

    level = str((risk_state or {}).get("level") or "normal")
    transition = str((risk_state or {}).get("transition") or "stable")
    # P1-13: level label ("降风险" for reduce) and transition text must not
    # read as contradictory. "降风险（风险升级）" sounds like the two halves
    # argue; the transition is a *relative* direction from the previous
    # window, so phrase it as such ("较上次升级") instead of an absolute
    # state ("风险升级"). Shared with the push-payload renderer via
    # TRANSITION_LABELS so both surfaces stay in sync.
    transition_text = TRANSITION_LABELS.get(transition, "状态待确认")
    cash_schedule = decision.get("cash_schedule") or {}
    cash_view = _cash_view(cash_schedule)
    # M1: an approved-but-review-pending sell keeps strategic_exit visible in
    # the report (labeled 卖出后可释放) even when no executable sell exists.
    # Pending sells come from two places: suppressed_actions, and approved
    # actions that were stopped at the executable gate (gate_rejected_sell).
    if gate_rejected_sell or any(
        str(x.get("signal") or "") in {"stop_loss", "take_profit", "reduce"}
        for x in (decision.get("suppressed_actions") or [])
        if isinstance(x, dict)
    ):
        cash_view["pending_sell"] = True
    data_notes = _data_notes(data_boundaries or {})
    unresolved_note = _unresolved_settlement_note(cash_schedule)
    if unresolved_note:
        data_notes = [unresolved_note, *data_notes]
    why_texts = [a["reason_summary"] for a in actions]
    for text in no_action_reasons:
        if text not in why_texts:
            why_texts.append(text)
    assistant = {
        "why": why_texts or ["当前没有满足执行条件的获批动作"],
        "conflict_summary": _conflict_summary(decision.get("unresolved_conflicts") or []),
        "conflict_details": conflict_details,
        "do_not_do": [
            _suppressed_user_text(x, by_id, reviews_by_id)
            for x in (decision.get("suppressed_actions") or [])[:5]
        ],
        "cash": cash_view,
        "risk": {
            "label": risk_label(level),
            "transition": transition_text,
            # P1-16: keep the raw enum alongside the rendered text so the
            # push-payload renderer can branch on state without comparing
            # Chinese strings (which would silently break on wording edits).
            "transition_key": transition,
            # P2-4: 风险状态表述基准消歧。risk_state.transition 是观察级
            # (相对上次观察),window_delta 是本窗口 session 级对比。当本
            # 窗口确实发生了 level 迁移(window_delta 有 risk_state.level
            # 变化)而 transition_key=unchanged 时,文案"与上次持平"与
            # 窗口变化卡"降风险→对冲/高风险"并排会让用户误读——这里
            # 显式补一个窗口基准注记,渲染层据其消歧。
            "window_level_change": _window_level_change_text(window_delta),
            "suspend_accumulation": bool((risk_state or {}).get("suspend_accumulation")),
            "reasons": _risk_reasons(risk_state or {}),
            "release_condition": _release_condition_text(risk_state or {}),
        },
        # C1-WP3: 确定性明日计划(非 LLM 创作,输入可追溯)
        # P5-1: by_market 传入与指令卡同 gate,杜绝"明日计划让执行/指令卡让等待"
        "tomorrow_plan": _tomorrow_plan(
            decision,
            conflict_details,
            by_id,
            by_market=by_market,
            data_notes=data_notes,
            risk_state=risk_state,
            structured_outlook=structured_outlook,
        ),
        "data_notes": data_notes,
        "research": research,
        "outlook": _project_outlook(structured_outlook) if structured_outlook is not None else _no_value,
        "outlook_delta": _project_outlook_delta(outlook_delta) if outlook_delta is not None else _no_value,
    }
    # Strip sentinel keys that were never set
    assistant = {k: v for k, v in assistant.items() if v is not _no_value}
    card = {
        "status": card_status,
        "status_label": card_label,
        "actions": actions,
        "actions_overflow": actions_overflow,
        "no_action_reasons": no_action_reasons[:2] if not actions else [],
        "next_checkpoint": actions[0]["next_checkpoint"] if actions else _session_checkpoint(session_id, session_intent),
    }
    if suppressed_reference:
        card["suppressed_actions_reference"] = suppressed_reference[:3]
    return {
        "instruction_card": card,
        "assistant_brief": assistant,
    }


def _session_checkpoint(session_id: str, session_intent: str) -> str:
    # Keys are the five sessions that actually exist in
    # stocks/config/scheduled_sessions.json (P1-10: the previous map used
    # pre-consolidation session ids that no longer exist, so every real run
    # fell through to the generic default).
    checkpoints = {
        "cn_post_open": "A股收盘后复盘窗口复核",
        "cn_after_close": "下一交易日 A股开盘后复核",
        "us_post_open": "美股收盘后复盘窗口复核",
        "us_after_close": "下一交易日 美股开盘后复核",
        "global_intelligence_watch": "下一情报巡逻窗口",
    }
    return checkpoints.get(session_id, "下一交易窗口复核")
