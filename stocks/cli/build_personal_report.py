#!/usr/bin/env python3
"""
[调试工具] 生成本地文本报告（不经过 LLM）

用途：调试 ReportAssemblyService 输出，快速查看脚手架层结果
状态：维护模式，非主链路
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.services.report_assembly_service import ReportAssemblyService


def main():
    print(ReportAssemblyService().render_text())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
