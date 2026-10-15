"""pytest 全局配置和通用 fixtures"""

from __future__ import annotations

import builtins
import fcntl
import io
import ipaddress
import os
import shlex
import socket
import subprocess
import urllib.parse
import urllib.request
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from stocks.domain.models import (
    FinancialAsset,
    Instrument,
    Quote,
)
from stocks.engine.scaffolds import MarketScaffold, PortfolioScaffold

# ------------------------------------------------------------------
# 通用 fixtures


@pytest.fixture(autouse=True)
def isolate_private_defaults(request, tmp_path_factory, monkeypatch):
    """Exercise real persistence against temporary defaults, never user records."""
    if request.node.get_closest_marker("integration") is not None:
        return

    import stocks.engine as engine
    from stocks.engine.advisory_shadow_store import AdvisoryShadowStore
    from stocks.engine.outlook_synthesizer import OutlookSynthesizer
    from stocks.engine.risk_state import RiskStateStore
    from stocks.providers import finnhub_quote, polygon_quote

    repo = Path(__file__).resolve().parents[1]
    root = tmp_path_factory.mktemp("isolated_defaults")
    for name in ("config", "data", "local", "secret"):
        (root / name).mkdir(parents=True)
    # Keep public configuration behavior. The legacy personal profile is not
    # a public configuration fixture and must not be discovered by defaults.
    for path in (repo / "stocks" / "config").iterdir():
        if path.is_file() and path.name != "investor_profile.json":
            (root / "config" / path.name).symlink_to(path)
    monkeypatch.setattr(engine, "DEFAULT_CONFIG_DIR", root / "config")
    monkeypatch.setattr(engine, "DEFAULT_DATA_DIR", root / "data")
    monkeypatch.setattr(engine, "LOCAL_DATA_DIR", root / "local")
    monkeypatch.setattr(engine, "SECRET_DIR", root / "secret")
    for module, key in ((finnhub_quote, "FINNHUB_KEY_PATH"), (polygon_quote, "POLYGON_KEY_PATH")):
        monkeypatch.setattr(module, key, root / "secret" / (key.lower() + ".md"))
        monkeypatch.setattr(module, "ALT_KEY_PATH", root / "secret" / (key.lower() + "-alt.md"))

    original_outlook_init = OutlookSynthesizer.__init__

    def isolated_outlook_init(self, config, **kwargs):
        config = deepcopy(config)
        paths = config.setdefault("paths", {})
        if not paths.get("secret_dir"):
            paths["secret_dir"] = str(root / "secret")
        outlook = config.setdefault("llm", {}).setdefault("outlook", {})
        if "cache_dir" not in outlook:
            outlook["cache_dir"] = str(root / "local" / "outlook_cache")
        original_outlook_init(self, config, **kwargs)

    monkeypatch.setattr(OutlookSynthesizer, "__init__", isolated_outlook_init)

    original_risk_init = RiskStateStore.__init__
    original_shadow_init = AdvisoryShadowStore.__init__

    def isolated_risk_init(self, path=None, config=None):
        path = Path(path or ".local/risk_state.json").resolve()
        if path == repo / ".local/risk_state.json":
            path = root / "local/risk_state.json"
        original_risk_init(self, path, config)

    def isolated_shadow_init(self, root_dir=".local/advisory_shadow"):
        path = Path(root_dir).resolve()
        if path == repo / ".local/advisory_shadow":
            path = root / "local/advisory_shadow"
        original_shadow_init(self, path)

    monkeypatch.setattr(RiskStateStore, "__init__", isolated_risk_init)
    monkeypatch.setattr(AdvisoryShadowStore, "__init__", isolated_shadow_init)

    for key in ("OPENAI_API_KEY", "OPENAI_COMPATIBLE_API_KEY", "LLM_API_KEY",
                "FINNHUB_API_KEY", "POLYGON_API_KEY", "STOCKS_LLM_UNUSED_API_KEY",
                "STOCKS_OUTLOOK_UNUSED_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    private_roots = (repo / ".local", repo / ".secret", Path("/opt/data/.secret"))
    private_files = (repo / "stocks/data/financial_assets.json", repo / "stocks/config/investor_profile.json")

    def check_path(file, dir_fd=None):
        if isinstance(file, (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(file))
            if not path.is_absolute() and dir_fd is not None:
                # shutil.rmtree opens children relative to a directory handle,
                # not cwd. Resolve that real handle before applying the guard.
                if hasattr(fcntl, "F_GETPATH"):
                    base = fcntl.fcntl(dir_fd, fcntl.F_GETPATH, b"\0" * 1024)
                    base = Path(os.fsdecode(base.split(b"\0", 1)[0]))
                else:
                    base = Path(f"/proc/self/fd/{dir_fd}").resolve(strict=True)
                path = base / path
            path = path.resolve()
            if path in private_files or any(path == base or base in path.parents for base in private_roots):
                raise AssertionError(f"unit test attempted access to real private data: {path}")

    def guarded_open(original):
        def call(file, *args, **kwargs):
            check_path(file, kwargs.get("dir_fd"))
            return original(file, *args, **kwargs)
        return call

    monkeypatch.setattr(builtins, "open", guarded_open(builtins.open))
    monkeypatch.setattr(io, "open", guarded_open(io.open))
    monkeypatch.setattr(os, "open", guarded_open(os.open))


@pytest.fixture(autouse=True)
def isolate_default_signal_ledger(tmp_path, monkeypatch):
    """Real tracker behavior, but default repository ledger never receives tests."""
    from stocks.engine.signal_tracker import SignalTracker
    original = SignalTracker.__init__
    production = Path(__file__).resolve().parents[1] / '.local' / 'signal_tracker'

    def isolated_init(self, tracker_dir):
        path = Path(tracker_dir).resolve()
        if path == production.resolve():
            path = tmp_path / 'default_signal_tracker'
        original(self, path)

    monkeypatch.setattr(SignalTracker, '__init__', isolated_init)

# ------------------------------------------------------------------


def _loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except (TypeError, ValueError):
        return False


@pytest.fixture(autouse=True)
def block_unmocked_urlopen(request, monkeypatch):
    """Block unmocked external I/O, including curl fallback; allow local servers."""
    if request.node.get_closest_marker("integration") is not None:
        return

    original_urlopen = urllib.request.urlopen
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex
    original_getaddrinfo = socket.getaddrinfo
    original_popen = subprocess.Popen

    def urlopen(url, *args, **kwargs):
        value = url.full_url if isinstance(url, urllib.request.Request) else url
        if not _loopback(urllib.parse.urlsplit(value).hostname):
            raise AssertionError("unit test attempted real external urlopen; mock the provider")
        return original_urlopen(url, *args, **kwargs)

    def guarded_connect(original):
        def connect(sock, address):
            if sock.family in (socket.AF_INET, socket.AF_INET6) and not _loopback(address[0]):
                raise AssertionError("unit test attempted real external socket connection")
            return original(sock, address)
        return connect

    def getaddrinfo(host, *args, **kwargs):
        if host is not None and not _loopback(host):
            raise AssertionError("unit test attempted real external DNS lookup")
        return original_getaddrinfo(host, *args, **kwargs)

    def popen(args, *positional, **kwargs):
        command = shlex.split(args) if isinstance(args, str) else list(args)
        if command and Path(os.fsdecode(command[0])).name in {"curl", "wget"}:
            urls = [arg for arg in command[1:] if isinstance(arg, str) and "://" in arg]
            if not urls or any(not _loopback(urllib.parse.urlsplit(url).hostname) for url in urls):
                raise AssertionError("unit test attempted real external curl/wget; mock the transport")
        return original_popen(args, *positional, **kwargs)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect(original_connect))
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect(original_connect_ex))
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(subprocess, "Popen", popen)


