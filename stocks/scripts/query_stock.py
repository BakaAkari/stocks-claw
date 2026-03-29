#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys


def detect_prefix(code: str, market: str | None = None) -> str:
    code = str(code)
    market = (market or '').lower()
    if market in ('sh', 'sh_stock', 'sh_a', 'sh_index'):
        return 'sh'
    if market in ('sz', 'sz_stock', 'sz_a', 'sz_index'):
        return 'sz'
    if code.startswith(('5', '6', '9')):
        return 'sh'
    return 'sz'


def fetch_quote(code: str, market: str | None = None) -> dict:
    prefix = detect_prefix(code, market)
    url = f'https://qt.gtimg.cn/q=s_{prefix}{code}'
    result = subprocess.run(
        ['curl', '-L', '--max-time', '20', '-s', url],
        check=True,
        capture_output=True,
    )
    text = result.stdout.decode('gbk', errors='replace').strip()
    if '="' not in text:
        raise RuntimeError('没有拿到有效返回')
    _, raw = text.split('="', 1)
    raw = raw.rstrip('";')
    parts = raw.split('~')
    if len(parts) < 10:
        raise RuntimeError('返回字段不够，接口可能变了')
    return {
        'name': parts[1] or '-',
        'code': parts[2] or code,
        'price': float(parts[3]) if parts[3] else None,
        'change': float(parts[4]) if parts[4] else None,
        'pct_change': float(parts[5]) if parts[5] else None,
        'volume_lot': float(parts[6]) if parts[6] else None,
        'amount_10k': float(parts[9]) if parts[9] else None,
        'market': prefix,
    }


def fmt_num(value, digits=2):
    if value is None:
        return '-'
    return f'{value:,.{digits}f}'


def fmt_pct(value):
    if value is None:
        return '-'
    sign = '+' if value > 0 else ''
    return f'{sign}{value:.2f}%'


def main():
    parser = argparse.ArgumentParser(description='查询单只 A 股/指数的最新简要行情')
    parser.add_argument('code', help='证券代码，如 600519 / 000001 / 000300')
    parser.add_argument('--market', help='可选：sh / sz / sh_index / sz_index')
    parser.add_argument('--json', action='store_true', help='输出 JSON')
    args = parser.parse_args()

    try:
        quote = fetch_quote(args.code, args.market)
    except Exception as e:
        print(f'查询失败：{e}', file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(quote, ensure_ascii=False, indent=2))
        return 0

    print(f"{quote['name']} ({quote['code']})")
    print(f"最新价：{fmt_num(quote['price'])}")
    print(f"涨跌额：{fmt_num(quote['change'])}")
    print(f"涨跌幅：{fmt_pct(quote['pct_change'])}")
    print(f"成交量：{fmt_num(quote['volume_lot'], 0)} 手")
    print(f"成交额：{fmt_num(quote['amount_10k'])} 万")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
