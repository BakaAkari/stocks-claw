from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.services.asset_memory_chat_service import AssetMemoryChatService
from stocks.services.asset_update_service import AssetUpdateService
from stocks.services.financial_memory_service import FinancialMemoryService


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as td:
        memory = FinancialMemoryService(Path(td) / 'financial_assets.json')
        updater = AssetUpdateService(memory)
        service = AssetMemoryChatService(updater)

        result1 = service.route('我买了点黄金')
        assert result1.handled is True
        assert result1.reason == 'confirm_update'
        assert '要不要更新金融资产状况' in (result1.response or '')
        assert '更新资产 名称=' in (result1.response or '')

        result2 = service.route('更新资产 名称=黄金ETF 平台=华泰 金额=120000 类型=etf')
        assert result2.handled is True
        assert result2.reason == 'updated'
        assert '已更新金融资产' in (result2.response or '')
        payload = memory.load()
        assert len(payload['assets']) == 1
        assert payload['assets'][0]['asset_name'] == '黄金ETF'

        result3 = service.route('更新资产 名称=黄金ETF 平台=华泰')
        assert result3.handled is True
        assert result3.reason == 'update_error'
        assert '资产更新失败' in (result3.response or '')

        result4 = service.route('今天天气不错')
        assert result4.handled is False
        assert result4.reason == 'not_asset_change'

    print('asset memory chat service ok')
