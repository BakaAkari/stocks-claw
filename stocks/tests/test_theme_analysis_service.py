from pathlib import Path
import sys
import tempfile
import json

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.services.news_input_service import NewsInputService
from stocks.services.theme_analysis_service import ThemeAnalysisService


class StubMarketSignalService:
    def validate_theme(self, theme_name):
        return {
            'validated': theme_name == '黄金',
            'summary': f'{theme_name} 已做最小行情验证',
            'signals': [
                {'asset_name': f'{theme_name}代理', 'pct_change': 1.2},
            ],
            'positives': 1,
            'negatives': 0,
        }

    def get_observation_signal(self, observation_name):
        return {
            'validated': observation_name in ('risk_on', 'us_benchmark', 'gold'),
            'summary': f'{observation_name} 已做最小行情验证',
            'signals': [
                {'asset_name': f'{observation_name}一号', 'pct_change': 1.1},
                {'asset_name': f'{observation_name}二号', 'pct_change': 0.6},
            ],
            'positives': 2,
            'negatives': 0,
        }


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as td:
        news_path = Path(td) / 'news_feed.json'
        with open(news_path, 'w', encoding='utf-8') as f:
            json.dump(
                {
                    'schema_version': 1,
                    'items': [
                        {
                            'title': '黄金价格继续走强',
                            'summary': '避险需求上升',
                            'tags': ['黄金'],
                            'published_at': '2026-03-27 10:00:00',
                        },
                        {
                            'title': 'AI 基础设施投资延续',
                            'summary': '算力链继续被关注',
                            'tags': ['AI', '算力'],
                            'published_at': '2026-03-27 09:00:00',
                        },
                    ],
                },
                f,
                ensure_ascii=False,
            )

        service = ThemeAnalysisService(
            NewsInputService(news_path),
            StubMarketSignalService(),
        )
        result = service.analyze(news_limit=10)
        assert result['hot_sector']['name'] in ('黄金', 'AI基础设施')
        assert result['hot_sector']['temperature'] == 'hot'
        assert result['hot_sector']['status'] in ('confirmed', 'watch')
        assert result['hot_sector']['news_score'] >= 1
        assert result['hot_sector']['cluster'] in ('科技链', '避险链')
        assert '最小行情验证' in result['hot_sector']['market_support']
        assert 'market_validation' in result['hot_sector']
        assert result['cold_sector']['temperature'] == 'cold'
        assert result['cold_sector']['status'] == 'none'
        assert 'watch_themes' in result
        assert all(item.get('cluster') != result['hot_sector'].get('cluster') for item in result['watch_themes'] if item.get('cluster'))
        assert result['cluster_scores']
        assert result['cluster_scores'][0]['cluster'] in ('科技链', '避险链')
        assert 'market_observations' in result
        assert 'risk_appetite' in result['market_observations']
        assert 'us_market' in result['market_observations']

    print('theme analysis service ok')
