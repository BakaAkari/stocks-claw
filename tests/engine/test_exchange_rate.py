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


def test_unsupported_currency_fails_instead_of_using_one_to_one():
    result = exchange_rate.convert_to_cny(100.0, "HKD")

    assert result.amount_cny is None
    assert result.rate is None
    assert result.status == "failed"
    assert result.source == "unsupported_currency"
