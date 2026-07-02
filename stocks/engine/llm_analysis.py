"""LLM 深度分析器 — 决策层分析

职责：基于 AnalysisContext 与统一分析宪法生成投资分析报告。

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
from pathlib import Path
from typing import Optional

from stocks.domain.models import AnalysisContext

logger = logging.getLogger(__name__)

# 默认 LLM 超时（秒）
_LLM_TIMEOUT = 360
_DEFAULT_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "personal_advice_prompt.txt"


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
        prompt_path: Optional[Path] = None,
    ):
        self.model = model
        self.enabled = enabled
        self.api_key = api_key
        self.base_url = base_url or "https://api.openai.com/v1"
        self.prompt_path = prompt_path or _DEFAULT_PROMPT_PATH

    async def generate_report(self, context: AnalysisContext) -> str:
        """基于上下文生成投资分析报告。

        构建详细分析 prompt，调用 LLM 生成中文投资分析报告。
        未启用时返回固定提示文本；调用失败时降级返回错误提示，不抛异常。
        """
        if not self.enabled:
            return "LLM analysis disabled."

        system_prompt = self.prompt_path.read_text(encoding="utf-8").strip()
        try:
            return await asyncio.to_thread(
                self._call_llm,
                system_prompt,
                context.raw_prompt_input,
            )
        except Exception as exc:
            logger.warning("Report generation failed: %s", exc)
            return "LLM analysis failed. Please review the raw data."

    def _call_llm(self, system_prompt: str, context_prompt: str) -> str:
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
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context_prompt},
            ],
            "temperature": 0.4,
            "max_tokens": 8192,
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
            msg = result["choices"][0]["message"]
            content = msg.get("content", "").strip()
            # 某些 reasoning 模型（如 kimi-k2.6）可能 content 为空但 reasoning_content 有内容
            if not content:
                reasoning = msg.get("reasoning_content", "").strip()
                if reasoning:
                    logger.warning("LLM returned empty content, using reasoning_content fallback")
                    return reasoning
            return content
        except Exception as exc:
            logger.warning("LLM API call failed: %s", exc)
            return ""
