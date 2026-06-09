"""LLM 工具函数 — 模型校验、配置验证"""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

# 模型 fallback 链：当首选模型不可用时依次尝试
_ENHANCER_FALLBACK_CHAIN = [
    "deepseek-v4-flash",
    "gpt-4o-mini",
    "gpt-4o",
]

_ANALYSIS_FALLBACK_CHAIN = [
    "kimi-k2.6",
    "gpt-4o",
    "deepseek-v4-pro",
    "gpt-4o-mini",
]


def _fetch_available_models(api_key: str, base_url: str, timeout: int = 10) -> set[str]:
    """同步调用 /v1/models 获取可用模型 ID 集合。

    网络异常、认证失败或解析错误时返回空集合，由调用方决定如何处理。
    """
    if not api_key or not base_url:
        return set()

    url = f"{base_url.rstrip('/')}/models"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "stocks-claw/2.0",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("无法获取可用模型列表 (%s): %s", url, exc)
        return set()

    models = data.get("data", [])
    if not isinstance(models, list):
        logger.warning("/v1/models 返回格式异常: %s", data)
        return set()

    available = {m.get("id", "") for m in models if isinstance(m, dict)}
    logger.info("检测到 %d 个可用模型", len(available))
    return available


def _resolve_model(
    preferred: str,
    available: set[str],
    fallback_chain: list[str],
) -> tuple[str, bool, bool]:
    """解析最终使用的模型。

    Args:
        preferred: 用户/配置指定的首选模型。
        available: 代理端可用模型 ID 集合。
        fallback_chain: 当首选不可用时依次尝试的 fallback 列表。

    Returns:
        (resolved_model, is_fallback, is_available)
        - resolved_model: 最终确定的模型名
        - is_fallback: 是否使用了 fallback（True = 不是首选）
        - is_available: resolved_model 是否在 available 中（False = 完全不可用）
    """
    if preferred in available:
        return preferred, False, True

    for candidate in fallback_chain:
        if candidate in available:
            logger.warning(
                "首选模型 '%s' 不可用，自动降级到 '%s'",
                preferred, candidate,
            )
            return candidate, True, True

    # 所有 fallback 都不可用
    logger.error(
        "首选模型 '%s' 及所有 fallback 均不可用，LLM 模块将被禁用。"
        "可用模型: %s",
        preferred, sorted(available)[:20],
    )
    return preferred, True, False


def validate_llm_models(
    enhancer_model: str,
    analysis_model: str,
    api_key: Optional[str],
    base_url: Optional[str],
) -> tuple[str, str, bool, bool]:
    """校验并解析 LLM 模型配置。

    在 StocksEngine 初始化时调用，确保配置的模型在代理端真实可用。
    如果代理不可达，保留原配置但记录警告（避免阻塞启动）。

    Args:
        enhancer_model: Enhancer 首选模型。
        analysis_model: Analysis 首选模型。
        api_key: API Key（None 时跳过校验）。
        base_url: Base URL（None 时跳过校验）。

    Returns:
        (resolved_enhancer, resolved_analysis, enhancer_available, analysis_available)
        - resolved_enhancer: 校验后的 Enhancer 模型
        - resolved_analysis: 校验后的 Analysis 模型
        - enhancer_available: Enhancer 模型是否真实可用
        - analysis_available: Analysis 模型是否真实可用
    """
    if not api_key or not base_url:
        logger.info("LLM API 未配置，跳过模型校验")
        return enhancer_model, analysis_model, True, True

    available = _fetch_available_models(api_key, base_url)
    if not available:
        # 代理不可达，保留原配置但标记风险
        logger.warning(
            "无法连接到 LLM 代理 (%s) 获取模型列表，保留原配置但可能不可用。"
            "请检查网络或代理状态。",
            base_url,
        )
        return enhancer_model, analysis_model, True, True

    resolved_e, enhancer_fallback, enhancer_available = _resolve_model(
        enhancer_model, available, _ENHANCER_FALLBACK_CHAIN
    )
    resolved_a, analysis_fallback, analysis_available = _resolve_model(
        analysis_model, available, _ANALYSIS_FALLBACK_CHAIN
    )

    if not enhancer_fallback and not analysis_fallback:
        logger.info(
            "LLM 模型校验通过: enhancer=%s, analysis=%s",
            resolved_e, resolved_a,
        )

    return resolved_e, resolved_a, enhancer_available, analysis_available
