from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WATCHLIST_PATH = ROOT / 'config' / 'watchlist.json'


class WatchlistService:
    """统一管理 watchlist，支持用户配置 + 自动补全 + 市场目标生成。"""

    DEFAULT_MARKETS = {
        'a': {'label': 'A股', 'watchlist': []},
        'us': {'label': '美股', 'watchlist': []},
    }

    CANONICAL_GROUPS = {
        'risk_assets_us': [('us', 'QQQ'), ('us', 'SPY'), ('us', 'IWM'), ('us', 'DIA')],
        'safe_haven': [('us', 'GLD'), ('us', 'IAU'), ('a', '518880')],
        'rates': [('us', 'IEF'), ('us', 'TLT')],
        'china_equity': [('a', '159915'), ('a', '159919'), ('a', '510300'), ('a', '510050')],
        'hk_china_tech_proxy': [('us', 'KWEB')],
    }

    USER_KEY_ASSET_CODES = {'NVDA', 'AAPL', 'MSFT', 'MSTR', 'BABA', 'UNH', 'GS', '601899', '518880', '159869', '159608', 'QQQ'}

    def __init__(self, path: Path | None = None):
        self.path = path or DEFAULT_WATCHLIST_PATH
        self._cache: dict | None = None
        self._mtime: float = 0.0

    def load(self) -> dict:
        # 热重载：检查文件修改时间
        try:
            current_mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            current_mtime = 0.0
        
        if self._cache is not None and current_mtime == self._mtime:
            return self._cache
        
        if not self.path.exists():
            self._cache = {'markets': json.loads(json.dumps(self.DEFAULT_MARKETS))}
            self._mtime = current_mtime
            return self._cache
        
        with open(self.path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            self._cache = {'markets': json.loads(json.dumps(self.DEFAULT_MARKETS))}
            self._mtime = current_mtime
            return self._cache
        data.setdefault('markets', json.loads(json.dumps(self.DEFAULT_MARKETS)))
        for key, default_val in self.DEFAULT_MARKETS.items():
            data['markets'].setdefault(key, {'label': default_val['label'], 'watchlist': []})
            data['markets'][key].setdefault('watchlist', [])
        
        self._cache = data
        self._mtime = current_mtime
        return self._cache

    def list_entries(self) -> list[dict]:
        data = self.load()
        entries: list[dict] = []
        for market_key, market_data in (data.get('markets') or {}).items():
            for item in market_data.get('watchlist', []) or []:
                entries.append({
                    'market_key': market_key,
                    'market': item.get('market') or market_key,
                    'code': str(item.get('code') or '').upper() if market_key == 'us' else str(item.get('code') or ''),
                    'name': item.get('name') or str(item.get('code') or ''),
                })
        return entries

    def build_market_targets(self) -> dict[str, list[tuple[str, str]]]:
        groups = {k: list(v) for k, v in self.CANONICAL_GROUPS.items()}
        user_key_assets: list[tuple[str, str]] = []
        seen = set()
        for item in self.list_entries():
            code = item['code']
            market = 'us' if item['market'] == 'us' else 'a'
            pair = (market, code)
            if code in self.USER_KEY_ASSET_CODES and pair not in seen:
                user_key_assets.append(pair)
                seen.add(pair)
        if not user_key_assets:
            user_key_assets = [('us', 'NVDA'), ('us', 'AAPL'), ('us', 'MSTR'), ('a', '601899')]
        groups['user_key_assets'] = user_key_assets
        return groups

    def summary_text(self) -> str:
        data = self.load()
        lines = ['当前监控列表：']
        for market_key, market_data in (data.get('markets') or {}).items():
            label = market_data.get('label', market_key)
            items = market_data.get('watchlist', [])
            lines.append(f'\n【{label}】共 {len(items)} 只')
            for item in items[:10]:
                lines.append(f"  {item.get('code')} - {item.get('name')}")
            if len(items) > 10:
                lines.append(f'  ... 等共 {len(items)} 只')
        return '\n'.join(lines)
