#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.validators import validate_all


def main():
    parser = argparse.ArgumentParser(description='校验股票系统配置')
    parser.add_argument('markets', nargs='*', help='要校验的市场，如 a us；默认全校验')
    args = parser.parse_args()

    validate_all(args.markets or None)
    print('config ok')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
