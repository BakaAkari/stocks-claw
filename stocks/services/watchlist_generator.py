#!/usr/bin/env python3
"""
从用户金融资产自动提取监控标的
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import sys
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from stocks.logging_utils import log_event
FINANCIAL_ASSETS_PATH = ROOT / 'stocks' / 'data' / 'financial_assets.json'
WATCHLIST_PATH = ROOT / 'stocks' / 'config' / 'watchlist.json'


class WatchlistGenerator:
    """从用户持仓自动生成监控列表"""
    
    # 资产名称到代码的映射（常见标的）
    # 格式: (匹配关键词, 代码, 市场)
    KNOWN_ASSETS = [
        # A股个股
        ('贵州茅台', '600519', 'sh'),
        ('平安银行', '000001', 'sz'),
        ('紫金矿业', '601899', 'sh'),
        ('紫金', '601899', 'sh'),
        # A股指数/ETF
        ('沪深300', '000300', 'sz_index'),
        ('创业板', '159915', 'sz'),
        ('创业板ETF', '159915', 'sz'),
        ('沪深300ETF', '510300', 'sh'),
        ('上证50', '510050', 'sh'),
        ('黄金ETF', '518880', 'sh'),
        ('华安黄金ETF', '518880', 'sh'),
        ('游戏ETF', '159869', 'sz'),  # 国泰中证动漫游戏ETF
        ('动漫游戏', '159869', 'sz'),
        ('稀有金属', '159608', 'sz'),  # 稀有金属ETF
        ('稀有金属ETF', '159608', 'sz'),
        # 美股
        ('QQQ', 'QQQ', 'us'),
        ('SPY', 'SPY', 'us'),
        ('纳指ETF', 'QQQ', 'us'),
        ('纳斯达克100ETF', 'QQQ', 'us'),
        ('广发纳斯达克100', 'QQQ', 'us'),
        ('大成纳斯达克100', 'QQQ', 'us'),
        ('纳斯达克100', 'QQQ', 'us'),
        ('标普500', 'SPY', 'us'),
        ('道琼斯', 'DIA', 'us'),
        ('GLD', 'GLD', 'us'),
        ('黄金ETF-US', 'GLD', 'us'),
        ('IAU', 'IAU', 'us'),
        # 美股个股
        ('NVDA', 'NVDA', 'us'),
        ('英伟达', 'NVDA', 'us'),
        ('AAPL', 'AAPL', 'us'),
        ('苹果', 'AAPL', 'us'),
        ('MSFT', 'MSFT', 'us'),
        ('微软', 'MSFT', 'us'),
        ('MSTR', 'MSTR', 'us'),
        ('MicroStrategy', 'MSTR', 'us'),
        ('BABA', 'BABA', 'us'),
        ('阿里巴巴', 'BABA', 'us'),
        ('UNH', 'UNH', 'us'),
        ('联合健康', 'UNH', 'us'),
        ('GS', 'GS', 'us'),
        ('高盛', 'GS', 'us'),
        ('KWEB', 'KWEB', 'us'),
        ('中概互联', 'KWEB', 'us'),
        ('IWM', 'IWM', 'us'),
        ('罗素2000', 'IWM', 'us'),
        ('DIA', 'DIA', 'us'),
        ('TLT', 'TLT', 'us'),
        ('IEF', 'IEF', 'us'),
    ]
    
    def __init__(
        self,
        assets_path: Path | None = None,
        watchlist_path: Path | None = None,
    ):
        self.assets_path = assets_path or FINANCIAL_ASSETS_PATH
        self.watchlist_path = watchlist_path or WATCHLIST_PATH
    
    def load_assets(self) -> list[dict]:
        """加载用户金融资产"""
        if not self.assets_path.exists():
            return []
        try:
            with open(self.assets_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data.get('assets', [])
        except Exception as e:
            log_event('watchlist_gen.assets_failed', error=str(e))
            return []
    
    def extract_symbols(self, assets: list[dict]) -> dict[str, list[dict]]:
        """
        从资产列表中提取监控标的
        
        Returns:
            {'a': [...], 'us': [...]}
        """
        a_shares = []
        us_shares = []
        seen = set()
        
        for asset in assets:
            name = asset.get('asset_name', '')
            asset_type = asset.get('asset_type', '')
            platform = asset.get('platform', '')
            notes = asset.get('notes', '')
            
            # 检查已知映射
            for known_name, code, market in self.KNOWN_ASSETS:
                if known_name in name or name in known_name:
                    key = (market, code)
                    if key not in seen:
                        seen.add(key)
                        entry = {'code': code, 'name': name, 'market': market}
                        if market == 'us':
                            us_shares.append(entry)
                        elif market in ('sh', 'sz', 'sh_index', 'sz_index'):
                            a_shares.append(entry)
                    break  # 找到匹配就停止
            
            # 从备注中提取代码
            # 匹配 "持仓 X 股" 或 "代码: XXX" 等
            code_patterns = [
                r'持仓\s*(\d+)\s*股',  # 持仓 400 股
                r'代码[：:]\s*(\d{6})',  # 代码: 600519
                r'(?:^|[\s；;])(\d{6})(?:$|[\s；;])',  # 6位数字，前后是分隔符
            ]
            
            # 单独处理美股代码提取，避免匹配到 USD 等
            us_pattern = r'(?:美股|US)[：:]\s*([A-Z]{2,5})(?:\s|$|；|;|，|,|\.)' 
            us_matches = re.findall(us_pattern, notes)
            for match in us_matches:
                code = match.upper()
                if code not in ('USD', 'ETF', 'HKD', 'CNY'):  # 过滤常见误匹配
                    key = ('us', code)
                    if key not in seen:
                        seen.add(key)
                        us_shares.append({'code': code, 'name': name, 'market': 'us'})
            for pattern in code_patterns:
                matches = re.findall(pattern, notes)
                for match in matches:
                    if isinstance(match, str) and match.isdigit() and len(match) == 6:
                        # 过滤金额（元、块、刀等）
                        idx = notes.find(match)
                        if idx >= 0:
                            after = notes[idx+6:idx+10]
                            if any(u in after for u in ['元', '块', '刀', '刀乐', 'USD', '美元']):
                                continue
                        # A股代码
                        code = match
                        market = 'sh' if code.startswith(('5', '6', '9')) else 'sz'
                        key = (market, code)
                        if key not in seen:
                            seen.add(key)
                            a_shares.append({'code': code, 'name': name, 'market': market})
        
        return {'a': a_shares, 'us': us_shares}
    
    def load_existing_watchlist(self) -> dict:
        """加载现有 watchlist"""
        if not self.watchlist_path.exists():
            return {'markets': {'a': {'label': 'A股', 'watchlist': []}, 'us': {'label': '美股', 'watchlist': []}}}
        try:
            with open(self.watchlist_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            log_event('watchlist_gen.load_failed', error=str(e))
            return {'markets': {'a': {'label': 'A股', 'watchlist': []}, 'us': {'label': '美股', 'watchlist': []}}}
    
    def merge_watchlist(self, existing: dict, extracted: dict[str, list[dict]]) -> dict:
        """合并现有 watchlist 和提取的标的"""
        result = json.loads(json.dumps(existing))  # 深拷贝
        
        for market_key, entries in extracted.items():
            if market_key not in result.get('markets', {}):
                continue
            
            existing_codes = {
                item.get('code') for item in result['markets'][market_key].get('watchlist', [])
            }
            
            for entry in entries:
                if entry['code'] not in existing_codes:
                    result['markets'][market_key]['watchlist'].append(entry)
                    existing_codes.add(entry['code'])
        
        return result
    
    def refresh(self) -> dict:
        """刷新 watchlist，添加从持仓提取的标的"""
        assets = self.load_assets()
        extracted = self.extract_symbols(assets)
        existing = self.load_existing_watchlist()
        merged = self.merge_watchlist(existing, extracted)
        
        # 保存
        self.watchlist_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.watchlist_path, 'w', encoding='utf-8') as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
            f.write('\n')
        
        a_count = len(merged['markets']['a']['watchlist'])
        us_count = len(merged['markets']['us']['watchlist'])
        log_event('watchlist.refreshed', a_shares=a_count, us_shares=us_count)
        
        return merged
    
    def get_summary(self) -> str:
        """获取当前 watchlist 摘要"""
        watchlist = self.load_existing_watchlist()
        lines = ['当前监控列表：']
        
        for market_key, market_data in watchlist.get('markets', {}).items():
            label = market_data.get('label', market_key)
            items = market_data.get('watchlist', [])
            lines.append(f'\n【{label}】共 {len(items)} 只')
            for item in items[:10]:  # 最多显示10只
                lines.append(f"  {item.get('code')} - {item.get('name')}")
            if len(items) > 10:
                lines.append(f'  ... 等共 {len(items)} 只')
        
        return '\n'.join(lines)


def main():
    """CLI 入口"""
    import argparse
    parser = argparse.ArgumentParser(description='从持仓刷新监控列表')
    parser.add_argument('--show', action='store_true', help='显示当前监控列表')
    args = parser.parse_args()
    
    gen = WatchlistGenerator()
    
    if args.show:
        print(gen.get_summary())
        return
    
    result = gen.refresh()
    a_count = len(result['markets']['a']['watchlist'])
    us_count = len(result['markets']['us']['watchlist'])
    print(f'已刷新监控列表：A股 {a_count} 只，美股 {us_count} 只')


if __name__ == '__main__':
    raise SystemExit(main())
