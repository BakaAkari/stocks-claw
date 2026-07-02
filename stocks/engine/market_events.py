"""新闻事件提取 — 将新闻列表转为可交易分析使用的结构化市场事件。"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from stocks.domain.models import FinancialAsset, Instrument, MarketEvent, NewsItem

_EVENT_KEYWORDS = {
    "monetary_policy": [
        "美联储", "fed", "fomc", "降息", "加息", "利率", "缩表", "扩表", "央行", "逆回购",
        "流动性", "准备金率", "mlf", "lpr",
    ],
    "macro_policy": [
        "政策", "财政", "发改委", "国务院", "刺激", "补贴", "消费券", "地产政策",
        "监管", "证监会", "税收", "关税",
    ],
    "earnings": [
        "财报", "业绩", "利润", "营收", "eps", "guidance", "预告", "亏损", "盈利",
    ],
    "geopolitical": [
        "地缘", "战争", "制裁", "出口管制", "禁令", "关税", "贸易战", "中东", "台海",
    ],
    "industry_theme": [
        "ai", "人工智能", "芯片", "半导体", "算力", "新能源", "军工", "机器人", "医药",
        "银行", "券商", "保险", "消费电子", "云计算", "数据中心",
    ],
    "market_movement": [
        "大涨", "大跌", "反弹", "跳水", "收涨", "收跌", "创新高", "新低", "暴跌", "暴涨",
        "纳指", "标普", "道指", "沪指", "创业板", "科创板",
    ],
}

_THEME_KEYWORDS = {
    "AI": ["ai", "人工智能", "大模型", "算力", "gpu", "英伟达", "nvidia"],
    "半导体": ["芯片", "半导体", "晶圆", "光刻", "存储", "英伟达", "nvidia", "高通", "qcom"],
    "军工": ["军工", "国防", "航天", "导弹", "无人机"],
    "金融": ["银行", "券商", "保险", "金融", "平安银行", "利差"],
    "新能源": ["新能源", "光伏", "锂电", "储能", "电动车", "tesla", "特斯拉"],
    "医药": ["医药", "创新药", "医疗", "药品", "fda"],
    "消费": ["消费", "零售", "白酒", "旅游", "餐饮"],
    "地产": ["地产", "房地产", "房贷", "按揭", "销售面积"],
    "汇率": ["人民币", "汇率", "美元指数", "dxy", "usdcny"],
    "利率": ["利率", "美债", "收益率", "降息", "加息", "流动性"],
}

_POSITIVE_KEYWORDS = [
    "利好", "上调", "超预期", "增长", "创新高", "批准", "放宽", "降息", "刺激", "回购",
    "beat", "surge", "record high", "approval",
]

_NEGATIVE_KEYWORDS = [
    "利空", "下调", "不及预期", "下滑", "亏损", "制裁", "禁令", "暴跌", "加息", "收紧",
    "miss", "plunge", "ban", "sanction",
]

_IMMEDIATE_KEYWORDS = ["突发", "刚刚", "盘前", "盘中", "after-hours", "pre-market", "紧急"]
_HIGH_URGENCY_KEYWORDS = ["大涨", "大跌", "暴涨", "暴跌", "制裁", "禁令", "降息", "加息", "财报"]

_MARKET_KEYWORDS = {
    "a": ["a股", "沪指", "深成指", "创业板", "科创板", "北向", "人民币", "央行", "证监会"],
    "us": [
        "美股", "纳指", "标普", "道指", "美联储", "美元", "美债", "sec", "nasdaq", "s&p",
        "dow", "nvidia", "microsoft", "apple", "qcom", "qualcomm",
    ],
    "hk": ["港股", "恒生", "恒指", "h股"],
    "global": ["全球", "原油", "黄金", "地缘", "战争", "关税", "美元指数"],
}


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
        affected_markets = self._affected_markets(text, instruments)
        affected_symbols = self._affected_symbols(text, instruments)
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
        rationale = self._rationale(event_type, themes, affected_markets, matched_holdings)

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

    def _affected_markets(self, text: str, instruments: list[Instrument]) -> list[str]:
        markets = {
            market
            for market, keywords in _MARKET_KEYWORDS.items()
            if any(keyword.lower() in text for keyword in keywords)
        }
        for inst in instruments:
            if inst.code.lower() in text or inst.name.lower() in text:
                markets.add(inst.market)
        if not markets and any(keyword in text for keyword in ("全球", "美元", "原油", "黄金")):
            markets.add("global")
        return sorted(markets)

    def _affected_symbols(self, text: str, instruments: list[Instrument]) -> list[str]:
        symbols = []
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
        if age_seconds is not None and age_seconds <= 2 * 60 * 60:
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
        score = 0.35
        score += min(event_hits, 3) * 0.1
        score += min(len(themes), 3) * 0.06
        if affected_symbols:
            score += 0.12
        if matched_holdings:
            score += 0.12
        return round(min(score, 0.95), 2)

    def _rationale(
        self,
        event_type: str,
        themes: list[str],
        affected_markets: list[str],
        matched_holdings: list[str],
    ) -> str:
        parts = [f"event_type={event_type}"]
        if themes:
            parts.append("themes=" + ",".join(themes[:3]))
        if affected_markets:
            parts.append("markets=" + ",".join(affected_markets))
        if matched_holdings:
            parts.append("holdings=" + ",".join(matched_holdings[:3]))
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
