"""Minimal OpenAI-compatible LLM client for the advisory pipeline.

The client reads credentials from the standard environment / .secret layout used
by the rest of the project and exposes a `complete(prompt) -> str` interface.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger("llm_client")

DEFAULT_MODEL = "deepseek-v4-pro"
DEFAULT_TIMEOUT = 360


def _load_secret(name: str) -> Optional[str]:
    secret_path = Path(".secret") / name
    if secret_path.exists():
        text = secret_path.read_text(encoding="utf-8").strip()
        # Some secret files store multiple lines: base_url\nkey. Return the
        # non-URL line as the key.
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return None
        if len(lines) == 1:
            return lines[0]
        # Prefer a line that looks like an API key over a URL.
        for line in lines:
            if not line.startswith("http"):
                return line
        return lines[0]
    return None


def _load_secret_url(name: str) -> Optional[str]:
    """Load a URL from a secret file. First line is preferred if it looks like a URL."""
    secret_path = Path(".secret") / name
    if not secret_path.exists():
        return None
    text = secret_path.read_text(encoding="utf-8").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    if lines[0].startswith("http"):
        return lines[0]
    for line in lines[1:]:
        if line.startswith("http"):
            return line
    return None


def _load_credentials() -> tuple[Optional[str], Optional[str]]:
    api_key = (
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("LLM_API_KEY")
        or _load_secret("openai-key.md")
    )
    base_url = (
        os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("LLM_BASE_URL")
        or _load_secret_url("openai-key.md")
        or _load_secret_url("openai-base-url.md")
        or "https://api.openai.com/v1"
    )
    return api_key, base_url


class LLMClient:
    """Minimal synchronous OpenAI-compatible client."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        stream: bool = False,
    ):
        self.model = model
        # 流式返回让连接持续有数据，避免代理网关对长生成的空闲超时（如 30 秒 502）
        self.stream = stream
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self._api_key, self._base_url = _load_credentials()
        if self.api_key is None:
            self.api_key = self._api_key
        if self.base_url is None:
            self.base_url = self._base_url

    def complete(self, prompt: str) -> str:
        if not self.api_key:
            raise RuntimeError("LLM API key not configured")

        url = f"{self.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You are a helpful financial analyst. Respond in JSON only, no markdown fences."},
                {"role": "user", "content": prompt},
            ],
            # 2026-08-13: 1.0 -> 0.2。deepseek-v4 系列是推理模型,temperature=1.0
            # 下输出不稳定(时而 content=JSON 成功,时而 content 空/reasoning_content
            # 为推理过程非 JSON,导致 advisory 的 json.loads 失败)。0.2 与情报分析
            # (analysis_temperature 0.1) 对齐,让研判 JSON 输出稳定。
            "temperature": 0.2,
            # 2026-08-13: 16384 -> 65536。deepseek-v4-flash 是推理模型,大 prompt
            # (137KB snapshot) 下 reasoning 消耗 ~16K token 占满 max_tokens,导致
            # 最终答案 content 为空 → json.loads 失败。留足空间给 reasoning+content。
            "max_tokens": 65536,
        }
        if self.stream:
            payload["stream"] = True
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
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            if self.stream:
                content = _read_stream_content(resp)
            else:
                result = json.loads(resp.read().decode("utf-8"))
                content = (result["choices"][0]["message"].get("content") or "").strip()
        if not content:
            # 推理模型(deepseek-v4) content 为空 = reasoning 占满 max_tokens,
            # 最终答案没产出。不能拿 reasoning_content(推理过程,非 JSON)冒充
            # 答案,否则上层 json.loads 失败。抛异常让上层 fallback 并记录原因。
            raise RuntimeError(
                "LLM returned empty content (reasoning consumed max_tokens); "
                "raise max_tokens or use a non-reasoning model"
            )
        return content


def _read_stream_content(resp) -> str:
    """拼接 SSE 流中的 delta.content；reasoning_content 是推理过程，不计入答案。"""
    parts: list[str] = []
    for raw in resp:
        line = raw.decode("utf-8").strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if data == "[DONE]":
            break
        chunk = json.loads(data)
        if chunk.get("error"):
            raise RuntimeError(f"LLM stream error: {chunk['error']}")
        for choice in chunk.get("choices") or []:
            piece = (choice.get("delta") or {}).get("content")
            if piece:
                parts.append(piece)
    return "".join(parts).strip()


def get_llm_client() -> LLMClient:
    """Return a default LLM client for the advisory pipeline."""
    return LLMClient()
