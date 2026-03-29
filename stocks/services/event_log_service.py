#!/usr/bin/env python3
"""
极简事件日志服务
记录今日建议生成历史，供 LLM 参考"今天已提过什么"
不记录完整原文，只记录关键判断指纹
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EVENTS_DIR = ROOT / 'stocks' / 'events'


@dataclass
class EventEntry:
    """事件条目"""
    timestamp: str
    event_type: str  # 'report_generated', 'alert_triggered', etc.
    fingerprint: str  # 内容指纹
    key_topics: list[str]  # 关键主题，如 ['黄金仓位', 'NVDA承压']
    summary: str  # 一句话摘要


class EventLogService:
    """极简事件日志，轻量实现，不扩展成时序数据库"""
    
    def __init__(self, events_dir: Path | None = None):
        self.events_dir = events_dir or EVENTS_DIR
        self.events_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_today_log_path(self) -> Path:
        """获取今日日志文件路径"""
        today = datetime.now().strftime('%Y-%m-%d')
        return self.events_dir / f'{today}.jsonl'
    
    def _extract_key_topics(self, report_text: str) -> list[str]:
        """从报告文本中提取关键主题"""
        topics = []
        
        # 简单的关键词匹配，提取重要判断
        keywords = [
            ('黄金', '黄金仓位'),
            ('NVDA', 'NVDA'),
            ('AAPL', 'AAPL'),
            ('科技股', '科技股'),
            ('美股', '美股'),
            ('A股', 'A股'),
            ('纳指', '纳指'),
            ('风险', '风险偏好'),
            ('防御', '防御结构'),
            ('进攻', '进攻方向'),
            ('观望', '观望建议'),
            ('止损', '止损'),
            ('止盈', '止盈'),
        ]
        
        for keyword, topic in keywords:
            if keyword in report_text:
                topics.append(topic)
        
        # 去重并保持顺序
        seen = set()
        unique_topics = []
        for t in topics:
            if t not in seen:
                seen.add(t)
                unique_topics.append(t)
        
        return unique_topics[:5]  # 最多5个主题
    
    def _generate_fingerprint(self, report_text: str) -> str:
        """生成报告指纹"""
        # 取前500字 + 关键主题区域的 hash
        key_section = report_text[:500]
        return hashlib.md5(key_section.encode('utf-8')).hexdigest()[:16]
    
    def log_report_generated(self, report_text: str) -> EventEntry:
        """记录报告生成事件"""
        entry = EventEntry(
            timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            event_type='report_generated',
            fingerprint=self._generate_fingerprint(report_text),
            key_topics=self._extract_key_topics(report_text),
            summary=self._generate_summary(report_text),
        )
        
        log_path = self._get_today_log_path()
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(asdict(entry), ensure_ascii=False) + '\n')
        
        return entry
    
    def _generate_summary(self, report_text: str) -> str:
        """生成一句话摘要"""
        # 尝试提取核心结论（第一段或包含"收束"的部分）
        lines = report_text.split('\n')
        
        # 找第一段非空行
        for line in lines:
            line = line.strip()
            if line and not line.startswith('#') and not line.startswith('*'):
                # 截取前50字
                return line[:50] + '...' if len(line) > 50 else line
        
        return '报告已生成'
    
    def get_today_events(self) -> list[EventEntry]:
        """获取今日所有事件"""
        log_path = self._get_today_log_path()
        if not log_path.exists():
            return []
        
        events = []
        try:
            with open(log_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    events.append(EventEntry(**data))
        except Exception:
            pass
        
        return events
    
    def get_today_topics(self) -> list[str]:
        """获取今日已提及的所有主题"""
        events = self.get_today_events()
        all_topics = []
        seen = set()
        
        for event in events:
            for topic in event.key_topics:
                if topic not in seen:
                    seen.add(topic)
                    all_topics.append(topic)
        
        return all_topics
    
    def has_topic_been_mentioned(self, topic: str) -> bool:
        """检查某主题今日是否已提及"""
        today_topics = self.get_today_topics()
        return topic in today_topics
    
    def summary_for_llm(self) -> str:
        """生成供 LLM 参考的今日事件摘要"""
        events = self.get_today_events()
        if not events:
            return ''
        
        lines = ['**今日已生成报告**：', '']
        for event in events:
            lines.append(f"- {event.timestamp}: {event.summary}")
            if event.key_topics:
                lines.append(f"  涉及：{', '.join(event.key_topics)}")
        
        return '\n'.join(lines)


def main():
    """CLI 入口"""
    import argparse
    parser = argparse.ArgumentParser(description='极简事件日志')
    parser.add_argument('--today-topics', action='store_true', help='显示今日已提及主题')
    parser.add_argument('--summary', action='store_true', help='显示今日摘要（供 LLM）')
    args = parser.parse_args()
    
    service = EventLogService()
    
    if args.today_topics:
        topics = service.get_today_topics()
        if topics:
            print('今日已提及主题：')
            for t in topics:
                print(f'  - {t}')
        else:
            print('今日暂无记录')
    elif args.summary:
        summary = service.summary_for_llm()
        if summary:
            print(summary)
        else:
            print('今日暂无报告记录')
    else:
        # 显示今日所有事件
        events = service.get_today_events()
        if events:
            for event in events:
                print(json.dumps({
                    'time': event.timestamp,
                    'type': event.event_type,
                    'topics': event.key_topics,
                    'summary': event.summary,
                }, ensure_ascii=False))
        else:
            print('今日暂无记录')


if __name__ == '__main__':
    raise SystemExit(main())
