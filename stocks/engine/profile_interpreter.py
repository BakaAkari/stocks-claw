"""ProfileInterpreter — 自然语言交易偏好 → 量化引擎参数。

在用户更新 investor_profile.json 后手动触发。调用 LLM（专业交易分析师
persona）将用户的自然语言偏好翻译为 QuantActionEngine 可消费的数字参数。

输出写入 .local/computed_profile.json，引擎后续 session 自动读取。
"""

from __future__ import annotations

import json
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
    "add_ladder": [0.02],
    "chase_enabled": False,
    "trend_confirm_days": 1,
    "max_single_position_pct": 15.0,
}

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
| add_ladder | 分批加仓比例 [首档,二档,...] | [0.02] | 更多档=更分散 |
| chase_enabled | 是否允许追高 | false | true=允许突破追入 |
| trend_confirm_days | 趋势证伪需连续确认天数 | 1 | 更大=更谨慎 |

## 偏好映射指南

- "接受浮亏"/"容忍回撤"/"分批加仓" → stop_loss 放宽到 -15~-20, add_ladder 多档
- "果断止损"/"严格风控" → stop_loss 收紧到 -5~-8
- "不追涨"/"等回调" → chase_enabled=false, left_add_min_rsi 降低
- "遇高减仓"/"落袋为安" → take_profit_levels 阈值降低、减仓比例加大
- "让利润奔跑" → take_profit_levels 阈值提高
- "趋势证伪才离场" → trend_confirm_days 提高, trend_break_ladder 放松

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
    al = params.get("add_ladder")
    if al is not None:
        if not isinstance(al, list) or not all(isinstance(x, (int, float)) and x >= 0 for x in al):
            errors.append("add_ladder 必须为非负浮点数列表")
    if not computed.get("reasoning"):
        errors.append("缺少 reasoning")
    return errors


def save_computed(computed: dict, output_path: Path) -> Path:
    output = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "params": computed.get("params", {}),
        "reasoning": computed.get("reasoning", {}),
        "style_summary": computed.get("style_summary", ""),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    return output_path


def load_computed(computed_path: Path) -> Optional[dict]:
    if not computed_path.exists():
        return None
    with open(computed_path) as f:
        return json.load(f)


def merge_with_defaults(computed: Optional[dict]) -> dict:
    merged = dict(DEFAULT_PARAMS)
    if computed and "params" in computed:
        for k, v in computed["params"].items():
            if k in merged:
                merged[k] = v
    return merged
