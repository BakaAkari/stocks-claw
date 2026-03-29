from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.services.chat_router_service import ChatRouterService
from stocks.services.command_service import CommandService


class StubPersonalReportService:
    def generate(self) -> str:
        return '今日概览\n- 路由测试个人简报'


if __name__ == '__main__':
    service = ChatRouterService(
        CommandService(personal_report_service=StubPersonalReportService())
    )

    result0 = service.route('个人简报')
    assert result0.handled is True
    assert result0.response is not None
    assert '路由测试个人简报' in result0.response

    result1 = service.route('查A股 紫金矿业')
    assert result1.handled is True
    assert result1.response is not None
    assert '紫金矿业' in result1.response

    result2 = service.route('今天天气不错')
    assert result2.handled is False
    assert result2.reason == 'not_stock_command'

    print('chat router ok')
