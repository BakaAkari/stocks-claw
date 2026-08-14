"""汇率缓存与换算测试。"""

import json
import time
from pathlib import Path

from stocks.engine import exchange_rate


def test_rate_cache_is_outside_secret_directory():
    """运行态汇率缓存不能写入凭据目录。"""
    cache_path = exchange_rate._RATE_CACHE_FILE

    assert cache_path.name == "exchange-rate-cache.json"
    assert cache_path.parent.name == "cache"
    assert ".secret" not in cache_path.parts
    assert cache_path == Path(__file__).resolve().parents[2] / "data" / "cache" / cache_path.name


def test_network_failure_uses_stale_cache(tmp_path, monkeypatch):
    cache_path = tmp_path / "exchange-rate-cache.json"
    stale_at = time.time() - exchange_rate._CACHE_TTL - 60
    cache_path.write_text(
        json.dumps({"rate": 6.75, "timestamp": stale_at}),
        encoding="utf-8",
    )
    monkeypatch.setattr(exchange_rate, "_RATE_CACHE_FILE", cache_path)
    monkeypatch.setattr(exchange_rate, "_load_fixed_rate", lambda: None)
    monkeypatch.setattr(exchange_rate, "_fetch_usd_cny_rate", lambda: None)

    result = exchange_rate.get_usd_cny_rate()

    assert result.rate == 6.75
    assert result.source == "stale_cache"
    assert result.timestamp == stale_at


def test_hardcoded_fallback_is_marked(tmp_path, monkeypatch):
    monkeypatch.setattr(exchange_rate, "_RATE_CACHE_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(exchange_rate, "_load_fixed_rate", lambda: None)
    monkeypatch.setattr(exchange_rate, "_fetch_usd_cny_rate", lambda: None)

    result = exchange_rate.get_usd_cny_rate()

    assert result.rate == 7.2
    assert result.source == "hardcoded_fallback"


def test_unsupported_currency_fails_instead_of_using_one_to_one(monkeypatch):
    """未知币种必须失败, 绝不能降级成 1:1 或凭空给一个汇率。"""
    # 阻断任何真实网络与缓存: 交叉汇率推导拿不到 USD 表 -> 非 HKD 无兜底 -> failed
    monkeypatch.setattr(exchange_rate, "_fetch_usd_base_rates", lambda: None)
    monkeypatch.setattr(exchange_rate, "_load_cache", lambda: None)

    result = exchange_rate.convert_to_cny(100.0, "ZZZ")

    assert result.amount_cny is None
    assert result.rate is None
    assert result.status == "failed"
    assert result.source == "unsupported_currency"


def test_hkd_conversion_supported_and_not_one_to_one(monkeypatch):
    """HKD 必须受支持: 用缓存里的 USD/CNY + HKD 钉住(联系汇率≈7.8)换算,
    且绝不把 100 HKD 当成 100 CNY。"""
    monkeypatch.setattr(exchange_rate, "_fetch_usd_base_rates", lambda: None)
    monkeypatch.setattr(exchange_rate, "_load_cache", lambda: (6.76, time.time()))
    monkeypatch.setattr(exchange_rate, "_load_fixed_rate", lambda: None)

    result = exchange_rate.convert_to_cny(7.8, "HKD")
    # 7.8 HKD 钉住 1 USD; 每 HKD→CNY = (USD→CNY)/(USD→HKD) = 6.76/7.8 ≈ 0.8667
    # 7.8 HKD 总额 → 7.8 × (6.76/7.8) = 6.76 CNY
    per_peg = 6.76 / 7.8
    expected = 7.8 * per_peg
    assert result.status in ("degraded", "ok")
    assert result.amount_cny is not None
    assert abs(result.amount_cny - expected) < 1e-6, f"expected ~{expected:.4f}, got {result.amount_cny}"
    # 绝不是 1:1 (7.8 HKD 不等于 7.8 CNY, 而是 ~6.76)
    assert result.amount_cny < 7.0
