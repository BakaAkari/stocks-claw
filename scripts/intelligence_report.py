#!/usr/bin/env python3
import json
import os
import textwrap
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://100.121.167.1:8317/v1")
API_KEY = os.environ.get("OPENAI_COMPATIBLE_API_KEY") or os.environ.get("OPENAI_API_KEY")

latest = Path("/mnt/user/code-project/stocks-claw/.local/scheduled_runs/latest/global_intelligence_watch.json")
if not latest.exists():
    print("No latest artifact found")
    raise SystemExit(1)

best = json.loads(latest.read_text())
status = best.get("status", "unknown")
run_id = best.get("run_id", "")
scheduled_for = best.get("scheduled_for", "")
priority = best.get("session_summary", {}).get("priority", "")
ctx = best.get("context_digest", {})
source = best.get("source_context", {})
macro = ctx.get("macro", {}) or {}
market_impact = ctx.get("market_impact", {}) or {}
clusters = ctx.get("clusters", []) or []
signals = ctx.get("signals", []) or []
article_count = source.get("article_count", 0)

clusters_text = []
for c in clusters[:6]:
    clusters_text.append(f"theme={c.get('theme')} urgency={c.get('urgency')} sentiment={c.get('sentiment')} summary={c.get('summary', '')}")

signals_text = []
for s in signals[:5]:
    signals_text.append(f"symbol={s.get('symbol')} direction={s.get('direction')} urgency={s.get('urgency')} rationale={s.get('rationale', '')}")

raw_block = textwrap.dedent(f"""
全球情报小时巡逻
时间: {scheduled_for}
状态: {status} 优先级: {priority}
文章数: {article_count} 事件簇数: {len(clusters)} 信号数: {len(signals)}

宏观快照:
VIX={macro.get('vix', 'N/A')}, 美债10Y={macro.get('us_10y_yield', 'N/A')}, USD/CNY={macro.get('usd_cny', 'N/A')}, 油={macro.get('crude_oil', 'N/A')}, 金={macro.get('gold', 'N/A')}

重点事件簇:
""")
raw_block += "\n".join(clusters_text)
raw_block += "\n\n操作信号:\n"
raw_block += "\n".join(signals_text)

if API_KEY:
    prompt = f"""你是中文金融分析助手。请将以下全球宏观情报数据整理成一份简洁的中文分析报告，不超过 900 字。
要求:
1. 先给出一句核心结论（风险偏好、主要市场影响）。
2. 结合宏观数据简述当前市场状态。
3. 对每个事件簇，翻译标题/摘要为中文，并说明市场影响。
4. 对每个操作信号，翻译理由为中文，给出买入/卖出/观察建议。
5. 最后列出数据边界（缺失或异常数据）。
6. 禁止承诺收益，禁止给出具体价格目标。

数据如下:
{raw_block}

请输出中文报告，使用 Markdown 简洁格式。"""
    payload = json.dumps({
        "model": "kimi-k2.6",
        "messages": [
            {"role": "system", "content": "你是中文金融分析助手，专门将英文宏观情报翻译为中文分析报告。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 1,
        "max_tokens": 1600,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    analysis = result["choices"][0]["message"]["content"]
    print("**\u5168\u7403\u60c5\u62a5\u5c0f\u65f6\u5de1\u903b\u5206\u6790**")
    print(f"\u65f6\u95f4: {scheduled_for}")
    print(f"\u72b6\u6001: {status} | \u4f18\u5149级: {priority}")
    print("")
    print(analysis)
    print(f"\nrun_id: `{run_id}`")
else:
    print("ERROR: no API key found for LLM translation")
    raise SystemExit(1)
