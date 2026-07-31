"""汇率工具 — 多币种资产统一换算

支持从免费 API 获取实时汇率，或从配置文件读取固定汇率。
默认基准币种为 CNY。
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 默认汇率 API（免费，无需 key）
_DEFAULT_RATE_API = "https://api.exchangerate-api.com/v4/latest/USD"

# 运行态缓存与凭据目录分离；该目录由 .gitignore 排除。
_RATE_CACHE_FILE = (
    Path(__file__).resolve().parents[2] / "data" / "cache" / "exchange-rate-cache.json"
)

# 缓存有效期（秒）
_CACHE_TTL = 3600 * 6  # 6 小时


@dataclass(frozen=True)
class ExchangeRateResult:
    """带来源标记的汇率结果。"""

    rate: float
    source: str
    timestamp: Optional[float] = None


@dataclass(frozen=True)
class ConversionResult:
    """币种换算结果；失败时 amount_cny/rate 为 None。"""

    amount_cny: Optional[float]
    rate: Optional[float]
    source: str
    status: str  # ok / degraded / failed


def _fetch_usd_cny_rate() -> Optional[float]:
    """从免费 API 获取 USD/CNY 汇率。

    网络异常时返回 None，由调用方决定是否使用缓存或默认值。
    """
    try:
        req = urllib.request.Request(
            _DEFAULT_RATE_API,
            headers={"User-Agent": "stocks-claw/2.0"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        rates = data.get("rates", {})
        cny_rate = rates.get("CNY")
        if cny_rate:
            logger.info("获取 USD/CNY 汇率: %.4f", cny_rate)
            _save_cache(cny_rate)
            return float(cny_rate)
    except Exception as exc:
        logger.warning("获取汇率失败: %s", exc)
    return None


def _load_cache() -> Optional[tuple[float, float]]:
    """加载缓存汇率及时间戳；是否过期由调用方判断。"""
    if not _RATE_CACHE_FILE.exists():
        return None
    try:
        with open(_RATE_CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        cached_rate = cache.get("rate")
        cached_at = cache.get("timestamp", 0)
        if cached_rate and cached_at:
            return float(cached_rate), float(cached_at)
    except Exception:
        pass
    return None


def _save_cache(rate: float) -> None:
    """保存汇率到缓存文件。"""
    try:
        _RATE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_RATE_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"rate": rate, "timestamp": time.time()}, f)
    except Exception:
        pass


def _load_fixed_rate() -> Optional[float]:
    """从环境变量或 .secret 文件加载固定汇率。"""
    env_rate = os.environ.get("USD_CNY_RATE", "").strip()
    if env_rate:
        try:
            return float(env_rate)
        except ValueError:
            pass

    fixed_file = Path(__file__).resolve().parents[2] / ".secret" / "usd-cny-rate.md"
    if fixed_file.exists():
        try:
            return float(fixed_file.read_text(encoding="utf-8").strip())
        except ValueError:
            pass
    return None


def get_usd_cny_rate() -> ExchangeRateResult:
    """获取 USD/CNY 汇率。

    优先级：固定配置 > 新鲜缓存 > 实时 API > 过期缓存 > 默认值(7.2)

    Returns:
        带来源与缓存时间戳的 USD/CNY 汇率。
    """
    # 1. 固定配置（用户明确指定，最高优先级）
    fixed = _load_fixed_rate()
    if fixed:
        return ExchangeRateResult(rate=fixed, source="fixed_config")

    # 2. 缓存（已验证过的数据，优先使用以避免网络阻塞）
    cached = _load_cache()
    if cached:
        cached_rate, cached_at = cached
        if (time.time() - cached_at) < _CACHE_TTL:
            logger.info("使用缓存汇率 USD/CNY: %.4f", cached_rate)
            return ExchangeRateResult(
                rate=cached_rate,
                source="cache",
                timestamp=cached_at,
            )

    # 3. 实时 API（缓存过期或不存在时获取）
    live = _fetch_usd_cny_rate()
    if live:
        return ExchangeRateResult(rate=live, source="live_api")

    # 4. 实时获取失败后，过期缓存仍优先于硬编码值
    if cached:
        cached_rate, cached_at = cached
        logger.warning("实时汇率不可用，使用过期缓存 USD/CNY: %.4f", cached_rate)
        return ExchangeRateResult(
            rate=cached_rate,
            source="stale_cache",
            timestamp=cached_at,
        )

    # 5. 最终硬编码兑底，必须显式标记
    _HARDCODED_FALLBACK_USD_CNY = 7.2

    logger.warning("无法获取 USD/CNY 汇率，使用带标记的默认值 %.1f", _HARDCODED_FALLBACK_USD_CNY)
    return ExchangeRateResult(rate=_HARDCODED_FALLBACK_USD_CNY, source="hardcoded_fallback")


def convert_to_cny(amount: float, currency: str) -> ConversionResult:
    """将指定币种的金额换算为 CNY。

    Args:
        amount: 金额
        currency: 币种代码（"CNY" / "USD" / "HKD" 等）

    Returns:
        带状态和来源的换算结果。
    """
    currency = (currency or "CNY").upper().strip()
    if currency == "CNY":
        return ConversionResult(amount, 1.0, "identity", "ok")
    if currency == "USD":
        result = get_usd_cny_rate()
        status = (
            "degraded"
            if result.source in {"stale_cache", "hardcoded_fallback"}
            else "ok"
        )
        return ConversionResult(amount * result.rate, result.rate, result.source, status)
    logger.error("不支持币种 '%s' 的自动换算；该资产不计入 CNY 合计", currency)
    return ConversionResult(None, None, "unsupported_currency", "failed")
