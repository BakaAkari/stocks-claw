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
    ):
        self.model = model
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
            "temperature": 1.0,
            "max_tokens": 16384,
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
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        msg = result["choices"][0]["message"]
        content = msg.get("content", "").strip()
        if not content:
            content = msg.get("reasoning_content", "").strip()
        return content


def get_llm_client() -> LLMClient:
    """Return a default LLM client for the advisory pipeline."""
    return LLMClient()
