from __future__ import annotations

from dataclasses import dataclass

from stocks.errors import AssetUpdateError
from stocks.services.asset_update_service import AssetUpdateService


@dataclass(frozen=True)
class AssetMemoryChatResult:
    handled: bool
    response: str | None = None
    reason: str | None = None


class AssetMemoryChatService:
    def __init__(self, asset_update_service: AssetUpdateService | None = None):
        self.asset_update_service = asset_update_service or AssetUpdateService()

    def route(self, text: str) -> AssetMemoryChatResult:
        raw = (text or '').strip()
        if not raw:
            return AssetMemoryChatResult(handled=False, reason='empty')

        if raw.startswith('更新资产'):
            try:
                record = self.asset_update_service.apply_update_command(raw)
            except AssetUpdateError as e:
                return AssetMemoryChatResult(handled=True, response=f'资产更新失败：{e}', reason='update_error')
            return AssetMemoryChatResult(
                handled=True,
                response=(
                    f"已更新金融资产：{record['asset_name']} / {record['platform']} / {record['amount']}"
                ),
                reason='updated',
            )

        if any(token in raw for token in ('买了', '卖了', '加了', '减了', '清仓', '资产有变化')):
            prompt = self.asset_update_service.build_confirmation_prompt(raw)
            return AssetMemoryChatResult(handled=True, response=prompt, reason='confirm_update')

        return AssetMemoryChatResult(handled=False, reason='not_asset_change')
