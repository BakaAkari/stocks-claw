import unittest

from stocks.services.constraint_chat_service import ConstraintChatService


class ConstraintChatServiceNormalizationTest(unittest.TestCase):
    def setUp(self):
        self.service = ConstraintChatService()

    def test_normalize_bucket_ranges_from_list_to_dict(self):
        updates = {
            'target_bucket_ranges': {
                '成长': [0, 30],
                '黄金': {'max': 25},
                '防守层': {'min': 20, 'max': 40},
            },
            'max_drawdown_tolerance': 15,
            'tactical_budget_ratio': 10,
        }
        normalized = self.service._normalize_updates(updates)
        self.assertEqual(normalized['target_bucket_ranges']['growth_total']['min'], 0.0)
        self.assertEqual(normalized['target_bucket_ranges']['growth_total']['max'], 0.3)
        self.assertEqual(normalized['target_bucket_ranges']['gold_buffer']['max'], 0.25)
        self.assertEqual(normalized['target_bucket_ranges']['defense']['min'], 0.2)
        self.assertEqual(normalized['max_drawdown_tolerance'], 0.15)
        self.assertEqual(normalized['tactical_budget_ratio'], 0.1)

    def test_reject_non_dict_updates(self):
        self.assertEqual(self.service._normalize_updates([]), {})
        self.assertEqual(self.service._normalize_updates('x'), {})


if __name__ == '__main__':
    unittest.main()
