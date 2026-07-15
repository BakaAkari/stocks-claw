"""Data Quality Gate — 数据异常守门 (Task 2)

纯函数模块：检测价格序列中的异常模式。
所有阈值定义为模块常量，并允许 engine config 覆盖。

检测器（按优先级）：
  single_bar_jump         — 相邻 bar 绝对变化 > threshold pct
  price_ma20_dislocation  — 现价 MA20 偏离 > threshold AND 近 20 bar 有 >25% 跳变
  prev_close_mismatch     — prev_close 与上一根 close 差异 > threshold
  mixed_adjustment_regime — 序列中有 >35% 跳变且比例接近 1:2/2:1 拆并区间
  source_regime_change    — 异常点附近数据源变化（仅证据，不阻断）
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

# ── 默认阈值 ──
DEFAULT_THRESHOLDS: dict[str, float] = {
    # single bar jump: 相邻 bar 绝对百分比变化超过此值即阻断
    "single_bar_jump_pct": 35.0,
    # MA20 偏离: 现价与 MA20 的偏离超过此值触发 dislocation 检测
    "ma20_dislocation_pct": 30.0,
    # dislocation 次条件: 近 20 bar 内存在跳变超过此值
    "dislocation_jump_window_pct": 25.0,
    # prev_close 与上一根 close 差异阈值
    "prev_close_mismatch_pct": 5.0,
    # 近 n 根 bar 检查跳变 (用于 dislocation 和 regime)
    "price_jump_window": 20,
    # regime 全范围 bar 数
    "regime_lookback": 60,
    # mixed adjustment ratio 判断区间 [low, high]
    "mixed_adjustment_ratio_min": 0.45,
    "mixed_adjustment_ratio_max": 0.55,
}

# ── 异常代码 ──
SINGLE_BAR_JUMP = "single_bar_jump"
PRICE_MA20_DISLOCATION = "price_ma20_dislocation"
PREV_CLOSE_MISMATCH = "prev_close_mismatch"
MIXED_ADJUSTMENT_REGIME = "mixed_adjustment_regime"
SOURCE_REGIME_CHANGE = "source_regime_change"
INSUFFICIENT_DATA = "insufficient_data"

# ── Severity levels ──
_SEVERITY_CRITICAL = "critical"
_SEVERITY_HIGH = "high"
_SEVERITY_WARNING = "warning"
_SEVERITY_INFO = "info"


def _validate_frame(frame: pd.DataFrame) -> Optional[dict]:
    """Validate frame has required columns and sufficient data.
    Returns an anomaly dict if insufficient, None otherwise."""
    if frame.empty or len(frame) < 2:
        return None
    required = {"close"}
    missing = required - set(frame.columns)
    if missing:
        return {
            "code": INSUFFICIENT_DATA,
            "severity": _SEVERITY_WARNING,
            "description": f"缺少必要列: {missing}",
            "bar_index": None,
            "evidence": {"missing_columns": list(missing)},
        }
    close_values = pd.to_numeric(frame["close"], errors="coerce")
    invalid_mask = close_values.isna() | ~close_values.map(lambda value: float("-inf") < value < float("inf"))
    if invalid_mask.any():
        invalid_indices = [int(index) for index in frame.index[invalid_mask]]
        return {
            "code": INSUFFICIENT_DATA,
            "severity": _SEVERITY_HIGH,
            "description": f"价格序列含非有限数值，位置: {invalid_indices}",
            "bar_index": invalid_indices[0] if invalid_indices else None,
            "evidence": {"invalid_indices": invalid_indices},
        }
    return None


def _calc_pct_change(prev: float, curr: float) -> float:
    """计算百分比变化 (绝对值)。"""
    if prev == 0:
        return 0.0
    return abs((curr - prev) / prev) * 100


def _detect_single_bar_jump(
    frame: pd.DataFrame, thresholds: dict
) -> list[dict]:
    """检测相邻 bar 绝对变化 > threshold (%)。"""
    anomalies: list[dict] = []
    jump_pct = thresholds["single_bar_jump_pct"]

    closes = frame["close"].values
    for i in range(1, len(closes)):
        change = _calc_pct_change(closes[i - 1], closes[i])
        if change > jump_pct:
            prev_val = closes[i - 1]
            cur_val = closes[i]
            anomalies.append({
                "code": SINGLE_BAR_JUMP,
                "severity": _SEVERITY_CRITICAL,
                "description": (
                    f"Bar {i}: 价格从 {prev_val:.4f} 跳变至 {cur_val:.4f}，"
                    f"变化 {change:.1f}%（阈值 >{jump_pct:.0f}%）"
                ),
                "bar_index": i,
                "evidence": {
                    "prev_close": float(prev_val),
                    "current_close": float(cur_val),
                    "change_pct": round(change, 2),
                    "threshold_pct": jump_pct,
                },
            })
    return anomalies


def _detect_price_ma20_dislocation(
    frame: pd.DataFrame,
    current_price: Optional[float],
    ma20: Optional[float],
    thresholds: dict,
) -> list[dict]:
    """检测现价与 MA20 偏离 + 近 20 bar 跳变。"""
    if current_price is None or ma20 is None or ma20 == 0:
        return []

    dislocation_pct = thresholds["ma20_dislocation_pct"]
    jump_window = int(thresholds["price_jump_window"])
    jump_pct = thresholds["dislocation_jump_window_pct"]

    # 偏离比
    deviation = abs((current_price - ma20) / ma20) * 100
    if deviation <= dislocation_pct:
        return []

    # 检查近 jump_window bar 是否存在 > jump_pct 的跳变
    closes = frame["close"].values
    recent = closes[-min(jump_window, len(closes)):]
    has_recent_jump = any(
        _calc_pct_change(recent[j], recent[j + 1]) > jump_pct
        for j in range(len(recent) - 1)
    )
    if not has_recent_jump:
        return []

    return [{
        "code": PRICE_MA20_DISLOCATION,
        "severity": _SEVERITY_HIGH,
        "description": (
            f"现价 {current_price:.4f} 偏离 MA20({ma20:.4f}) "
            f"{deviation:.1f}%（阈值 >{dislocation_pct:.0f}%），"
            f"且近 {jump_window} bar 存在异常跳变"
        ),
        "bar_index": len(frame) - 1,
        "evidence": {
            "current_price": current_price,
            "ma20": ma20,
            "deviation_pct": round(deviation, 2),
            "threshold_pct": dislocation_pct,
            "recent_jump_check": int(jump_window),
        },
    }]


def _detect_prev_close_mismatch(
    frame: pd.DataFrame, thresholds: dict
) -> list[dict]:
    """检测 prev_close 与上一根 close 的差异 > threshold。"""
    anomalies: list[dict] = []
    mismatch_pct = thresholds["prev_close_mismatch_pct"]

    if "prev_close" not in frame.columns:
        return []

    prev_closes = frame["prev_close"].values
    closes = frame["close"].values

    for i in range(1, len(frame)):
        actual_prev = closes[i - 1]
        stated_prev = prev_closes[i]
        if actual_prev == 0:
            continue
        diff = abs(stated_prev - actual_prev) / actual_prev * 100
        if diff > mismatch_pct:
            anomalies.append({
                "code": PREV_CLOSE_MISMATCH,
                "severity": _SEVERITY_WARNING,
                "description": (
                    f"Bar {i}: prev_close({stated_prev:.4f}) 与上一根 close({actual_prev:.4f}) "
                    f"差异 {diff:.2f}%（阈值 >{mismatch_pct:.0f}%）"
                ),
                "bar_index": i,
                "evidence": {
                    "stated_prev_close": float(stated_prev),
                    "actual_prev_close": float(actual_prev),
                    "diff_pct": round(diff, 2),
                    "threshold_pct": mismatch_pct,
                },
            })
    return anomalies


def _detect_mixed_adjustment_regime(
    frame: pd.DataFrame, thresholds: dict
) -> list[dict]:
    """检测混合调整区间 — 大跳变且比例接近 1:2/2:1 拆并区间。"""
    jump_pct = thresholds["single_bar_jump_pct"]
    lookback = int(thresholds["regime_lookback"])
    ratio_min = thresholds["mixed_adjustment_ratio_min"]
    ratio_max = thresholds["mixed_adjustment_ratio_max"]

    closes = frame["close"].values
    if len(closes) < 3:
        return []
    start_index = max(0, len(closes) - lookback)
    closes = closes[start_index:]

    # 找所有 > jump_pct 的跳变
    jumps: list[tuple[int, float, float]] = []
    for i in range(1, len(closes)):
        prev_val = closes[i - 1]
        cur_val = closes[i]
        change = _calc_pct_change(prev_val, cur_val)
        if change > jump_pct and prev_val > 0 and cur_val > 0:
            ratio = prev_val / cur_val  # 2.70/1.33 ≈ 2.03 (接近 2:1)
            jumps.append((start_index + i, prev_val, cur_val, ratio))

    # 检查比例是否接近 1:2 (~0.5) 或 2:1 (~2.0)
    for bar_idx, prev_val, cur_val, ratio in jumps:
        is_near_0_5 = ratio_min <= ratio <= ratio_max
        is_near_2_0 = 1.0 / ratio_max <= ratio <= 1.0 / ratio_min

        if is_near_0_5 or is_near_2_0:
            regime_type = "1:2（拆细）" if is_near_0_5 else "2:1（合并）"
            return [{
                "code": MIXED_ADJUSTMENT_REGIME,
                "severity": _SEVERITY_HIGH,
                "description": (
                    f"Bar {bar_idx}: 价格跳变 {prev_val:.4f}→{cur_val:.4f} "
                    f"比例为 {prev_val/cur_val:.4f}，接近 {regime_type} 拆并区间"
                ),
                "bar_index": bar_idx,
                "evidence": {
                    "prev_price": float(prev_val),
                    "current_price": float(cur_val),
                    "ratio": round(prev_val / cur_val, 4),
                    "suggested_regime": regime_type,
                    "lookback_bars": lookback,
                },
            }]

    return []


def _detect_source_regime_change(
    frame: pd.DataFrame, anomalies: list[dict]
) -> list[dict]:
    """在已检测到的异常点附近检查数据源变化。"""
    if "data_source" not in frame.columns:
        return []

    # 只在已有 anomaly 的 bar_index 附近检查
    anomaly_indices = {a["bar_index"] for a in anomalies if a.get("bar_index") is not None}

    results: list[dict] = []
    for i in range(1, len(frame)):
        prev_source = frame.iloc[i - 1].get("data_source", "")
        curr_source = frame.iloc[i].get("data_source", "")
        if prev_source and curr_source and prev_source != curr_source:
            # Only flag if near an anomaly or if it's a big change
            if i in anomaly_indices or _calc_pct_change(
                frame.iloc[i - 1]["close"], frame.iloc[i]["close"]
            ) > 15.0:
                results.append({
                    "code": SOURCE_REGIME_CHANGE,
                    "severity": _SEVERITY_INFO,
                    "description": (
                        f"Bar {i}: 数据源从 {prev_source} 切换为 {curr_source}"
                    ),
                    "bar_index": i if i in anomaly_indices else None,
                    "evidence": {
                        "from_source": str(prev_source),
                        "to_source": str(curr_source),
                    },
                })

    return results


def detect_price_anomalies(
    frame: pd.DataFrame,
    *,
    current_price: Optional[float] = None,
    ma20: Optional[float] = None,
    thresholds: Optional[dict] = None,
) -> list[dict]:
    """检测价格序列中的数据异常。

    纯函数，无副作用。返回 anomaly dict 列表：
      { code, severity, description, bar_index, evidence }

    Parameters
    ----------
    frame : pd.DataFrame
        至少包含 'close' 列的价格序列。应包含 datetime 索引或 datetime 列，
        用于定位异常发生的时间。
    current_price : float or None
        当前最新价格，用于 MA20 dislocation 检测。
    ma20 : float or None
        当前 MA20 值，用于 dislocation 检测。
    thresholds : dict or None
        覆盖默认阈值。缺省使用 DEFAULT_THRESHOLDS。

    Returns
    -------
    list[dict]
        按严重程度降序排列的异常列表。
    """
    effective = {**DEFAULT_THRESHOLDS, **(thresholds or {})}

    # HistoryCache 的原生列名是 price；测试/部分调用方使用 close。
    # 在函数内部归一化，避免调用方复制转换逻辑。
    normalized = frame
    if "close" not in normalized.columns and "price" in normalized.columns:
        normalized = normalized.rename(columns={"price": "close"})

    # 验证
    validation = _validate_frame(normalized)
    if validation:
        return [validation]
    scalar_values = {"current_price": current_price, "ma20": ma20}
    invalid_scalars = [
        name for name, value in scalar_values.items()
        if value is not None and (pd.isna(value) or not float("-inf") < float(value) < float("inf"))
    ]
    if invalid_scalars:
        return [{
            "code": INSUFFICIENT_DATA,
            "severity": _SEVERITY_HIGH,
            "description": f"检测输入含非有限数值: {invalid_scalars}",
            "bar_index": None,
            "evidence": {"invalid_fields": invalid_scalars},
        }]
    if len(normalized) < 2:
        return []

    results: list[dict] = []

    # 1. single_bar_jump (最严重)
    results.extend(_detect_single_bar_jump(normalized, effective))

    # 2. mixed_adjustment_regime
    results.extend(_detect_mixed_adjustment_regime(normalized, effective))

    # 3. price_ma20_dislocation
    results.extend(_detect_price_ma20_dislocation(normalized, current_price, ma20, effective))

    # 4. prev_close_mismatch
    results.extend(_detect_prev_close_mismatch(normalized, effective))

    # 5. source_regime_change (有 anomaly 时才产生 evidence)
    results.extend(_detect_source_regime_change(normalized, results))

    # 按 severity 排序：critical → high → warning → info
    severity_rank = {_SEVERITY_CRITICAL: 0, _SEVERITY_HIGH: 1, _SEVERITY_WARNING: 2, _SEVERITY_INFO: 3}
    results.sort(key=lambda a: severity_rank.get(a.get("severity", _SEVERITY_INFO), 99))

    return results


def compute_action_eligible(anomalies: list[dict]) -> tuple[bool, list[str]]:
    """根据 anomaly 列表判断是否可执行技术动作。

    Returns
    -------
    (eligible, blocked_reasons)
        eligible: True 表示可以执行技术动作
        blocked_reasons: 若组织则给出原因列表
    """
    blocked_reasons: list[str] = []
    for a in anomalies:
        sev = a.get("severity", "")
        if sev in (_SEVERITY_CRITICAL, _SEVERITY_HIGH):
            blocked_reasons.append(
                f"{a['code']}: {a['description']}"
            )
    if blocked_reasons:
        return False, blocked_reasons
    return True, []
