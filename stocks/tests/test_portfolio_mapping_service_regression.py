import unittest

from stocks.services.portfolio_mapping_service import PortfolioMappingService


class PortfolioMappingServiceRegressionTest(unittest.TestCase):
    def test_build_maps_assets_into_expected_buckets_and_pressures(self):
        service = PortfolioMappingService()
        memory = {
            'assets': [
                {'asset_name': '建行理财', 'asset_type': '理财', 'amount': 200000, 'notes': '稳健理财'},
                {'asset_name': '黄金积存', 'asset_type': '黄金', 'amount': 266000, 'notes': '黄金均价约1060 当前亏损约5万'},
                {'asset_name': '华安黄金ETF', 'asset_type': '黄金ETF', 'amount': 102000, 'notes': 'ETF盈利中'},
                {'asset_name': '广发纳斯达克100ETF', 'asset_type': 'ETF', 'amount': 50000, 'notes': '纳指'},
                {'asset_name': '信息产业基金', 'asset_type': '基金', 'amount': 18000, 'notes': '美股科技映射'},
                {'asset_name': 'NVDA', 'asset_type': '股票', 'amount': 28000, 'platform': 'IBKR'},
                {'asset_name': '游戏ETF', 'asset_type': 'ETF', 'amount': 5000, 'notes': '行业ETF'},
                {'asset_name': '紫金矿业', 'asset_type': '股票', 'amount': 12000, 'notes': '资源'},
                {'asset_name': 'USD 分红险', 'asset_type': '保险', 'amount': 50000, 'notes': '5年期分红险 当前第一年'},
                {'asset_name': 'USD', 'asset_type': '现金', 'amount': 20000, 'notes': 'Buying Power'},
            ],
            'portfolio_profile_notes': {
                'investment_preference': '稳健，更偏成长'
            },
        }
        market_state = {
            'risk_appetite': {'state': 'cooling'},
            'tech_state': {'state': 'under_pressure'},
            'safe_haven_state': {'state': 'strengthening'},
            'china_state': {'state': 'stable'},
        }

        result = service.build(memory, market_state)

        structure = result['portfolio_structure']
        impact = result['market_impact']
        pressure = result['pressure_map']

        self.assertIn('defense', structure['dominant_layers'])
        self.assertIn('gold_buffer', structure['dominant_layers'])
        self.assertEqual(structure['buffer_strength'], 'strong')
        self.assertEqual(structure['growth_exposure'], 'moderate')
        self.assertTrue(structure['locked_assets_present'])

        self.assertIn('黄金积存', impact['beneficiaries'])
        self.assertIn('华安黄金ETF', impact['buffers'])
        self.assertIn('广发纳斯达克100ETF', impact['under_pressure'])
        self.assertIn('NVDA', impact['under_pressure'])
        self.assertTrue(any('A股宽基相对稳' in x for x in impact['notes']))

        self.assertEqual(pressure['gold_buffer'], 'benefiting')
        self.assertEqual(pressure['us_growth'], 'under_pressure')
        self.assertEqual(pressure['defense'], 'supportive')
        self.assertEqual(pressure['liquidity'], 'supportive')
        self.assertEqual(pressure['locked'], 'long_horizon')
        self.assertTrue(any('稳健偏成长' in x for x in result['interpretation']))


if __name__ == '__main__':
    unittest.main()
