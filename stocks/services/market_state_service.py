from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from stocks.logging_utils import log_event
from stocks.services.market_data_service import MarketDataService
from stocks.services.news_input_service import NewsInputService

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / 'data' / 'market_state.json'


class MarketStateService:
    def __init__(
        self,
        market_data_service: MarketDataService | None = None,
        news_service: NewsInputService | None = None,
        data_path: Path | None = None,
    ):
        self.market_data_service = market_data_service or MarketDataService()
        self.news_service = news_service or NewsInputService()
        self.data_path = data_path or DATA_PATH

    def refresh(self) -> dict:
        quotes_payload = self.market_data_service.refresh()
        return self.build_from_payload(quotes_payload)

    def build_from_payload(self, quotes_payload: dict | None = None) -> dict:
        quotes_payload = quotes_payload or self.market_data_service.load()
        groups = quotes_payload.get('groups') or {}
        news_items = self.news_service.latest_items(limit=10)

        us_risk = self._valid_items(groups.get('risk_assets_us'))
        safe_haven = self._valid_items(groups.get('safe_haven'))
        rates = self._valid_items(groups.get('rates'))
        china = self._valid_items(groups.get('china_equity'))
        hk_proxy = self._valid_items(groups.get('hk_china_tech_proxy'))
        user_key_assets = self._valid_items(groups.get('user_key_assets'))

        payload = {
            'schema_version': 1,
            'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'risk_appetite': self._risk_appetite_state(us_risk, safe_haven, news_items),
            'tech_state': self._tech_state(user_key_assets, news_items),
            'safe_haven_state': self._safe_haven_state(safe_haven, news_items),
            'china_state': self._china_state(china, hk_proxy),
            'rates_state': self._rates_state(rates),
            'cross_asset_summary': self._cross_asset_summary(us_risk, safe_haven, china, rates, hk_proxy),
            'market_data_stats': quotes_payload.get('stats') or {},
        }
        self.save(payload)
        log_event('market_state.built', risk=payload['risk_appetite']['state'], tech=payload['tech_state']['state'])
        return payload

    def load(self) -> dict:
        if not self.data_path.exists():
            return {
                'schema_version': 1,
                'updated_at': None,
            }
        with open(self.data_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def save(self, payload: dict) -> None:
        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.data_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write('\n')

    def _valid_items(self, items: list[dict] | None) -> list[dict]:
        return [item for item in (items or []) if not item.get('error') and item.get('pct_change') is not None]

    def _avg_pct(self, items: list[dict]) -> float | None:
        if not items:
            return None
        values = [float(item['pct_change']) for item in items if item.get('pct_change') is not None]
        if not values:
            return None
        return round(sum(values) / len(values), 4)

    def _risk_appetite_state(self, us_risk: list[dict], safe_haven: list[dict], news_items: list[dict]) -> dict:
        us_avg = self._avg_pct(us_risk)
        gold_avg = self._avg_pct(safe_haven)
        news_text = ' '.join(f"{item.get('title','')} {item.get('summary','')}" for item in news_items).lower()
        evidence = []
        if us_avg is not None:
            evidence.append(f'风险资产均值 {us_avg}%')
        if gold_avg is not None:
            evidence.append(f'避险资产均值 {gold_avg}%')
        if 'war' in news_text or 'iran' in news_text or 'oil' in news_text:
            evidence.append('新闻存在地缘/油价扰动线索')

        if us_avg is not None and gold_avg is not None:
            if us_avg < 0 and gold_avg > 0:
                state = 'cooling'
            elif us_avg > 0 and gold_avg <= 0:
                state = 'risk_on'
            elif us_avg < 0 and gold_avg < 0:
                state = 'broad_risk_off'
            else:
                state = 'mixed'
        else:
            state = 'unknown'
        return {'state': state, 'evidence': evidence}

    def _tech_state(self, user_key_assets: list[dict], news_items: list[dict]) -> dict:
        tech_items = [item for item in user_key_assets if str(item.get('code') or '').upper() in ('NVDA', 'AAPL', 'MSFT', 'MSTR')]
        avg = self._avg_pct(tech_items)
        evidence = []
        if tech_items:
            evidence.extend([f"{item.get('code')} {item.get('pct_change')}%" for item in tech_items[:4]])
        news_text = ' '.join(f"{item.get('title','')} {item.get('summary','')}" for item in news_items).lower()
        if 'apple' in news_text or 'nvidia' in news_text or 'nasdaq' in news_text or 'tech' in news_text:
            evidence.append('新闻仍命中科技主线关键词')

        if avg is None:
            state = 'unknown'
        elif avg <= -1.0:
            state = 'under_pressure'
        elif avg < 0:
            state = 'soft'
        elif avg >= 1.0:
            state = 'expanding'
        else:
            state = 'mixed'
        return {'state': state, 'evidence': evidence}

    def _safe_haven_state(self, safe_haven: list[dict], news_items: list[dict]) -> dict:
        avg = self._avg_pct(safe_haven)
        evidence = []
        if safe_haven:
            evidence.extend([f"{item.get('code') or item.get('name')} {item.get('pct_change')}%" for item in safe_haven[:3]])
        news_text = ' '.join(f"{item.get('title','')} {item.get('summary','')}" for item in news_items).lower()
        if 'gold' in news_text or '避险' in news_text or 'oil' in news_text:
            evidence.append('新闻存在黄金/避险相关线索')

        if avg is None:
            state = 'unknown'
        elif avg > 0.5:
            state = 'strengthening'
        elif avg >= 0:
            state = 'supported'
        else:
            state = 'weakening'
        return {'state': state, 'evidence': evidence}

    def _china_state(self, china: list[dict], hk_proxy: list[dict]) -> dict:
        china_avg = self._avg_pct(china)
        hk_avg = self._avg_pct(hk_proxy)
        evidence = []
        if china:
            evidence.extend([f"{item.get('code')} {item.get('pct_change')}%" for item in china[:4]])
        if hk_proxy:
            evidence.extend([f"{item.get('code')} {item.get('pct_change')}%" for item in hk_proxy[:2]])

        if china_avg is None:
            state = 'unknown'
        elif china_avg > 0.3:
            state = 'stable_positive'
        elif china_avg >= 0:
            state = 'stable'
        elif hk_avg is not None and hk_avg < china_avg:
            state = 'mixed_pressure'
        else:
            state = 'under_pressure'
        return {'state': state, 'evidence': evidence}

    def _rates_state(self, rates: list[dict]) -> dict:
        avg = self._avg_pct(rates)
        evidence = []
        if rates:
            evidence.extend([f"{item.get('code')} {item.get('pct_change')}%" for item in rates[:2]])
        if avg is None:
            state = 'unknown'
        elif avg > 0.2:
            state = 'bonds_bid'
        elif avg < -0.2:
            state = 'rates_pressure'
        else:
            state = 'neutral'
        return {'state': state, 'evidence': evidence}

    def _cross_asset_summary(self, us_risk: list[dict], safe_haven: list[dict], china: list[dict], rates: list[dict], hk_proxy: list[dict]) -> list[str]:
        lines = []
        us_avg = self._avg_pct(us_risk)
        gold_avg = self._avg_pct(safe_haven)
        china_avg = self._avg_pct(china)
        rates_avg = self._avg_pct(rates)
        hk_avg = self._avg_pct(hk_proxy)

        if us_avg is not None and gold_avg is not None and us_avg < 0 and gold_avg > 0:
            lines.append('风险资产承压而黄金偏强，市场更像防守而不是全面 risk-on')
        if china_avg is not None and us_avg is not None and china_avg >= 0 > us_avg:
            lines.append('中国宽基没有跟随美股风险资产同步转弱，更像稳住承接')
        if hk_avg is not None and hk_avg < 0 and china_avg is not None and china_avg >= 0:
            lines.append('港股/中概代理偏弱，但A股宽基仍稳，说明中国风险资产内部也在分化')
        if rates_avg is not None and rates_avg > 0:
            lines.append('债券代理偏强，说明资金对防守资产仍有需求')
        if not lines:
            lines.append('跨资产信号暂时没有形成特别清晰的单边结论，市场更像分化拉扯')
        return lines[:4]
