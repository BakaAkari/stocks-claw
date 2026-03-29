#!/usr/bin/env python3
"""
轻量健康巡检服务
检查数据新鲜度和系统状态，不自动修复，只输出告警
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATA_PATH = ROOT / 'stocks' / 'data'
REPORTS_PATH = ROOT / 'stocks' / 'reports'


@dataclass
class HealthCheckResult:
    component: str
    status: str  # 'ok', 'warning', 'error'
    message: str
    last_updated: datetime | None = None


class HealthCheckService:
    """轻量健康检查，只读不修复"""
    
    #  freshness thresholds
    THRESHOLDS = {
        'market_quotes': timedelta(minutes=30),
        'news_feed': timedelta(hours=2),
        'market_state': timedelta(hours=1),
    }
    
    def __init__(self, data_path: Path | None = None):
        self.data_path = data_path or DATA_PATH
    
    def _get_file_mtime(self, filename: str) -> datetime | None:
        """获取文件修改时间"""
        filepath = self.data_path / filename
        if not filepath.exists():
            return None
        try:
            mtime = filepath.stat().st_mtime
            return datetime.fromtimestamp(mtime)
        except Exception:
            return None
    
    def _check_freshness(self, name: str, filename: str, threshold: timedelta) -> HealthCheckResult:
        """检查数据新鲜度"""
        mtime = self._get_file_mtime(filename)
        if mtime is None:
            return HealthCheckResult(
                component=name,
                status='error',
                message=f'{filename} 不存在',
                last_updated=None
            )
        
        age = datetime.now() - mtime
        if age > threshold:
            return HealthCheckResult(
                component=name,
                status='warning',
                message=f'{filename} 已 {int(age.total_seconds() / 60)} 分钟未更新（阈值 {int(threshold.total_seconds() / 60)} 分钟）',
                last_updated=mtime
            )
        
        return HealthCheckResult(
            component=name,
            status='ok',
            message=f'{filename} 正常（{int(age.total_seconds() / 60)} 分钟前更新）',
            last_updated=mtime
        )
    
    def check_all(self) -> list[HealthCheckResult]:
        """执行所有检查"""
        results = []
        
        # 数据新鲜度检查
        results.append(self._check_freshness(
            'market_quotes', 'market_quotes.json', self.THRESHOLDS['market_quotes']
        ))
        results.append(self._check_freshness(
            'news_feed', 'news_feed.json', self.THRESHOLDS['news_feed']
        ))
        results.append(self._check_freshness(
            'market_state', 'market_state.json', self.THRESHOLDS['market_state']
        ))
        
        # 检查最新报告是否存在
        latest_report = REPORTS_PATH / 'personal-latest.md'
        if latest_report.exists():
            mtime = datetime.fromtimestamp(latest_report.stat().st_mtime)
            age = datetime.now() - mtime
            results.append(HealthCheckResult(
                component='latest_report',
                status='ok',
                message=f'最新报告存在（{int(age.total_seconds() / 60)} 分钟前生成）',
                last_updated=mtime
            ))
        else:
            results.append(HealthCheckResult(
                component='latest_report',
                status='warning',
                message='personal-latest.md 不存在',
                last_updated=None
            ))
        
        return results
    
    def summary_text(self) -> str:
        """生成检查摘要"""
        results = self.check_all()
        
        lines = ['**系统健康巡检**', '']
        
        warnings = [r for r in results if r.status == 'warning']
        errors = [r for r in results if r.status == 'error']
        ok_count = len([r for r in results if r.status == 'ok'])
        
        if errors:
            lines.append(f'🚨 发现 {len(errors)} 个错误：')
            for r in errors:
                lines.append(f'  - {r.component}: {r.message}')
            lines.append('')
        
        if warnings:
            lines.append(f'⚠️ 发现 {len(warnings)} 个警告：')
            for r in warnings:
                lines.append(f'  - {r.component}: {r.message}')
            lines.append('')
        
        if not errors and not warnings:
            lines.append(f'✅ 所有检查通过（{ok_count} 项正常）')
        else:
            lines.append(f'✅ {ok_count} 项正常')
        
        return '\n'.join(lines)
    
    def has_issues(self) -> bool:
        """是否有需要关注的问题"""
        results = self.check_all()
        return any(r.status in ('warning', 'error') for r in results)


def main():
    """CLI 入口"""
    import argparse
    parser = argparse.ArgumentParser(description='轻量健康巡检')
    parser.add_argument('--json', action='store_true', help='输出 JSON 格式')
    args = parser.parse_args()
    
    service = HealthCheckService()
    results = service.check_all()
    
    if args.json:
        output = []
        for r in results:
            output.append({
                'component': r.component,
                'status': r.status,
                'message': r.message,
                'last_updated': r.last_updated.isoformat() if r.last_updated else None,
            })
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(service.summary_text())
    
    # 如果有错误，返回非零退出码
    if any(r.status == 'error' for r in results):
        return 2
    if any(r.status == 'warning' for r in results):
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
