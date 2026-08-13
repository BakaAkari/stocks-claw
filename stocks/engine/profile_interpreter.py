"""ProfileInterpreter — 自然语言交易偏好 → 量化引擎参数。

在用户更新 investor_profile.json 后手动触发。调用 LLM（专业交易分析师
persona）将用户的自然语言偏好翻译为 QuantActionEngine 可消费的数字参数。

输出写入 .local/computed_profile.json，引擎后续 session 自动读取。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ── 默认引擎参数（与 quant_action.py _DEFAULT_QUANT_CONFIG 同步）──
DEFAULT_PARAMS: dict[str, Any] = {
    "stop_loss_pct": -12.0,
    "mid_stop_pct": -10.0,
    "mid_stop_ratio": 0.3,
    "warning_loss_pct": -8.0,
    "take_profit_levels": [[10.0, 0.25], [20.0, 0.25], [30.0, 0.50]],
    "profit_pullback_pct": -2.0,
    "profit_pullback_min_pnl": 3.0,
    "trend_ma20_break_cutoff": 0.995,
    "trend_break_ladder": [[0.995, 0.25], [0.980, 0.50], [0.950, 0.75], [0.850, 1.0]],
    "default_position_limit_pct": 5.0,
    "trend_confirmed_limit_pct": 10.0,
    "left_add_max_rsi": 65.0,
    "left_add_min_rsi": 40.0,
    # ── 个性化扩展参数 ──
    "ma20_pullback_add_ratios": [0.02],
    "chase_enabled": False,
    "trend_break_extra_deviation_pct": 0.0,
    "max_single_position_pct": 15.0,
    # ── 变现侧：高置信加仓 ──
    "high_conviction_evidence_threshold": 0.7,
    "high_conviction_add_ratio": 0.05,
    "high_conviction_limit_pct": 15.0,
}

# ── 旧键名 → 新键名映射（用于兼容迁移）──
_OLD_TO_NEW_PARAM: dict[str, str] = {
    "trend_confirm_days": "trend_break_extra_deviation_pct",
    "add_ladder": "ma20_pullback_add_ratios",
}
_OLD_KEYS: set[str] = set(_OLD_TO_NEW_PARAM)
_LOGGER = logging.getLogger(__name__)

INTERPRETER_SYSTEM_PROMPT = """你是一位资深量化交易策略师，专门将交易者的自然语言偏好翻译为量化引擎参数。

## 你的任务
阅读用户的交易偏好描述，为每个参数输出一个数值和一句解释。
不要输出你"认为"用户应该用的参数——输出**最能体现用户已陈述偏好**的参数。

## 参数说明

| 参数 | 含义 | 默认值 | 调整方向 |
|------|------|--------|---------|
| stop_loss_pct | 硬止损线（负数%） | -12.0 | 更负=更宽容 |
| mid_stop_pct | 中间减仓线 | -10.0 | 更负=延迟减仓 |
| warning_loss_pct | 警示线 | -8.0 | 更负=更晚提醒 |
| take_profit_levels | 止盈阶梯 [[阈值%,减仓%],...] | [[10,25],[20,25],[30,50]] | 更高阈值=更贪 |
| profit_pullback_pct | 单日回撤触发减仓 | -2.0 | 更负=更容忍回撤 |
| profit_pullback_min_pnl | 回撤保护最低浮盈 | 3.0 | 更高=只保护大盈利 |
| trend_ma20_break_cutoff | MA20跌破触发线 | 0.995 | 更低=趋势确认更宽松 |
| trend_break_ladder | 趋势跌破阶梯减仓 | [[0.995,0.25],...] | 调整各档 |
| left_add_max_rsi | 左侧加仓RSI上限 | 65.0 | 更高=强趋势中也可加 |
| left_add_min_rsi | 左侧加仓RSI下限 | 40.0 | 更低=只深回调时加 |
| ma20_pullback_add_ratios | 分批加仓比例（按 MA20 偏离选档）[首档,二档,...] | [0.02] | 更多档=更分散 |
| chase_enabled | 是否允许追高 | false | true=允许突破追入 |
| trend_break_extra_deviation_pct | MA20 触发需额外偏离百分点 | 0.0 | 更大=要求更深偏离才触发 |

## 偏好映射指南

