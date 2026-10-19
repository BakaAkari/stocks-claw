"""投资日报 — 影响新闻、近期日历、持仓操作，三部分均附 LLM 判断。

隐私边界：日报与发给模型的输入都不含金额、数量、总资产或其他资产明细；
持仓只以名称/代码和百分比、技术位置出现。模型输出经过校验：新闻链接必须
来自输入、持仓必须是输入中的持仓、操作取固定枚举；不通过即标注不可用，
不伪造判断。日历和新闻列表是确定性的，模型失败时仍然输出。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

BRIEF_SCHEMA_VERSION = 1
NEWS_PER_HOLDING = 3
GENERAL_NEWS_LIMIT = 10
EVENT_HORIZON_DAYS = 14
ACTIONS = ("持有", "观察", "加仓", "减仓", "止损", "止盈")


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value), 2)


def _pct_vs(price: Optional[float], level: Optional[float]) -> Optional[float]:
    if price is None or not level:
        return None
    return round((price / level - 1) * 100, 2)


def _code_of(key: str) -> str:
    return key.split(":", 1)[1] if ":" in key else key


def _news_view(item: dict) -> dict:
    return {
        "title": item.get("title"),
        "source": item.get("source_name"),
        "published_at": str(item.get("published_at") or "")[:10] or None,
        "url": item.get("url"),
        "is_filing": item.get("source_type") == "filing",
    }


def _matched_news(code: str, news: list[dict]) -> list[dict]:
    matched = [
        n for n in news
        if (n.get("raw_metadata") or {}).get("symbol") == code or code in (n.get("tags") or [])
    ]
    matched.sort(key=lambda n: (n.get("source_type") == "filing", str(n.get("published_at") or "")), reverse=True)
    return [_news_view(n) for n in matched[:NEWS_PER_HOLDING]]


def _weight_band(weight: Optional[float]) -> Optional[str]:
    if weight is None:
        return None
    return "轻仓" if weight < 1 else "中等仓位" if weight < 3 else "较重仓位"


def _pnl_band(pnl: Optional[float]) -> Optional[str]:
    if pnl is None:
        return None
    if pnl <= -20:
        return "深度浮亏"
    if pnl <= -5:
        return "浮亏"
    if pnl < 5:
        return "接近成本"
    return "浮盈" if pnl < 15 else "较大浮盈"


def _in_horizon(event: dict) -> bool:
    days = event.get("days_until")
    return days is None or 0 <= days <= EVENT_HORIZON_DAYS


def build_brief_input(context: dict, metrics: Optional[dict] = None) -> dict:
    """从 ``AnalysisContext.to_dict()`` 投影出不含金额的日报输入。

    ``metrics``：instrument_key → ``compute_trader_metrics`` 结果；缺失时只用上下文里的基础指标。
    """
    from stocks.engine.trader_metrics import market_regime

    metrics = metrics or {}
    news = context.get("news") or []
    events = context.get("upcoming_events") or []
    rotation = {i.get("symbol"): i for i in (context.get("rotation") or {}).get("items") or []}
    valuations = context.get("position_valuations") or []
    total = sum(_num(v.get("market_value_cny")) or 0.0 for v in valuations)

    holdings: list[dict] = []
    for v in valuations:
        key = v.get("instrument_key")
        if v.get("valuation_method") != "market_quote" or not key:
            continue
        price = _num(v.get("price"))
        ind = v.get("indicators") or {}
        value = _num(v.get("market_value_cny"))
        technical = metrics.get(key) or {
            "vs_ma20_pct": _pct_vs(price, _num(ind.get("ma_20"))),
            "vs_ma60_pct": _pct_vs(price, _num(ind.get("ma_60"))),
            "rsi_14": _num(ind.get("rsi_14")),
            "r20_pct": _num((rotation.get(key) or {}).get("r20")),
        }
        holdings.append({
            "name": v.get("display_name") or key,
            "key": key,
            "price": price,
            "day_change_pct": _num(v.get("one_day_change_pct")),
            # 仓位与浮盈亏只给档位：模型据此判断轻重，但无从写出精确的资产数字
            "position_size": _weight_band(round(value / total * 100, 2) if value is not None and total else None),
            "pnl_band": _pnl_band(_num(v.get("pnl_pct"))),
            "_weight": value or 0.0,
            "technical": technical,
            "price_freshness": (v.get("evidence") or {}).get("price_freshness"),
            "news": _matched_news(_code_of(key), news),
        })
    holdings.sort(key=lambda h: h["_weight"], reverse=True)
    for h in holdings:
        del h["_weight"]

    held = {h["key"]: h["name"] for h in holdings}
    calendar = []
    for e in events:
        if not _in_horizon(e):
            continue
        symbols = e.get("affected_symbols") or []
        if e.get("event_type") == "earnings":
            # 财报首个 affected_symbol 是发布公司本身；只保留持仓公司的财报
            if not symbols or symbols[0] not in held:
                continue
            related = [held[symbols[0]]]
        else:
            related = [held[s] for s in symbols if s in held]
        calendar.append({
            "date": e.get("date"), "name": e.get("name"), "event_type": e.get("event_type"),
            "related_holdings": related,
        })
    calendar.sort(key=lambda e: str(e.get("date")))

    held_codes = {_code_of(k) for k in held}
    general_news = [
        _news_view(n) for n in news
        if n.get("scope") != "holding" and (n.get("raw_metadata") or {}).get("symbol") not in held_codes
    ][:GENERAL_NEWS_LIMIT]

    return {
        "schema_version": BRIEF_SCHEMA_VERSION,
        "generated_at": context.get("generated_at") or datetime.now(timezone.utc).isoformat(),
        "holdings": holdings,
        "calendar": calendar,
        "general_news": general_news,
        "market": market_regime(context),
        "unavailable_data": ["估值（PE/PB/分位）", "资金流（主力/北向）", "财务与盈利预期", "期权与隐含波动率"],
    }


PROMPT = """你是一名资深交易员兼用户的私人投资助理，用简体中文输出。根据输入写今天的投资日报判断。
输入只包含持仓名称、仓位档位、浮盈亏档位、技术指标、相关新闻、近期日历和市场环境；不包含任何金额。
不要描述用户的资产规模或持仓数量；仓位轻重和浮盈亏只用输入中的档位词表述。

