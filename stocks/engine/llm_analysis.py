"""LLM 深度分析器 — 决策层分析

职责：
1. 基于 AnalysisContext 生成投资分析报告
2. 从自然语言中提取约束条件

设计原则：
- 默认禁用（llm_analysis.enabled = false）
- 使用 Agent 自己的 LLM 或指定模型
- 失败降级
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from typing import Optional

from stocks.domain.models import AnalysisContext

logger = logging.getLogger(__name__)

# 默认 LLM 超时（秒）
_LLM_TIMEOUT = 60


class LLMAnalysis:
    """LLM 深度分析器

    基于 AnalysisContext 生成结构化投资分析报告，
    或从自然语言中提取投资约束条件。
    默认禁用，启用后使用较强模型（默认 gpt-4o）。
    """

    def __init__(
        self,
        model: str = "kimi-k2.6",
        enabled: bool = True,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.model = model
        self.enabled = enabled
        self.api_key = api_key
        self.base_url = base_url or "https://api.openai.com/v1"

    async def generate_report(self, context: AnalysisContext) -> str:
        """基于上下文生成投资分析报告。

        构建详细分析 prompt，调用 LLM 生成中文投资分析报告。
        未启用时返回固定提示文本；调用失败时降级返回错误提示，不抛异常。
        """
        if not self.enabled:
            return "LLM analysis disabled."

        prompt = self._build_analysis_prompt(context)
        try:
            return await asyncio.to_thread(self._call_llm, prompt)
        except Exception as exc:
            logger.warning("Report generation failed: %s", exc)
            return "LLM analysis failed. Please review the raw data."

    async def extract_constraints(self, text: str) -> dict:
        """从自然语言文本中提取约束条件。

        解析用户输入（如"科技股不超过 30%"、"保留 10% 现金"等），
        返回结构化约束字典，供下游规则引擎使用。
        未启用或调用失败时返回空字典。
        """
        if not self.enabled:
            return {}

        prompt = (
            "请从以下用户输入中提取投资约束条件，并以 JSON 格式返回。\n\n"
            "支持的约束类型：\n"
            "- bucket_limits: 各资产类别占比上下限，如 {'股票': {'min': 0.5, 'max': 0.8}}\n"
            "- cash_reserve: 现金保留比例，如 {'min': 0.05, 'max': 0.15}\n"
            "- sector_limits: 行业/主题占比限制，如 {'科技': {'max': 0.3}}\n"
            "- rebalancing_frequency: 调仓频率，如 'monthly' / 'quarterly'\n"
            "- risk_tolerance: 风险承受度，如 'conservative' / 'moderate' / 'aggressive'\n"
            "- excluded_assets: 排除的标的列表\n\n"
            "如果某项未提及，不要包含该键。\n"
            "不要输出 markdown 代码块，只输出纯 JSON。\n\n"
            f"用户输入：{text}\n\n"
            "JSON："
        )

        try:
            raw = await asyncio.to_thread(self._call_llm, prompt)
        except Exception as exc:
            logger.warning("Constraint extraction failed: %s", exc)
            return {}

        if not raw:
            return {}

        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").strip()
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse constraint JSON: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _build_analysis_prompt(self, context: AnalysisContext) -> str:
        """构建分析 prompt。

        将 AnalysisContext 中的结构化数据转换为人类可读格式，
        供 LLM 生成投资分析报告。
        """
        lines: list[str] = [
            "你是一位专业的个人投资顾问。请基于以下数据生成一份简洁的投资分析报告（不超过 500 字），用中文输出。",
            "",
            "=== 组合概况 ===",
            f"资产数量：{context.asset_count}",
            f"组合约束：{json.dumps(context.portfolio_constraints, ensure_ascii=False)}",
            f"组合画像：{json.dumps(context.portfolio_profile, ensure_ascii=False)}",
            "",
            "=== 市场行情 ===",
        ]

        for market, qs in context.quotes.items():
            lines.append(f"  [{market}]")
            for q in qs:
                inst = q.instrument
                info_parts = [f"{inst.code} {inst.name}"]
                if q.price is not None:
                    info_parts.append(f"价格 {q.price}")
                if q.pct_change is not None:
                    info_parts.append(f"涨跌 {q.pct_change:+.2f}%")
                lines.append(f"    {' | '.join(info_parts)}")

        lines.extend([
            "",
            "=== 市场状态 ===",
            json.dumps(context.market_state.to_dict(), ensure_ascii=False),
            "",
            "=== 组合映射 ===",
            json.dumps(context.portfolio_mapping.to_dict(), ensure_ascii=False),
            "",
            "=== 偏离检查 ===",
        ])
        for dc in context.drift_checks:
            lines.append(
                f"  {dc.bucket}: 当前 {dc.current_ratio:.2%}, "
                f"目标 [{dc.target_min or '-'} , {dc.target_max or '-'}], "
                f"状态 {dc.status}, 偏离 {dc.gap:+.2%}"
            )

        if context.market_summary_nl:
            lines.extend([
                "",
                "=== 行情摘要 ===",
                context.market_summary_nl,
            ])

        if context.news:
            lines.extend([
                "",
                "=== 近期新闻 ===",
            ])
            for n in context.news[:5]:
                lines.append(f"  - [{n.source_name}] {n.title}")

        if context.recent_snapshots:
            lines.extend([
                "",
                "=== 历史快照 ===",
                json.dumps(context.recent_snapshots, ensure_ascii=False),
            ])

        lines.extend([
            "",
            "请给出：1) 当前组合状态总评；2) 主要风险点；3) 调仓建议（如有）。",
        ])

        return "\n".join(lines)

    def _call_llm(self, prompt: str) -> str:
        """同步调用 LLM API，返回模型生成的文本。

        使用标准 OpenAI Chat Completions API 格式，兼容任何提供相同格式的服务。
        异常时返回空字符串，由调用方决定是否降级。
        """
        if not self.api_key:
            logger.warning("LLM call skipped: api_key not configured")
            return ""

        url = f"{self.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.4,
            "max_tokens": 1024,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=_LLM_TIMEOUT) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            logger.warning("LLM API call failed: %s", exc)
            return ""
