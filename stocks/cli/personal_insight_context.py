#!/usr/bin/env python3
"""
[调试工具] 输出金融记忆 + 新闻输入的组合上下文

用途：调试 PersonalInsightService，查看输入 LLM 的原始上下文
状态：维护模式，非主链路
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.services.personal_insight_service import PersonalInsightService


def main():
    parser = argparse.ArgumentParser(description='[调试] 输出金融记忆 + 新闻输入的组合上下文')
    parser.add_argument('--news-limit', type=int, default=5, help='最多包含几条新闻')
    parser.add_argument('--format', choices=['json', 'text'], default='text')
    args = parser.parse_args()

    service = PersonalInsightService()
    if args.format == 'json':
        print(json.dumps(service.build_context(news_limit=args.news_limit), ensure_ascii=False, indent=2))
    else:
        print(service.render_prompt_input(news_limit=args.news_limit))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
