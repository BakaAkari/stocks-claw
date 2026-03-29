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
                            'amount': 120000,
                            'asset_type': 'etf',
                        }
                    ],
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
                            'title': '黄金主题升温',
                            'summary': '避险情绪带动黄金关注度上升',
                            'published_at': '2026-03-27 10:00:00',
                        }
                    ],
                },
                f,
                ensure_ascii=False,
            )

        service = PersonalInsightService(
            FinancialMemoryService(memory_path),
            NewsInputService(news_path),
        )
        ctx = service.build_context(news_limit=3)
        assert ctx['financial_memory']['asset_count'] == 1
        assert ctx['news_input']['count'] == 1
        text = service.render_prompt_input(news_limit=3)
        assert '黄金ETF' in text
        assert '黄金主题升温' in text

    print('personal insight service ok')
