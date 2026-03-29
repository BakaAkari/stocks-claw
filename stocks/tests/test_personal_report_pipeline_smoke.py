import unittest

from stocks.services.personal_llm_report_service import PersonalLLMReportService


class PersonalReportPipelineSmokeTest(unittest.TestCase):
    def test_prompt_and_feishu_formatter_reflect_new_pipeline(self):
        service = PersonalLLMReportService(model='kimi-k2.5')
        prompt = service.build_prompt()

        self.assertIn('完整金融记忆', prompt)
        self.assertIn('authoritative_full_financial_memory', prompt)
        self.assertIn('market_state', prompt)
        self.assertIn('portfolio_mapping', prompt)
        self.assertIn('advisory_plan', prompt)
        self.assertIn('结构化分析信号', prompt)

        formatted = service.format_for_feishu(
            '# 核心判断\n\n今天先看 `QQQ` 和 159915\n\n---\n\n- NVDA 承压\n- 黄金走强\n'
        )
        self.assertIn('**核心判断**', formatted)
        self.assertIn('`QQQ`', formatted)
        self.assertIn('`159915`', formatted)
        self.assertIn('---', formatted)
        self.assertIn('- `NVDA` 承压', formatted)


if __name__ == '__main__':
    unittest.main()
