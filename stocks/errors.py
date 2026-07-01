"""stocks-claw 分层异常体系

所有 stocks 模块的异常基类，支持可恢复/不可恢复分级，
为降级链（degradation chain）提供决策依据。
"""

from __future__ import annotations

from typing import Optional

# ------------------------------------------------------------------
# 基类
# ------------------------------------------------------------------

class StocksError(Exception):
    """所有 stocks-claw 异常的基类"""

    def __init__(self, message: str, *, source: Optional[str] = None, detail: Optional[str] = None):
        super().__init__(message)
        self.source = source  # 异常来源模块/Provider 名称
        self.detail = detail  # 详细错误信息（可包含原始异常 traceback）

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.source:
            parts.append(f"[source={self.source}]")
        if self.detail:
            parts.append(f"detail={self.detail}")
        return " ".join(parts)


class ConfigError(StocksError):
    """配置错误 — 不可恢复，需人工修复"""
    pass


class ValidationError(StocksError):
    """数据校验错误 — 不可恢复，输入数据不合法"""
    pass


class EngineError(StocksError):
    """引擎内部错误 — 不可恢复，代码逻辑缺陷"""
    pass


# ------------------------------------------------------------------
# Provider 异常体系
# ------------------------------------------------------------------

class ProviderError(StocksError):
    """Provider 数据获取异常基类

    所有子类通过 `is_retryable` 属性标记是否可恢复。
    降级链据此决策：重试、切备用 Provider、或直接放弃。
    """
    is_retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        source: Optional[str] = None,
        detail: Optional[str] = None,
        retry_after: Optional[int] = None,  # 秒，用于 RateLimit
    ):
        super().__init__(message, source=source, detail=detail)
        self.retry_after = retry_after


class ProviderTimeoutError(ProviderError):
    """网络超时 — 可恢复，通常重试或切备用 Provider"""
    is_retryable = True


class ProviderNetworkError(ProviderError):
    """网络错误（DNS、连接断开、SSL 错误等）— 可恢复"""
    is_retryable = True


class ProviderRateLimitError(ProviderError):
    """API 限流 — 可恢复，需等待 retry_after 后重试"""
    is_retryable = True


class ProviderDataError(ProviderError):
    """数据解析/格式错误 — 可恢复，切备用 Provider 或返回降级数据"""
    is_retryable = True


class ProviderAuthError(ProviderError):
    """认证失败（API Key 无效、权限不足）— 不可恢复"""
    is_retryable = False


class ProviderConfigError(ProviderError):
    """Provider 配置错误（参数缺失、格式错误）— 不可恢复"""
    is_retryable = False


class ProviderNotFoundError(ProviderError):
    """指定 Provider 不存在 — 不可恢复"""
    is_retryable = False


# ------------------------------------------------------------------
# 降级记录（非异常，用于传递降级状态）
# ------------------------------------------------------------------

class DegradationRecord:
    """记录一次降级事件，供上层分析和展示"""

    def __init__(
        self,
        market: str,
        primary_provider: str,
        fallback_provider: Optional[str] = None,
        error: Optional[ProviderError] = None,
        result: str = "empty",  # "success", "fallback_success", "empty"
        message: str = "",
    ):
        self.market = market
        self.primary_provider = primary_provider
        self.fallback_provider = fallback_provider
        self.error = error
        self.result = result
        self.message = message

    @property
    def error_type(self) -> Optional[str]:
        return type(self.error).__name__ if self.error else None

    @property
    def error_retryable(self) -> Optional[bool]:
        return self.error.is_retryable if self.error else None

    def to_dict(self) -> dict:
        return {
            "market": self.market,
            "primary_provider": self.primary_provider,
            "fallback_provider": self.fallback_provider,
            "error_type": self.error_type,
            "error_retryable": self.error_retryable,
            "result": self.result,
            "message": self.message,
        }

    def __repr__(self) -> str:
        return f"DegradationRecord({self.market}: {self.primary_provider} -> {self.fallback_provider or 'EMPTY'} [{self.result}])"
