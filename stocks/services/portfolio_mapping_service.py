from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from stocks.logging_utils import log_event
from stocks.services.financial_memory_service import FinancialMemoryService
from stocks.services.market_state_service import MarketStateService

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / 'data' / 'portfolio_mapping.json'


class PortfolioMappingService:
    def __init__(
        self,
        memory_service: FinancialMemoryService | None = None,
        market_state_service: MarketStateService | None = None,
        data_path: Path | None = None,
    ):
        self.memory_service = memory_service or FinancialMemoryService()
        self.market_state_service = market_state_service or MarketStateService()
        self.data_path = data_path or DATA_PATH

    def refresh(self) -> dict:
        memory = self.memory_service.load()
        market_state = self.market_state_service.load()
        payload = self.build(memory, market_state)
        self.save(payload)
        log_event('portfolio_mapping.built', dominant=payload['portfolio_structure']['dominant_layers'])
        return payload

    def build(self, memory: dict, market_state: dict) -> dict:
        assets = memory.get('assets') or []
        profile = memory.get('portfolio_profile_notes') or {}

        buckets = {
            'defense': [],
            'gold_buffer': [],
            'us_growth': [],
            'us_diversified': [],
            'china_thematic': [],
            'china_resource': [],
            'locked': [],
            'liquidity': [],
            'other': [],
        }
        bucket_amounts = {key: 0.0 for key in buckets}
        asset_details = []

        for asset in assets:
            bucket, tag = self._classify_asset(asset)
            buckets[bucket].append(asset)
            bucket_amounts[bucket] += self._safe_amount(asset.get('amount'))
            asset_details.append({
                'asset_name': asset.get('asset_name'),
                'bucket': bucket,
                'tag': tag,
                'amount': self._safe_amount(asset.get('amount')),
                'notes': asset.get('notes'),
            })

        total = sum(bucket_amounts.values()) or 1.0
        ratios = {k: round(v / total, 4) for k, v in bucket_amounts.items()}
        dominant = [k for k, _ in sorted(bucket_amounts.items(), key=lambda x: x[1], reverse=True) if bucket_amounts[k] > 0][:3]

        market_impact = self._market_impact(buckets, market_state)
        interpretation = self._interpretation(bucket_amounts, ratios, market_state, profile)
        pressure_map = self._pressure_map(buckets, market_state)

        return {
            'schema_version': 1,
            'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'portfolio_structure': {
                'dominant_layers': dominant,
                'bucket_amounts': bucket_amounts,
                'bucket_ratios': ratios,
                'growth_exposure': self._growth_label(ratios.get('us_growth', 0.0) + ratios.get('us_diversified', 0.0)),
                'buffer_strength': self._buffer_label(ratios.get('gold_buffer', 0.0)),
                'liquidity_status': self._liquidity_label(ratios.get('liquidity', 0.0)),
                'locked_assets_present': bool(buckets['locked']),
            },
            'market_impact': market_impact,
            'pressure_map': pressure_map,
            'interpretation': interpretation,
            'profile_reference': profile,
            'asset_names_by_bucket': {key: [item.get('asset_name') for item in value] for key, value in buckets.items()},
            'asset_details': asset_details,
        }

    def load(self) -> dict:
        if not self.data_path.exists():
            return {'schema_version': 1, 'updated_at': None}
        with open(self.data_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def save(self, payload: dict) -> None:
        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.data_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write('\n')

    def _classify_asset(self, asset: dict) -> tuple[str, str]:
        text = ' '.join([
            str(asset.get('asset_name') or ''),
            str(asset.get('asset_type') or ''),
            str(asset.get('notes') or ''),
            str(asset.get('platform') or ''),
        ]).lower()
        name = str(asset.get('asset_name') or '')

        if any(k in text for k in ('现金', '流动性', 'buying power', '现金管理', '货币')):
            return 'liquidity', '机动资金'
        if any(k in text for k in ('分红险', '寿险', '5年期', '长期锁定')):
            return 'locked', '长期锁定'
        if any(k in text for k in ('黄金', 'gold', '黄金etf', '贵金属')):
            if 'etf' in text:
                return 'gold_buffer', '黄金ETF缓冲'
            return 'gold_buffer', '黄金积存缓冲'
        if any(k in text for k in ('纳指', '美股科技', 'aapl', 'nvda', 'mstr', '信息产业')):
            return 'us_growth', '美股成长'
        if any(k in text for k in ('baba', '中概股', '美股金融', 'gs', 'unh')):
            return 'us_diversified', '美股非纯科技分散'
        if any(k in text for k in ('紫金矿业', '稀有金属')):
            return 'china_resource', '资源链'
        if any(k in text for k in ('游戏etf', '行业etf', '股票etf', 'a股个股')):
            return 'china_thematic', 'A股主题'
        if any(k in text for k in ('理财', '固定收益', '固收', '稳健理财')):
            return 'defense', '防守底仓'
        if name:
            return 'other', '未归类'
        return 'other', '未归类'

    def _market_impact(self, buckets: dict[str, list[dict]], market_state: dict) -> dict:
        beneficiaries = []
        under_pressure = []
        buffers = []
        likely_to_miss_upside = []
        notes = []

        risk_state = (market_state.get('risk_appetite') or {}).get('state')
        tech_state = (market_state.get('tech_state') or {}).get('state')
        safe_state = (market_state.get('safe_haven_state') or {}).get('state')
        china_state = (market_state.get('china_state') or {}).get('state')

        if safe_state in ('strengthening', 'supported'):
            beneficiaries.extend(self._names(buckets['gold_buffer']))
            buffers.extend(self._names(buckets['gold_buffer']))
            notes.append('黄金缓冲层当前确实在工作，不是摆设')

        if risk_state in ('cooling', 'broad_risk_off') or tech_state in ('under_pressure', 'soft'):
            under_pressure.extend(self._names(buckets['us_growth']))
            under_pressure.extend(self._names(buckets['us_diversified']))
            beneficiaries.extend(self._names(buckets['defense']))
            beneficiaries.extend(self._names(buckets['liquidity']))
            notes.append('风险偏好降温时，防守底仓和机动资金更舒服，成长层更容易先承压')

        if china_state in ('stable', 'stable_positive'):
            notes.append('A股宽基相对稳，但你手里主要是主题/资源，不等于自动享受宽基稳住')

        if buckets['us_growth'] and (risk_state in ('cooling', 'broad_risk_off') or tech_state == 'under_pressure'):
            likely_to_miss_upside.append('若科技主线快速反弹，美股成长层能跟，但整体弹性未必特别猛')

        if buckets['locked']:
            notes.append('长期锁定资产不是今天市场波动的响应器，别和高流动仓位混着看')

        return {
            'beneficiaries': self._dedupe(beneficiaries)[:10],
            'under_pressure': self._dedupe(under_pressure)[:10],
            'buffers': self._dedupe(buffers)[:6],
            'likely_to_miss_upside': self._dedupe(likely_to_miss_upside)[:4],
            'notes': notes[:8],
        }

    def _pressure_map(self, buckets: dict[str, list[dict]], market_state: dict) -> dict:
        risk_state = (market_state.get('risk_appetite') or {}).get('state')
        tech_state = (market_state.get('tech_state') or {}).get('state')
        safe_state = (market_state.get('safe_haven_state') or {}).get('state')

        return {
            'us_growth': self._bucket_pressure_label('us_growth', risk_state, tech_state, safe_state),
            'us_diversified': self._bucket_pressure_label('us_diversified', risk_state, tech_state, safe_state),
            'gold_buffer': self._bucket_pressure_label('gold_buffer', risk_state, tech_state, safe_state),
            'china_thematic': self._bucket_pressure_label('china_thematic', risk_state, tech_state, safe_state),
            'china_resource': self._bucket_pressure_label('china_resource', risk_state, tech_state, safe_state),
            'defense': self._bucket_pressure_label('defense', risk_state, tech_state, safe_state),
            'liquidity': self._bucket_pressure_label('liquidity', risk_state, tech_state, safe_state),
            'locked': 'long_horizon',
        }

    def _bucket_pressure_label(self, bucket: str, risk_state: str | None, tech_state: str | None, safe_state: str | None) -> str:
        if bucket == 'gold_buffer':
            if safe_state in ('strengthening', 'supported'):
                return 'benefiting'
            return 'neutral'
        if bucket in ('defense', 'liquidity'):
            if risk_state in ('cooling', 'broad_risk_off'):
                return 'supportive'
            return 'neutral'
        if bucket == 'us_growth':
            if tech_state in ('under_pressure', 'soft'):
                return 'under_pressure'
            return 'sensitive_to_risk_on'
        if bucket == 'us_diversified':
            if risk_state in ('cooling', 'broad_risk_off'):
                return 'mild_pressure'
            return 'mixed'
        if bucket in ('china_thematic', 'china_resource'):
            if risk_state in ('cooling', 'broad_risk_off'):
                return 'mixed'
            return 'neutral'
        return 'neutral'

    def _interpretation(self, bucket_amounts: dict[str, float], ratios: dict[str, float], market_state: dict, profile: dict) -> list[str]:
        lines = []
        risk_state = (market_state.get('risk_appetite') or {}).get('state')
        tech_state = (market_state.get('tech_state') or {}).get('state')
        safe_state = (market_state.get('safe_haven_state') or {}).get('state')

        if ratios.get('defense', 0) + ratios.get('liquidity', 0) >= 0.35:
            lines.append('防守底仓和机动资金偏厚，组合先天更擅长扛波动，而不是抢最猛的进攻段')
        if ratios.get('gold_buffer', 0) >= 0.25 and safe_state in ('strengthening', 'supported'):
            lines.append('黄金层当前不是拖累，而是有效缓冲；只是黄金内部工具与成本差异很大，体验不会完全一致')
        if ratios.get('us_growth', 0) > 0 and tech_state in ('under_pressure', 'soft'):
            lines.append('美股成长层保留了主线暴露，但在当前环境里更像波动来源，不像效率来源')
        if ratios.get('us_growth', 0) + ratios.get('us_diversified', 0) <= 0.15:
            lines.append('成长暴露有，但总量不重；若市场迅速转强，你更像稳稳跟，而不是领跑')
        if ratios.get('china_thematic', 0) + ratios.get('china_resource', 0) > 0:
            lines.append('A股这部分更偏主题/资源，而不是纯宽基，所以“市场稳住”不等于你的A股仓位就会特别舒服')
        if ratios.get('locked', 0) > 0:
            lines.append('长期锁定资产应该单独看时间维度，它提供的是结构稳定性，不是短线响应速度')
        if '稳健' in str(profile.get('investment_preference') or ''):
            lines.append('对“稳健偏成长”的目标来说，这套结构当前是匹配的，关键是接受它天然更重平衡而不是爆发')
        if risk_state == 'cooling':
            lines.append('当前更值得盯的是成长层何时止压，而不是急着怀疑整套组合逻辑')
        return lines[:8]

    def _safe_amount(self, value) -> float:
        try:
            return float(value)
        except Exception:
            return 0.0

    def _names(self, items: list[dict]) -> list[str]:
        return [str(item.get('asset_name') or '未知资产') for item in items]

    def _dedupe(self, items: list[str]) -> list[str]:
        out = []
        seen = set()
        for item in items:
            if item not in seen:
                out.append(item)
                seen.add(item)
        return out

    def _growth_label(self, ratio: float) -> str:
        if ratio >= 0.25:
            return 'high'
        if ratio >= 0.12:
            return 'moderate'
        if ratio > 0:
            return 'light'
        return 'none'

    def _buffer_label(self, ratio: float) -> str:
        if ratio >= 0.3:
            return 'strong'
        if ratio >= 0.15:
            return 'moderate'
        if ratio > 0:
            return 'light'
        return 'none'

    def _liquidity_label(self, ratio: float) -> str:
        if ratio >= 0.1:
            return 'ample'
        if ratio > 0:
            return 'adequate'
        return 'thin'
