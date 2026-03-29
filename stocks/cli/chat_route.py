#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.services.chat_router_service import ChatRouterService


def main():
    parser = argparse.ArgumentParser(description='模拟聊天消息路由到股票命令系统')
    parser.add_argument('text', help='收到的聊天文本')
    args = parser.parse_args()

    service = ChatRouterService()
    result = service.route(args.text)
    if not result.handled:
        print('NO_MATCH')
        return 2

    print(result.response or '')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
