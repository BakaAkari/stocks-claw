from __future__ import annotations

import json
from pathlib import Path

from stocks.errors import FinancialMemoryError
from stocks.logging_utils import log_event

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NEWS_PATH = ROOT / 'data' / 'news_feed.json'


class NewsInputService:
    def __init__(self, path: Path | None = None):
        self.path = path or DEFAULT_NEWS_PATH

    def load(self) -> dict:
        if not self.path.exists():
            return {
                'schema_version': 1,
                'updated_at': None,
                'items': [],
            }
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            raise FinancialMemoryError(f'读取新闻输入失败: {e}') from e

        if not isinstance(data, dict):
            raise FinancialMemoryError('新闻输入文件格式错误: 顶层必须是对象')

        items = data.get('items')
        if items is None:
            data['items'] = []
        elif not isinstance(items, list):
            raise FinancialMemoryError('新闻输入文件格式错误: items 必须是数组')

        return data

    def save(self, payload: dict) -> None:
        if not isinstance(payload, dict):
            raise FinancialMemoryError('保存新闻输入失败: payload 必须是对象')
        payload = dict(payload)
        payload.setdefault('schema_version', 1)
        payload.setdefault('updated_at', None)
        payload.setdefault('items', [])
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.write('\n')
        except Exception as e:
            raise FinancialMemoryError(f'保存新闻输入失败: {e}') from e
        log_event('news_input.saved', count=len(payload.get('items', [])), path=str(self.path))

    def list_items(self) -> list[dict]:
        payload = self.load()
        items = payload.get('items', [])
        log_event('news_input.loaded', count=len(items), path=str(self.path))
        return items

    def latest_items(self, limit: int = 5) -> list[dict]:
        items = self.list_items()
        sorted_items = sorted(items, key=lambda x: x.get('published_at') or '', reverse=True)
        return sorted_items[: max(0, limit)]
