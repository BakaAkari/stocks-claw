from pathlib import Path
import sys
import tempfile
import json

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.services.financial_memory_service import FinancialMemoryService
from stocks.services.news_input_service import NewsInputService
from stocks.services.personal_insight_service import PersonalInsightService
from stocks.services.report_assembly_service import ReportAssemblyService
from stocks.services.theme_analysis_service import ThemeAnalysisService


class StubMarketSignalService:
    def validate_theme(self, theme_name):
        return {
            'validated': True,
            'summary': f'{theme_name} 已接入最小行情验证',
            'signals': [
                {'asset_name': f'{theme_name}一号', 'code': '000001', 'pct_change': 1.23},
                {'asset_name': f'{theme_name}二号', 'code': '000002', 'pct_change': 0.88},
                {'asset_name': f'{theme_name}三号', 'code': '000003', 'pct_change': -0.12},
            ],
            'positives': 2,
            'negatives': 1,
        }

    def get_observation_signal(self, observation_name):
        return {
            'validated': observation_name != 'china_benchmark',
            'summary': f'{observation_name} 已接入最小行情验证',
            'signals': [
                {'asset_name': f'{observation_name}一号', 'code': '100001', 'pct_change': 1.05},
                {'asset_name': f'{observation_name}二号', 'code': '100002', 'pct_change': 0.72},
                {'asset_name': f'{observation_name}三号', 'code': '100003', 'pct_change': -0.18},
            ],
            'positives': 2,
            'negatives': 1,
        }


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as td:
        memory_path = Path(td) / 'financial_assets.json'
        news_path = Path(td) / 'news_feed.json'

        with open(memory_path, 'w', encoding='utf-8') as f:
            json.dump(
                {
                    'schema_version': 1,
                    'assets': [
                        {
                            'asset_name': '黄金ETF',
                            'platform': '华泰',
                            'amount': 100000,
                            'asset_type': '黄金',
                            'notes': '防守仓位',
                        }
                    ],
                    'portfolio_profile_notes': {
                        'investment_preference': '稳健，更偏成长'
                    },
                },
                f,
                ensure_ascii=False,
            )

        with open(news_path, 'w', encoding='utf-8') as f:
            json.dump(
                {
                    'schema_version': 1,
                    'items': [
                        {
                            'title': '黄金价格继续走强',
                            'summary': '避险需求上升，黄金资产继续受关注',
                            'tags': ['黄金'],
                            'published_at': '2026-03-27 10:00:00',
                        }
                    ],
                },
                f,
                ensure_ascii=False,
            )

        report_service = ReportAssemblyService(
            PersonalInsightService(
                FinancialMemoryService(memory_path),
                NewsInputService(news_path),
            ),
            ThemeAnalysisService(
                NewsInputService(news_path),
                StubMarketSignalService(),
            ),
        )
        report = report_service.build()
        assert 'overview' in report
        assert report['hot_sector']['temperature'] == 'hot'
        assert report['hot_sector']['status'] in ('confirmed', 'watch')
        assert '已接入最小行情验证' in report['hot_sector']['market_support']
        assert 'watch_themes' in report
        assert 'market_observations' in report
        assert 'portfolio_roles' in report
        assert 'portfolio_health' in report
        assert 'recent_snapshots' in report
        assert len(report['user_relevance']) >= 1
        assert len(report['risk_notes']) >= 3
        assert any(('黄金' in line or '避险' in line) for line in report['user_relevance'])
        text = report_service.render_text()
        assert '今日概览' in text
        assert '风险偏好' in text
        assert '美股观察' in text
        assert '黄金观察' in text
        assert '中国市场观察' in text
        assert '与你资产相关' in text
        assert '最近几版快照' in text or report['recent_snapshots'] == []

    print('report assembly service ok')
