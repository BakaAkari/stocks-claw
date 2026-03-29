import unittest

from stocks.services.advisory_service import AdvisoryService
from stocks.services.financial_memory_service import FinancialMemoryService


class AdvisoryServiceTest(unittest.TestCase):
    def setUp(self):
        self.service = AdvisoryService()

    def test_build_outputs_non_command_advice(self):
        memory = {
            'portfolio_profile_notes': {
                'investment_preference': '稳健，更偏成长',
            },
            'assets': [],
        }
        market_state = {
            'risk_appetite': {'state': 'cooling'},
            'tech_state': {'state': 'under_pressure'},
            'safe_haven_state': {'state': 'strengthening'},
            'china_state': {'state': 'stable'},
        }
        portfolio_mapping = {
            'portfolio_structure': {
                'dominant_layers': ['gold_buffer', 'us_growth', 'defense'],
                'bucket_ratios': {
                    'gold_buffer': 0.31,
                    'us_growth': 0.22,
                    'us_diversified': 0.08,
                    'defense': 0.12,
                    'liquidity': 0.03,
                    'china_thematic': 0.10,
                    'china_resource': 0.06,
                },
                'growth_exposure': 'high',
                'buffer_strength': 'strong',
                'liquidity_status': 'thin',
            },
            'market_impact': {
                'notes': ['黄金缓冲层当前确实在工作，不是摆设'],
            },
            'pressure_map': {
                'us_growth': 'under_pressure',
                'gold_buffer': 'benefiting',
                'defense': 'supportive',
                'liquidity': 'supportive',
            },
        }

        result = self.service.build(memory, market_state, portfolio_mapping)

        self.assertEqual(result['posture']['action_bias'], 'conditional_rebalance_candidate')
        self.assertTrue(result['allocation_advice'])
        self.assertTrue(result['conditional_recommendations'])
        self.assertTrue(result['drift_checks'])
        self.assertIn('不输出明确买卖指令', ''.join(result['boundaries']))
        self.assertTrue(any(item['kind'] == 'drift_check' for item in result['allocation_advice']))
        self.assertTrue(any(item['status'] == 'active' for item in result['conditional_recommendations']))

    def test_build_prefers_user_defined_constraints_when_present(self):
        memory = {
            'portfolio_profile_notes': {
                'investment_preference': '稳健，更偏成长',
            },
            'portfolio_constraints': {
                'target_bucket_ranges': {
                    'growth_total': {'min': 0.08, 'max': 0.20},
                    'liquidity': {'min': 0.08, 'max': 0.20},
                },
                'notes': ['用户已确认目标区间'],
            },
            'assets': [],
        }
        market_state = {
            'risk_appetite': {'state': 'cooling'},
            'tech_state': {'state': 'under_pressure'},
            'safe_haven_state': {'state': 'neutral'},
            'china_state': {'state': 'neutral'},
        }
        portfolio_mapping = {
            'portfolio_structure': {
                'dominant_layers': ['us_growth', 'gold_buffer'],
                'bucket_ratios': {
                    'gold_buffer': 0.10,
                    'us_growth': 0.24,
                    'us_diversified': 0.05,
                    'defense': 0.18,
                    'liquidity': 0.03,
                },
                'growth_exposure': 'high',
                'buffer_strength': 'light',
                'liquidity_status': 'thin',
            },
            'market_impact': {'notes': []},
            'pressure_map': {},
        }

        result = self.service.build(memory, market_state, portfolio_mapping)

        self.assertEqual(result['constraint_policy']['source'], 'user_defined')
        self.assertTrue(any(item['bucket'] == 'growth_total' and item['status'] == 'above_max' for item in result['drift_checks']))
        self.assertTrue(any(item['bucket'] == 'liquidity' and item['status'] == 'below_min' for item in result['drift_checks']))


class FinancialMemoryConstraintsTest(unittest.TestCase):
    """测试约束系统的读写功能"""
    
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.temp_dir = tempfile.mkdtemp()
        self.memory_path = Path(self.temp_dir) / 'test_financial_assets.json'
        self.service = FinancialMemoryService(path=self.memory_path)
    
    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_load_constraints_returns_empty_dict_when_not_set(self):
        """当约束未设置时，应返回空字典"""
        constraints = self.service.load_constraints()
        self.assertIsInstance(constraints, dict)
        self.assertEqual(constraints, {})
    
    def test_save_and_load_constraints(self):
        """测试约束的保存和读取"""
        test_constraints = {
            'schema_version': 1,
            'target_bucket_ranges': {
                'growth_total': {'min': 0.10, 'max': 0.30, 'rationale': '稳健偏成长'},
                'gold_buffer': {'min': 0.10, 'max': 0.25}
            },
            'locked_assets': ['香港中国银行5年期寿险'],
            'max_drawdown_tolerance': 0.20,
            'allow_stop_loss': False
        }
        
        self.service.save_constraints(test_constraints)
        loaded = self.service.load_constraints()
        
        self.assertEqual(loaded['target_bucket_ranges']['growth_total']['max'], 0.30)
        self.assertEqual(loaded['locked_assets'], ['香港中国银行5年期寿险'])
        self.assertEqual(loaded['max_drawdown_tolerance'], 0.20)
    
    def test_update_constraints_partial(self):
        """测试约束的增量更新"""
        # 先保存初始约束
        initial = {'target_bucket_ranges': {'growth_total': {'min': 0.10, 'max': 0.30}}}
        self.service.save_constraints(initial)
        
        # 增量更新
        updates = {'max_drawdown_tolerance': 0.25, 'allow_stop_loss': True}
        result = self.service.update_constraints(updates)
        
        # 验证保留了旧字段，添加了新字段
        self.assertEqual(result['target_bucket_ranges']['growth_total']['max'], 0.30)
        self.assertEqual(result['max_drawdown_tolerance'], 0.25)
        self.assertEqual(result['allow_stop_loss'], True)


if __name__ == '__main__':
    unittest.main()
