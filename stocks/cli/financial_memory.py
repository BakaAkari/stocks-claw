#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.services.asset_update_service import AssetUpdateService
from stocks.services.financial_memory_service import FinancialMemoryService


def main():
    parser = argparse.ArgumentParser(description='金融记忆读取与更新')
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('list', help='查看金融资产目录')

    upsert = sub.add_parser('upsert', help='新增或更新金融资产')
    upsert.add_argument('--name', required=True, help='资产名称')
    upsert.add_argument('--platform', required=True, help='平台')
    upsert.add_argument('--amount', required=True, type=float, help='持有金额')
    upsert.add_argument('--type', default='unknown', help='资产类型')
    upsert.add_argument('--notes', default='', help='备注')

    apply_cmd = sub.add_parser('apply-command', help='按更新协议写入资产')
    apply_cmd.add_argument('text', help='完整命令，如 更新资产 名称=黄金 平台=华泰 金额=120000')

    args = parser.parse_args()
    memory = FinancialMemoryService()
    updater = AssetUpdateService(memory)

    if args.command == 'list':
        print(json.dumps(memory.load(), ensure_ascii=False, indent=2))
        return 0

    if args.command == 'upsert':
        record = updater.upsert_asset(
            asset_name=args.name,
            platform=args.platform,
            amount=args.amount,
            asset_type=args.type,
            notes=args.notes,
            confirmed_by_user=True,
        )
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0

    if args.command == 'apply-command':
        record = updater.apply_update_command(args.text)
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0

    return 1


if __name__ == '__main__':
    raise SystemExit(main())
