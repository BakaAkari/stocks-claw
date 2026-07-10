#!/usr/bin/env python3
"""全球情报小时巡逻 — 结构化数据 + 轻量 LLM 总结。

产出两样东西：
1. stdout — 结构化数据 + 3-5 句中文总结，供 hourly cron 直接推送飞书
2. .local/intelligence/latest_brief.json — 紧凑情报摘要，供推送 Agent 读取（不含 LLM）

推送 Agent 读 brief JSON 做完整分析；hourly 推送只需快速扫描总结。
"""
import json
import os
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path("/mnt/user/code-project/stocks-claw")
LATEST = PROJECT_ROOT / ".local/scheduled_runs/latest/global_intelligence_watch.json"
BRIEF_PATH = PROJECT_ROOT / ".local/intelligence"
BRIEF_FILE = BRIEF_PATH / "latest_brief.json"
ENV_FILE = Path("/opt/data/.env")

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


def _load_api_key() -> str:
    """从 .env 加载 LLM API key（不依赖 shell export）。"""
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("OPENAI_COMPATIBLE_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
            if line.startswith("OPENAI_API_KEY=") and "COMPATIBLE" not in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("OPENAI_COMPATIBLE_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""


def _clean_title(title: str) -> str:
    title = title.replace("&nbsp;&nbsp;", " — ")
    title = title.replace("&amp;", "&")
    title = title.replace("&#39;", "'")
    return title.strip()


def _dedup_articles(articles: list) -> list:
    seen = set()
    result = []
    for a in articles:
        key = a.get("title", "")[:80].lower()
        if key not in seen:
            seen.add(key)
            result.append(a)
    return result


def build_brief(data: dict) -> dict:
    digest = data.get("context_digest", {})
    clusters = digest.get("clusters", []) or []
    signals = digest.get("signals", []) or []
    macro = digest.get("macro", {}) or {}
    quotes = digest.get("quotes", {}) or {}

    macro_compact = {}
    for key, label in [
        ("vix", "VIX"), ("us_10y_yield", "US10Y"),
        ("dxy", "DXY"), ("usd_cny", "USDCNY"),
        ("crude_oil", "OIL"), ("gold", "GOLD"),
    ]:
        val = macro.get(key)
        if val is not None:
            macro_compact[label] = round(val, 2) if isinstance(val, float) else val

    movers = []
    for sym, q in quotes.items():
        if isinstance(q, dict) and q.get("pct_change") is not None:
            movers.append({
                "symbol": sym,
                "pct": round(q["pct_change"], 2),
                "price": round(q.get("price", 0), 2),
            })
    movers.sort(key=lambda x: abs(x["pct"]), reverse=True)

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

    if brief["macro"]:
        items = [f"{k} {v}" for k, v in brief["macro"].items()]
        lines.append(f"**宏观** {' | '.join(items)}")
        lines.append("")

    if brief["key_movers"]:
        m = [f"{qm['symbol']} {qm['pct']:+.2f}%" for qm in brief["key_movers"][:10]]
        lines.append(f"**行情** {' | '.join(m)}")
        lines.append("")

    if brief["clusters"]:
        lines.append("**事件**")
        for c in brief["clusters"][:8]:
            urgency_mark = "CRITICAL " if c["urgency"] == "critical" else ""
            lines.append(
                f"- [{c['theme']}] {urgency_mark}"
                f"{c['title'][:150]} — {c['source']}"
            )
        lines.append("")

    if brief["signals"]:
        lines.append("**信号**")
        for s in brief["signals"]:
            d = {"buy": "买入", "sell": "卖出"}.get(s["direction"], s["direction"])
            lines.append(f"- {d} `{s['symbol']}` — {s['rationale'][:120]}")
        lines.append("")

    # LLM 快速总结
    summary = _llm_summary(brief)
    if summary:
        lines.append("**快速总结**")
        lines.append(summary)
        lines.append("")

    lines.append(
        f"采集: {ts} | {cluster_count} 簇 | {brief['signal_count']} 信号 "
        f"| `{brief['run_id']}...`"
    )

    return "\n".join(lines)


def _llm_summary(brief: dict) -> str:
    """调用 LLM 生成 3-5 句中文市场总结。仅引用已展示的数据。"""
    api_key = _load_api_key()
    if not api_key:
        return ""

    base_url = os.environ.get("OPENAI_BASE_URL", "http://100.121.167.1:8317/v1")

    parts = []
    if brief["macro"]:
        parts.append("宏观: " + ", ".join(f"{k}={v}" for k, v in brief["macro"].items()))
    if brief["key_movers"]:
        m = [f"{qm['symbol']} {qm['pct']:+.2f}%" for qm in brief["key_movers"][:6]]
        parts.append("显著波动: " + ", ".join(m))
    if brief["clusters"]:
        c_parts = []
        for c in brief["clusters"][:4]:
            c_parts.append(f"[{c['theme']}] urgency={c['urgency']} {c['title'][:100]}")
        parts.append("事件: " + " | ".join(c_parts))
    if brief["signals"]:
        s_parts = []
        for s in brief["signals"]:
            s_parts.append(f"{s['direction']} {s['symbol']}: {s['rationale'][:60]}")
        parts.append("信号: " + " | ".join(s_parts))

    data_block = "\n".join(parts)

    system = (
        "你是全球市场快讯编辑。基于已列出的事实数据，用中文写 3-5 句话总结。\n"
        "硬性约束:\n"
        "- 只能引用上面已列出的数据，不得编造\n"
        "- 格式: 飞书纯文本，用**加粗**标关键变化\n"
        "- 内容: 一句话宏观 + 最值得关注的 1-2 个事件 + 对用户组合的提醒\n"
        "- 不超过 150 字\n"
        "- 不承诺收益"
    )

    payload = json.dumps({
        "model": "deepseek-v4-pro",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"本小时数据:\n\n{data_block}\n\n请总结。"},
        ],
        "temperature": 1,
        "max_tokens": 300,
    }, ensure_ascii=False).encode("utf-8")

    try:
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return result["choices"][0]["message"]["content"].strip()
    except Exception:
        return ""


def main():
    if not LATEST.exists():
        print("No latest artifact found")
        raise SystemExit(1)

    data = json.loads(LATEST.read_text())
    brief = build_brief(data)

    BRIEF_PATH.mkdir(parents=True, exist_ok=True)
    BRIEF_FILE.write_text(
        json.dumps(brief, ensure_ascii=False, indent=2)
    )

    print(format_stdout(brief))


if __name__ == "__main__":
    main()
