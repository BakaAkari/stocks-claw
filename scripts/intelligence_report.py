#!/usr/bin/env python3
"""全球情报小时巡逻 - 直接格式化输出，不经过 LLM。

读取 global_intelligence_watch 产物的 context_digest，
将事件聚类和操作信号直接以中文结构化展示。
不做翻译、不编造、不调用 LLM。
"""
import json
from pathlib import Path

LATEST = Path("/mnt/user/code-project/stocks-claw/.local/scheduled_runs/latest/global_intelligence_watch.json")

if not LATEST.exists():
    print("No latest artifact found")
    raise SystemExit(1)

data = json.loads(LATEST.read_text())
status = data.get("status", "unknown")
run_id = data.get("run_id", "")
scheduled_for = data.get("scheduled_for", "")
digest = data.get("context_digest", {})

clusters = digest.get("clusters", []) or []
signals_list = digest.get("signals", []) or []
macro = digest.get("macro", {}) or {}
quotes = digest.get("quotes", {}) or {}
article_count = len(clusters) if clusters else 0

# Title
ts = (scheduled_for or "")[:19]
print(f"**全球情报 - {ts}**")
print()

# Status summary
if status == "ok":
    cluster_themes = {c.get("theme", "?") for c in clusters}
    crit = [c for c in clusters if c.get("urgency") == "critical"]
    active_themes = ", ".join(sorted(cluster_themes)[:4]) if cluster_themes else "无"
    if crit:
        print(f"关键警报: {len(crit)} 个事件标为 critical - {', '.join(c.get('theme','') for c in crit)}")
    else:
        print(f"本小时 {len(clusters)} 个事件主题: {active_themes}")
print()

# Macro snapshot
if macro:
    print("**宏观**")
    items = []
    for key, label in [("vix", "VIX"), ("us_10y_yield", "美债10Y"), ("dxy", "DXY"),
                        ("usd_cny", "USD/CNY"), ("crude_oil", "原油"), ("gold", "黄金")]:
        val = macro.get(key)
        if val is not None:
            if isinstance(val, float):
                items.append(f"{label} {val:.2f}")
            else:
                items.append(f"{label} {val}")
    print(" | ".join(items))
    print()

# Quotes snapshot
if quotes:
    print("**行情**")
    q_items = []
    for sym, q in quotes.items():
        if isinstance(q, dict):
            pct = q.get("pct_change")
            if pct is not None:
                q_items.append(f"{sym} {pct:+.2f}%")
            elif q.get("price") is not None:
                q_items.append(f"{sym} {q['price']:.2f}")
    if q_items:
        print(" | ".join(q_items[:12]))
    print()

# Event clusters
if clusters:
    print("**事件**")
    for c in clusters[:6]:
        theme = c.get("theme", "?")
        urgency = c.get("urgency", "medium")
        summary = c.get("summary", "")[:200]
        urgency_mark = "[CRITICAL] " if urgency == "critical" else ""
        print(f"- {urgency_mark}[{theme}] {summary}")
    print()

# Signals
if signals_list:
    print("**信号**")
    for s in signals_list[:6]:
        direction = s.get("direction", "?")
        symbol = s.get("symbol", "?")
        rationale = s.get("rationale", "")[:120]
        if direction == "buy":
            dir_label = "买入"
        elif direction == "sell":
            dir_label = "卖出"
        else:
            dir_label = direction
        print(f"- {dir_label} `{symbol}` - {rationale}")
    print()

# Data boundary
print(f"采集: {ts} | {len(clusters)} 簇 | {len(signals_list)} 信号")
print()
print(f"`{run_id[:24]}...`")
