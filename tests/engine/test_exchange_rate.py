"""汇率缓存与换算测试。"""

from pathlib import Path

from stocks.engine import exchange_rate


def test_rate_cache_is_outside_secret_directory():
    """运行态汇率缓存不能写入凭据目录。"""
    cache_path = exchange_rate._RATE_CACHE_FILE

    assert cache_path.name == "exchange-rate-cache.json"
    assert cache_path.parent.name == "cache"
    assert ".secret" not in cache_path.parts
    assert cache_path == Path(__file__).resolve().parents[2] / "data" / "cache" / cache_path.name
