from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from stocks.domain.models import Quote
from stocks.logging_utils import log_event
from stocks.services.query_service import QueryService
from stocks.services.watchlist_service import WatchlistService

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / 'data' / 'market_quotes.json'


class MarketDataService:
    def __init__(self, query_service: QueryService | None = None, data_path: Path | None = None, watchlist_service: WatchlistService | None = None):
        self.query_service = query_service or QueryService()
        self.data_path = data_path or DATA_PATH
        self.watchlist_service = watchlist_service or WatchlistService()

    def refresh(self) -> dict:
        groups: dict[str, list[dict]] = {}
        total = 0
        ok = 0
        targets_by_group = self.watchlist_service.build_market_targets()
        for group, targets in targets_by_group.items():
            items = []
            for market_key, keyword in targets:
                total += 1
                try:
                    quote = self.query_service.query(market_key, keyword)
                    items.append(self._quote_to_dict(quote))
                    ok += 1
                except Exception as e:
                    items.append({
                        'market': market_key,
                        'keyword': keyword,
                        'error': str(e),
                    })
                    log_event('market_data.quote_failed', group=group, market=market_key, keyword=keyword, error=str(e))
            groups[group] = items

        payload = {
            'schema_version': 1,
            'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'groups': groups,
            'stats': {
                'total_targets': total,
                'successful_targets': ok,
                'failed_targets': total - ok,
            },
        }
        self.save(payload)
        log_event('market_data.refreshed', total=total, ok=ok, failed=total - ok)
        return payload

    def load(self) -> dict:
        if not self.data_path.exists():
            return {
                'schema_version': 1,
                'updated_at': None,
                'groups': {},
                'stats': {'total_targets': 0, 'successful_targets': 0, 'failed_targets': 0},
            }
        with open(self.data_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def save(self, payload: dict) -> None:
        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.data_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write('\n')

    def _quote_to_dict(self, quote: Quote) -> dict:
        return {
            'market': quote.instrument.market,
            'code': quote.instrument.code,
            'name': quote.instrument.name,
            'exchange': quote.instrument.exchange,
            'price': quote.price,
            'change': quote.change,
            'pct_change': quote.pct_change,
            'open_price': quote.open_price,
            'high': quote.high,
            'low': quote.low,
            'prev_close': quote.prev_close,
            'volume_lot': quote.volume_lot,
            'amount_10k': quote.amount_10k,
        }
