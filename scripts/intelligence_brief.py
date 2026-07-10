#!/usr/bin/env python3
"""全球情报小时巡逻 — 纯数据采集+事件聚类，不调 LLM。

产出两样东西：
1. stdout — 结构化数据文本，供 hourly cron 直接推送飞书
2. .local/intelligence/latest_brief.json — 紧凑情报摘要，供推送 Agent 读取

推送 Agent 在构造推送时读取 brief JSON，自己做综合分析。
不再有独立的 LLM 情报分析调用。
"""
import json
import time
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path("/mnt/user/code-project/stocks-claw")
LATEST = PROJECT_ROOT / ".local/scheduled_runs/latest/global_intelligence_watch.json"
BRIEF_PATH = PROJECT_ROOT / ".local/intelligence"
BRIEF_FILE = BRIEF_PATH / "latest_brief.json"

# ── Theme → portfolio exposure tags ──
THEME_EXPOSURE_MAP = {
    "geopolitics": "能源XLE 黄金NEM 国防ITA",
    "energy": "XLE能源",
    "technology": "NVDA 半导体ETF 纳指QDII 科创50",
    "earnings": "NVDA 半导体ETF 科创50",
    "monetary_policy": "黄金NEM 债券SGOV 科技纳指",
    "crypto": "无直接持仓",
    "macro_data": "全组合",
    "general": "需具体分析",
    "healthcare": "无直接持仓",
    "financials": "无直接持仓",
    "real_estate": "无直接持仓",
}


def _clean_title(title: str) -> str:
    """Strip HTML entities and RSS cruft from titles."""
    title = title.replace("&nbsp;&nbsp;", " — ")
    title = title.replace("&amp;", "&")
    title = title.replace("&#39;", "'")
    return title.strip()


def _dedup_articles(articles: list) -> list:
    """Dedup articles by title prefix (first 80 chars)."""
    seen = set()
    result = []
    for a in articles:
        key = a.get("title", "")[:80].lower()
        if key not in seen:
            seen.add(key)
            result.append(a)
    return result


def build_brief(data: dict) -> dict:
    """Build compact intelligence brief from raw watch data."""
    digest = data.get("context_digest", {})
    clusters = digest.get("clusters", []) or []
    signals = digest.get("signals", []) or []
    macro = digest.get("macro", {}) or {}
    quotes = digest.get("quotes", {}) or {}

    # ── Macro ──
    macro_compact = {}
    for key, label in [
        ("vix", "VIX"), ("us_10y_yield", "US10Y"),
        ("dxy", "DXY"), ("usd_cny", "USDCNY"),
        ("crude_oil", "OIL"), ("gold", "GOLD"),
    ]:
        val = macro.get(key)
        if val is not None:
            macro_compact[label] = round(val, 2) if isinstance(val, float) else val

    # ── Key movers ──
    movers = []
    for sym, q in quotes.items():
        if isinstance(q, dict) and q.get("pct_change") is not None:
            movers.append({
                "symbol": sym,
                "pct": round(q["pct_change"], 2),
                "price": round(q.get("price", 0), 2),
            })
    movers.sort(key=lambda x: abs(x["pct"]), reverse=True)

    # ── Clusters (deduped, compact) ──
    cluster_briefs = []
    for c in clusters:
        articles = _dedup_articles(c.get("articles", []))
        if not articles:
            continue
        primary = articles[0]
        title = _clean_title(primary.get("title", ""))
        theme = c.get("theme", "general")
        cluster_briefs.append({
            "theme": theme,
            "urgency": c.get("urgency", "medium"),
            "sentiment": c.get("sentiment", "neutral"),
            "title": title[:200],
            "source": primary.get("source_name", "?"),
            "published_at": primary.get("published_at", ""),
            "article_count": len(articles),
            "portfolio_relevance": THEME_EXPOSURE_MAP.get(theme, "需具体分析"),
            "cluster_id": c.get("cluster_id", ""),
        })

    # ── Signals ──
    signal_briefs = []
    for s in signals:
        signal_briefs.append({
            "direction": s.get("direction", "?"),
            "symbol": s.get("symbol", "?"),
            "rationale": s.get("rationale", "")[:200],
        })

    return {
        "collected_at": data.get("scheduled_for", "")[:19],
        "macro": macro_compact,
        "key_movers": movers[:10],
        "clusters": cluster_briefs,
        "signals": signal_briefs,
        "cluster_count": len(cluster_briefs),
        "signal_count": len(signal_briefs),
        "run_id": data.get("run_id", "")[:24],
    }


def format_stdout(brief: dict) -> str:
    """Format structured data for hourly Feishu delivery."""
    lines = []
    ts = brief["collected_at"]
    cluster_count = brief["cluster_count"]

    urgent = [c for c in brief["clusters"] if c["urgency"] == "critical"]
    themes = sorted({c["theme"] for c in brief["clusters"]})

    lines.append(f"**全球情报 - {ts}**")
    lines.append("")

    if urgent:
        lines.append(f"关键警报: {len(urgent)} 个事件标为 critical")
    else:
        lines.append(f"本小时 {cluster_count} 个事件主题: {', '.join(themes[:5])}")
    lines.append("")

    # Macro
    if brief["macro"]:
        items = [f"{k} {v}" for k, v in brief["macro"].items()]
        lines.append(f"**宏观** {' | '.join(items)}")
        lines.append("")

    # Key movers
    if brief["key_movers"]:
        m = [f"{qm['symbol']} {qm['pct']:+.2f}%" for qm in brief["key_movers"][:10]]
        lines.append(f"**行情** {' | '.join(m)}")
        lines.append("")

    # Clusters
    if brief["clusters"]:
        lines.append("**事件**")
        for c in brief["clusters"][:8]:
            urgency_mark = "CRITICAL " if c["urgency"] == "critical" else ""
            lines.append(
                f"- [{c['theme']}] {urgency_mark}"
                f"{c['title'][:150]} — {c['source']}"
            )
        lines.append("")

    # Signals
    if brief["signals"]:
        lines.append("**信号**")
        for s in brief["signals"]:
            d = {"buy": "买入", "sell": "卖出"}.get(s["direction"], s["direction"])
            lines.append(f"- {d} `{s['symbol']}` — {s['rationale'][:120]}")
        lines.append("")

    lines.append(
        f"采集: {ts} | {cluster_count} 簇 | {brief['signal_count']} 信号 "
        f"| `{brief['run_id']}...`"
    )

    return "\n".join(lines)


def main():
    if not LATEST.exists():
        print("No latest artifact found")
        raise SystemExit(1)

    data = json.loads(LATEST.read_text())
    brief = build_brief(data)

    # Write brief for push agents
    BRIEF_PATH.mkdir(parents=True, exist_ok=True)
    BRIEF_FILE.write_text(
        json.dumps(brief, ensure_ascii=False, indent=2)
    )

    # Print for hourly delivery
    print(format_stdout(brief))


if __name__ == "__main__":
    main()
