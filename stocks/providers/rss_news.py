"""RSS 新闻 Provider — 基于标准库解析 RSS  feed

支持 RSS 2.0 与 Atom 源，来源名称和语言由配置传入。
无需 API key，纯标准库实现。
"""

from __future__ import annotations

import asyncio
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Optional

from stocks.domain.models import NewsItem

# 默认 RSS 源
_DEFAULT_RSS_URL = "https://www.chinanews.com.cn/rss/finance.xml"


def _parse_rss_item(
    item_elem: ET.Element,
    *,
    source_name: str,
    language: str,
    scope: str = "general",
) -> Optional[NewsItem]:
    """解析单个 RSS <item> 元素为 NewsItem。"""
    title = ""
    link = ""
    pub_date = None
    description = ""

    for child in item_elem:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag == "title":
            title = (child.text or "").strip()
        elif tag == "link":
            link = (child.text or "").strip() or child.attrib.get("href", "").strip()
        elif tag in ("pubDate", "pubdate", "published", "updated"):
            raw = (child.text or "").strip()
            try:
                pub_date = parsedate_to_datetime(raw)
            except (TypeError, ValueError):
                try:
                    pub_date = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                except ValueError:
                    pass
        elif tag in ("description", "summary", "content"):
            # 去除 HTML 标签，只保留纯文本前 200 字
            raw = (child.text or "").strip()
            # 简单去除 HTML
            import re
            clean = re.sub(r"<[^>]+>", "", raw).strip()
            description = clean[:200]

    if not title:
        return None

    return NewsItem(
        title=title,
        url=link,
        source_name=source_name,
        source_type="rss",
        published_at=pub_date,
        summary=description if description else None,
        language=language,
        scope=scope,
    )


class RSSNewsProvider:
    """RSS 新闻 Provider

    从 RSS feed 获取新闻，无需 API key。
    支持自定义 URL、来源名称与语言。
    """

    @property
    def name(self) -> str:
        return f"rss_{self.source_name}"

    def __init__(
        self,
        rss_url: Optional[str] = None,
        *,
        source_name: str = "中新网财经",
        language: str = "unknown",
        scope: str = "general",
    ):
        self.rss_url = rss_url or _DEFAULT_RSS_URL
        self.source_name = source_name
        self.language = language
        self.scope = scope

    def _fetch_sync(self) -> list[NewsItem]:
        """同步获取并解析 RSS feed。"""
        try:
            req = urllib.request.Request(
                self.rss_url,
                headers={"User-Agent": "Mozilla/5.0 (stocks-claw/2.0)"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                xml_data = resp.read()
        except Exception:
            return []

        try:
            root = ET.fromstring(xml_data)
        except ET.ParseError:
            return []

        # RSS 2.0 格式: <rss><channel><item>...</item></channel></rss>
        items: list[NewsItem] = []
        channel = root.find("channel")
        if channel is None:
            # 尝试 Atom 格式
            channel = root

        item_elements = [
            element
            for element in channel.iter()
            if element.tag.split("}")[-1] in {"item", "entry"}
        ]
        for item_elem in item_elements:
            news = _parse_rss_item(
                item_elem,
                source_name=self.source_name,
                language=self.language,
                scope=self.scope,
            )
            if news is not None:
                items.append(news)

        return items

    async def fetch(self, max_items: int = 10) -> list[NewsItem]:
        """异步获取新闻列表。"""
        items = await asyncio.to_thread(self._fetch_sync)
        return items[:max_items]
