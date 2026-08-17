#!/usr/bin/env python3
"""手动重发 LLM 渲染的推送报告到它的目标飞书群。

为什么需要: 推送脚本(cron, no_agent)在 LLM 渲染失败时会返回非 0 退出并告警,
报告本身不会推送。Kari 手动重发时, 报告的 artifact 通常已超过 45 分钟 age 门禁,
常规 cron 路径无法重发。此脚本:
  1. 用 --force-llm(跳过 age 门禁) 重新渲染最新 artifact 的 LLM 报告
     (复用 run_llm_report.py 的 fail-closed 渲染 + 3 次重试 + 标 fail 语义);
  2. 从 Hermes cron jobs.json 解析该 session 的目标飞书群(单一事实源);
  3. 用 lark-oapi 把报告作为 text 消息发送到该群。

用法:
  python scripts/resend_report.py --session cn_after_close
  python scripts/resend_report.py --session cn_after_close --chat oc_xxx  # 覆盖目标群
  python scripts/resend_report.py --session cn_after_close --dry-run      # 只渲染不发送

依赖: 飞书凭据在 /opt/data/.env (FEISHU_APP_ID / FEISHU_APP_SECRET);
      lark-oapi 与 Hermes 同源 python-packages。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_HERMES_HOME = Path(os.environ.get("HERMES_HOME", "/opt/data"))
_JOBS_JSON = _HERMES_HOME / "cron" / "jobs.json"
_DOTENV = _HERMES_HOME / ".env"

# 推送 job 的 cron 脚本名 -> session 名
_SCRIPT_PREFIX = "stocks-claw-push-"
# article age 上限, 与 run_llm_report 一致(force-llm 跳过)
_PUSH_SESSIONS = {"cn_after_close", "cn_post_open", "daily_intel",
                  "us_after_close", "us_post_open"}


def _load_deliver_chats() -> dict[str, str]:
    """从 Hermes jobs.json 解析 {session: chat_id}。单一事实源, 跟随群配置变化。"""
    mapping: dict[str, str] = {}
    try:
        data = json.loads(_JOBS_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"无法读取 {_JOBS_JSON}: {exc}")

    def walk(obj):
        if isinstance(obj, dict):
            script = obj.get("script")
            deliver = obj.get("deliver")
            if isinstance(script, str) and script.startswith(_SCRIPT_PREFIX) and isinstance(deliver, str):
                session = script[len(_SCRIPT_PREFIX):].removesuffix(".sh")
                m = re.search(r"feishu:(oc_[A-Za-z0-9]+)", deliver)
                if m:
                    mapping.setdefault(session, m.group(1))
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(data)
    return mapping


def _env(key: str) -> str:
    m = re.search(rf"^{key}=(.+)$", _DOTENV.read_text(encoding="utf-8"), re.M)
    if not m:
        raise SystemExit(f"missing {key} in {_DOTENV}")
    return m.group(1).strip().strip('"').strip("'")


def _render_llm_report(session: str, now_iso: str) -> str:
    """用 --force-llm 重新渲染, 返回 LLM 报告文本; 失败则非 0 退出。"""
    import time
    ts = now_iso or time.strftime("%Y-%m-%dT%H:%M:%S%z")
    cmd = [
        sys.executable, str(_HERE / "run_llm_report.py"),
        "--session", session,
        "--artifact-root", str(_REPO / ".local/scheduled_runs/latest"),
        "--now", ts, "--force-llm",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=200)
    if proc.returncode != 0:
        raise SystemExit(
            f"LLM 渲染失败(exit={proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    text = proc.stdout
    if not text.strip():
        raise SystemExit("LLM 渲染返回空报告")
    return text


def _build_post_payload(markdown: str) -> str:
    """构造飞书 post 富文本 payload(markdown 渲染)。

    与 Hermes 飞书适配器一致: msg_type=post + tag=md 子块, 让 ** 加粗 /
    列表 / 标题真正渲染; 不能用手工拼接的 msg_type=text(飞书按纯文本显示,
    markdown 符号原样露出)。
    """
    # 带 ``` 围栏代码块时按行拆分, 否则整段一个 md 块即可
    if "```" not in markdown:
        return json.dumps(
            {"zh_cn": {"content": [[{"tag": "md", "text": markdown}]]}},
            ensure_ascii=False,
        )
    rows = []
    current = []
    in_code = False

    def flush():
        nonlocal current
        if current:
            seg = "\n".join(current)
            if seg.strip():
                rows.append([{"tag": "md", "text": seg}])
        current = []

    for raw in markdown.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            if not in_code:
                flush()
            current.append(raw)
            in_code = not in_code
            if not in_code:
                flush()
            continue
        current.append(raw)
    flush()
    if not rows:
        rows = [[{"tag": "md", "text": markdown}]]
    return json.dumps({"zh_cn": {"content": rows}}, ensure_ascii=False)


def _send_feishu_text(chat_id: str, text: str) -> str:
    """发送 markdown 报告到飞书群(post 富文本), 返回 message_id。"""
    sys.path.insert(0, "/opt/data/python-packages")
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
        CreateMessageRequestBody,
        CreateMessageRequest,
    )
    client = lark.Client.builder() \
        .app_id(_env("FEISHU_APP_ID")) \
        .app_secret(_env("FEISHU_APP_SECRET")) \
        .domain(lark.FEISHU_DOMAIN) \
        .log_level(lark.LogLevel.ERROR) \
        .build()
    req = CreateMessageRequest.builder() \
        .receive_id_type("chat_id") \
        .request_body(CreateMessageRequestBody.builder()
                      .receive_id(chat_id)
                      .msg_type("post")
                      .content(_build_post_payload(text))
                      .build()) \
        .build()
    resp = client.im.v1.message.create(req)
    if not resp.success():
        raise SystemExit(f"飞书发送失败 code={resp.code} msg={resp.msg}")
    return resp.data.message_id


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True, help="cn_after_close / cn_post_open / daily_intel / us_after_close / us_post_open")
    ap.add_argument("--now", help="覆盖 now 时间(默认当前时间)")
    ap.add_argument("--chat", help="覆盖目标飞书群 chat_id")
    ap.add_argument("--dry-run", action="store_true", help="只渲染, 不发送")
    args = ap.parse_args()

    if args.session not in _PUSH_SESSIONS:
        raise SystemExit(f"未知 session {args.session}; 可选: {sorted(_PUSH_SESSIONS)}")

    # 目标群: 优先 --chat, 否则从 jobs.json 解析
    chats = _load_deliver_chats()
    chat_id = args.chat or chats.get(args.session)
    if not chat_id:
        raise SystemExit(
            f"找不到 {args.session} 的目标飞书群; 请用 --chat 显式指定"
        )

    text = _render_llm_report(args.session, args.now or "")
    print("--- LLM 报告(预览前 500 字) ---")
    print(text[:500])

    if args.dry_run:
        print(f"DRY-RUN: 已渲染 {len(text)} 字, 未发送。目标群 {chat_id}")
        return 0

    msg_id = _send_feishu_text(chat_id, text)
    print(f"SENT OK to chat {chat_id} message_id={msg_id} ({len(text)} 字)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