@pytest.fixture
def sample_assets() -> list[FinancialAsset]:
    """标准测试资产组合 — 4 个 bucket 各覆盖"""
    return [
        FinancialAsset(name="现金", platform="银行", amount=100000, asset_type="现金管理"),
        FinancialAsset(name="理财", platform="银行", amount=200000, asset_type="理财"),
        FinancialAsset(name="沪深300ETF", platform="券商", amount=150000, asset_type="股票ETF"),
        FinancialAsset(name="华安黄金ETF", platform="支付宝", amount=50000, asset_type="黄金ETF"),
    ]


@pytest.fixture
def sample_constraints() -> dict:
    """标准约束配置"""
    return {
        "权益": {"min": 0.20, "max": 0.60},
        "固收": {"min": 0.15, "max": 0.50},
        "现金": {"min": 0.05, "max": 0.30},
        "黄金": {"min": 0.00, "max": 0.15},
    }


@pytest.fixture
def sample_instruments() -> list[Instrument]:
    """标准 watchlist 标的"""
    return [
        Instrument(code="000300", name="沪深300", market="a", exchange="sz_index", category="equity_cn"),
        Instrument(code="518880", name="华安黄金ETF", market="a", exchange="sh", category="gold"),
        Instrument(code="QQQ", name="纳斯达克100", market="us", category="tech"),
    ]


