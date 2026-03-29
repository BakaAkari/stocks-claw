from __future__ import annotations

from stocks.services.financial_memory_service import FinancialMemoryService
from stocks.services.news_input_service import NewsInputService


class PersonalInsightService:
    def __init__(
        self,
        memory_service: FinancialMemoryService | None = None,
        news_service: NewsInputService | None = None,
    ):
        self.memory_service = memory_service or FinancialMemoryService()
        self.news_service = news_service or NewsInputService()

    def build_context(self, news_limit: int = 5) -> dict:
        memory = self.memory_service.load()
        assets = memory.get('assets', [])
        news_items = self.news_service.latest_items(limit=news_limit)
        return {
            'financial_memory': {
                'updated_at': memory.get('updated_at'),
                'asset_count': len(assets),
                'assets': assets,
                'portfolio_profile_notes': memory.get('portfolio_profile_notes') or {},
                'notes': memory.get('notes'),
                'schema_version': memory.get('schema_version'),
            },
            'news_input': {
                'count': len(news_items),
                'items': news_items,
            },
        }

    def render_prompt_input(self, news_limit: int = 5) -> str:
        ctx = self.build_context(news_limit=news_limit)
        memory = ctx['financial_memory']
        profile = memory.get('portfolio_profile_notes') or {}
        lines = ['用户金融记忆：']
        lines.append(f"- 更新时间：{memory.get('updated_at') or '未知'}")
        lines.append(f"- 资产数量：{memory.get('asset_count')}")
        if profile:
            lines.append('- 用户画像与分析口径：')
            for key, value in profile.items():
                if value not in (None, ''):
                    lines.append(f'  - {key}: {value}')
        if memory.get('notes'):
            lines.append(f"- 用户补充备注：{memory.get('notes')}")

        lines.append('- 全量资产：')
        for item in memory['assets']:
            name = item.get('asset_name') or '未知资产'
            platform = item.get('platform') or '未知平台'
            amount = item.get('amount')
            asset_type = item.get('asset_type') or '未分类'
            notes = item.get('notes')
            confirmed = item.get('confirmed_by_user')
            base = f'  - {name} | 平台={platform} | 金额={amount} | 类型={asset_type}'
            if notes:
                base += f' | 备注={notes}'
            if confirmed is not None:
                base += f' | 用户确认={confirmed}'
            lines.append(base)

        lines.append('')
        lines.append('新闻输入：')
        lines.append(f"- 新闻条数：{ctx['news_input']['count']}")
        for item in ctx['news_input']['items']:
            title = item.get('title') or '无标题'
            summary = item.get('summary') or ''
            published_at = item.get('published_at') or '未知时间'
            source = item.get('source') or '未知来源'
            tags = item.get('tags') or []
            tag_text = f" | tags={','.join(tags)}" if tags else ''
            lines.append(f"- {published_at} | {source} | {title} | {summary}{tag_text}")
        return '\n'.join(lines)
