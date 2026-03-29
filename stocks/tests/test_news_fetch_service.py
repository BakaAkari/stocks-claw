from pathlib import Path
import sys
import tempfile
import json

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.services.news_fetch_service import NewsFetchService
from stocks.services.news_input_service import NewsInputService


class StubNewsFetchService(NewsFetchService):
    def fetch_rss(self, url: str, limit: int = 10) -> list[dict]:
        return [
            {
                'source': url,
                'title': '测试新闻一',
                'summary': '摘要一',
                'url': 'https://example.com/1',
                'published_at': '2026-03-27 10:00:00',
                'tags': [],
                'quality_flag': 'normal',
            },
            {
                'source': url,
                'title': '测试新闻二',
                'summary': '摘要二',
                'url': 'https://example.com/2',
                'published_at': '2026-03-27 09:00:00',
                'tags': [],
                'quality_flag': 'normal',
            },
        ][:limit]

    def fetch_gnews(self, query: str, limit: int = 10, lang: str = 'zh') -> list[dict]:
        return [
            {
                'source': 'GNews',
                'title': '测试GNews',
                'summary': 'GNews摘要',
                'url': 'https://example.com/g1',
                'published_at': '2026-03-27 08:00:00',
                'tags': [query],
                'quality_flag': 'normal',
            }
        ][:limit]


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as td:
        news_path = Path(td) / 'news_feed.json'
        source_path = Path(td) / 'news_sources.json'
        with open(source_path, 'w', encoding='utf-8') as f:
            json.dump(
                {
                    'sources': [
                        {'name': 'stub-rss', 'type': 'rss', 'url': 'https://example.com/rss'},
                        {'name': 'stub-gnews', 'type': 'gnews', 'query': 'gold market', 'lang': 'en'},
                    ]
                },
                f,
                ensure_ascii=False,
            )

        service = StubNewsFetchService(NewsInputService(news_path), source_path)
        payload = service.refresh(limit_per_source=2)
        assert len(payload['items']) == 3
        saved = NewsInputService(news_path).load()
        assert len(saved['items']) == 3
        assert saved['items'][0]['title'] == '测试新闻一'

    print('news fetch service ok')
