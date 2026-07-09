#!/usr/bin/env python3
"""全球情报小时巡逻 - 数据事实 + LLM 总结。

前半部分：直接格式化 context_digest 的结构化数据。
后半部分：调用 LLM 对已展示的数据做综合分析和前瞻判断。
LLM 只能引用前半部分已列出的数据，不得编造。
"""
import json
import os
import urllib.request
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

# ═══════════════════════════════════════════
# Part 1: Structured data (no LLM)
# ═══════════════════════════════════════════

ts = (scheduled_for or "")[:19]
print(f"**全球情报 - {ts}**")
print()

# Status
if status == "ok":
    cluster_themes = {c.get("theme", "?") for c in clusters}
    crit = [c for c in clusters if c.get("urgency") == "critical"]
    active_themes = ", ".join(sorted(cluster_themes)[:4]) if cluster_themes else "无"
    if crit:
        print(f"关键警报: {len(crit)} 个事件标为 critical - {', '.join(c.get('theme','') for c in crit)}")
    else:
        print(f"本小时 {len(clusters)} 个事件主题: {active_themes}")
print()

# Macro
if macro:
    print("**宏观**")
    items = []
    for key, label in [("vix", "VIX"), ("us_10y_yield", "美债10Y"), ("dxy", "DXY"),
                        ("usd_cny", "USD/CNY"), ("crude_oil", "原油"), ("gold", "黄金")]:
        val = macro.get(key)
        if val is not None:
            items.append(f"{label} {val:.2f}" if isinstance(val, float) else f"{label} {val}")
    print(" | ".join(items))
    print()

# Quotes
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

# Clusters
if clusters:
    print("**事件**")
    for c in clusters[:6]:
        theme = c.get("theme", "?")
        urgency = c.get("urgency", "medium")
        summary = c.get("summary", "")[:200]
        urgency_mark = "`CRITICAL` " if urgency == "critical" else ""
        print(f"- {urgency_mark}[{theme}] {summary}")
    print()

# Signals
if signals_list:
    print("**信号**")
    for s in signals_list[:6]:
        direction = s.get("direction", "?")
        symbol = s.get("symbol", "?")
        rationale = s.get("rationale", "")[:120]
        dir_label = {"buy": "买入", "sell": "卖出"}.get(direction, direction)
        print(f"- {dir_label} `{symbol}` - {rationale}")
    print()

# ═══════════════════════════════════════════
# Part 2: LLM synthesis
# ═══════════════════════════════════════════

API_KEY = os.environ.get("OPENAI_COMPATIBLE_API_KEY") or os.environ.get("OPENAI_API_KEY")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://100.121.167.1:8317/v1")

if not API_KEY:
    print("数据边界: LLM key 未配置，跳过综合分析")
    print()
    print(f"`{run_id[:24]}...`")
    raise SystemExit(0)

# Build a compact data summary for the LLM
data_block_parts = []

# Macro
if macro:
    m_items = []
    for k, v in macro.items():
        if isinstance(v, (int, float)) and k not in ("timestamp", "source"):
            m_items.append(f"{k}={v}")
    if m_items:
        data_block_parts.append("宏观: " + ", ".join(m_items))

# Quotes with notable moves
if quotes:
    movers = []
    for sym, q in quotes.items():
        if isinstance(q, dict) and q.get("pct_change") is not None:
            if abs(q["pct_change"]) > 0.5:
                movers.append(f"{sym} {q['pct_change']:+.2f}%")
    if movers:
        data_block_parts.append("显著波动: " + ", ".join(movers[:8]))

# Clusters
if clusters:
    c_parts = []
    for c in clusters[:5]:
        c_parts.append(
            f"[{c.get('theme')}] urgency={c.get('urgency')} sentiment={c.get('sentiment')} "
            f"summary={c.get('summary','')[:150]}"
        )
    data_block_parts.append("事件: " + " | ".join(c_parts))

# Signals
if signals_list:
    s_parts = []
    for s in signals_list[:5]:
        s_parts.append(f"{s.get('direction')} {s.get('symbol')}: {s.get('rationale','')[:80]}")
    data_block_parts.append("信号: " + " | ".join(s_parts))

data_block = "\n".join(data_block_parts)

system_prompt = (
    "你是用户的全球市场情报分析师。你的任务是基于以下**已列出的事实数据**，"
    "给出 3-5 句话的综合分析和前瞻判断。\n\n"
    "硬性约束:\n"
    "- 只能引用上面已列出的数据，不得编造任何未在数据中出现的新闻、事件、数字\n"
    "- 如果数据不足以形成判断，诚实说明'数据不足以判断'\n"
    "- 分析应包含: 当前市场状态一句话 + 最值得关注的风险或机会 + 对用户组合可能的影响\n"
    "- 格式: 飞书兼容纯文本，仅用**加粗**和-列表。禁用 # | ``` > --- HTML\n"
    "- 不超过 200 字\n"
    "- 不承诺收益，不给出具体价格目标"
)

user_prompt = f"以下是本小时采集的事实数据:\n\n{data_block}\n\n请给出综合分析和前瞻判断。"

payload = json.dumps({
    "model": "deepseek-v4-pro",
    "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ],
    "temperature": 1,
    "max_tokens": 400,
}, ensure_ascii=False).encode("utf-8")

try:
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    analysis = result["choices"][0]["message"]["content"]
except Exception:
    analysis = "综合分析暂时不可用（LLM 接口未响应），数据事实部分已完整展示。"

print("**综合分析**")
print(analysis)
print()
print(f"采集: {ts} | {len(clusters)} 簇 | {len(signals_list)} 信号")
print(f"`{run_id[:24]}...`")
