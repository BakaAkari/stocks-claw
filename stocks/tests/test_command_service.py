from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.services.command_service import CommandService


class StubPersonalReportService:
    def generate(self) -> str:
        return '今日概览\n- 测试个人简报'


if __name__ == '__main__':
    service = CommandService(personal_report_service=StubPersonalReportService())

    result0 = service.handle('个人简报')
    assert result0 is not None
    assert result0.kind == 'personal_report'
    assert '测试个人简报' in result0.content

    result1 = service.handle('查A股 紫金矿业')
    assert result1 is not None
    assert result1.kind == 'query'
    assert '紫金矿业' in result1.content

    result2 = service.handle('A股简报')
    assert result2 is not None
    assert result2.kind == 'report'
    assert 'A股简报' in result2.content

    result3 = service.handle('今天天气不错')
    assert result3 is None

    print('command service ok')
