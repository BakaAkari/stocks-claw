#!/usr/bin/env python3
"""
自然语言约束录入服务
通过 LLM 从自然语言中提取结构化约束，而不是强制填表
"""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import Any

import sys
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from stocks.llm_config import get_constraint_llm_config
from stocks.logging_utils import log_event
from stocks.services.financial_memory_service import FinancialMemoryService


class ConstraintChatService:
    """通过自然语言对话维护和更新用户投资约束"""
    
    BUCKET_ALIASES = {
        '成长': 'growth_total',
        '成长仓': 'growth_total',
        'growth': 'growth_total',
        '黄金': 'gold_buffer',
        '黄金仓': 'gold_buffer',
        'gold': 'gold_buffer',
        '防守': 'defense',
        '防守层': 'defense',
        '稳健': 'defense',
        '流动性': 'liquidity',
        '现金': 'liquidity',
        '机动': 'liquidity',
        '机动仓': 'liquidity',
    }
    
    def __init__(self, memory_service: FinancialMemoryService | None = None, model: str | None = None):
        self.memory_service = memory_service or FinancialMemoryService()
        self.llm_config = get_constraint_llm_config(model)
        
    def _call_llm(self, messages: list[dict], temperature: float = 0.4) -> str:
        """调用 LLM 提取信息"""
        payload = {
            'model': self.llm_config.model,
            'messages': messages,
            'temperature': temperature,
            'max_tokens': 1500,
        }
        req = urllib.request.Request(
            self.llm_config.url,
            data=json.dumps(payload).encode('utf-8'),
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {self.llm_config.api_key}',
            },
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read().decode('utf-8'))
        choices = body.get('choices', [])
        if not choices:
            return ''
        return (choices[0].get('message', {}).get('content') or '').strip()
    
    def _extract_json(self, response: str) -> dict:
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response, re.DOTALL)
            if json_match:
                try:
                    return json.loads(json_match.group(1))
                except json.JSONDecodeError:
                    return {}
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                try:
                    return json.loads(json_match.group(0))
                except json.JSONDecodeError:
                    return {}
            return {}

    def _normalize_ratio(self, value: Any) -> Any:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            v = float(value)
            if v > 1:
                return round(v / 100.0, 4)
            if v < 0:
                return 0.0
            return round(v, 4)
        return value

    def _normalize_bucket_name(self, bucket: str) -> str:
        text = str(bucket or '').strip()
        return self.BUCKET_ALIASES.get(text, text)

    def _normalize_bucket_config(self, config: Any) -> Any:
        if config is None:
            return None
        if isinstance(config, list) and len(config) == 2:
            return {
                'min': self._normalize_ratio(config[0]),
                'max': self._normalize_ratio(config[1]),
            }
        if isinstance(config, dict):
            normalized = {}
            if 'min' in config:
                normalized['min'] = self._normalize_ratio(config.get('min'))
            if 'max' in config:
                normalized['max'] = self._normalize_ratio(config.get('max'))
            if 'rationale' in config and config.get('rationale'):
                normalized['rationale'] = config.get('rationale')
            return normalized
        return None

    def _normalize_updates(self, updates: Any) -> dict:
        if not isinstance(updates, dict):
            return {}
        normalized: dict[str, Any] = {}
        for key, value in updates.items():
            if key == 'target_bucket_ranges' and isinstance(value, dict):
                bucket_ranges = {}
                for bucket, config in value.items():
                    normalized_bucket = self._normalize_bucket_name(bucket)
                    normalized_config = self._normalize_bucket_config(config)
                    if normalized_config is not None:
                        bucket_ranges[normalized_bucket] = normalized_config
                    elif config is None:
                        bucket_ranges[normalized_bucket] = None
                if bucket_ranges:
                    normalized[key] = bucket_ranges
            elif key in ('max_drawdown_tolerance', 'tactical_budget_ratio'):
                normalized[key] = self._normalize_ratio(value)
            elif key in ('allow_stop_loss', 'allow_take_profit'):
                normalized[key] = bool(value)
            elif key == 'locked_assets' and isinstance(value, list):
                normalized[key] = [str(item).strip() for item in value if str(item).strip()]
            elif key == 'rebalance_trigger':
                normalized[key] = str(value).strip()
            elif value is None:
                normalized[key] = None
        return normalized

    def parse_natural_language(self, user_input: str, current_constraints: dict | None = None) -> dict:
        """
        从自然语言中提取约束更新
        
        示例输入：
        - "我希望成长仓位不超过30%，黄金控制在20%以内"
        - "暂停止损建议，我暂时不想割肉"
        - "把香港保险标记为长期锁定资产"
        - "我能接受的最大回撤是15%"
        """
        current = current_constraints or {}
        current_text = json.dumps(current, ensure_ascii=False, indent=2) if current else '{}'
        
        system_prompt = '''你是一个投资约束提取助手。从用户的自然语言描述中提取结构化约束。

可识别的约束类型：
1. target_bucket_ranges - 各资产桶目标区间（如"成长10-30%"、"黄金不超过20%"）
2. locked_assets - 长期锁定资产列表（如"香港保险不动"）
3. max_drawdown_tolerance - 最大回撤容忍度（如"能接受20%亏损"）
4. allow_stop_loss - 是否允许止损建议（如"暂停割肉"）
5. allow_take_profit - 是否允许止盈建议
6. tactical_budget_ratio - 机动资金比例（如"留10%做机动"）
7. rebalance_trigger - 再平衡触发条件

输出格式要求：
只返回有效的 JSON 对象，不要有任何解释文字。如果某字段未提及，不要包含它。
如果用户说"取消"或"删除"某个约束，将该字段值设为 null。

当前约束：
''' + current_text

        messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_input},
        ]
        
        response = self._call_llm(messages, temperature=0.3)
        updates = self._extract_json(response)
        return self._normalize_updates(updates)
    
    def apply_updates(self, updates: dict) -> dict:
        """将提取的更新应用到现有约束"""
        current = self.memory_service.load_constraints()
        
        # 合并更新
        for key, value in updates.items():
            if value is None:
                # 删除约束
                current.pop(key, None)
            elif key == 'target_bucket_ranges' and isinstance(value, dict):
                # 合并区间配置
                current.setdefault('target_bucket_ranges', {})
                for bucket, config in value.items():
                    if config is None:
                        current['target_bucket_ranges'].pop(bucket, None)
                    else:
                        current['target_bucket_ranges'][bucket] = config
            elif key == 'locked_assets' and isinstance(value, list):
                # 合并锁定资产
                current.setdefault('locked_assets', [])
                for asset in value:
                    if asset.startswith('-') or asset.startswith('删除'):
                        asset_name = asset.lstrip('-').strip().lstrip('删除').strip()
                        if asset_name in current['locked_assets']:
                            current['locked_assets'].remove(asset_name)
                    elif asset not in current['locked_assets']:
                        current['locked_assets'].append(asset)
            else:
                current[key] = value
        
        # 保存
        self.memory_service.save_constraints(current)
        log_event('constraints.updated', fields=list(updates.keys()))
        return current
    
    def process_user_input(self, user_input: str) -> tuple[bool, str, dict]:
        """
        处理用户自然语言输入
        
        Returns:
            (success, message, updated_constraints)
        """
        current = self.memory_service.load_constraints()
        updates = self.parse_natural_language(user_input, current)
        
        if not updates:
            return False, '没能从这段话里提取到约束信息，换个方式说说？', current
        
        new_constraints = self.apply_updates(updates)
        
        # 生成确认消息
        change_summary = []
        for key in updates:
            if key == 'target_bucket_ranges':
                change_summary.append('调整了资产桶目标区间')
            elif key == 'locked_assets':
                change_summary.append('更新了长期锁定资产')
            elif key == 'max_drawdown_tolerance':
                change_summary.append(f'设置最大回撤容忍度为 {updates.get(key)}')
            elif key == 'allow_stop_loss':
                val = updates.get(key)
                change_summary.append(f'{"允许" if val else "暂停"}止损建议')
            elif key == 'allow_take_profit':
                val = updates.get(key)
                change_summary.append(f'{"允许" if val else "暂停"}止盈建议')
            elif key == 'tactical_budget_ratio':
                change_summary.append(f'设置机动资金比例为 {updates.get(key)}')
        
        msg = '已更新：' + '、'.join(change_summary) if change_summary else '约束已更新'
        return True, msg, new_constraints
    
    def get_current_constraints_summary(self) -> str:
        """获取当前约束的摘要文本"""
        constraints = self.memory_service.load_constraints()
        if not constraints:
            return '当前还没有设置任何投资约束。'
        
        lines = ['当前约束设置：']
        
        if 'target_bucket_ranges' in constraints:
            lines.append('\n【资产桶目标区间】')
            for bucket, config in constraints['target_bucket_ranges'].items():
                if isinstance(config, dict):
                    min_v = config.get('min', '不限')
                    max_v = config.get('max', '不限')
                    min_display = f"{min_v * 100:.0f}%" if isinstance(min_v, (int, float)) else min_v
                    max_display = f"{max_v * 100:.0f}%" if isinstance(max_v, (int, float)) else max_v
                else:
                    min_display, max_display = '不限', '不限'
                lines.append(f'  {bucket}: {min_display} ~ {max_display}')
        
        if 'locked_assets' in constraints:
            lines.append('\n【长期锁定资产】')
            for asset in constraints['locked_assets']:
                lines.append(f'  - {asset}')
        
        if 'max_drawdown_tolerance' in constraints:
            val = constraints['max_drawdown_tolerance']
            lines.append(f'\n【最大回撤容忍】{val * 100:.0f}%')
        
        if 'allow_stop_loss' in constraints:
            val = constraints['allow_stop_loss']
            lines.append(f'【止损建议】{"允许" if val else "暂停"}')
        
        if 'allow_take_profit' in constraints:
            val = constraints['allow_take_profit']
            lines.append(f'【止盈建议】{"允许" if val else "暂停"}')
        
        if 'tactical_budget_ratio' in constraints:
            val = constraints['tactical_budget_ratio']
            lines.append(f'【机动资金比例】{val * 100:.0f}%')
        
        return '\n'.join(lines)


def main():
    """CLI 入口"""
    import argparse
    parser = argparse.ArgumentParser(description='自然语言约束录入')
    parser.add_argument('input_text', nargs='?', help='自然语言描述，如"成长仓位控制在30%以内"')
    parser.add_argument('--show', action='store_true', help='显示当前约束')
    args = parser.parse_args()
    
    service = ConstraintChatService()
    
    if args.show:
        print(service.get_current_constraints_summary())
        return
    
    if not args.input_text:
        print('用法：')
        print('  python3 -m stocks.services.constraint_chat_service "成长仓位控制在30%以内"')
        print('  python3 -m stocks.services.constraint_chat_service --show')
        return
    
    success, msg, constraints = service.process_user_input(args.input_text)
    print(msg)
    if success:
        print('\n' + service.get_current_constraints_summary())


if __name__ == '__main__':
    raise SystemExit(main())
