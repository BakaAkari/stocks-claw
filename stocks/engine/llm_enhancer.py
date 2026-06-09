"""LLM 数据增强器 — 数据层语义处理

职责：
1. 新闻摘要生成（原始摘要缺失时）
2. 跨源去重（相同新闻不同来源）
3. 质量分级（importance/urgency/category/sentiment）
4. 行情自然语言摘要

设计原则：
- 使用低成本模型（默认 gpt-4o-mini）
- 默认禁用（llm_enhancer.enabled = false）
- 失败降级（异常时返回原始数据）
- 不阻塞主流程
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from difflib import SequenceMatcher
from typing import Optional

from stocks.domain.models import EnhancedNewsItem, NewsItem, Quote

logger = logging.getLogger(__name__)

# 默认去重相似度阈值
_DEDUP_THRESHOLD = 0.8

# 默认 LLM 超时（秒）
_LLM_TIMEOUT = 30


class LLMEnhancer:
    """LLM 数据增强器

    对原始新闻和行情数据进行轻量 LLM 增强，提升下游 Agent 的语义理解能力。
    默认禁用，启用后使用低成本模型（gpt-4o-mini）以降低费用。
    """

    def __init__(
        self,
        model: str = "deepseek-v4-flash",
        enabled: bool = True,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.model = model
        self.enabled = enabled
        self.api_key = api_key
        self.base_url = base_url or "https://api.openai.com/v1"

    async def enhance_news(self, news: list[NewsItem]) -> list[EnhancedNewsItem]:
        """增强新闻列表。

        流程：
        1. 若未启用，直接包装为 EnhancedNewsItem 返回。
        2. 基于标题相似度去重。
        3. 对缺失摘要的新闻调用 LLM 生成摘要。
        4. 对每条新闻调用 LLM 进行分级分类。
        5. 组装 EnhancedNewsItem 列表。

        任何 LLM 调用失败均降级为原始数据，不抛异常。
        """
        if not self.enabled:
            return [
                EnhancedNewsItem(
                    title=n.title,
                    url=n.url,
                    source_name=n.source_name,
                    source_type=n.source_type,
                    published_at=n.published_at,
                    summary=n.summary,
                    language=n.language,
                    tags=n.tags,
                    raw_metadata=n.raw_metadata,
                )
                for n in news
            ]

        try:
            deduped = self._deduplicate(news)
        except Exception as exc:
            logger.warning("News deduplication failed: %s", exc)
            deduped = news

        enhanced: list[EnhancedNewsItem] = []
        for item in deduped:
            try:
                llm_summary: Optional[str] = None
                if not item.summary:
                    llm_summary = await asyncio.to_thread(self._generate_summary, item)

                classification = await asyncio.to_thread(self._classify_news, item)
            except Exception as exc:
                logger.warning("LLM enhancement failed for '%s': %s", item.title, exc)
                classification = {}
                llm_summary = None

            enhanced.append(
                EnhancedNewsItem(
                    title=item.title,
                    url=item.url,
                    source_name=item.source_name,
                    source_type=item.source_type,
                    published_at=item.published_at,
                    summary=item.summary,
                    language=item.language,
                    tags=item.tags,
                    raw_metadata=item.raw_metadata,
                    importance=classification.get("importance", "unknown"),
                    urgency=classification.get("urgency", "unknown"),
                    category=classification.get("category", "unknown"),
                    sentiment=classification.get("sentiment", "unknown"),
                    relevance_tags=classification.get("relevance_tags", []),
                    llm_generated_summary=llm_summary,
                    enhanced_by_llm=bool(llm_summary or classification),
                )
            )

        return enhanced

    async def generate_market_summary(
        self,
        quotes: dict[str, list[Quote]],
        market_state: dict,
        timeout: int = 5,
    ) -> str:
        """生成行情自然语言摘要。

        基于行情数据和市场状态生成一段人类可读的市场摘要。
        未启用或调用失败时返回空字符串。
        """
        if not self.enabled:
            return ""

        prompt = self._build_market_summary_prompt(quotes, market_state)
        try:
            return await asyncio.to_thread(self._call_llm, prompt, timeout)
        except Exception as exc:
            logger.warning("Market summary generation failed: %s", exc)
            return ""

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _call_llm(self, prompt: str, timeout: Optional[int] = None) -> str:
        """同步调用 LLM API，返回模型生成的文本。

        使用标准 OpenAI Chat Completions API 格式，兼容任何提供相同格式的服务。
        异常时返回空字符串，由调用方决定是否降级。
        """
        if not self.api_key:
            logger.warning("LLM call skipped: api_key not configured")
            return ""

        url = f"{self.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 512,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout or _LLM_TIMEOUT) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            logger.warning("LLM API call failed: %s", exc)
            return ""

    def _deduplicate(self, news: list[NewsItem]) -> list[NewsItem]:
        """基于标题相似度去重。

        使用 difflib.SequenceMatcher 计算标题相似度，
        相似度 > 0.8 视为重复，保留第一条。
        """
        if not news:
            return []

        kept: list[NewsItem] = []
        for item in news:
            is_duplicate = False
            for ref in kept:
                similarity = SequenceMatcher(None, item.title, ref.title).ratio()
                if similarity > _DEDUP_THRESHOLD:
                    is_duplicate = True
                    break
            if not is_duplicate:
                kept.append(item)
        return kept

    def _generate_summary(self, news: NewsItem) -> str:
        """为单条新闻生成摘要。

        当原始摘要缺失时，调用 LLM 基于标题和来源生成简短摘要。
        """
        prompt = (
            f"请为以下新闻生成一句简短的中文摘要（不超过 30 字）。\n\n"
            f"标题：{news.title}\n"
            f"来源：{news.source_name}\n\n"
            f"摘要："
        )
        return self._call_llm(prompt)

    def _classify_news(self, news: NewsItem) -> dict:
        """对新闻进行分级分类。

        调用 LLM 返回结构化字段：importance、urgency、category、sentiment、relevance_tags。
        要求模型以 JSON 格式输出，便于解析。
        """
        prompt = (
            "请对以下新闻进行分析，并以 JSON 格式返回结果（不要包含 markdown 代码块标记）。\n\n"
            "字段要求：\n"
            "- importance: high / medium / low\n"
            "- urgency: immediate / high / medium / low\n"
            "- category: 宏观政策 / 行业动态 / 个股新闻 / 国际市场 / 其他\n"
            "- sentiment: positive / negative / neutral\n"
            "- relevance_tags: 相关主题标签列表（如 ['AI', '半导体']）\n\n"
            f"标题：{news.title}\n"
            f"摘要：{news.summary or '无'}\n"
            f"来源：{news.source_name}\n\n"
            "JSON："
        )
        raw = self._call_llm(prompt)
        if not raw:
            return {}

        # 尝试提取 JSON 内容（兼容模型偶尔包裹在 ```json 中的情况）
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").strip()
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()

        try:
            result = json.loads(cleaned)
            # 只保留期望字段
            return {
                k: result.get(k, "unknown" if k != "relevance_tags" else [])
                for k in ("importance", "urgency", "category", "sentiment", "relevance_tags")
            }
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse LLM classification JSON: %s", exc)
            return {}

    def _build_market_summary_prompt(
        self,
        quotes: dict[str, list[Quote]],
        market_state: dict,
    ) -> str:
        """构建行情摘要 prompt。"""
        lines: list[str] = [
            "请基于以下行情数据生成一段简短的市场摘要（不超过 100 字），用中文输出。",
            "",
            "行情数据：",
        ]
        for market, qs in quotes.items():
            for q in qs:
                inst = q.instrument
                price_info = f"{inst.code} {inst.name}: 价格 {q.price}" if q.price is not None else f"{inst.code} {inst.name}: 价格未知"
                if q.pct_change is not None:
                    price_info += f", 涨跌 {q.pct_change:+.2f}%"
                lines.append(f"  [{market}] {price_info}")

        if market_state:
            lines.extend(["", "市场状态：", json.dumps(market_state, ensure_ascii=False)])

        lines.extend(["", "市场摘要："])
        return "\n".join(lines)
