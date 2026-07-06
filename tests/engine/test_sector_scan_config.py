from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SECTOR_SCAN_PATH = REPO_ROOT / "stocks" / "config" / "sector_scan.json"
WATCHLIST_PATH = REPO_ROOT / "stocks" / "config" / "watchlist.json"

SUPPORTED_MARKETS = {"a", "us", "crypto"}
SUPPORTED_POOLS = {"broad", "sector", "defensive", "rates", "ai_chain"}


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _key(item: dict) -> str:
    return f"{item.get('market')}:{item.get('code')}"


def test_sector_scan_config_is_valid_and_deduplicated():
    data = _load_json(SECTOR_SCAN_PATH)

    assert isinstance(data, list)
    assert data

    keys = [_key(item) for item in data]
    assert len(keys) == len(set(keys))

    for item in data:
        assert item.get("code")
        assert item.get("name")
        assert item.get("market") in SUPPORTED_MARKETS
        assert item.get("category")
        assert item.get("pool") in SUPPORTED_POOLS
        assert item.get("market") != "hk"
        if item.get("market") == "a":
            assert item.get("exchange") in {"sh", "sz"}


def test_sector_scan_does_not_duplicate_watchlist():
    sector_keys = {_key(item) for item in _load_json(SECTOR_SCAN_PATH)}
    watchlist_keys = {_key(item) for item in _load_json(WATCHLIST_PATH)}

    assert sector_keys.isdisjoint(watchlist_keys)


def test_a_share_scan_pool_has_controlled_expansion_coverage():
    data = _load_json(SECTOR_SCAN_PATH)
    a_items = [item for item in data if item.get("market") == "a"]
    categories = {item.get("category") for item in a_items}

    assert 30 <= len(a_items) <= 40
    assert {
        "宽基_上证50",
        "宽基_中证500",
        "宽基_中证1000",
        "宽基_科创100",
        "宽基_创业板50",
        "半导体",
        "人工智能",
        "机器人",
        "软件",
        "通信",
        "传媒",
        "有色",
        "煤炭",
        "钢铁",
        "化工",
        "稀土",
        "白酒",
        "食品饮料",
        "家电",
        "旅游",
        "电力",
        "红利",
        "银行",
        "保险",
        "港股_恒生科技",
        "港股_恒生医疗",
        "港股_互联网",
    }.issubset(categories)


def test_hong_kong_theme_items_are_a_share_listed_proxies():
    data = _load_json(SECTOR_SCAN_PATH)
    hk_proxy_items = [
        item for item in data if str(item.get("category", "")).startswith("港股_")
    ]

    assert {item["code"] for item in hk_proxy_items} == {"513130", "513060", "513770"}
    for item in hk_proxy_items:
        assert item["market"] == "a"
        assert item["exchange"] in {"sh", "sz"}
        assert item["pool"] == "sector"


def test_sector_scan_excludes_known_split_distortion_symbols():
    data = _load_json(SECTOR_SCAN_PATH)
    sector_keys = {_key(item) for item in data}

    # 515880 在 2026-07 出现份额拆分，当前历史链路会把拆分误读为价格暴跌。
    assert "a:515880" not in sector_keys
    assert "a:159695" in sector_keys
