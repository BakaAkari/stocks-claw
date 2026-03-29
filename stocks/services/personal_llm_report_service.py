from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path

from stocks.llm_config import get_personal_advice_llm_config, LLMConfig
from stocks.logging_utils import log_event
from stocks.services.report_assembly_service import ReportAssemblyService

ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = ROOT / 'prompts' / 'personal_advice_prompt.txt'

# Fallback 模型配置
FALLBACK_MODEL = os.getenv('STOCKS_FALLBACK_LLM_MODEL', 'kimi-k2.5')


class PersonalLLMReportService:
    def __init__(self, model: str | None = None, fallback_model: str | None = None):
        config = get_personal_advice_llm_config(model)
        self.model = config.model
        self.fallback_model = fallback_model or FALLBACK_MODEL
        self.report_service = ReportAssemblyService()
        self.url = config.url
        self.api_key = config.api_key

    def _inline_code_market_tokens(self, text: str) -> str:
        patterns = [
            r'\b[A-Z]{2,5}\b',
            r'\b\d{6}\b',
        ]
        for pattern in patterns:
            text = re.sub(pattern, lambda m: f'`{m.group(0)}`', text)
        return text

    def _anonymize_numbers(self, text: str) -> str:
        """隐式化：将具体金额数字替换为模糊描述"""
        import re
        
        # 替换 "X万"、"X.X万" 为模糊描述（保留成本价等合理数字）
        # 但不替换股票代码、百分比、年份等
        
        # 替换金额数字（如 26.6万、40万、36.8万）
        text = re.sub(r'\d+\.?\d*万', '【某金额】', text)
        
        # 替换大数字金额（如 50000、6285元）
        text = re.sub(r'\d{4,}元', '【某金额】', text)
        text = re.sub(r'\d{4,}美元', '【某金额】', text)
        
        # 替换占比百分比中的具体数值（如 35.7%、11.5%）
        # 保留一位小数的百分比替换
        text = re.sub(r'约\d+\.?\d*%', '约【某比例】', text)
        text = re.sub(r'\(\d+\.?\d*%\)', '（【某比例】）', text)
        
        # 替换浮亏/浮盈具体金额（如 浮亏5万、盈利6285元）
        text = re.sub(r'浮亏约?\d+\.?\d*万?', '浮亏【某金额】', text)
        text = re.sub(r'浮盈约?\d+\.?\d*万?', '浮盈【某金额】', text)
        text = re.sub(r'盈利约?\d+\.?\d*万?', '盈利【某金额】', text)
        text = re.sub(r'亏损约?\d+\.?\d*万?', '亏损【某金额】', text)
        
        # 清理连续的【某金额】
        text = re.sub(r'【某金额】+', '【某金额】', text)
        
        return text

    def format_for_feishu(self, content: str) -> str:
        text = (content or '').strip()
        
        # 先进行数字隐式化处理
        text = self._anonymize_numbers(text)
        
        lines = []
        last_blank = True
        for raw in text.splitlines():
            line = raw.rstrip()
            stripped = line.strip()
            if not stripped:
                if not last_blank:
                    lines.append('')
                last_blank = True
                continue

            if stripped in ('---', '——', '***'):
                if not last_blank and lines[-1] != '---':
                    lines.append('')
                lines.append('---')
                lines.append('')
                last_blank = True
                continue

            if stripped.startswith('### '):
                lines.append(f'**{self._inline_code_market_tokens(stripped[4:].strip())}**')
            elif stripped.startswith('## '):
                lines.append(f'**{self._inline_code_market_tokens(stripped[3:].strip())}**')
            elif stripped.startswith('# '):
                lines.append(f'**{self._inline_code_market_tokens(stripped[2:].strip())}**')
            elif stripped.startswith('- ') or stripped.startswith('> ') or stripped.startswith('* '):
                prefix, body = stripped[:2], stripped[2:].strip()
                lines.append(f'{prefix}{self._inline_code_market_tokens(body)}')
            else:
                lines.append(self._inline_code_market_tokens(stripped))
            last_blank = False
        return '\n'.join(lines).strip()

    def _build_prompt_context(self) -> str:
        report = self.report_service.build()
        insight = self.report_service.insight_service.build_context()
        memory = insight.get('financial_memory') or {}
        full_memory_context = {
            'updated_at': memory.get('updated_at'),
            'asset_count': memory.get('asset_count'),
            'portfolio_profile_notes': memory.get('portfolio_profile_notes') or {},
            'assets': memory.get('assets') or [],
            'notes': memory.get('notes'),
            'schema_version': memory.get('schema_version'),
        }
        context = {
            'authoritative_full_financial_memory': full_memory_context,
            'market_state': report.get('market_state'),
            'portfolio_mapping': report.get('portfolio_mapping'),
            'advisory_plan': report.get('advisory_plan'),
            'market_observations': report.get('market_observations'),
            'hot_sector': report.get('hot_sector'),
            'cold_sector': report.get('cold_sector'),
            'watch_themes': report.get('watch_themes'),
            'portfolio_roles': report.get('portfolio_roles'),
            'portfolio_health': report.get('portfolio_health'),
            'representative_assets': report.get('representative_assets'),
            'user_relevance_candidates': report.get('user_relevance'),
            'structural_hints': report.get('structural_hints'),
            'analysis_signals': report.get('analysis_signals'),
            'risk_notes_candidates': report.get('risk_notes'),
            'recent_snapshots': report.get('recent_snapshots'),
            'overview_hint': report.get('overview'),
            'conclusion_hint': report.get('conclusion'),
        }
        raw_context = self.report_service.insight_service.render_prompt_input()
        structured_context = json.dumps(context, ensure_ascii=False, indent=2)
        return (
            "### 最高优先级：完整金融记忆（权威输入）\n"
            f"{json.dumps(full_memory_context, ensure_ascii=False, indent=2)}"
            "\n\n### 原始上下文（可读版）\n"
            f"{raw_context}"
            "\n\n### 结构化分析信号（仅作辅助脚手架，不得覆盖完整资产事实）\n"
            f"{structured_context}"
        )

    def build_prompt(self) -> str:
        template = PROMPT_PATH.read_text(encoding='utf-8')
        context = self._build_prompt_context()
        return template.replace('{{context}}', context)

    def _call_llm(self, model: str, prompt: str) -> str:
        """调用 LLM，返回格式化后的内容"""
        payload = {
            'model': model,
            'messages': [
                {
                    'role': 'system',
                    'content': '你是一个冷静、简洁、克制，但必须真正理解用户组合结构并能基于完整上下文与最近几版变化做综合分析的中文个人金融助手。禁止编造，但允许做结构性归纳和判断。'
                },
                {
                    'role': 'user',
                    'content': prompt,
                },
            ],
            'temperature': 0.6,
            'max_tokens': 1800,
        }
        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode('utf-8'),
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {self.api_key}',
            },
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            body = json.loads(r.read().decode('utf-8'))

        choices = body.get('choices') or []
        if not choices:
            raise RuntimeError(f'个人研报 LLM 返回异常: {body}')
        message = choices[0].get('message', {})
        content = (message.get('content') or '').strip()
        if not content:
            raise RuntimeError('个人研报 LLM 返回空内容')
        return self.format_for_feishu(content)

    def generate(self) -> str:
        prompt = self.build_prompt()
        
        # 首先尝试主模型
        try:
            content = self._call_llm(self.model, prompt)
            log_event('personal_llm_report.success', model=self.model)
            return content
        except Exception as primary_error:
            log_event('personal_llm_report.failed', model=self.model, error=str(primary_error))
            
            # 如果主模型失败且 fallback 模型不同，尝试 fallback
            if self.fallback_model != self.model:
                try:
                    content = self._call_llm(self.fallback_model, prompt)
                    log_event('personal_llm_report.success', model=self.fallback_model, fallback_from=self.model)
                    return f"[使用 fallback 模型 {self.fallback_model} 生成]\n\n{content}"
                except Exception as fallback_error:
                    log_event('personal_llm_report.failed', model=self.fallback_model, fallback_from=self.model, error=str(fallback_error))
                    raise RuntimeError(f'主模型 {self.model} 和 fallback 模型 {self.fallback_model} 均调用失败') from fallback_error
            else:
                raise RuntimeError(f'个人研报 LLM 调用失败: {primary_error}') from primary_error
