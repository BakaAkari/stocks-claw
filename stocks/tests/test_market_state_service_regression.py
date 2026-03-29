import tempfile
import unittest
from pathlib import Path

from stocks.services.market_state_service import MarketStateService


class StubNewsService:
    def latest_items(self, limit=10):
        return [
            {
                'title': 'Gold rises as oil and geopolitical worries persist',
                'summary': 'Nvidia and Apple remain under pressure while gold stays bid',
            }
        ]


class MarketStateServiceRegressionTest(unittest.TestCase):
    def test_build_from_payload_generates_expected_states(self):
        with tempfile.TemporaryDirectory() as td:
            service = MarketStateService(news_service=StubNewsService(), data_path=Path(td) / 'market_state.json')
            payload = {
                'groups': {
                    'risk_assets_us': [
                        {'code': 'QQQ', 'pct_change': -2.1},
                        {'code': 'SPY', 'pct_change': -1.2},
                    ],
                    'safe_haven': [
                        {'code': 'GLD', 'pct_change': 1.3},
                        {'code': 'IAU', 'pct_change': 1.0},
                    ],
                    'rates': [
                        {'code': 'IEF', 'pct_change': 0.4},
                        {'code': 'TLT', 'pct_change': 0.6},
                    ],
                    'china_equity': [
                        {'code': '510300', 'pct_change': 0.2},
                        {'code': '510050', 'pct_change': 0.4},
                    ],
                    'hk_china_tech_proxy': [
                        {'code': 'KWEB', 'pct_change': -1.1},
                    ],
                    'user_key_assets': [
                        {'code': 'NVDA', 'pct_change': -2.6},
                        {'code': 'AAPL', 'pct_change': -1.3},
                        {'code': 'MSTR', 'pct_change': -3.1},
                    ],
                },
                'stats': {
                    'total_targets': 11,
                    'successful_targets': 11,
                    'failed_targets': 0,
                },
            }
            result = service.build_from_payload(payload)

            self.assertEqual(result['risk_appetite']['state'], 'cooling')
            self.assertEqual(result['tech_state']['state'], 'under_pressure')
            self.assertEqual(result['safe_haven_state']['state'], 'strengthening')
            self.assertEqual(result['china_state']['state'], 'stable')
            self.assertEqual(result['rates_state']['state'], 'bonds_bid')
            self.assertTrue(any('风险资产承压而黄金偏强' in x for x in result['cross_asset_summary']))
            self.assertEqual(result['market_data_stats']['successful_targets'], 11)


if __name__ == '__main__':
    unittest.main()