每个持仓的 technical 字段含义：
- trend：alignment 均线排列，vs_ma20/60_pct 相对均线偏离，ma20_slope_5d_pct / ma60_slope_10d_pct 均线斜率；
- momentum：RSI、MACD 柱及其 3 日变化（判断动能增强/衰竭/背离）、5/20/60 日涨跌、相对基准（A股对沪深300，美股对标普500）的 20 日强弱；
- volatility：ATR14 占价格百分比、20 日年化波动、布林 %B、带宽及其 60 日分位（低分位 = 收口，常见于变盘前）；
- volume：今日量比、5 日均量/20 日均量、20 日 OBV 方向（量价是否确认）；
- levels：20/60 日高低点（支撑/阻力）、距 60 日高点回撤、60 日最大回撤、2 倍 ATR 止损参考价。
market：宏观数值（VIX、美债收益率、美元、汇率、油、金等）、A股/美股广度（站上 MA20 的比例）、20 日最强/最弱板块。
unavailable_data 列出系统没有的数据：不得编造这些内容，需要时说明缺失。

按资深交易员的框架评估每个持仓：
1) 主趋势：均线排列 + 斜率 + 价格相对均线，判断上升/下降/震荡；
2) 动能：RSI 区间与 MACD 柱变化，识别超买超卖、动能衰竭或背离；
3) 量价：放量突破/缩量回调/放量下跌，OBV 是否确认价格；
4) 相对强弱：跑赢还是跑输基准，结合板块强弱判断是否处在强势方向；
5) 波动与风险：用 ATR 定止损距离和分批间距，布林收口提示变盘；
6) 关键价位与风险收益比：以 20/60 日高低点和均线为支撑阻力，估算到止损与到目标的空间之比；
7) 市场环境与事件：广度、VIX、利率、美元与近期财报/FOMC，事件前降低确定性；
8) 仓位档位与浮盈亏档位：决定是加仓、持有还是兑现，深度浮亏不机械摊平。
证据相互矛盾或不足时选“观察”并写清在等什么信号。

输出要求：
1. news：从输入新闻中挑出对持仓或市场真正有影响的条目（最多 8 条，宁缺毋滥），url 必须原样来自输入；
   impact 写清影响哪个持仓/市场、利好还是利空、为什么。news_judgment 2-3 句总结新闻面。
2. calendar_judgment：2-3 句，结合市场环境说明未来两周要提前准备什么、哪些持仓受影响。
3. holdings：对每一个持仓给出：
   action 只能是 {actions} 之一；
   how：怎么操作，写触发条件、分批节奏与具体价位（加仓位/减仓位/止损位/目标位，取自 levels、均线或 ATR），
        用比例或“一部分/分批”，不写金额或股数；
   levels：一句话列出支撑、阻力、止损参考价和大致风险收益比；
   reason：按上面的框架写关键依据（趋势、动能、量价、相对强弱、波动、事件、档位）。
4. overall：1-2 句总体结论，含市场环境判断。
5. 只使用输入中的数字与事实。

只输出 JSON：
{{"news": [{{"url": "...", "impact": "..."}}], "news_judgment": "...", "calendar_judgment": "...",
  "holdings": [{{"key": "market:code", "action": "...", "how": "...", "levels": "...", "reason": "..."}}],
  "overall": "..."}}

