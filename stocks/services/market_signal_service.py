from __future__ import annotations

from stocks.services.query_service import QueryService


class MarketSignalService:
    THEME_REPRESENTATIVES = {
        '黄金': [('a', '紫金矿业'), ('a', '山东黄金'), ('a', '赤峰黄金')],
        'AI基础设施': [('a', '中际旭创'), ('a', '工业富联'), ('a', '沪电股份')],
        '卫星': [('a', '中国卫星'), ('a', '上海沪工'), ('a', '航天电子')],
        '美股科技': [('us', 'NVDA'), ('us', 'MSFT'), ('us', 'AAPL')],
        '美股医药': [('us', 'LLY'), ('us', 'PFE'), ('us', 'JNJ')],
        '支付与金融科技': [('us', 'PYPL'), ('us', 'V'), ('us', 'MA')],
    }

    OBSERVATION_REPRESENTATIVES = {
        'risk_on': [('us', 'QQQ'), ('us', 'SPY'), ('a', '159915')],
        'gold': [('us', 'GLD'), ('us', 'IAU'), ('a', '518880')],
        'us_benchmark': [('us', 'QQQ'), ('us', 'SPY'), ('us', 'DIA')],
        'china_benchmark': [('a', '159919'), ('a', '510300'), ('a', '510050')],
    }

    def __init__(self, query_service: QueryService | None = None):
        self.query_service = query_service or QueryService()

    def get_representative_assets(self, theme_name: str | None, limit: int = 3) -> list[dict]:
        validation = self.validate_theme(theme_name)
        signals = validation.get('signals', [])
        items = [item for item in signals if not item.get('error')]
        items = sorted(items, key=lambda x: x.get('pct_change') or 0, reverse=True)
        return items[: max(0, limit)]

    def validate_theme(self, theme_name: str | None) -> dict:
        if not theme_name:
            return {
                'validated': False,
                'summary': '无主题，无法进行行情验证',
                'signals': [],
            }

        targets = self.THEME_REPRESENTATIVES.get(theme_name, [])
        if not targets:
            return {
                'validated': False,
                'summary': '当前主题尚未配置代表标的，待补充行情验证',
                'signals': [],
            }

        return self._validate_targets(targets, success_text='主题获得最小行情确认', fallback_text='主题暂仅保留新闻侧依据')

    def get_observation_signal(self, observation_name: str) -> dict:
        targets = self.OBSERVATION_REPRESENTATIVES.get(observation_name, [])
        if not targets:
            return {
                'validated': False,
                'summary': '当前观察维度尚未配置代表标的',
                'signals': [],
            }
        return self._validate_targets(targets, success_text='观察维度获得最小行情确认', fallback_text='观察维度暂缺清晰行情确认')

    def _validate_targets(self, targets: list[tuple[str, str]], success_text: str, fallback_text: str) -> dict:
        signals = []
        positives = 0
        negatives = 0
        for market_key, keyword in targets:
            try:
                quote = self.query_service.query(market_key, keyword)
                pct = quote.pct_change or 0
                if pct > 0:
                    positives += 1
                elif pct < 0:
                    negatives += 1
                signals.append(
                    {
                        'asset_name': quote.instrument.name,
                        'code': quote.instrument.code,
                        'market': market_key,
                        'price': quote.price,
                        'pct_change': quote.pct_change,
                        'selection_reason': '代表资产 + 当前行情验证',
                    }
                )
            except Exception as e:
                signals.append(
                    {
                        'asset_name': keyword,
                        'market': market_key,
                        'error': str(e),
                    }
                )

        validated = positives > 0
        if validated:
            summary = f'代表标的中有 {positives} 个上涨，{success_text}'
        elif negatives > 0:
            summary = f'代表标的中有 {negatives} 个下跌，{fallback_text}'
        else:
            summary = fallback_text

        return {
            'validated': validated,
            'summary': summary,
            'signals': signals,
            'positives': positives,
            'negatives': negatives,
        }
