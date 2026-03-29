from __future__ import annotations

from stocks.errors import AssetUpdateError
from stocks.logging_utils import log_event
from stocks.services.financial_memory_service import FinancialMemoryService


class AssetUpdateService:
    def __init__(self, memory_service: FinancialMemoryService | None = None):
        self.memory_service = memory_service or FinancialMemoryService()

    def build_confirmation_prompt(self, text: str) -> str:
        raw = (text or '').strip()
        if not raw:
            raise AssetUpdateError('缺少资产变化描述')
        return (
            f'你提到了资产变化：{raw}\n'
            '要不要更新金融资产状况？如需更新，请按这个格式发送：\n'
            '更新资产 名称=<资产名称> 平台=<平台> 金额=<持有金额> [类型=<资产类型>] [备注=<备注>]'
        )

    def parse_update_command(self, text: str) -> dict:
        raw = (text or '').strip()
        if not raw.startswith('更新资产'):
            raise AssetUpdateError('不是资产更新命令')

        body = raw[len('更新资产'):].strip()
        if not body:
            raise AssetUpdateError('缺少资产更新内容')

        parsed = {}
        alias = {
            '名称': 'asset_name',
            '平台': 'platform',
            '金额': 'amount',
            '类型': 'asset_type',
            '备注': 'notes',
        }
        for token in body.split():
            if '=' not in token:
                continue
            key, value = token.split('=', 1)
            key = key.strip()
            value = value.strip()
            mapped = alias.get(key)
            if mapped and value:
                parsed[mapped] = value

        if 'asset_name' not in parsed:
            raise AssetUpdateError('缺少名称=<资产名称>')
        if 'platform' not in parsed:
            raise AssetUpdateError('缺少平台=<平台>')
        if 'amount' not in parsed:
            raise AssetUpdateError('缺少金额=<持有金额>')
        return parsed

    def apply_update_command(self, text: str) -> dict:
        parsed = self.parse_update_command(text)
        return self.upsert_asset(
            asset_name=parsed['asset_name'],
            platform=parsed['platform'],
            amount=parsed['amount'],
            asset_type=parsed.get('asset_type'),
            notes=parsed.get('notes'),
            confirmed_by_user=True,
        )

    def upsert_asset(
        self,
        *,
        asset_name: str,
        platform: str,
        amount: float | int,
        asset_type: str | None = None,
        notes: str | None = None,
        confirmed_by_user: bool = True,
    ) -> dict:
        asset_name = (asset_name or '').strip()
        platform = (platform or '').strip()
        asset_type = (asset_type or '').strip() or 'unknown'
        notes = (notes or '').strip() or None

        if not asset_name:
            raise AssetUpdateError('缺少资产名称')
        if not platform:
            raise AssetUpdateError('缺少平台')
        try:
            amount_value = float(amount)
        except Exception as e:
            raise AssetUpdateError('持有金额必须是数字') from e
        if amount_value < 0:
            raise AssetUpdateError('持有金额不能为负数')

        payload = self.memory_service.load()
        assets = payload.get('assets', [])

        matched = None
        for item in assets:
            if item.get('asset_name') == asset_name and item.get('platform') == platform:
                matched = item
                break

        record = {
            'asset_name': asset_name,
            'platform': platform,
            'amount': amount_value,
            'asset_type': asset_type,
            'notes': notes,
            'confirmed_by_user': bool(confirmed_by_user),
        }

        if matched is None:
            assets.append(record)
            action = 'created'
        else:
            matched.update(record)
            action = 'updated'

        payload['assets'] = assets
        self.memory_service.save(payload)
        log_event('financial_memory.asset_upserted', action=action, asset_name=asset_name, platform=platform)
        return record