- "接受浮亏"/"容忍回撤"/"分批加仓" → stop_loss 放宽到 -15~-20, ma20_pullback_add_ratios 多档
- "果断止损"/"严格风控" → stop_loss 收紧到 -5~-8
- "不追涨"/"等回调" → chase_enabled=false, left_add_min_rsi 降低
- "遇高减仓"/"落袋为安" → take_profit_levels 阈值降低、减仓比例加大
- "让利润奔跑" → take_profit_levels 阈值提高
- "趋势证伪才离场" → trend_break_extra_deviation_pct 提高, trend_break_ladder 放松

## 输出格式

严格 JSON，不要任何前缀或后缀：
{
  "params": { "stop_loss_pct": -18.0, ... },
  "reasoning": { "stop_loss_pct": "依据：用户说'接受浮亏分批加仓'，从默认-12%放宽至-18%", ... },
  "style_summary": "一句话概括（中文，40字以内）"
}

只输出用户偏好明确涉及的参数，未涉及的用默认值。
reasoning 必须引用用户原文。
"""

USER_PROMPT_TEMPLATE = """根据以下用户偏好，输出个性化引擎参数。

## 用户偏好
{preferences}

## 风险承受
{risk_tolerance}

## 投资期限
{investment_horizon}

## 约束条件
{constraints}

