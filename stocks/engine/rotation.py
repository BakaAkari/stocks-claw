"""板块轮动脚手架 — 基于历史收盘序列的相对强弱排名。

输出的是"过去 5/20 根 K 线谁强谁弱"这一价格事实排名，不是买卖建议；
方向判断由 Agent 结合催化剂日历与组合结构完成。

数据来源是 HistoryCache 的日 K 序列（含当日已记录行情），因此结果的
as_of 是各标的最近一根 K 线时间，不是实时值；历史不足的标的显式进入
missing 列表，绝不伪造。
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from stocks.domain.models import Instrument

SHORT_BARS = 5
LONG_BARS = 20

ROTATION_SCHEMA_VERSION = 1


def _clean_frame(frame: pd.DataFrame) -> Optional[pd.DataFrame]:
    if frame is None or frame.empty or "price" not in frame.columns:
        return None
    cleaned = frame.copy()
    cleaned["timestamp"] = pd.to_datetime(
        cleaned["timestamp"], format="ISO8601", utc=True, errors="coerce"
    )
    cleaned["price"] = pd.to_numeric(cleaned["price"], errors="coerce")
    cleaned = cleaned.dropna(subset=["timestamp", "price"]).sort_values("timestamp")
    if cleaned.empty:
        return None
    return cleaned


def _window_return(prices: pd.Series, bars: int) -> Optional[float]:
    """最近 bars 根 K 线的累计涨跌幅（百分数）；历史不足返回 None。"""
    if len(prices) < bars + 1:
        return None
    start = float(prices.iloc[-(bars + 1)])
    latest = float(prices.iloc[-1])
    if start <= 0:
        return None
    return round((latest / start - 1.0) * 100, 4)


def compute_rotation(
    frames: dict[str, pd.DataFrame],
    instruments: dict[str, Instrument],
    scan_keys: Optional[set[str]] = None,
) -> dict:
    """计算轮动排名。

    Args:
        frames: "market:code" -> 历史 DataFrame（HistoryCache.get_history 输出）
        instruments: "market:code" -> Instrument（提供 name/category）
        scan_keys: 属于板块扫描池（非用户 watchlist）的 key 集合

    Returns:
        结构化轮动字典；无可用数据时 status = "no_data"。
    """
    scan_keys = scan_keys or set()
    items: list[dict] = []
    missing: list[str] = []
    oldest_as_of: Optional[pd.Timestamp] = None

    for key, instrument in instruments.items():
        frame = _clean_frame(frames.get(key))
        if frame is None:
            missing.append(key)
            continue
        prices = frame["price"]
        r5 = _window_return(prices, SHORT_BARS)
        r20 = _window_return(prices, LONG_BARS)
        if r5 is None and r20 is None:
            missing.append(key)
            continue
        ma20 = (
            float(prices.tail(LONG_BARS).mean()) if len(prices) >= LONG_BARS else None
        )
        latest_price = float(prices.iloc[-1])
        as_of = frame["timestamp"].iloc[-1]
        if oldest_as_of is None or as_of < oldest_as_of:
            oldest_as_of = as_of
        items.append(
            {
                "symbol": key,
                "name": instrument.name or instrument.code,
                "category": instrument.category or "unknown",
                "pool": getattr(instrument, "pool", None)
                or ("scan" if key in scan_keys else "core"),
                "universe": "scan" if key in scan_keys else "watchlist",
                "r5": r5,
                "r20": r20,
                "above_ma20": (latest_price > ma20) if ma20 is not None else None,
                "bars": int(len(prices)),
                "as_of": as_of.isoformat(),
            }
        )

    if not items:
        return {
            "schema_version": ROTATION_SCHEMA_VERSION,
            "status": "no_data",
            "as_of": None,
            "window": {"short_bars": SHORT_BARS, "long_bars": LONG_BARS},
            "items": [],
            "category_momentum": {},
            "leaders": [],
            "laggards": [],
            "missing": sorted(missing),
        }

    def _rank_key(item: dict) -> float:
        # 优先按 r20 排序；r20 缺失的标的按 r5 排,并排在有 r20 的后面
        if item["r20"] is not None:
            return item["r20"]
        return -1e9 + (item["r5"] or 0.0)

    items.sort(key=_rank_key, reverse=True)
    for rank, item in enumerate(items, start=1):
        item["rank"] = rank

    ranked = [item for item in items if item["r20"] is not None]
    leaders = [item["symbol"] for item in ranked[:3]]
    laggards = [item["symbol"] for item in ranked[-3:][::-1]] if ranked else []
    if len(ranked) <= 3:
        laggards = []

    category_momentum: dict[str, dict] = {}
    for item in items:
        bucket = category_momentum.setdefault(
            item["category"], {"r5_values": [], "r20_values": [], "count": 0}
        )
        bucket["count"] += 1
        if item["r5"] is not None:
            bucket["r5_values"].append(item["r5"])
        if item["r20"] is not None:
            bucket["r20_values"].append(item["r20"])
    for category, bucket in category_momentum.items():
        r5_values = bucket.pop("r5_values")
        r20_values = bucket.pop("r20_values")
        bucket["r5"] = round(sum(r5_values) / len(r5_values), 4) if r5_values else None
        bucket["r20"] = (
            round(sum(r20_values) / len(r20_values), 4) if r20_values else None
        )

    status = "ok" if not missing else "partial"
    return {
        "schema_version": ROTATION_SCHEMA_VERSION,
        "status": status,
        "as_of": oldest_as_of.isoformat() if oldest_as_of is not None else None,
        "window": {"short_bars": SHORT_BARS, "long_bars": LONG_BARS},
        "items": items,
        "category_momentum": category_momentum,
        "leaders": leaders,
        "laggards": laggards,
        "missing": sorted(missing),
    }
