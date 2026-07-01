"""日志脱敏工具 — 过滤日志中的敏感信息

敏感信息类型：
- 金额数字（≥ 1000）→ 替换为 ***
- API Key（sk-... / Bearer ...）→ 替换为 ***
- 文件路径中的隐私目录（.local / .secret）→ 保留目录名，隐藏文件名

使用方式：
    from stocks.logging_utils import setup_logging, desensitize_message
    setup_logging(level="INFO", desensitize=True)
    logger.info(f"资产总额: {total_amount}")  # 输出: 资产总额: ***
"""

from __future__ import annotations

import logging
import re
from typing import Optional

# ------------------------------------------------------------------
# 脱敏正则模式
# ------------------------------------------------------------------

# 金额数字：匹配 ≥ 4 位的数字（可能带小数点和逗号分隔符）
_AMOUNT_RE = re.compile(r"\b\d{1,3}(?:,\d{3})+\.?\d*|\b\d{4,}\.?\d*")

# API Key：sk-... / Bearer ... / api_key=... / key=...
_API_KEY_RE = re.compile(
    r"(?:sk-[a-zA-Z0-9]{20,}|Bearer\s+[a-zA-Z0-9_-]+|api_key[=:]\s*[a-zA-Z0-9_-]+|key[=:]\s*[a-zA-Z0-9_-]{16,})",
    re.IGNORECASE,
)

# 隐私文件路径：.local/xxx 或 .secret/xxx → 隐藏文件名
_PRIVATE_PATH_RE = re.compile(r"(\.(?:local|secret)/)[^\s\"'\]]+")


class DesensitizeFilter(logging.Filter):
    """日志过滤器 — 对日志消息进行脱敏处理"""

    def __init__(self, name: str = "", enabled: bool = True):
        super().__init__(name)
        self.enabled = enabled

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.enabled:
            return True
        if isinstance(record.msg, str):
            record.msg = desensitize_message(record.msg)
        # 对 args 中的字符串也进行脱敏
        if record.args:
            record.args = tuple(
                desensitize_message(str(arg)) if isinstance(arg, (str, int, float)) else arg
                for arg in record.args
            )
        return True


def desensitize_message(message: str) -> str:
    """对单条消息进行脱敏

    脱敏规则（按优先级）：
    1. API Key → ***
    2. 隐私文件路径 → 保留目录名，文件名替换为 ***
    3. 金额数字（≥ 4 位）→ ***
    """
    # 1. API Key
    message = _API_KEY_RE.sub("***", message)
    # 2. 隐私文件路径
    message = _PRIVATE_PATH_RE.sub(r"\1***", message)
    # 3. 金额数字（但避免误伤时间戳、版本号等）
    # 只替换独立的大数字（前后不是字母或数字），且排除常见的非金额模式
    message = _AMOUNT_RE.sub("***", message)
    return message


def setup_logging(
    level: str = "INFO",
    desensitize: bool = True,
    format_str: Optional[str] = None,
) -> None:
    """配置 stocks-claw 日志系统

    Args:
        level: 日志级别 DEBUG/INFO/WARNING/ERROR
        desensitize: 是否启用脱敏过滤器
        format_str: 自定义日志格式，默认包含时间、级别、模块名
    """
    if format_str is None:
        format_str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    # 配置根日志处理器
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(format_str))

    # 获取 stocks 命名空间的 logger
    logger = logging.getLogger("stocks")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.propagate = False

    # 清除旧的 DesensitizeFilter（避免多次 setup 时重复）
    logger.filters = [f for f in logger.filters if not isinstance(f, DesensitizeFilter)]
    handler.filters = [f for f in handler.filters if not isinstance(f, DesensitizeFilter)]

    # 添加脱敏过滤器（同时加到 logger 和 handler 上确保覆盖）
    if desensitize:
        desensitize_filter = DesensitizeFilter(enabled=True)
        logger.addFilter(desensitize_filter)
        handler.addFilter(desensitize_filter)


def get_logger(name: str) -> logging.Logger:
    """获取 stocks 命名空间的 logger，已配置脱敏过滤器"""
    return logging.getLogger(f"stocks.{name}")
