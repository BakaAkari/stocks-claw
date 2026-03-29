from pathlib import Path
import sys
import tempfile
import json

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.services.news_input_service import NewsInputService


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 'news_feed.json'
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(
                {
                    'schema_version': 1,
                    'items': [
                        {
                            'title': '旧新闻',
                            'summary': 'A',
                            'published_at': '2026-03-27 09:00:00',
                        },
                        {
                            'title': '新新闻',
                            'summary': 'B',
                            'published_at': '2026-03-27 10:00:00',
                        },
                    ],
                },
                f,
                ensure_ascii=False,
            )

        service = NewsInputService(path)
        items = service.list_items()
        assert len(items) == 2
        latest = service.latest_items(limit=1)
        assert len(latest) == 1
        assert latest[0]['title'] == '新新闻'

    print('news input service ok')
