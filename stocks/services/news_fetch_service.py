from __future__ import annotations

import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from stocks.errors import FinancialMemoryError
from stocks.logging_utils import log_event
from stocks.services.news_input_service import NewsInputService

ROOT = Path(__file__).resolve().parents[1]
NEWS_SOURCES_PATH = ROOT / 'config' / 'news_sources.json'
GNEWS_KEY_PATH = ROOT.parent / '.secret' / 'gnews-key.md'
JUHE_KEY_PATH = ROOT.parent / '.secret' / 'juhe-key.md'
JUHE_CAIJING_KEY_PATH = ROOT.parent / '.secret' / 'juhe-caijing-key.md'


class NewsFetchService:
    def __init__(
        self,
        news_service: NewsInputService | None = None,
        sources_path: Path | None = None,
    ):
        self.news_service = news_service or NewsInputService()
        self.sources_path = sources_path or NEWS_SOURCES_PATH

    def load_sources(self) -> list[dict]:
        try:
            with open(self.sources_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            raise FinancialMemoryError(f'读取新闻源配置失败: {e}') from e
        return data.get('sources', [])

    def fetch_rss(self, url: str, limit: int = 10) -> list[dict]:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
        except Exception as e:
            raise FinancialMemoryError(f'抓取 RSS 失败: {e}') from e

        try:
            root = ET.fromstring(raw)
        except Exception as e:
            raise FinancialMemoryError(f'解析 RSS 失败: {e}') from e

        items = []
        for item in root.findall('.//item')[: max(0, limit)]:
            items.append(
                {
                    'source': url,
                    'title': (item.findtext('title') or '').strip(),
                    'summary': (item.findtext('description') or '').strip(),
                    'url': (item.findtext('link') or '').strip(),
                    'published_at': (item.findtext('pubDate') or '').strip(),
                    'tags': [],
                    'quality_flag': 'normal',
                }
            )
        return items

    def fetch_gnews(self, query: str, limit: int = 10, lang: str = 'zh') -> list[dict]:
        api_key = GNEWS_KEY_PATH.read_text(encoding='utf-8').strip()
        params = urllib.parse.urlencode(
            {
                'q': query,
                'lang': lang,
                'max': min(max(1, limit), 10),
                'apikey': api_key,
            }
        )
        url = f'https://gnews.io/api/v4/search?{params}'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode('utf-8'))
        except Exception as e:
            raise FinancialMemoryError(f'抓取 GNews 失败: {e}') from e

        items = []
        for item in (data.get('articles') or [])[: max(0, limit)]:
            items.append(
                {
                    'source': (item.get('source') or {}).get('name') or 'GNews',
                    'title': (item.get('title') or '').strip(),
                    'summary': (item.get('description') or '').strip(),
                    'url': (item.get('url') or '').strip(),
                    'published_at': (item.get('publishedAt') or '').strip(),
                    'tags': [query],
                    'quality_flag': 'normal',
                }
            )
        return items

    def fetch_juhe(self, query: str | None = None, limit: int = 10) -> list[dict]:
        """抓取聚合数据新闻API（财经类别）
        
        API文档: https://www.juhe.cn/docs/api/id/235
        免费额度: 100次/天
        """
        try:
            api_key = JUHE_KEY_PATH.read_text(encoding='utf-8').strip()
            if not api_key or api_key == 'YOUR_JUHE_API_KEY_HERE':
                log_event('news_fetch.juhe_skipped', reason='api_key_not_configured')
                return []
        except Exception:
            log_event('news_fetch.juhe_skipped', reason='api_key_file_not_found')
            return []

        # 聚合数据新闻头条API - 财经类型(type=caijing)
        params = urllib.parse.urlencode(
            {
                'key': api_key,
                'type': 'caijing',  # 财经类别
                'page': 1,
                'page_size': min(max(1, limit), 30),  # API限制最大30条
            }
        )
        url = f'http://v.juhe.cn/toutiao/index?{params}'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode('utf-8'))
        except Exception as e:
            log_event('news_fetch.juhe_failed', error=str(e))
            return []  # 失败时不中断，返回空列表

        # 检查API返回状态
        error_code = data.get('error_code')
        if error_code != 0:
            reason = data.get('reason', 'unknown')
            log_event('news_fetch.juhe_api_error', error_code=error_code, reason=reason)
            return []

        result = data.get('result', {})
        articles = result.get('data', [])

        items = []
        for item in articles[: max(0, limit)]:
            items.append(
                {
                    'source': item.get('author_name') or item.get('media_name') or '聚合数据',
                    'title': (item.get('title') or '').strip(),
                    'summary': '',  # 聚合数据API不返回摘要
                    'url': (item.get('url') or '').strip(),
                    'published_at': (item.get('date') or '').strip(),
                    'tags': ['caijing', '财经'],
                    'quality_flag': 'normal',
                }
            )
        
        log_event('news_fetch.juhe_success', count=len(items))
        return items

    def fetch_juhe_caijing(self, limit: int = 10) -> list[dict]:
        """抓取聚合数据财经新闻API (ID 743)
        
        API文档: https://www.juhe.cn/docs/api/id/743
        接口地址: http://apis.juhe.cn/fapigx/caijing/query
        """
        try:
            api_key = JUHE_CAIJING_KEY_PATH.read_text(encoding='utf-8').strip()
            if not api_key or api_key == 'YOUR_JUHE_CAIJING_API_KEY_HERE':
                log_event('news_fetch.juhe_caijing_skipped', reason='api_key_not_configured')
                return []
        except Exception:
            log_event('news_fetch.juhe_caijing_skipped', reason='api_key_file_not_found')
            return []

        url = f'http://apis.juhe.cn/fapigx/caijing/query?key={api_key}'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode('utf-8'))
        except Exception as e:
            log_event('news_fetch.juhe_caijing_failed', error=str(e))
            return []

        # 检查API返回状态
        error_code = data.get('error_code')
        if error_code != 0:
            reason = data.get('reason', 'unknown')
            log_event('news_fetch.juhe_caijing_api_error', error_code=error_code, reason=reason)
            return []

        result = data.get('result', {})
        articles = result.get('newslist', [])

        items = []
        for item in articles[: max(0, limit)]:
            items.append(
                {
                    'source': item.get('source') or '聚合数据财经',
                    'title': (item.get('title') or '').strip(),
                    'summary': '',  # API不返回摘要
                    'url': (item.get('url') or '').strip(),
                    'published_at': (item.get('ctime') or '').strip(),
                    'tags': ['caijing743', '财经新闻'],
                    'quality_flag': 'normal',
                }
            )
        
        log_event('news_fetch.juhe_caijing_success', count=len(items))
        return items

    def refresh(self, limit_per_source: int = 10) -> dict:
        sources = self.load_sources()
        collected = []
        for source in sources:
            source_type = source.get('type')
            if source_type == 'rss':
                url = source.get('url')
                if not url:
                    continue
                items = self.fetch_rss(url, limit=limit_per_source)
                collected.extend(items)
            elif source_type == 'gnews':
                query = source.get('query') or 'stock market OR gold OR us stocks'
                lang = source.get('lang') or 'zh'
                items = self.fetch_gnews(query=query, limit=limit_per_source, lang=lang)
                collected.extend(items)
            elif source_type == 'juhe':
                # 聚合数据财经新闻 (ID 235)
                items = self.fetch_juhe(limit=limit_per_source)
                collected.extend(items)
            elif source_type == 'juhe_caijing':
                # 聚合数据财经新闻 (ID 743)
                items = self.fetch_juhe_caijing(limit=limit_per_source)
                collected.extend(items)

        payload = {
            'schema_version': 1,
            'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'items': collected,
        }
        self.news_service.save(payload)
        log_event('news_fetch.refreshed', count=len(collected), sources=len(sources))
        return payload
