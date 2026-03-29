#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.services.command_service import CommandService


def main():
    parser = argparse.ArgumentParser(description='处理股票命令，如 查A股 紫金矿业 / A股简报')
    parser.add_argument('text', help='完整命令文本')
    args = parser.parse_args()

    service = CommandService()
    result = service.handle(args.text)
    if result is None:
        print('不认识这个命令')
        return 1

    print(result.content)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