@pytest.fixture
def sample_quotes() -> dict[str, list[Quote]]:
    """标准测试行情 — A股和美股各两只"""
    return {
        "a": [
            Quote(
                instrument=Instrument(code="000300", name="沪深300", market="a", category="equity_cn"),
                price=3542.33,
                change=12.45,
                pct_change=0.35,
            ),
            Quote(
                instrument=Instrument(code="518880", name="华安黄金ETF", market="a", category="gold"),
                price=4.55,
                change=-0.02,
                pct_change=-0.44,
            ),
        ],
        "us": [
            Quote(
                instrument=Instrument(code="QQQ", name="纳斯达克100", market="us", category="tech"),
                price=385.20,
                change=5.30,
                pct_change=1.40,
            ),
        ],
    }


@pytest.fixture
def portfolio_scaffold() -> PortfolioScaffold:
    return PortfolioScaffold()


@pytest.fixture
def market_scaffold() -> MarketScaffold:
    return MarketScaffold()


# ------------------------------------------------------------------
# Mock fixtures
# ------------------------------------------------------------------


@pytest.fixture
def mock_tencent_provider():
    """Mock 腾讯 Provider — 返回固定行情"""
    provider = Mock()
    provider.name = "tencent_a"
    provider.supported_markets = ["a"]

    async def _fetch_batch(instruments):
        return [
            Quote(
                instrument=inst,
                price=3542.33,
                change=12.45,
                pct_change=0.35,
            )
            for inst in instruments
        ]

    provider.fetch_batch = AsyncMock(side_effect=_fetch_batch)
    provider.fetch = AsyncMock(return_value=Quote(
        instrument=Instrument(code="000300", name="沪深300", market="a"),
        price=3542.33,
        change=12.45,
        pct_change=0.35,
    ))
    return provider


@pytest.fixture
def mock_finnhub_provider():
    """Mock Finnhub Provider — 返回固定行情"""
    provider = Mock()
    provider.name = "finnhub"
    provider.supported_markets = ["us", "crypto"]

    async def _fetch_batch(instruments):
        return [
            Quote(
                instrument=inst,
                price=385.20,
                change=5.30,
                pct_change=1.40,
            )
            for inst in instruments
        ]

    provider.fetch_batch = AsyncMock(side_effect=_fetch_batch)
    provider.fetch = AsyncMock(return_value=Quote(
        instrument=Instrument(code="QQQ", name="纳斯达克100", market="us"),
        price=385.20,
        change=5.30,
        pct_change=1.40,
    ))
    return provider


@pytest.fixture
def mock_registry(mock_tencent_provider, mock_finnhub_provider):
    """Mock ProviderRegistry — 预注册两个 Provider"""
    from stocks.providers.registry import ProviderRegistry

    registry = ProviderRegistry()
    registry.register(mock_tencent_provider)
    registry.register(mock_finnhub_provider)
    return registry


# ------------------------------------------------------------------
# pytest 配置
# ------------------------------------------------------------------


pytest_plugins = ("pytest_asyncio",)


def pytest_configure(config):
    """注册自定义标记"""
    config.addinivalue_line("markers", "integration: 集成测试（需要网络）")
    config.addinivalue_line("markers", "slow: 慢速测试（> 1s）")
