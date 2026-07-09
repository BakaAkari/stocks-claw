"""Intelligence analyzer: cluster news, assess market impact, generate signals.

Operates on raw snapshots from NewsIntelligenceStore. No external network calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from stocks.engine.news_intelligence_store import (
    EventCluster,
    IntelligenceSignal,
    IntelligenceSnapshot,
)
from stocks.logging_utils import get_logger

logger = get_logger("intelligence_analyzer")

# Theme keywords grouped by macro topic.
THEME_KEYWORDS = {
    "geopolitics": [
        "war",
        "conflict",
        "tension",
        "sanction",
        "iran",
        "israel",
        "ukraine",
        "russia",
        "china",
        "taiwan",
        "military",
        "strike",
        "drone",
        "attack",
    ],
    "monetary_policy": [
        "fed",
        "federal reserve",
        "interest rate",
        "rate hike",
        "rate cut",
        "powell",
        "fomc",
        "central bank",
        " ECB ",
        "BOJ",
        "PBOC",
        "yield",
    ],
    "earnings": [
        "earnings",
        "revenue",
        "profit",
        "guidance",
        "beat",
        "miss",
        "EPS",
        "quarterly",
        "reported",
        "results",
    ],
    "technology": [
        "AI",
        "artificial intelligence",
        "chip",
        "semiconductor",
        "nvidia",
        "tesla",
        "big tech",
        "magnificent seven",
        "tech stock",
        "cloud",
    ],
    "energy": [
        "oil",
        "crude",
        "energy",
        "OPEC",
        "gas",
        "petroleum",
        "renewable",
        "solar",
    ],
    "macro_data": [
        "CPI",
        "inflation",
        "PPI",
        "GDP",
        "nonfarm",
        "unemployment",
        "jobs report",
        "retail sales",
        "PMI",
        "industrial production",
    ],
}

MARKET_SENTIMENT_POSITIVE = [
    "surge",
    "rally",
    "jump",
    "soar",
    "gain",
    "rise",
    "record high",
    "bullish",
    "strong",
    "beat",
    "raise guidance",
    "optimistic",
]
MARKET_SENTIMENT_NEGATIVE = [
    "plunge",
    "crash",
    "tumble",
    "slump",
    "drop",
    "fall",
    "bearish",
    "recession",
    "miss",
    "cut guidance",
    "fear",
    "panic",
    "sell-off",
]

# Map theme to typical affected markets/assets.
THEME_MARKETS = {
    "geopolitics": ["equity", "oil", "gold", "dxy"],
    "monetary_policy": ["equity", "bond", "dxy", "gold"],
    "earnings": ["equity", "tech"],
    "technology": ["equity", "tech"],
    "energy": ["oil", "equity", "energy"],
    "macro_data": ["equity", "bond", "dxy", "gold"],
}


@dataclass(frozen=True)
class AnalysisResult:
    """Result of analyzing intelligence snapshots."""

    analyzed_at: datetime
    clusters: list[EventCluster]
    market_impact: dict
    signals: list[IntelligenceSignal]
    data_quality: dict
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "analyzed_at": self.analyzed_at.isoformat(),
            "clusters": [c.to_dict() for c in self.clusters],
            "market_impact": self.market_impact,
            "signals": [s.to_dict() for s in self.signals],
            "data_quality": self.data_quality,
            "metadata": self.metadata,
        }


class IntelligenceAnalyzer:
    """Analyze harvested intelligence snapshots.

    Args:
        lookback_hours: How many hours of snapshots to read for context.
        holdings: Optional list of user holdings (instrument_key or symbol) for matching.
    """

    def __init__(
        self,
        *,
        lookback_hours: int = 6,
        holdings: Optional[list[str]] = None,
    ):
        self.lookback_hours = max(1, lookback_hours)
        self.holdings = set(holdings or [])
        self._article_index = 0

    def analyze(self, snapshots: list[IntelligenceSnapshot]) -> AnalysisResult:
        analyzed_at = datetime.now(timezone.utc)
        if not snapshots:
            return AnalysisResult(
                analyzed_at=analyzed_at,
                clusters=[],
                market_impact={},
                signals=[],
                data_quality={"status": "degraded", "errors": ["no snapshots"]},
            )

        # Use most recent snapshot as primary data, plus recent articles from all snapshots.
        primary = max(snapshots, key=lambda s: s.collected_at)
        recent_articles = []
        cutoff = analyzed_at - timedelta(hours=self.lookback_hours)
        for snapshot in snapshots:
            for article in snapshot.articles:
                published = _parse_iso(
                    article.get("published_at", "") or primary.collected_at.isoformat()
                )
                if published >= cutoff:
                    recent_articles.append(article)

        # Sort and limit for performance
        recent_articles.sort(
            key=lambda a: _parse_iso(a.get("published_at", "")).timestamp(),
            reverse=True,
        )
        recent_articles = recent_articles[:80]

        clusters = self._cluster_articles(recent_articles)
        market_impact = self._assess_market_impact(clusters, primary)
        signals = self._generate_signals(clusters, primary)
        data_quality = self._build_data_quality(snapshots, clusters, signals, recent_articles)
        return AnalysisResult(
            analyzed_at=analyzed_at,
            clusters=clusters,
            market_impact=market_impact,
            signals=signals,
            data_quality=data_quality,
            metadata={
                "snapshots_used": len(snapshots),
                "articles_analyzed": len(recent_articles),
                "lookback_hours": self.lookback_hours,
            },
        )

    # ------------------------------------------------------------------
    # Clustering
    # ------------------------------------------------------------------
    def _cluster_articles(self, articles: list[dict]) -> list[EventCluster]:
        # First, assign each article to its dominant theme.
        themed: dict[str, list[dict]] = {}
        for article in articles:
            theme = self._detect_theme(article)
            themed.setdefault(theme, []).append(article)

        clusters: list[EventCluster] = []
        for theme, items in themed.items():
            if not items:
                continue
            # Sort by recency, take top 5 representative articles
            items.sort(
                key=lambda a: _parse_iso(a.get("published_at", "")).timestamp(),
                reverse=True,
            )
            representative = items[:5]
            summary_sentences = []
            for item in representative[:3]:
                title = item.get("title", "")
                summary = item.get("summary", "")
                if summary and len(summary) > 20:
                    summary_sentences.append(f"{title}: {summary}")
                else:
                    summary_sentences.append(title)
            summary = (
                "; ".join(summary_sentences) if summary_sentences else f"{theme} related news flow"
            )
            sentiment = self._aggregate_sentiment(items)
            urgency = self._aggregate_urgency(items)
            confidence = min(0.95, 0.4 + 0.1 * len(representative))
            clusters.append(
                EventCluster(
                    cluster_id=f"{theme}_{self._article_index:04d}",
                    theme=theme,
                    event_type=theme,
                    summary=summary,
                    articles=[
                        {
                            "title": a.get("title", ""),
                            "url": a.get("url", ""),
                            "source_name": a.get("source_name", ""),
                            "published_at": a.get("published_at"),
                        }
                        for a in representative
                    ],
                    affected_markets=THEME_MARKETS.get(theme, ["equity"]),
                    affected_symbols=self._extract_symbols(items),
                    sentiment=sentiment,
                    urgency=urgency,
                    confidence=confidence,
                    formed_at=datetime.now(timezone.utc),
                )
            )
            self._article_index += 1

        # Sort clusters by urgency then confidence
        urgency_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        clusters.sort(
            key=lambda c: (urgency_order.get(c.urgency, 2), -c.confidence),
            reverse=True,
        )
        return clusters

    # ------------------------------------------------------------------
    # Market impact assessment
    # ------------------------------------------------------------------
    def _assess_market_impact(
        self, clusters: list[EventCluster], snapshot: IntelligenceSnapshot
    ) -> dict:
        impact = {
            "equity": {"direction": "neutral", "confidence": 0.0, "drivers": []},
            "bond": {"direction": "neutral", "confidence": 0.0, "drivers": []},
            "oil": {"direction": "neutral", "confidence": 0.0, "drivers": []},
            "gold": {"direction": "neutral", "confidence": 0.0, "drivers": []},
            "dxy": {"direction": "neutral", "confidence": 0.0, "drivers": []},
            "china_assets": {"direction": "neutral", "confidence": 0.0, "drivers": []},
        }

        for cluster in clusters:
            for market in cluster.affected_markets:
                if market not in impact:
                    continue
                direction = self._direction_from_sentiment(cluster.sentiment)
                if cluster.confidence > impact[market]["confidence"]:
                    impact[market]["direction"] = direction
                    impact[market]["confidence"] = cluster.confidence
                impact[market]["drivers"].append(cluster.theme)

        # Enrich with macro quote readings
        macro = snapshot.macro
        if macro.get("vix") is not None and macro["vix"] > 25:
            impact["equity"]["direction"] = "negative"
            impact["equity"]["drivers"].append(f"VIX elevated at {macro['vix']}")
        elif macro.get("vix") is not None and macro["vix"] < 15:
            impact["equity"]["drivers"].append(f"VIX calm at {macro['vix']}")
        if macro.get("us_10y_yield") is not None and macro["us_10y_yield"] > 4.5:
            impact["bond"]["drivers"].append(f"10Y yield high at {macro['us_10y_yield']}%")
        elif macro.get("us_10y_yield") is not None and macro["us_10y_yield"] < 3.5:
            impact["bond"]["drivers"].append(f"10Y yield low at {macro['us_10y_yield']}%")

        return impact

    # ------------------------------------------------------------------
    # Signal generation
    # ------------------------------------------------------------------
    def _generate_signals(
        self,
        clusters: list[EventCluster],
        snapshot: IntelligenceSnapshot,
    ) -> list[IntelligenceSignal]:
        signals: list[IntelligenceSignal] = []
        macro = snapshot.macro
        quotes = snapshot.quotes

        # VIX signal
        if macro.get("vix") is not None:
            vix = macro["vix"]
            if vix > 25:
                signals.append(
                    self._signal(
                        "VIX",
                        "VIX volatility index",
                        "sell",
                        f"VIX at {vix} indicates elevated fear; reduce risk exposure",
                        "VIX falls back below 20",
                        "VIX can remain elevated longer than expected",
                        urgency="high",
                    )
                )
            elif vix < 14:
                signals.append(
                    self._signal(
                        "VIX",
                        "VIX volatility index",
                        "watch",
                        f"VIX at {vix} is complacent; watch for sudden spikes",
                        "VIX rises above 18",
                        "Low VIX does not prevent sell-offs",
                        urgency="low",
                    )
                )

        # Gold signal
        if macro.get("gold") is not None or "GLD" in quotes:
            gld_quote = quotes.get("GLD", {})
            gld_change = gld_quote.get("pct_change") if isinstance(gld_quote, dict) else None
            if gld_change is not None and gld_change > 1.5:
                signals.append(
                    self._signal(
                        "GLD",
                        "SPDR Gold Shares",
                        "buy",
                        "Gold rising on safe-haven or inflation-hedge flow",
                        "Gold gives back gains and closes below prior support",
                        "Risk-on rotation can reverse gold quickly",
                        urgency="medium",
                    )
                )
            elif gld_change is not None and gld_change < -1.5:
                signals.append(
                    self._signal(
                        "GLD",
                        "SPDR Gold Shares",
                        "sell",
                        "Gold weakening; risk-off bid fading or real rates rising",
                        "Gold recovers and closes above recent low",
                        "False breakdown or dollar strength may persist",
                        urgency="medium",
                    )
                )

        # Oil signal
        if macro.get("crude_oil") is not None or "USO" in quotes:
            uso_quote = quotes.get("USO", {})
            uso_change = uso_quote.get("pct_change") if isinstance(uso_quote, dict) else None
            if uso_change is not None and uso_change > 2.0:
                signals.append(
                    self._signal(
                        "USO",
                        "United States Oil Fund",
                        "buy",
                        "Oil price surging on supply or geopolitical risk",
                        "Oil rolls over and loses the breakout",
                        "Energy names can be volatile; geopolitical risk can unwind fast",
                        urgency="high",
                    )
                )
            elif uso_change is not None and uso_change < -2.0:
                signals.append(
                    self._signal(
                        "USO",
                        "United States Oil Fund",
                        "sell",
                        "Oil breaking down; supply relief or demand worry",
                        "Oil recovers above prior support",
                        "OPEC action or geopolitical flare can reverse the move",
                        urgency="medium",
                    )
                )

        # Bitcoin
        btc_quote = quotes.get("BTCUSDT", {})
        btc_change = btc_quote.get("pct_change") if isinstance(btc_quote, dict) else None
        if btc_change is not None and abs(btc_change) > 3.0:
            direction = "buy" if btc_change > 0 else "sell"
            signals.append(
                self._signal(
                    "BTCUSDT",
                    "Bitcoin",
                    direction,
                    f"Bitcoin moving {btc_change:+.2f}%",
                    "Price reverses the move intraday",
                    "Crypto is volatile and can gap against macro moves",
                    urgency="medium",
                )
            )

        # Theme-driven watchlist signals
        for cluster in clusters:
            if cluster.urgency in ("high", "critical") and cluster.confidence >= 0.5:
                for symbol in cluster.affected_symbols:
                    if any(symbol in h for h in self.holdings):
                        # Skip if we already generated a symbol-specific signal
                        if any(s.symbol == symbol for s in signals):
                            continue
                        signals.append(
                            self._signal(
                                symbol,
                                f"{symbol} linked to {cluster.theme}",
                                self._direction_from_sentiment(cluster.sentiment),
                                f"News flow: {cluster.summary[:120]}",
                                "Cluster narrative changes or price reverses",
                                f"Correlation with {cluster.theme} may break",
                                urgency=cluster.urgency,
                                confidence=cluster.confidence,
                            )
                        )

        # Sort by urgency and confidence
        urgency_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        signals.sort(key=lambda s: (urgency_order.get(s.urgency, 2), -s.confidence))
        return signals[:15]

    def _signal(
        self,
        symbol: str,
        name: str,
        direction: str,
        rationale: str,
        falsification: str,
        risk_source: str,
        *,
        horizon: str = "short_term",
        confidence: float = 0.6,
        urgency: str = "medium",
    ) -> IntelligenceSignal:
        return IntelligenceSignal(
            symbol=symbol,
            name=name,
            direction=direction,
            horizon=horizon,
            rationale=rationale,
            falsification=falsification,
            risk_source=risk_source,
            confidence=confidence,
            urgency=urgency,
            generated_at=datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _detect_theme(self, article: dict) -> str:
        text = f"{article.get('title', '')} {article.get('summary', '')}".lower()
        scores: dict[str, int] = {}
        for theme, keywords in THEME_KEYWORDS.items():
            scores[theme] = sum(1 for kw in keywords if kw.lower() in text)
        if not scores or max(scores.values()) == 0:
            return "general"
        return max(scores, key=scores.get)

    def _extract_symbols(self, articles: list[dict]) -> list[str]:
        symbols: set[str] = set()
        for article in articles:
            for tag in article.get("tags", []):
                if tag and len(tag) <= 10:
                    symbols.add(tag.upper())
            # Simple ticker extraction from title/summary
            text = f"{article.get('title', '')} {article.get('summary', '')}"
            for match in re.findall(r"([A-Z]{2,5})", text):
                if match not in {"ETF", "CPI", "GDP", "FED", "PPI", "PMI", "EPS", "AI", "US", "CN"}:
                    symbols.add(match)
        return sorted(symbols)[:10]

    def _aggregate_sentiment(self, articles: list[dict]) -> str:
        positive = 0
        negative = 0
        for article in articles:
            text = f"{article.get('title', '')} {article.get('summary', '')}".lower()
            positive += sum(1 for w in MARKET_SENTIMENT_POSITIVE if w.lower() in text)
            negative += sum(1 for w in MARKET_SENTIMENT_NEGATIVE if w.lower() in text)
        if positive > negative * 1.5:
            return "positive"
        if negative > positive * 1.5:
            return "negative"
        return "neutral"

    def _aggregate_urgency(self, articles: list[dict]) -> str:
        if not articles:
            return "low"
        # Count strong negative / crisis words
        critical_words = ["crash", "war", "attack", "invasion", "default", "collapse", "recession"]
        count = 0
        for article in articles:
            text = f"{article.get('title', '')} {article.get('summary', '')}".lower()
            count += sum(1 for w in critical_words if w in text)
        if count >= 3:
            return "critical"
        if count >= 1:
            return "high"
        return "medium"

    def _direction_from_sentiment(self, sentiment: str) -> str:
        return {"positive": "positive", "negative": "negative", "neutral": "neutral"}.get(
            sentiment, "neutral"
        )

    def _build_data_quality(
        self,
        snapshots: list[IntelligenceSnapshot],
        clusters: list[EventCluster],
        signals: list[IntelligenceSignal],
        articles: list[dict],
    ) -> dict:
        errors = []
        if len(snapshots) < 1:
            errors.append("No snapshots available")
        if len(articles) < 5:
            errors.append("Insufficient articles for analysis")
        if not clusters:
            errors.append("No event clusters formed")
        return {
            "status": "ok" if not errors else "degraded",
            "errors": errors,
            "snapshots": len(snapshots),
            "articles": len(articles),
            "clusters": len(clusters),
            "signals": len(signals),
        }


def _parse_iso(value: str) -> datetime:
    if not value:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
