"""新闻事件提取 — 将新闻列表转为可交易分析使用的结构化市场事件。"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from stocks.domain.models import FinancialAsset, Instrument, MarketEvent, NewsItem
from stocks.engine.config_loader import DEFAULT_ENGINE_CONFIG

# ---------------------------------------------------------------------------
# 关键词/情绪词典的唯一数据源：config_loader.DEFAULT_ENGINE_CONFIG["market_events"]。
# 引擎代码不再内嵌词典；调词（增删关键词、调整情绪词）只改配置，不动代码。
# 下列模块级名字保留为视图别名，供本模块方法与既有引用使用。
# ---------------------------------------------------------------------------
_MARKET_EVENTS_CONFIG: dict = DEFAULT_ENGINE_CONFIG.get("market_events", {})

_EVENT_KEYWORDS: dict = _MARKET_EVENTS_CONFIG.get("event_keywords", {})
_THEME_KEYWORDS: dict = _MARKET_EVENTS_CONFIG.get("theme_keywords", {})
_POSITIVE_KEYWORDS: list = _MARKET_EVENTS_CONFIG.get("positive_keywords", [])
_NEGATIVE_KEYWORDS: list = _MARKET_EVENTS_CONFIG.get("negative_keywords", [])
_IMMEDIATE_KEYWORDS: list = _MARKET_EVENTS_CONFIG.get("immediate_keywords", [])
_HIGH_URGENCY_KEYWORDS: list = _MARKET_EVENTS_CONFIG.get("high_urgency_keywords", [])
_MARKET_KEYWORDS: dict = _MARKET_EVENTS_CONFIG.get("market_keywords", {})

# ── 提取器规则常量（规则版本 rules_v1 的组成部分）──

# 新闻年龄 ≤ 2 小时视为高紧急度
_URGENCY_AGE_HIGH_SECONDS = 2 * 60 * 60

# 无任何市场关键词命中时，命中以下线索才归为 global（与 market_keywords["global"]
# 口径一致；保留为独立常量以免误触发"关税/地缘"等泛化词）
_GLOBAL_MARKET_FALLBACK_HINTS = ("全球", "美元", "原油", "黄金")

# 置信度评分权重：调参前需重新评估输出分布，避免与规则版本解耦
_CONFIDENCE_BASE_SCORE = 0.35
_CONFIDENCE_EVENT_HIT_WEIGHT = 0.10
_CONFIDENCE_THEME_WEIGHT = 0.06
_CONFIDENCE_SYMBOL_BONUS = 0.12
_CONFIDENCE_HOLDING_BONUS = 0.12
_CONFIDENCE_SCOPE_HOLDING_BONUS = 0.12
_CONFIDENCE_MAX_HITS_CAP = 3
_CONFIDENCE_MAX_SCORE = 0.95


class MarketEventExtractor:
    """确定性新闻事件提取器。

    这层不追求替代 LLM 判断，而是保证在没有 LLM 的情况下也能产出稳定的
    事件类型、主题、影响窗口、情绪和持仓匹配线索。
    """

    def extract(
        self,
        news: list[NewsItem],
        *,
        assets: list[FinancialAsset],
        instruments: list[Instrument],
        generated_at: str,
        max_events: int = 20,
    ) -> tuple[list[MarketEvent], dict]:
        events = [
            self._extract_one(item, index, assets, instruments, generated_at)
            for index, item in enumerate(news)
        ]
        events.sort(key=self._sort_key, reverse=True)
        events = events[:max_events]
        return events, self.build_digest(events)

    def build_digest(self, events: list[MarketEvent]) -> dict:
        themes = Counter(theme for event in events for theme in event.themes)
        markets = Counter(market for event in events for market in event.affected_markets)
        sentiments = Counter(event.sentiment for event in events)
        urgency = Counter(event.urgency for event in events)
        matched_holdings = sorted({
            holding for event in events for holding in event.matched_holdings
        })

        return {
            "schema_version": 1,
            "generated_by": "rules_v1",
            "event_count": len(events),
            "top_events": [event.to_dict() for event in events[:5]],
            "themes": dict(themes.most_common()),
            "affected_markets": dict(markets.most_common()),
            "sentiment": dict(sentiments.most_common()),
            "urgency": dict(urgency.most_common()),
            "matched_holdings": matched_holdings,
        }

    def _extract_one(
        self,
        item: NewsItem,
        index: int,
        assets: list[FinancialAsset],
        instruments: list[Instrument],
        generated_at: str,
    ) -> MarketEvent:
        text = self._event_text(item)
        event_type, event_hits = self._event_type(text, item)
        themes = self._themes(text, item)
        affected_markets = self._affected_markets(text, instruments, item)
        affected_symbols = self._affected_symbols(text, instruments, item)
        matched_holdings = self._matched_holdings(text, assets)
        sentiment = self._sentiment(text, item)
        urgency = self._urgency(text, item, generated_at)
        impact_horizon = self._impact_horizon(event_type, themes)
        confidence = self._confidence(
            event_hits=event_hits,
            themes=themes,
            affected_symbols=affected_symbols,
            matched_holdings=matched_holdings,
            item=item,
        )
        rationale = self._rationale(
            event_type, themes, affected_markets, matched_holdings, item.scope
        )

        return MarketEvent(
            title=item.title,
            url=item.url,
            source_name=item.source_name,
            source_type=item.source_type,
            published_at=item.published_at,
            summary=item.summary,
            event_type=event_type,
            themes=themes,
            affected_markets=affected_markets,
            affected_symbols=affected_symbols,
            matched_holdings=matched_holdings,
            sentiment=sentiment,
            urgency=urgency,
            impact_horizon=impact_horizon,
            confidence=confidence,
            rationale=rationale,
            raw_news_index=index,
        )

    def _event_text(self, item: NewsItem) -> str:
        parts = [
            item.title or "",
            item.summary or "",
            " ".join(item.tags),
        ]
        return " ".join(parts).lower()

    def _event_type(self, text: str, item: NewsItem) -> tuple[str, int]:
        best_type = "other"
        best_hits = 0
        for event_type, keywords in _EVENT_KEYWORDS.items():
            hits = sum(1 for keyword in keywords if keyword.lower() in text)
            if hits > best_hits:
                best_type = event_type
                best_hits = hits
        return best_type, best_hits

    def _themes(self, text: str, item: NewsItem) -> list[str]:
        themes = {
            theme
            for theme, keywords in _THEME_KEYWORDS.items()
            if any(keyword.lower() in text for keyword in keywords)
        }
        return sorted(themes)

    def _affected_markets(
        self, text: str, instruments: list[Instrument], item: NewsItem
    ) -> list[str]:
        markets = {
            market
            for market, keywords in _MARKET_KEYWORDS.items()
            if any(keyword.lower() in text for keyword in keywords)
        }
        for inst in instruments:
            if inst.code.lower() in text or inst.name.lower() in text:
                markets.add(inst.market)
        if item.scope == "holding" and item.raw_metadata.get("market"):
            markets.add(str(item.raw_metadata["market"]))
        if not markets and any(
            keyword in text for keyword in _GLOBAL_MARKET_FALLBACK_HINTS
        ):
            markets.add("global")
        return sorted(markets)

    def _affected_symbols(
        self, text: str, instruments: list[Instrument], item: NewsItem
    ) -> list[str]:
        symbols = []
        if item.scope == "holding" and item.raw_metadata.get("symbol"):
            market = item.raw_metadata.get("market")
            if market:
                symbols.append(f"{market}:{item.raw_metadata['symbol']}")
        for inst in instruments:
            if inst.code.lower() in text or inst.name.lower() in text:
                symbols.append(f"{inst.market}:{inst.code}")
        return sorted(set(symbols))

    def _matched_holdings(self, text: str, assets: list[FinancialAsset]) -> list[str]:
        matched = []
        for asset in assets:
            haystacks = [asset.name.lower(), asset.asset_type.lower()]
            if asset.notes:
                haystacks.extend(part.strip().lower() for part in asset.notes.replace("，", ",").split(","))
            if any(token and token in text for token in haystacks):
                matched.append(asset.name)
        return sorted(set(matched))

    def _sentiment(self, text: str, item: NewsItem) -> str:
        positive = sum(1 for keyword in _POSITIVE_KEYWORDS if keyword.lower() in text)
        negative = sum(1 for keyword in _NEGATIVE_KEYWORDS if keyword.lower() in text)
        if positive > negative:
            return "positive"
        if negative > positive:
            return "negative"
        return "neutral"

    def _urgency(self, text: str, item: NewsItem, generated_at: str) -> str:
        if any(keyword.lower() in text for keyword in _IMMEDIATE_KEYWORDS):
            return "immediate"
        if any(keyword.lower() in text for keyword in _HIGH_URGENCY_KEYWORDS):
            return "high"

        age_seconds = self._age_seconds(item.published_at, generated_at)
        if age_seconds is not None and age_seconds <= _URGENCY_AGE_HIGH_SECONDS:
            return "high"
        return "medium"

    def _impact_horizon(self, event_type: str, themes: list[str]) -> str:
        if event_type in {"monetary_policy", "macro_policy", "geopolitical"}:
            return "medium_term"
        if event_type in {"earnings", "market_movement", "company_news"}:
            return "intraday_to_short_term"
        if themes:
            return "short_to_medium_term"
        return "short_term"

    def _confidence(
        self,
        *,
        event_hits: int,
        themes: list[str],
        affected_symbols: list[str],
        matched_holdings: list[str],
        item: NewsItem,
    ) -> float:
        score = _CONFIDENCE_BASE_SCORE
        score += min(event_hits, _CONFIDENCE_MAX_HITS_CAP) * _CONFIDENCE_EVENT_HIT_WEIGHT
        score += min(len(themes), _CONFIDENCE_MAX_HITS_CAP) * _CONFIDENCE_THEME_WEIGHT
        if affected_symbols:
            score += _CONFIDENCE_SYMBOL_BONUS
        if matched_holdings:
            score += _CONFIDENCE_HOLDING_BONUS
        if item.scope == "holding":
            score += _CONFIDENCE_SCOPE_HOLDING_BONUS
        return round(min(score, _CONFIDENCE_MAX_SCORE), 2)

    def _rationale(
        self,
        event_type: str,
        themes: list[str],
        affected_markets: list[str],
        matched_holdings: list[str],
        scope: str,
    ) -> str:
        parts = [f"event_type={event_type}"]
        if themes:
            parts.append("themes=" + ",".join(themes[:3]))
        if affected_markets:
            parts.append("markets=" + ",".join(affected_markets))
        if matched_holdings:
            parts.append("holdings=" + ",".join(matched_holdings[:3]))
        if scope == "holding":
            parts.append("scope=holding")
        return "; ".join(parts)

    def _sort_key(self, event: MarketEvent) -> tuple:
        urgency_rank = {"immediate": 4, "high": 3, "medium": 2, "low": 1}
        timestamp = event.published_at.timestamp() if event.published_at else 0.0
        return (urgency_rank.get(event.urgency, 0), event.confidence, timestamp)

    def _age_seconds(self, published_at: datetime | None, generated_at: str) -> int | None:
        if published_at is None:
            return None
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        try:
            now = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        except ValueError:
            now = datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return max(0, int((now - published_at).total_seconds()))
