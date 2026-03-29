from __future__ import annotations

"""
主题分析服务 - 已冻结 (Frozen)

状态说明:
- 该服务当前处于冻结状态，不再新增主题、关键词或复杂评分逻辑
- 保留现有功能作为市场状态层的输入之一
- 后续建议质量提升应通过增强 LLM 分析能力实现，而非扩展此规则引擎

设计原则:
- 轻量主题识别即可，不追求 exhaustive 覆盖
- 让 LLM 基于原始新闻做综合判断，而非依赖硬编码主题映射
- 如需新增市场观察维度，优先在 MarketState 层做轻量归纳

修改约束:
- 可修复 bug
- 不可新增主题、关键词、评分规则
- 不可扩展为更复杂的主题引擎
"""

from collections import defaultdict

from stocks.services.market_signal_service import MarketSignalService
from stocks.services.news_input_service import NewsInputService


class ThemeAnalysisService:
    THEME_RELATIONS = {
        'AI基础设施': {'cluster': '科技链', 'aliases': ['算力', '半导体', 'AI']},
        '美股科技': {'cluster': '科技链', 'aliases': ['纳指科技', '美股AI']},
        '美股医药': {'cluster': '医药链', 'aliases': ['制药', '生物科技']},
        '支付与金融科技': {'cluster': '金融科技链', 'aliases': ['支付', 'fintech']},
        '黄金': {'cluster': '避险链', 'aliases': ['贵金属', '避险']},
        '卫星': {'cluster': '军工航天链', 'aliases': ['航天', '卫星互联网']},
    }

    HOT_KEYWORDS = {
        '黄金': '黄金',
        'gold': '黄金',
        '避险': '黄金',
        'AI': 'AI基础设施',
        '算力': 'AI基础设施',
        '半导体': 'AI基础设施',
        'nvidia': '美股科技',
        'apple': '美股科技',
        'microsoft': '美股科技',
        'nasdaq': '美股科技',
        'tech': '美股科技',
        'pfizer': '美股医药',
        'eli lilly': '美股医药',
        'pharma': '美股医药',
        'paypal': '支付与金融科技',
        'visa': '支付与金融科技',
        'mastercard': '支付与金融科技',
        'payments': '支付与金融科技',
    }

    COLD_KEYWORDS = {
        '卫星': '卫星',
        '航天': '卫星',
        'biotech slump': '美股医药',
        'payment slowdown': '支付与金融科技',
    }

    def __init__(
        self,
        news_service: NewsInputService | None = None,
        market_signal_service: MarketSignalService | None = None,
    ):
        self.news_service = news_service or NewsInputService()
        self.market_signal_service = market_signal_service or MarketSignalService()

    def analyze(self, news_limit: int = 10) -> dict:
        news_items = self.news_service.latest_items(limit=news_limit)
        hot_hits: dict[str, list[dict]] = defaultdict(list)
        cold_hits: dict[str, list[dict]] = defaultdict(list)
        hot_scores: dict[str, float] = defaultdict(float)
        cold_scores: dict[str, float] = defaultdict(float)

        for item in news_items:
            title = str(item.get('title') or '')
            summary = str(item.get('summary') or '')
            tags = ' '.join(item.get('tags') or [])
            text = ' '.join([title, summary, tags]).lower()

            hot_matched: set[str] = set()
            cold_matched: set[str] = set()
            for keyword, theme in self.HOT_KEYWORDS.items():
                if keyword.lower() in text:
                    hot_matched.add(theme)
                    hot_scores[theme] += self._keyword_score(keyword, title, summary, tags)
            for keyword, theme in self.COLD_KEYWORDS.items():
                if keyword.lower() in text:
                    cold_matched.add(theme)
                    cold_scores[theme] += self._keyword_score(keyword, title, summary, tags)

            for theme in hot_matched:
                hot_hits[theme].append(item)
            for theme in cold_matched:
                cold_hits[theme].append(item)

        cluster_scores = self._build_cluster_scores(hot_scores, cold_scores)
        hot_sector = self._pick_theme(hot_hits, hot_scores, temperature='hot')
        cold_sector = self._pick_theme(cold_hits, cold_scores, temperature='cold')
        hot_sector = self._enrich_theme_relation(hot_sector) if hot_sector is not None else None
        cold_sector = self._enrich_theme_relation(cold_sector) if cold_sector is not None else None
        if hot_sector is not None:
            hot_sector = self._attach_market_validation(hot_sector)
            hot_sector = self._classify_theme(hot_sector)
        if cold_sector is not None:
            cold_sector = self._attach_market_validation(cold_sector)
            cold_sector = self._classify_theme(cold_sector)

        watch_themes = self._build_watch_themes(hot_hits, cold_hits, hot_sector, cold_sector)

        if cold_sector is None:
            cold_sector = self._empty_theme('cold', '今日未识别到合格冷板块')

        if hot_sector is None:
            hot_sector = self._empty_theme('hot', '今日未识别到合格热板块')

        observations = self._build_observations(hot_sector, cold_sector, cluster_scores)

        return {
            'hot_sector': hot_sector,
            'cold_sector': cold_sector,
            'watch_themes': [self._enrich_theme_relation(item) for item in watch_themes],
            'market_observations': observations,
            'cluster_scores': cluster_scores,
            'news_count': len(news_items),
        }

    def _empty_theme(self, temperature: str, news_support: str) -> dict:
        return {
            'name': None,
            'theme_type': 'narrative',
            'temperature': temperature,
            'status': 'none',
            'cluster': None,
            'aliases': [],
            'news_support': news_support,
            'market_support': '待接行情验证',
            'future_potential': '待补充',
            'market_validation': None,
        }

    def _enrich_theme_relation(self, sector: dict) -> dict:
        sector = dict(sector)
        relation = self.THEME_RELATIONS.get(sector.get('name')) or {}
        sector['cluster'] = relation.get('cluster')
        sector['aliases'] = relation.get('aliases', [])
        return sector

    def _classify_theme(self, sector: dict) -> dict:
        sector = dict(sector)
        validation = sector.get('market_validation') or {}
        signals = validation.get('signals') or []
        hit_count = max(1, len([item for item in signals if not item.get('error')]))
        positive_count = validation.get('positives') or 0
        news_strength = 2 if '；' in (sector.get('news_support') or '') else 1

        if positive_count >= max(1, hit_count // 2) and news_strength >= 1:
            sector['status'] = 'confirmed'
        elif news_strength >= 1:
            sector['status'] = 'watch'
        else:
            sector['status'] = 'none'
        return sector

    def _build_watch_themes(
        self,
        hot_hits: dict[str, list[dict]],
        cold_hits: dict[str, list[dict]],
        hot_sector: dict | None,
        cold_sector: dict | None,
    ) -> list[dict]:
        selected = {item.get('name') for item in (hot_sector, cold_sector) if item and item.get('name')}
        selected_clusters = {item.get('cluster') for item in (hot_sector, cold_sector) if item and item.get('cluster')}
        watches: list[dict] = []
        merged = []
        for theme, items in hot_hits.items():
            merged.append((theme, items, 'hot', self._theme_score(items)))
        for theme, items in cold_hits.items():
            merged.append((theme, items, 'cold', self._theme_score(items)))

        for theme, items, temperature, score in sorted(merged, key=lambda x: (x[3], len(x[1])), reverse=True):
            if theme in selected:
                continue
            sector = self._pick_theme({theme: items}, {theme: score}, temperature=temperature)
            if sector is None:
                continue
            sector = self._enrich_theme_relation(sector)
            if sector.get('cluster') and sector.get('cluster') in selected_clusters:
                continue
            sector = self._attach_market_validation(sector)
            sector = self._classify_theme(sector)
            if sector.get('status') != 'none':
                watches.append(sector)
                if sector.get('cluster'):
                    selected_clusters.add(sector.get('cluster'))
            if len(watches) >= 2:
                break
        return watches

    def _build_cluster_scores(self, hot_scores: dict[str, float], cold_scores: dict[str, float]) -> list[dict]:
        cluster_totals: dict[str, float] = defaultdict(float)
        cluster_themes: dict[str, set[str]] = defaultdict(set)

        for theme, score in {**hot_scores, **cold_scores}.items():
            relation = self.THEME_RELATIONS.get(theme) or {}
            cluster = relation.get('cluster')
            if not cluster:
                continue
            cluster_totals[cluster] += score
            cluster_themes[cluster].add(theme)

        ranked = []
        for cluster, score in cluster_totals.items():
            ranked.append({
                'cluster': cluster,
                'score': round(score, 2),
                'themes': sorted(cluster_themes[cluster]),
            })
        return sorted(ranked, key=lambda x: (x['score'], len(x['themes'])), reverse=True)

    def _keyword_score(self, keyword: str, title: str, summary: str, tags: str) -> float:
        score = 0.0
        key = keyword.lower()
        if key in title.lower():
            score += 2.0
        if key in summary.lower():
            score += 1.0
        if key in tags.lower():
            score += 1.5
        return score or 0.5

    def _theme_score(self, items: list[dict]) -> float:
        unique_titles = {str(item.get('title') or '').strip().lower() for item in items if item.get('title')}
        return len(unique_titles) + len(items) * 0.1

    def _pick_theme(self, hits: dict[str, list[dict]], scores: dict[str, float], temperature: str) -> dict | None:
        if not hits:
            return None
        theme, items = max(hits.items(), key=lambda x: (scores.get(x[0], 0), len(x[1])))
        titles = []
        seen = set()
        for item in items:
            title = item.get('title')
            if title and title not in seen:
                titles.append(title)
                seen.add(title)
            if len(titles) >= 2:
                break
        return {
            'name': theme,
            'theme_type': 'narrative',
            'temperature': temperature,
            'news_support': '；'.join(titles) if titles else '新闻命中主题关键词',
            'news_score': round(scores.get(theme, 0), 2),
            'market_support': '待接行情验证',
            'future_potential': '基于新闻叙事初步判断，待补充行情与更细分析',
            'market_validation': None,
        }

    def _attach_market_validation(self, sector: dict) -> dict:
        validation = self.market_signal_service.validate_theme(sector.get('name'))
        sector = dict(sector)
        sector['market_validation'] = validation
        sector['market_support'] = validation.get('summary') or sector.get('market_support')
        return sector

    def _build_observations(self, hot_sector: dict, cold_sector: dict, cluster_scores: list[dict]) -> dict:
        def summarize_signals(validation: dict | None) -> str:
            signals = (validation or {}).get('signals', [])[:3]
            parts = []
            for item in signals:
                if item.get('pct_change') is None or item.get('error'):
                    continue
                parts.append(f"{item.get('asset_name')} {item.get('pct_change')}%")
            return '，'.join(parts) if parts else '暂无清晰代表资产表现'

        risk_signal = self.market_signal_service.get_observation_signal('risk_on')
        gold_signal = self.market_signal_service.get_observation_signal('gold')
        us_signal = self.market_signal_service.get_observation_signal('us_benchmark')
        china_signal = self.market_signal_service.get_observation_signal('china_benchmark')
        lead_cluster = cluster_scores[0] if cluster_scores else None

        risk_lines = []
        if lead_cluster and lead_cluster.get('cluster') == '避险链':
            risk_lines.append('避险链得分领先，风险偏好更偏防守而非进攻')
        elif hot_sector.get('cluster') == '科技链' and (risk_signal.get('negatives') or 0) >= 2:
            risk_lines.append('科技链仍是主线，但风险偏好在降温，主线更像高波动承压')
        elif hot_sector.get('name') == '黄金':
            risk_lines.append('风险偏好偏保守，资金更关注避险线索')
        elif (risk_signal.get('positives') or 0) >= 2:
            risk_lines.append('风险偏好仍偏向成长方向，指数与成长代理维持正向')
        elif (risk_signal.get('negatives') or 0) >= 2:
            risk_lines.append('风险偏好有所降温，成长代理资产整体偏弱')
        else:
            risk_lines.append('风险偏好暂无明确单边倾向')
        risk_lines.append(summarize_signals(risk_signal))

        us_lines = []
        if hot_sector.get('name') in ('美股科技', '美股医药', '支付与金融科技'):
            us_lines.append(f"当前主线偏向{hot_sector.get('name')}")
            us_lines.append(summarize_signals(hot_sector.get('market_validation')))
        elif us_signal.get('validated'):
            us_lines.append('美股基准资产维持偏强，市场主线暂未完全走坏')
            us_lines.append(summarize_signals(us_signal))
        else:
            us_lines.append('当前未识别到明确的美股主线')
            us_lines.append(summarize_signals(us_signal))

        gold_lines = []
        if hot_sector.get('name') == '黄金' or cold_sector.get('name') == '黄金':
            target = hot_sector if hot_sector.get('name') == '黄金' else cold_sector
            gold_lines.append(f"黄金主题关注度变化：{target.get('market_support')}")
            gold_lines.append(summarize_signals(target.get('market_validation')))
        elif gold_signal.get('validated'):
            gold_lines.append('黄金代理资产维持偏强，避险资产并未失去支撑')
            gold_lines.append(summarize_signals(gold_signal))
        else:
            gold_lines.append('黄金方向暂未成为今日核心主题')
            gold_lines.append(summarize_signals(gold_signal))

        china_lines = []
        china_summary = summarize_signals(china_signal)
        if hot_sector.get('cluster') == '科技链':
            china_lines.append('中国市场没有跟着美股科技一起转弱，宽基更像稳住而不是补跌')
            china_lines.append(china_summary)
        elif hot_sector.get('name') == '黄金':
            china_lines.append('中国市场里更显眼的是黄金映射和宽基稳定，不是高弹性进攻')
            china_lines.append(china_summary)
        elif hot_sector.get('name') == '卫星':
            china_lines.append('中国市场有题材活跃痕迹，但还没强到带动更大范围扩散')
            china_lines.append(china_summary)
        elif china_signal.get('validated'):
            china_lines.append('中国市场基准资产偏稳，但稳住不等于走出独立主线')
            china_lines.append(china_summary)
        else:
            china_lines.append('中国市场更像跟随稳定，没有出现能单独立住的新主线')
            china_lines.append(china_summary)

        return {
            'risk_appetite': risk_lines,
            'us_market': us_lines,
            'gold': gold_lines,
            'china_market': china_lines,
        }
