#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.services.news_fetch_service import NewsFetchService


def main():
    parser = argparse.ArgumentParser(description='刷新真实新闻输入（当前最小支持 RSS）')
    parser.add_argument('--limit-per-source', type=int, default=10)
    args = parser.parse_args()

    payload = NewsFetchService().refresh(limit_per_source=args.limit_per_source)
    print(json.dumps({'updated_at': payload.get('updated_at'), 'count': len(payload.get('items', []))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
