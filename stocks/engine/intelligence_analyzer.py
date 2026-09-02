"""Intelligence analyzer: cluster news, assess market impact, generate signals.

Operates on raw snapshots from NewsIntelligenceStore.
LLMIntelligenceAnalyzer makes one LLM call per harvest for semantic analysis.
IntelligenceAnalyzer is the keyword-rules fallback.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from stocks.engine.config_loader import DEFAULT_ENGINE_CONFIG
from stocks.engine.news_intelligence_store import (
    EventCluster,
    IntelligenceSignal,
    IntelligenceSnapshot,
)
from stocks.engine.quant_action import _INTEL_SIGNAL_PROXY
from stocks.logging_utils import get_logger

logger = get_logger("intelligence_analyzer")


_INTELLIGENCE_CFG = DEFAULT_ENGINE_CONFIG.get("intelligence", {})


# Theme keywords grouped by macro topic. Defaults are copied from the legacy
# hard-coded list; they can be overridden via DEFAULT_ENGINE_CONFIG["intelligence"].
THEME_KEYWORDS: dict[str, list[str]] = dict(
    _INTELLIGENCE_CFG.get("theme_keywords", {})
    or {
        "geopolitics": ["war", "conflict", "tension", "sanction", "iran", "israel", "ukraine", "russia", "china", "taiwan", "military", "strike", "drone", "attack"],
        "monetary_policy": ["fed", "federal reserve", "interest rate", "rate hike", "rate cut", "powell", "fomc", "central bank", " ECB ", "BOJ", "PBOC", "yield"],
        "earnings": ["earnings", "revenue", "profit", "guidance", "beat", "miss", "EPS", "quarterly", "reported", "results"],
        "technology": ["AI", "artificial intelligence", "chip", "semiconductor", "nvidia", "tesla", "big tech", "magnificent seven", "tech stock", "cloud"],
        "energy": ["oil", "crude", "energy", "OPEC", "gas", "petroleum", "renewable", "solar"],
        "macro_data": ["CPI", "inflation", "PPI", "GDP", "nonfarm", "unemployment", "jobs report", "retail sales", "PMI", "industrial production"],
    }
)

MARKET_SENTIMENT_POSITIVE: list[str] = list(
    _INTELLIGENCE_CFG.get("positive_keywords", [])
    or ["surge", "rally", "jump", "soar", "gain", "rise", "record high", "bullish", "strong", "beat", "raise guidance", "optimistic"]
)
MARKET_SENTIMENT_NEGATIVE: list[str] = list(
    _INTELLIGENCE_CFG.get("negative_keywords", [])
    or ["plunge", "crash", "tumble", "slump", "drop", "fall", "bearish", "recession", "miss", "cut guidance", "fear", "panic", "sell-off"]
)

# Map theme to typical affected markets/assets.
THEME_MARKETS: dict[str, list[str]] = dict(
    _INTELLIGENCE_CFG.get("theme_markets", {})
    or {
        "geopolitics": ["equity", "oil", "gold", "dxy"],
        "monetary_policy": ["equity", "bond", "dxy", "gold"],
        "earnings": ["equity", "tech"],
        "technology": ["equity", "tech"],
        "energy": ["oil", "equity", "energy"],
        "macro_data": ["equity", "bond", "dxy", "gold"],
    }
)


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

    # Known valid ticker/ETF symbols. Defaults are the legacy hard-coded list;
    # they can be overridden via DEFAULT_ENGINE_CONFIG["intelligence"]["known_symbols"].
    _KNOWN_SYMBOLS = frozenset(
        _INTELLIGENCE_CFG.get("known_symbols", [])
        or [
            "SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "IVV", "VWO", "EFA",
            "GLD", "SLV", "GDX", "NEM", "XAU",
            "USO", "XLE", "XOM", "CVX", "OIH",
            "TLT", "IEF", "SHY", "AGG", "LQD", "HYG", "BND", "SGOV",
            "EEM", "FXI", "KWEB", "ASHR", "MCHI",
            "NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "AVGO",
            "AMD", "INTC", "QCOM", "MU", "ARM", "SMCI",
            "JPM", "GS", "BAC", "WFC", "C", "MS", "BLK", "V", "MA",
            "XLV", "XBI", "XLP", "XLY", "XLF", "XLI", "XLK", "XLU", "XLB",
            "ITA", "NOC", "LMT", "RTX", "PPA",
            "SOXX", "SMH", "SOXL", "SOXS",
            "BTC", "ETH", "BTCUSDT", "ETHUSDT",
            "VIX", "VIXY", "UVXY", "VXX", "SVXY",
            "GC", "CL", "NG", "SI", "HG", "ZC", "ZS", "ZW",
        ]
    )

    _NOISE_WORDS = frozenset({
        "CRYPTO", "GENERAL", "STOCK", "STOCKS", "MARKET", "TRADE",
        "TRADING", "BANK", "BANKS", "FUND", "FUNDS", "INDEX",
        "RATE", "RATES", "YIELD", "BOND", "BONDS", "OIL",
        "GOLD", "SILVER", "ENERGY", "METAL", "METALS",
        "CHIP", "CHIPS", "TECH", "FINANCE", "HEALTH",
        "DATA", "NEWS", "ALERT", "UPDATE", "REPORT",
        "BREAKING", "LATEST", "WATCH", "LIVE", "EXCLUSIVE",
    })

    def __init__(
        self,
        *,
        lookback_hours: int = 6,
        holdings: Optional[list[str]] = None,
    ):
        self.lookback_hours = max(1, lookback_hours)
        self.holdings = set(holdings or [])
        self._article_index = 0

    def analyze(
        self,
        snapshots: list[IntelligenceSnapshot],
        *,
        analyzed_at: Optional[datetime] = None,
    ) -> AnalysisResult:
        analyzed_at = analyzed_at or datetime.now(timezone.utc)
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

        # Bitcoin 动量规则已移除(2026-09-02): "跌>3%→sell" 在 8 月上升趋势中
        # 持续逆势, 结算 149 样本 0% 胜率(去重后 14 独立样本仍 0%)。
        # 跨日多样本支撑的唯一确定性失败源, 裁剪。

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
            generation_method="rule_fallback",
            source_as_of=datetime.now(timezone.utc),
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


def _robust_json_parse(content: str) -> Optional[dict]:
    """Parse JSON from LLM output, handling common formatting issues.

    Tries multiple strategies: strict parse, markdown extraction,
    bracket extraction, and basic repair (trailing commas, truncated output).
    """
    attempts = [content]

    # Strategy 1: extract from markdown code block
    if "```json" in content:
        attempts.append(content.split("```json")[1].split("```")[0].strip())
    elif "```" in content:
        attempts.append(content.split("```")[1].split("```")[0].strip())

    # Strategy 2: extract from first { to last }
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        attempts.append(content[start:end+1])

    for attempt in attempts:
        if not attempt or not attempt.strip():
            continue
        # Strategy A: strict parse
        try:
            return json.loads(attempt)
        except json.JSONDecodeError:
            pass
        # Strategy B: strip trailing commas before ] or }
        import re as _re_strict
        fixed = _re_strict.sub(r',\s*}', '}', attempt)
        fixed = _re_strict.sub(r',\s*]', ']', fixed)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass
        # Strategy C: try truncating to the last complete JSON structure
        # LLM output often gets truncated mid-array or mid-object.
        # Walk backwards from the end, try to close open structures.
        stripped = attempt.rstrip()
        if stripped.endswith(','):
            stripped = stripped[:-1]
            try:
                return json.loads(stripped + "\n]\n}")
            except json.JSONDecodeError:
                pass
        # Strategy D: find last complete cluster + signal, reconstruct
        # Count braces to find valid closing point
        depth = 0
        last_good_pos = 0
        in_string = False
        escaped = False
        for i, ch in enumerate(stripped):
            if escaped:
                escaped = False
                continue
            if ch == '\\':
                escaped = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch in '{[':
                depth += 1
            elif ch in '}]':
                depth -= 1
                if depth == 0:
                    last_good_pos = i + 1
        if last_good_pos > 0 and last_good_pos < len(stripped):
            truncated = stripped[:last_good_pos]
            try:
                return json.loads(truncated)
            except json.JSONDecodeError:
                pass

    return None


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


class LLMIntelligenceAnalyzer:
    """LLM-driven intelligence analyzer — replaces keyword matching with semantic analysis.

    One LLM call per harvest handles: dedup, multi-label clustering, sentiment×asset,
    cross-cluster synthesis, and portfolio-aware signal generation.

    Falls back to IntelligenceAnalyzer (keyword rules) on any failure.
    """

    _LLM_PROMPT_SYSTEM = (
        "你是全球市场情报分析师。分析新闻文章，生成结构化JSON。\n\n"
        "规则:\n"
        "1. 去重: 同一事件的多篇报道合并为一条dedup_article，列出article_ids\n"
        "2. 聚类: 按主题分cluster，summary_cn用中文写1-2句摘要\n"
        "3. 主题: theme用英文(geopolitics/monetary_policy/macro_data/china_policy/earnings/energy/technology/semiconductor/new_energy/consumer/healthcare/financials/real_estate/defense/utilities/crypto/general)\n"
        "4. 信号: 仅在置信度>=0.65时生成；symbol 优先从'用户持仓'清单里选相关标的代码(如 a:512480/us:NVDA)；"
        "若持仓清单无直接相关标的，才用大类代理(SPY/QQQ/GLD/USO/XLE/NVDA)代表方向\n"
        "5. article_ids: 每个cluster必须包含article_ids数组，引用新闻的编号[0][1]等\n"
        "6. 跨集群合成: cross_cluster_synthesis_cn用中文写一句传导链\n"
        "7. 没有重要事件时clusters可为空\n\n"
        "输出严格JSON:\n"
        '{"schema_version":1,"dedup_articles":[{"article_ids":[0,3],"representative_title":"...","duplicate_count":2}],'
        '"clusters":[{"theme":"geopolitics","sub_cluster":"us_iran","summary_cn":"...","article_ids":[0,3],'
        '"sentiment":{"equity":"bearish","oil":"bullish","gold":"bullish"},"urgency":"critical","confidence":0.85}],'
        '"cross_cluster_synthesis_cn":"","signals":[{"symbol":"USO","direction":"buy","rationale_cn":"...","source_article_ids":[0,3],'
        '"confidence":0.75,"falsification_cn":"..."}],"notes":[]}'
    )

    def __init__(
        self,
        *,
        holdings: Optional[list[str]] = None,
        model: str = "deepseek-v4-pro",
        temperature: float = 0.1,
        timeout: int = 90,
        max_input_articles: int = 80,
        fallback_to_rules: bool = True,
        env_file_path: Optional[Path] = None,
        base_url: Optional[str] = None,
    ):
        self.holdings = set(holdings or [])
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.max_input_articles = max_input_articles
        self.fallback_to_rules = fallback_to_rules
        self._target_signal_count = 8  # minimum signals per run
        self._env_file_path = env_file_path
        # _api_key_override: 保留字段供未来传参扩展; 当前 __init__ 无 api_key 参数
        self._api_key_override: Optional[str] = None
        self._base_url_override = base_url
        self._fallback = IntelligenceAnalyzer(lookback_hours=6, holdings=list(self.holdings))
        self._api_key: Optional[str] = None
        self._base_url: Optional[str] = None

    def _load_api_config(self) -> tuple[str, str]:
        """解析 LLM API key 与 base_url。

        8/11 修复: 与 `stocks/engine/__init__.py::_load_openai_config` 完全对齐
        —— 优先级 传参 > 进程环境变量 > `.secret/*.md` bare-value 文件。
        此前本方法从 `/opt/data/.env` 读 OPENAI_COMPATIBLE_API_KEY, 该 key
        (sk-UDyoWeD) 对 deepseek base_url 返回 401, 且 base_url 从不读 env
        文件 → 每次 LLM 调用失败 → 静默回退规则分析 → 信号层常年 0 信号。
        """
        if self._api_key and self._base_url:
            return self._api_key, self._base_url

        secret_dir = Path(__file__).resolve().parents[2] / ".secret"

        # 显式传入 secret env 文件时沿用 KEY=VALUE 解析(兼容旧配置)
        env_key = ""
        env_url = ""
        if self._env_file_path is not None:
            env_file = Path(self._env_file_path)
            if env_file.exists():
                for line in env_file.read_text().splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("\"'")
                    if k in ("OPENAI_COMPATIBLE_API_KEY", "OPENAI_API_KEY") and not env_key:
                        env_key = v
                    if k in ("OPENAI_BASE_URL", "OPENAI_COMPATIBLE_BASE_URL", "STOCKS_LLM__FALLBACK_BASE_URL") and not env_url:
                        env_url = v

        # 优先级(2026-08-12 修正): .secret 工作文件 > env 文件 > os.environ。
        # 关键: os.environ 的 OPENAI_COMPATIBLE_API_KEY 可能是其他服务
        # (Lyric/Syl profile) 的残留 key, 对 deepseek base_url 401 —
        # 之前让 os.environ 优先导致生产路径永远 401 → 回退规则 → 0 信号。
        # .secret/openai-key.md 是 stocks/engine/__init__.py 消费的权威
        # 工作 key, 必须最优先。
        api_key = ""
        if not api_key:
            key_file = secret_dir / "openai-key.md"
            if key_file.exists():
                api_key = key_file.read_text("utf-8").strip()
        if not api_key:
            api_key = env_key
        if not api_key:
            api_key = os.environ.get("OPENAI_API_KEY", "") or os.environ.get("OPENAI_COMPATIBLE_API_KEY", "")

        base_url = ""
        if not base_url:
            url_file = secret_dir / "openai-base-url.md"
            if url_file.exists():
                base_url = url_file.read_text("utf-8").strip()
        if not base_url:
            base_url = env_url
        if not base_url:
            base_url = os.environ.get("OPENAI_BASE_URL", "") or os.environ.get("STOCKS_LLM__FALLBACK_BASE_URL", "")

        self._api_key = api_key or ""
        self._base_url = base_url or ""
        return self._api_key, self._base_url

    def analyze(self, snapshots: list[IntelligenceSnapshot]) -> AnalysisResult:
        analyzed_at = datetime.now(timezone.utc)
        if not snapshots:
            return AnalysisResult(
                analyzed_at=analyzed_at, clusters=[], market_impact={},
                signals=[], data_quality={"status": "degraded", "errors": ["no snapshots"]},
            )

        primary = max(snapshots, key=lambda s: s.collected_at)
        articles = primary.articles[:self.max_input_articles]
        macro = primary.macro or {}

        if not articles:
            return self._fallback.analyze(snapshots)

        api_key, base_url = self._load_api_config()
        if not api_key:
            logger.warning("LLMIntelligenceAnalyzer: no API key — falling back to rules")
            return self._fallback_analyze(snapshots)

        try:
            llm_result = self._call_llm(articles, macro, api_key, base_url)
            clusters = self._parse_clusters(llm_result, articles)
            signals = self._parse_signals(llm_result)
            market_impact = self._build_market_impact(llm_result, clusters, macro, signals)
            data_quality = self._build_quality(llm_result, articles, clusters, signals)

            # affected_symbols 保持 LLM 原样(LLM 标了就标了,没标就空)。
            # 旧 backfill 用 sorted(signal_symbols)[:3] 一刀切回填,让所有
            # cluster 的 affected_symbols 相同(能源 cluster 标黄金),产生错误
            # 关联。真正的主题→持仓匹配在消费端 match_intelligence
            # (theme_to_exposure)。market_impact 在本段之前已用原样 clusters
            # 算完,不受影响。

            # F3(2026-08-12): 不再调用 _pad_category_signals — analyzer 层
            # 静态 9 类 padding 与消费端 match_intelligence 的动态
            # category padding 双层冗余; 保留消费端(按实际持仓 exposure_tag
            # 补位, _compute_coverage 已排除其 directional 统计)。
            # signals 保持真实 LLM 候选(3-4 条), 不注入规则 hold 占位。

            return AnalysisResult(
                analyzed_at=analyzed_at,
                clusters=clusters,
                market_impact=market_impact,
                signals=signals,
                data_quality=data_quality,
                metadata={
                    "analysis_mode": "llm",
                    "model": self.model,
                    "articles_input": len(articles),
                    "dedup_count": len(llm_result.get("dedup_articles", [])),
                    "cross_cluster": llm_result.get("cross_cluster_synthesis_cn", ""),
                },
            )
        except Exception as exc:
            logger.warning(f"LLMIntelligenceAnalyzer failed: {exc} — falling back to rules")
            if self.fallback_to_rules:
                return self._fallback_analyze(snapshots)
            raise

    def _fallback_analyze(self, snapshots: list[IntelligenceSnapshot]) -> AnalysisResult:
        result = self._fallback.analyze(snapshots)
        # Tag as fallback
        dq = dict(result.data_quality)
        dq["analysis_mode"] = "fallback_rules"
        return AnalysisResult(
            analyzed_at=result.analyzed_at,
            clusters=result.clusters,
            market_impact=result.market_impact,
            signals=result.signals,
            data_quality=dq,
            metadata={**(result.metadata or {}), "analysis_mode": "fallback_rules"},
        )

    def _call_llm(self, articles: list[dict], macro: dict, api_key: str, base_url: str) -> dict:
        prompt = self._build_prompt(articles, macro)
        payload = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._LLM_PROMPT_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature,
            # 8/11 修复: deepseek-v4-flash 是推理模型, 思考链占用大量 token,
            # 8000 会被 finish_reason=length 截断导致 content 为空。提到 24000。
            "max_tokens": 24000,
        }, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        # 8/11 修复: 推理模型(content 为空)时回退 reasoning_content,
        # 与 outlook_synthesizer._parse_response 同源处理。
        message = result["choices"][0].get("message", {})
        content = (message.get("content") or "").strip()
        if not content:
            content = (message.get("reasoning_content") or "").strip()
        # Extract JSON from markdown code block if present
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        parsed = _robust_json_parse(content)
        if parsed is None:
            raise ValueError(f"Failed to parse LLM response as JSON: {content[:200]}...")

        if not isinstance(parsed, dict):
            raise ValueError(f"LLM returned non-dict: {type(parsed)}")
        return parsed

    def _build_prompt(self, articles: list[dict], macro: dict) -> str:
        parts = []

        # Holdings context
        if self.holdings:
            parts.append(f"## 用户持仓\n{', '.join(sorted(self.holdings))}\n")

        # Macro context
        macro_items = []
        for key, label in [
            ("vix", "VIX"), ("us_10y_yield", "US10Y"), ("dxy", "DXY"),
            ("usd_cny", "USDCNY"), ("crude_oil", "OIL"), ("gold", "GOLD"),
        ]:
            val = macro.get(key)
            if val is not None:
                macro_items.append(f"{label}={val}")
        if macro_items:
            parts.append(f"## 宏观数据\n{', '.join(macro_items)}\n")

        # Articles
        parts.append(f"## 新闻文章 ({len(articles)} 篇)\n")
        for i, a in enumerate(articles):
            title = a.get("title", "")[:150]
            source = a.get("source_name", "?")
            published = (a.get("published_at") or "")[:19]
            summary = (a.get("summary") or "")[:200]
            parts.append(f"[{i}] {title}")
            parts.append(f"    来源: {source} | {published}")
            if summary:
                parts.append(f"    摘要: {summary}")
            parts.append("")

        parts.append("请分析以上数据，输出JSON。")
        return "\n".join(parts)

    def _parse_clusters(self, llm_result: dict, articles: list[dict]) -> list[EventCluster]:
        clusters = []
        for i, c in enumerate(llm_result.get("clusters", [])):
            article_ids = c.get("article_ids", c.get("articles", []))
            article_refs = []
            for aid in article_ids:
                if isinstance(aid, int) and 0 <= aid < len(articles):
                    a = articles[aid]
                    article_refs.append({
                        "title": a.get("title", ""),
                        "url": a.get("url", ""),
                        "source_name": a.get("source_name", ""),
                        "published_at": a.get("published_at"),
                    })
            # If LLM didn't return article IDs, use representative articles
            if not article_refs and articles:
                # Take first 3 articles as representative
                for a in articles[:3]:
                    article_refs.append({
                        "title": a.get("title", ""),
                        "url": a.get("url", ""),
                        "source_name": a.get("source_name", ""),
                        "published_at": a.get("published_at"),
                    })

            sentiment = c.get("sentiment", {})
            sentiment_str = "neutral"
            if isinstance(sentiment, dict):
                directions = list(sentiment.values())
                bulls = sum(1 for d in directions if d == "bullish")
                bears = sum(1 for d in directions if d == "bearish")
                sentiment_str = "positive" if bulls > bears else ("negative" if bears > bulls else "neutral")

            sub = c.get("sub_cluster", "")
            theme = c.get("theme", "general")
            summary = c.get("summary_cn", c.get("summary", f"{theme} related events"))

            clusters.append(EventCluster(
                cluster_id=f"{theme}_{i:04d}",
                theme=theme,
                event_type=theme,
                summary=f"[{sub}] {summary}" if sub else summary,
                articles=article_refs[:5],
                affected_markets=c.get("affected_markets", ["equity"]),
                affected_symbols=c.get("affected_symbols", []),
                sentiment=sentiment_str,
                urgency=c.get("urgency", "medium"),
                confidence=c.get("confidence", 0.7),
                formed_at=datetime.now(timezone.utc),
            ))
        return clusters

    def _parse_signals(self, llm_result: dict) -> list[IntelligenceSignal]:
        signals = []
        for s in llm_result.get("signals", []):
            sym = s.get("symbol", "") or s.get("asset", "") or "GENERAL"
            signals.append(IntelligenceSignal(
                symbol=sym,
                name=s.get("name", sym),
                direction=s.get("direction", "watch"),
                horizon=s.get("horizon", "short_term"),
                rationale=s.get("rationale_cn", s.get("rationale", "")),
                falsification=s.get("falsification_cn", s.get("falsification", "")),
                risk_source=s.get("risk_source", "llm_analysis"),
                confidence=s.get("confidence", 0.5),
                urgency=s.get("urgency", "medium"),
                generated_at=datetime.now(timezone.utc),
                generation_method="llm",
                source_as_of=datetime.now(timezone.utc),
                source_article_ids=[int(i) for i in (s.get("source_article_ids") or [])],
            ))
        return signals

    # Symbol → asset class mapping for market impact. Defaults are the legacy
    # hard-coded list; override via DEFAULT_ENGINE_CONFIG["intelligence"]["symbol_to_asset"].
    _SYMBOL_TO_ASSET = dict(
        _INTELLIGENCE_CFG.get("symbol_to_asset", {})
        or {
            "SPY": "equity", "QQQ": "equity", "NVDA": "equity", "IWM": "equity",
            "XLE": "oil", "USO": "oil",
            "GLD": "gold", "NEM": "gold", "IAU": "gold",
            "TLT": "bond", "SGOV": "bond", "SHY": "bond",
            "UUP": "dxy",
            "KWEB": "china_assets", "FXI": "china_assets", "ASHR": "china_assets",
        }
    )

    def _build_market_impact(
        self, llm_result: dict, clusters: list[EventCluster], macro: dict,
        signals: list[IntelligenceSignal],
    ) -> dict:
        impact = {
            "equity": {"direction": "neutral", "confidence": 0.0, "drivers": []},
            "bond": {"direction": "neutral", "confidence": 0.0, "drivers": []},
            "oil": {"direction": "neutral", "confidence": 0.0, "drivers": []},
            "gold": {"direction": "neutral", "confidence": 0.0, "drivers": []},
            "dxy": {"direction": "neutral", "confidence": 0.0, "drivers": []},
            "china_assets": {"direction": "neutral", "confidence": 0.0, "drivers": []},
        }
        # Layer 1: cluster-level sentiment (coarse)
        for c in clusters:
            for market in ["equity", "oil", "gold", "bond", "dxy"]:
                if market in (c.affected_markets or []):
                    impact[market]["drivers"].append(c.theme)
                    if c.sentiment == "positive":
                        impact[market]["direction"] = "positive"
                        impact[market]["confidence"] = max(impact[market]["confidence"], c.confidence)
                    elif c.sentiment == "negative":
                        impact[market]["direction"] = "negative"
                        impact[market]["confidence"] = max(impact[market]["confidence"], c.confidence)

        # Layer 2: signal-level direction (fine-grained, highest priority)
        for sig in signals:
            asset = self._SYMBOL_TO_ASSET.get(sig.symbol)
            if not asset or sig.direction == "watch":
                continue
            if sig.direction in ("buy", "accumulate", "long"):
                impact[asset]["direction"] = "positive"
                impact[asset]["confidence"] = max(impact[asset]["confidence"], sig.confidence)
                impact[asset]["drivers"].append(f"signal:{sig.symbol}_buy")
            elif sig.direction in ("sell", "reduce", "short"):
                impact[asset]["direction"] = "negative"
                impact[asset]["confidence"] = max(impact[asset]["confidence"], sig.confidence)
                impact[asset]["drivers"].append(f"signal:{sig.symbol}_sell")

        # Layer 3: macro override
        if macro.get("vix", 0) > 25:
            impact["equity"]["direction"] = "negative"
            impact["equity"]["drivers"].append(f"VIX elevated at {macro['vix']}")
        elif macro.get("vix", 100) < 15:
            impact["equity"]["drivers"].append(f"VIX calm at {macro['vix']}")
        if macro.get("us_10y_yield", 0) > 4.5:
            impact["bond"]["drivers"].append(f"10Y yield high at {macro['us_10y_yield']}%")

        return impact

    def _build_quality(self, llm_result: dict, articles: list[dict],
                       clusters: list[EventCluster], signals: list[IntelligenceSignal]) -> dict:
        errors = []
        if not clusters:
            errors.append("No event clusters formed")
        if len(articles) < 5:
            errors.append("Insufficient articles")
        notes = llm_result.get("notes", [])
        return {
            "status": "ok" if not errors else "degraded",
            "errors": errors + notes,
            "articles": len(articles),
            "clusters": len(clusters),
            "signals": len(signals),
            "analysis_mode": "llm",
            "dedup_articles": len(llm_result.get("dedup_articles", [])),
        }



# =======================================================================
# Task 3: MatchedSignal, unified matching, coverage, brief health
# =======================================================================


@dataclass(frozen=True)
class MatchedSignal:
    """A single matched intelligence signal for a position.

    Produced by match_intelligence() and consumed by _build_drivers,
    _detect_dissent, and IntelConflictRule — all three consumers get the
    same standardized result.
    """

    matched_symbol: str
    direction: str          # buy / sell / hold / neutral
    rationale: str
    generation_method: str  # llm / rule_fallback / category_padding
    match_method: str       # exact / proxy / exposure_tag / category
    source_as_of: datetime
    urgency: str = "medium"
    dissent: "Optional[dict]" = None  # R4 冲突证据(2026-08-12)


def coerce_intelligence_signals(raw_signals) -> list[IntelligenceSignal]:
    """Normalize persisted dicts or model objects into IntelligenceSignal objects."""
    if isinstance(raw_signals, dict):
        values = raw_signals.values()
    else:
        values = raw_signals or []
    result = []
    for item in values:
        if isinstance(item, IntelligenceSignal):
            result.append(item)
            continue
        if not isinstance(item, dict):
            continue
        payload = dict(item)
        payload.setdefault("symbol", "?")
        payload.setdefault("name", payload["symbol"])
        payload.setdefault("direction", "watch")
        payload.setdefault("horizon", "short_term")
        payload.setdefault("rationale", "")
        payload.setdefault("falsification", "")
        payload.setdefault("risk_source", "")
        payload.setdefault("confidence", 0.0)
        payload.setdefault("urgency", "medium")
        payload.setdefault("generated_at", datetime.now(timezone.utc).isoformat())
        result.append(IntelligenceSignal.from_dict(payload))
    return result


def match_intelligence(
    position: dict,
    signals: list[IntelligenceSignal],
) -> list[MatchedSignal]:
    """Unified intelligence matcher — produces standardized MatchedSignal list
    consumed by _build_drivers, _detect_dissent, and IntelConflictRule.

    Matching priority: exact suffix → proxy → exposure_tag → category_padding.
    Category padding only fires when no direct match exists for an exposure tag.
    """
    inst_key = str(position.get("instrument_key", "")).lower()
    classification = position.get("classification") or {}
    exposure_tags = [t.lower() for t in (classification.get("exposure_tags") or [])]
    matched: list[MatchedSignal] = []
    seen_symbols: set[str] = set()

    for sig in signals:
        sym = sig.symbol
        sym_lower = sym.lower()
        proxy = _INTEL_SIGNAL_PROXY.get(sym, sym)
        proxy_lower = proxy.lower()

        # 1. Exact match: instrument_key ends with :symbol
        if inst_key.endswith(f":{sym_lower}") or inst_key == sym_lower:
            matched.append(MatchedSignal(
                matched_symbol=sym,
                direction=sig.direction,
                rationale=sig.rationale,
                generation_method=sig.generation_method,
                match_method="exact",
                source_as_of=sig.source_as_of or sig.generated_at,
                urgency=sig.urgency,
                dissent=sig.dissent,
            ))
            seen_symbols.add(sym_lower)
            continue

        # 2. Proxy match: proxy target matches position key
        if (inst_key.endswith(f":{proxy_lower}")
                or proxy_lower == inst_key):
            matched.append(MatchedSignal(
                matched_symbol=sym,
                direction=sig.direction,
                rationale=sig.rationale,
                generation_method=sig.generation_method,
                match_method="proxy",
                source_as_of=sig.source_as_of or sig.generated_at,
                urgency=sig.urgency,
                dissent=sig.dissent,
            ))
            seen_symbols.add(sym_lower)
            continue

        # 3. Exposure tag match
        if (sym_lower in exposure_tags
                or sig.name.lower() in exposure_tags
                or proxy_lower in exposure_tags):
            matched.append(MatchedSignal(
                matched_symbol=sym,
                direction=sig.direction,
                rationale=sig.rationale,
                generation_method=sig.generation_method,
                match_method="exposure_tag",
                source_as_of=sig.source_as_of or sig.generated_at,
                urgency=sig.urgency,
                dissent=sig.dissent,
            ))
            seen_symbols.add(sym_lower)

    # 4. Category padding: only when an intelligence payload exists.
    # Empty/stale signal sets must remain unavailable, not synthetic-neutral.
    if not signals:
        return matched
    for tag in exposure_tags:
        if tag not in seen_symbols:
            matched.append(MatchedSignal(
                matched_symbol=tag,
                direction="neutral",
                rationale=f"Category padding — no direct signal for {tag}",
                generation_method="category_padding",
                match_method="category",
                source_as_of=datetime.now(timezone.utc),
            ))
            seen_symbols.add(tag)

    return matched


def _compute_coverage(matched: list[MatchedSignal]) -> dict:
    """Compute 6-dimension coverage breakdown.

    - field: total matched signals with populated fields
    - directional: signals with non-neutral direction AND not from
      category_padding (padding == coverage by field only, not direction)
    - padding: signals from category_padding only
    - exact / proxy / category: by match_method
    """
    unique = list({
        (
            m.matched_symbol, m.direction, m.generation_method,
            m.match_method, m.source_as_of.isoformat(),
        ): m
        for m in matched
    }.values())
    field = len(unique)
    directional = sum(
        1 for m in unique
        if m.generation_method != "category_padding"
        and m.direction in (
            "buy", "sell", "reduce", "bullish", "bearish",
            "positive", "negative",
        )
    )
    padding = sum(1 for m in unique if m.generation_method == "category_padding")
    exact = sum(1 for m in unique if m.match_method == "exact")
    proxy = sum(1 for m in unique if m.match_method == "proxy")
    exposure_tag = sum(1 for m in unique if m.match_method == "exposure_tag")
    category = sum(1 for m in unique if m.match_method == "category")
    return {
        "field": field,
        "directional": directional,
        "padding": padding,
        "exact": exact,
        "proxy": proxy,
        "exposure_tag": exposure_tag,
        "category": category,
    }



def _intel_consensus_direction_from_matched(matched: list) -> str:
    """Determine consensus direction from matched signals.
    Category-padding signals are ignored for direction consensus.

    Shared by _build_drivers and IntelConflictRule so both consumers
    get the same opinion from the same matched signals.
    """
    dirs = []
    for m in matched:
        if m.generation_method == "category_padding" and m.direction == "neutral":
            continue
        d = m.direction.lower()
        if d in ("buy", "bullish", "positive"):
            dirs.append("bullish")
        elif d in ("sell", "bearish", "negative", "reduce"):
            dirs.append("bearish")
        else:
            dirs.append("neutral")
    b = dirs.count("bullish")
    s = dirs.count("bearish")
    if b > s:
        return "bullish"
    if s > b:
        return "bearish"
    return "neutral"


def _compute_brief_health(
    watch_collected_at: datetime,
    brief_generated_at: datetime,
    *,
    max_age_hours: float = 48.0,
) -> dict:
    """Evaluate brief health by comparing its generation time against the
    latest global watch collection time.

    Returns dict with:
      status: "ok" | "stale"
      age_minutes: age in minutes
      risk_eligible: False when stale (brief must not participate in
                     risk state upgrades)
    """
    age = watch_collected_at - brief_generated_at
    age_minutes = max(0.0, age.total_seconds() / 60.0)
    stale = age_minutes >= max_age_hours * 60.0
    return {
        "status": "stale" if stale else "ok",
        "age_minutes": round(age_minutes, 1),
        "risk_eligible": not stale,
    }
