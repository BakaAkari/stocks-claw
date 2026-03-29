#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.logging_utils import log_event
from stocks.services.event_log_service import EventLogService
from stocks.services.news_fetch_service import NewsFetchService
from stocks.services.personal_llm_report_service import PersonalLLMReportService

REPORTS_DIR = ROOT / 'stocks' / 'reports'
LATEST_PATH = REPORTS_DIR / 'personal-latest.md'
DEDUP_STATE_PATH = REPORTS_DIR / '.dedup_state.json'


def save_report(text: str) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    path = REPORTS_DIR / f'personal-report-{timestamp}.md'
    path.write_text(text + '\n', encoding='utf-8')
    LATEST_PATH.write_text(text + '\n', encoding='utf-8')
    return path


def _get_content_fingerprint(text: str) -> str:
    """提取报告核心内容指纹（去重用）"""
    # 提取关键判断句（包含方向性建议的部分）
    lines = text.split('\n')
    key_lines = []
    for line in lines:
        line = line.strip()
        # 关注：方向性建议、结构提示、结论
        if any(k in line for k in ['更适合', '建议', '优先', '注意', '关注', '结论', '判断']):
            key_lines.append(line)
    # 取前 5 个关键句 + 全文前 300 字作为指纹基础
    fingerprint_text = '\n'.join(key_lines[:5]) + text[:300]
    return hashlib.md5(fingerprint_text.encode('utf-8')).hexdigest()[:16]


def _check_and_update_dedup(content_hash: str, cooldown_minutes: int = 60) -> bool:
    """
    检查是否应在冷却期内发送
    Returns: True = 应该发送, False = 重复，跳过
    """
    import json
    from datetime import datetime, timedelta
    
    state = {}
    if DEDUP_STATE_PATH.exists():
        try:
            state = json.loads(DEDUP_STATE_PATH.read_text(encoding='utf-8'))
        except Exception:
            state = {}
    
    last_hash = state.get('last_hash')
    last_sent = state.get('last_sent')
    
    if last_hash == content_hash and last_sent:
        last_time = datetime.fromisoformat(last_sent)
        if datetime.now() - last_time < timedelta(minutes=cooldown_minutes):
            return False  # 在冷却期内，重复
    
    # 更新状态
    state['last_hash'] = content_hash
    state['last_sent'] = datetime.now().isoformat()
    DEDUP_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEDUP_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    return True


def main():
    parser = argparse.ArgumentParser(description='生成适合定时任务投递的个人市场观察简报')
    parser.add_argument('--refresh-news', action='store_true', help='生成前先刷新新闻输入')
    parser.add_argument('--limit-per-source', type=int, default=5, help='每个新闻源抓取条数')
    parser.add_argument('--save', action='store_true', help='保存报告到 reports 目录')
    parser.add_argument('--skip-dedup', action='store_true', help='跳过重复检测强制发送')
    parser.add_argument('--dedup-cooldown', type=int, default=60, help='重复冷却时间（分钟，默认60）')
    parser.add_argument('--model', type=str, default=None, help='指定主模型（默认使用配置）')
    parser.add_argument('--fallback-model', type=str, default=None, help='指定 fallback 模型（默认 kimi-k2.5）')
    args = parser.parse_args()

    try:
        if args.refresh_news:
            NewsFetchService().refresh(limit_per_source=args.limit_per_source)
        report = PersonalLLMReportService(model=args.model, fallback_model=args.fallback_model).generate()
        
        # 冷却去重检查
        if not args.skip_dedup:
            content_hash = _get_content_fingerprint(report)
            should_send = _check_and_update_dedup(content_hash, args.dedup_cooldown)
            if not should_send:
                log_event('personal_report.deduplicated', reason='cooldown_active')
                print(f'[冷却中] 内容与 {args.dedup_cooldown} 分钟内发送的版本相似，跳过投递')
                return 0
        
        saved_path = save_report(report) if args.save else None
    except Exception as e:
        print(f'个人市场观察生成失败\n- 原因：{e}')
        return 2

    print(report)
    if saved_path:
        print(f'\n[saved] {saved_path}')
    
    # 记录事件日志
    EventLogService().log_report_generated(report)
    
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
