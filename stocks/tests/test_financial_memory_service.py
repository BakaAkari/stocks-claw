from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.services.financial_memory_service import FinancialMemoryService
from stocks.services.asset_update_service import AssetUpdateService


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 'financial_assets.json'
        memory = FinancialMemoryService(path)
        assert memory.load()['assets'] == []

        updater = AssetUpdateService(memory)
        updater.upsert_asset(
            asset_name='黄金ETF',
            platform='华泰',
            amount=120000,
            asset_type='etf',
            notes='长期观察',
        )

        payload = memory.load()
        assert len(payload['assets']) == 1
        assert payload['assets'][0]['asset_name'] == '黄金ETF'
        assert payload['assets'][0]['platform'] == '华泰'

        updater.upsert_asset(
            asset_name='黄金ETF',
            platform='华泰',
            amount=150000,
            asset_type='etf',
        )
        payload = memory.load()
        assert len(payload['assets']) == 1
        assert payload['assets'][0]['amount'] == 150000

        parsed = updater.parse_update_command('更新资产 名称=标普ETF 平台=富途 金额=200000 类型=etf 备注=核心仓位')
        assert parsed['asset_name'] == '标普ETF'
        assert parsed['platform'] == '富途'
        assert parsed['amount'] == '200000'

        updater.apply_update_command('更新资产 名称=标普ETF 平台=富途 金额=200000 类型=etf 备注=核心仓位')
        payload = memory.load()
        assert len(payload['assets']) == 2
        assert payload['assets'][1]['asset_name'] == '标普ETF'

    print('financial memory service ok')
