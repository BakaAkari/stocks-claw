from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class LLMConfig:
    model: str
    url: str
    api_key: str


DEFAULT_MODEL = os.getenv('STOCKS_LLM_MODEL', 'gpt-5.4')
FALLBACK_MODEL = os.getenv('STOCKS_FALLBACK_LLM_MODEL', 'kimi-k2.5')
DEFAULT_URL = os.getenv('STOCKS_LLM_URL', 'http://192.168.50.55:8317/v1/chat/completions')
DEFAULT_API_KEY = os.getenv('STOCKS_LLM_API_KEY', 'sk-local-4db88e044a2e42b020a81d40')
CONSTRAINT_MODEL = os.getenv('STOCKS_CONSTRAINT_LLM_MODEL', DEFAULT_MODEL)
DAILY_REPORT_MODEL = os.getenv('STOCKS_DAILY_LLM_MODEL', DEFAULT_MODEL)


def get_personal_advice_llm_config(model: str | None = None) -> LLMConfig:
    return LLMConfig(model=model or DEFAULT_MODEL, url=DEFAULT_URL, api_key=DEFAULT_API_KEY)


def get_constraint_llm_config(model: str | None = None) -> LLMConfig:
    return LLMConfig(model=model or CONSTRAINT_MODEL, url=DEFAULT_URL, api_key=DEFAULT_API_KEY)


def get_daily_report_llm_config(model: str | None = None) -> LLMConfig:
    return LLMConfig(model=model or DAILY_REPORT_MODEL, url=DEFAULT_URL, api_key=DEFAULT_API_KEY)