输入：
{payload}
"""


def _validate(raw: dict, brief_input: dict) -> dict:
    known_urls = {n["url"] for h in brief_input["holdings"] for n in h["news"]} | {
        n["url"] for n in brief_input["general_news"]}
    held = {h["key"] for h in brief_input["holdings"]}
    news = [
        {"url": n["url"], "impact": n["impact"].strip()}
        for n in raw.get("news") or []
        if isinstance(n, dict) and n.get("url") in known_urls and isinstance(n.get("impact"), str) and n["impact"].strip()
    ][:8]
    holdings = []
    for h in raw.get("holdings") or []:
        if not isinstance(h, dict) or h.get("key") not in held or h.get("action") not in ACTIONS:
            continue
        if not all(isinstance(h.get(f), str) and h[f].strip() for f in ("how", "levels", "reason")):
            continue
        holdings.append({k: h[k].strip() for k in ("key", "action", "how", "levels", "reason")})
    texts = {k: raw.get(k) for k in ("news_judgment", "calendar_judgment", "overall")}
    if not all(isinstance(v, str) and v.strip() for v in texts.values()):
        raise ValueError("judgment fields must be non-empty strings")
    if held and not holdings:
        raise ValueError("no valid holding judgments")
    return {**{k: v.strip() for k, v in texts.items()}, "news": news, "holdings": holdings,
            "missing_holdings": sorted(held - {h["key"] for h in holdings})}


def judge_brief(brief_input: dict, llm_clients: list) -> dict:
    """依次尝试模型，校验输出；全部失败时返回 unavailable。"""
    if not llm_clients:
        return {"status": "unavailable", "error": "llm_not_configured"}
    payload = json.dumps(brief_input, ensure_ascii=False, default=str)
    prompt = PROMPT.format(actions="/".join(ACTIONS), payload=payload)
    errors = []
    for client in llm_clients:
        try:
            raw = client.complete(prompt).strip()
            if raw.startswith("```"):
                raw = raw.strip("`").removeprefix("json").strip()
            return {"status": "ok", "model": getattr(client, "model", None), **_validate(json.loads(raw), brief_input)}
        except Exception as exc:
            errors.append(f"{getattr(client, 'model', '?')}: {type(exc).__name__}: {exc}")
    return {"status": "unavailable", "error": "; ".join(errors)}


def render_brief(brief_input: dict, judgment: Optional[dict]) -> str:
    ok = bool(judgment) and judgment.get("status") == "ok"
    names = {h["key"]: h["name"] for h in brief_input["holdings"]}
    all_news = {n["url"]: n for h in brief_input["holdings"] for n in h["news"]}
    all_news.update({n["url"]: n for n in brief_input["general_news"]})
    lines = [f"# 投资日报 {str(brief_input['generated_at'])[:16].replace('T', ' ')} UTC", ""]
    if judgment and not ok:
        lines += [f"> LLM 判断不可用：{judgment.get('error')}", ""]

    lines.append("## 影响新闻")
    if ok and judgment["news"]:
        for item in judgment["news"]:
            n = all_news[item["url"]]
            lines.append(f"- [{n['title']}]({n['url']})")
            lines.append(f"  - {item['impact']}")
    else:
        for h in brief_input["holdings"]:
            for n in h["news"]:
                lines.append(f"- {h['name']}：[{n['title']}]({n['url']})")
        for n in brief_input["general_news"]:
            lines.append(f"- [{n['title']}]({n['url']})")
    if ok:
        lines.append(f"\n**判断**：{judgment['news_judgment']}")
    lines.append("")

    lines.append("## 近期投资日历")
    for e in brief_input["calendar"] or []:
        related = f"（{'、'.join(e['related_holdings'])}）" if e["related_holdings"] else ""
        lines.append(f"- {e['date']} {e['name']}{related}")
    if not brief_input["calendar"]:
        lines.append("- 未来两周无已登记事件")
    if ok:
        lines.append(f"\n**判断**：{judgment['calendar_judgment']}")
    lines.append("")

    lines.append("## 持仓操作")
    if ok:
        for h in judgment["holdings"]:
            lines.append(f"- **{names[h['key']]}：{h['action']}**")
            lines.append(f"  - 怎么做：{h['how']}")
            lines.append(f"  - 关键价位：{h['levels']}")
            lines.append(f"  - 依据：{h['reason']}")
        for key in judgment["missing_holdings"]:
            lines.append(f"- {names[key]}：模型未给出有效判断")
        lines.append(f"\n**总体**：{judgment['overall']}")
    else:
        lines.append("- 无 LLM 判断")
    stale = [h["name"] for h in brief_input["holdings"] if h.get("price_freshness") not in (None, "fresh")]
    if stale:
        lines += ["", f"> 行情非最新：{'、'.join(stale)}"]
    return "\n".join(lines).rstrip() + "\n"
