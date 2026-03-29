#!/usr/bin/env python3
"""
Heartbeat 健康检查入口
发现问题时输出告警文本（供 message 工具发送）
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.services.health_check_service import HealthCheckService


def main():
    service = HealthCheckService()
    
    # 只在有问题时输出（供 cron/message 捕获）
    if service.has_issues():
        print(service.summary_text())
        return 1
    
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