## 默认参数
{defaults_json}
"""


def load_profile(profile_path: Path) -> dict:
    if not profile_path.exists():
        raise FileNotFoundError(f"未找到 investor_profile: {profile_path}")
    with open(profile_path) as f:
        return json.load(f)


def build_prompt(profile: dict) -> str:
    prefs = profile.get("preferences", [])
    prefs_text = "\n".join(f"- {p}" for p in prefs) if prefs else "（未填写偏好）"
    constraints = profile.get("constraints", {})
    notes = constraints.get("notes", []) if isinstance(constraints, dict) else []
    notes_text = "\n".join(f"- {n}" for n in notes) if notes else "（无特殊约束）"
    return USER_PROMPT_TEMPLATE.format(
        preferences=prefs_text,
        risk_tolerance=profile.get("risk_tolerance", "未设置"),
        investment_horizon=profile.get("investment_horizon", "未设置"),
        constraints=notes_text,
        defaults_json=json.dumps(DEFAULT_PARAMS, ensure_ascii=False, indent=2),
    )


def validate_computed(computed: dict) -> list[str]:
    errors = []
    params = computed.get("params", {})
    old_keys = sorted(set(params) & _OLD_KEYS)
    if old_keys:
        errors.append(
            "检测到已废弃参数键，请先通过 load_computed 迁移: "
            + ", ".join(old_keys)
        )
    for key in ("stop_loss_pct", "mid_stop_pct", "warning_loss_pct"):
        v = params.get(key)
        if v is not None and (not isinstance(v, (int, float)) or v >= 0):
            errors.append(f"{key} 必须为负数，当前 {v}")
    tp = params.get("take_profit_levels")
    if tp is not None:
        if not isinstance(tp, list) or not all(
            isinstance(p, list) and len(p) == 2 and
            isinstance(p[0], (int, float)) and isinstance(p[1], (int, float))
            for p in tp
        ):
            errors.append("take_profit_levels 格式错误")
    tl = params.get("trend_break_ladder")
    if tl is not None:
        if not isinstance(tl, list) or not all(isinstance(p, list) and len(p) == 2 for p in tl):
            errors.append("trend_break_ladder 格式错误")
    al = params.get("ma20_pullback_add_ratios")
    if al is not None:
        if not isinstance(al, list) or not all(isinstance(x, (int, float)) and x >= 0 for x in al):
            errors.append("ma20_pullback_add_ratios 必须为非负浮点数列表")
    ed = params.get("trend_break_extra_deviation_pct")
    if ed is not None:
        if not isinstance(ed, (int, float)) or ed < 0:
            errors.append("trend_break_extra_deviation_pct 必须为非负浮点数")
    if not computed.get("reasoning"):
        errors.append("缺少 reasoning")
    return errors


def save_computed(computed: dict, output_path: Path) -> Path:
    output = {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "params": computed.get("params", {}),
        "reasoning": computed.get("reasoning", {}),
        "style_summary": computed.get("style_summary", ""),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=output_path.parent, prefix=f".{output_path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, output_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    return output_path


def _migrate_reasoning(reasoning: dict, params: dict) -> tuple[dict, bool]:
    migrated = dict(reasoning or {})
    changed = False
    if "trend_confirm_days" in migrated:
        migrated.pop("trend_confirm_days")
        extra = params.get("trend_break_extra_deviation_pct", 0.0)
        migrated.setdefault(
            "trend_break_extra_deviation_pct",
            f"兼容迁移：旧参数对应 MA20 触发额外偏离 {extra}%；"
            "系统不跟踪连续天数",
        )
        changed = True
    if "add_ladder" in migrated:
        old_reason = migrated.pop("add_ladder")
        migrated.setdefault("ma20_pullback_add_ratios", old_reason)
        changed = True
    return migrated, changed


def load_computed(computed_path: Path) -> Optional[dict]:
    if not computed_path.exists():
        return None
    with open(computed_path) as f:
        data: dict = json.load(f)

    params: dict = data.get("params", {})
    given_keys = set(params.keys())

    # ── 同一参数的新旧别名共存时拒绝，避免歧义 ──
    conflicts = [
        (old_key, new_key)
        for old_key, new_key in _OLD_TO_NEW_PARAM.items()
        if old_key in given_keys and new_key in given_keys
    ]
    if conflicts:
        pairs = ", ".join(f"{old}/{new}" for old, new in conflicts)
        raise ValueError(
            "computed_profile.params 同时包含同一参数的新旧键名: "
            f"{pairs}。请删除旧键后重试。"
        )

    # ── 旧键兼容迁移（无论旧文件是否错误标成 schema v2）──
    has_old = bool(given_keys & _OLD_KEYS)
    needs_write = False
    if has_old:
        migrated: dict[str, Any] = {}
        for old_key, new_key in _OLD_TO_NEW_PARAM.items():
            if old_key in params:
                if old_key == "trend_confirm_days":
                    # 旧: trend_confirm_days=1 → 新: trend_break_extra_deviation_pct=0
                    # 每多 1 天 = 0.5 百分点额外偏离
                    days = params[old_key]
                    if not isinstance(days, (int, float)) or days < 1:
                        raise ValueError("trend_confirm_days 必须为 >= 1 的数值")
                    migrated[new_key] = round((days - 1) * 0.5, 2)
                    _LOGGER.warning(
                        "computed_profile 兼容迁移: %s=%s → %s=%s  (旧字段已删除)",
                        old_key, params[old_key], new_key, migrated[new_key],
                    )
                else:
                    # add_ladder → ma20_pullback_add_ratios (同数据)
                    migrated[new_key] = params[old_key]
                    _LOGGER.warning(
                        "computed_profile 兼容迁移: %s=%s → %s=%s  (旧字段已删除)",
                        old_key, params[old_key], new_key, migrated[new_key],
                    )
        # 保留其他参数
        for k, v in params.items():
            if k not in _OLD_KEYS:
                migrated[k] = v
        data["params"] = migrated
        data["schema_version"] = 2
        needs_write = True

    migrated_reasoning, reasoning_changed = _migrate_reasoning(
        data.get("reasoning") or {}, data.get("params") or {}
    )
    if reasoning_changed:
        data["reasoning"] = migrated_reasoning
        data["schema_version"] = 2
        needs_write = True
        _LOGGER.warning(
            "computed_profile reasoning 使用旧参数语义，已迁移为事实描述"
        )

    if needs_write:
        # ── 原子写回迁移后的文件 ──
        fd, tmp_path = tempfile.mkstemp(
            dir=computed_path.parent,
            prefix=".computed_profile_migrate_",
            suffix=".json",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tf:
                json.dump(data, tf, ensure_ascii=False, indent=2)
            os.replace(tmp_path, computed_path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
        _LOGGER.info(
            "computed_profile 已自动迁移至 schema_version=2 并写回 %s",
            computed_path,
        )

    return data


def merge_with_defaults(computed: Optional[dict]) -> dict:
    merged = dict(DEFAULT_PARAMS)
    if computed and "params" in computed:
        for k, v in computed["params"].items():
            if k in merged:
                merged[k] = v
    return merged
