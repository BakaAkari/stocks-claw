#!/usr/bin/env python3
"""Settle tracked signals against current prices.

Run via cron: every 6 hours, checks unsettled signals and settles them
if their time windows (24h / 1w) have elapsed.

P0-5 fix (2026-08-14): previously priced signals with `harvester._fetch_quotes()`,
which is BTC/crypto-only and returned only {"BTCUSDT": ...}. Every A-share ETF
(a:512400) and US stock (us:AAPL) symbol never matched -> never settled (226 stock
signals, 0 settled). Now A-share uses tencent(+eastmoney fallback), US uses finnhub;
crypto keeps the original harvester branch so existing BTC settlements are preserved.
"""
import os
import sys
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

PROJECT_ROOT = os.environ.get("STOCKS_CLAW_REPO_ROOT", "/mnt/user/code-project/stocks-claw")
sys.path.insert(0, PROJECT_ROOT)

from stocks.domain.models import Instrument  # noqa: E402
from stocks.engine.signal_tracker import SignalTracker  # noqa: E402
from stocks.providers.tencent_a import TencentAQuoteProvider  # noqa: E402
from stocks.providers.eastmoney_a import EastmoneyAQuoteProvider  # noqa: E402
from stocks.providers.finnhub_quote import FinnhubQuoteProvider  # noqa: E402


def _split_symbol(symbol: str) -> tuple[Optional[str], str]:
    """'a:512400' -> ('a','512400'); 'us:AAPL' -> ('us','AAPL'); 'BTCUSDT' -> ('crypto','BTCUSDT')."""
    if ":" in symbol:
        market, code = symbol.split(":", 1)
        return market, code
    return "crypto", symbol


def _instrument_for(market: str, code: str) -> Optional[Instrument]:
    if market == "a":
        return Instrument(code=code, name="", market="a", category="equity_cn")
    if market == "us":
        return Instrument(code=code.upper(), name="", market="us", category="equity_us")
    return None


def _price_map(quotes) -> dict[str, float]:
    out = {}
    for q in quotes or []:
        if q and q.price is not None and q.instrument:
            out[q.instrument.code.upper()] = q.price
    return out


async def _fetch_stock_prices(symbols: list[str]) -> dict[str, float]:
    """Fetch stock prices for a/us symbols via engine providers -> {symbol: price}.

    A-share: tencent (stable) primary, eastmoney fallback. US: finnhub.
    Falls back to per-symbol fetch when a batch returns empty (providers flaky).
    """
    a_providers = [TencentAQuoteProvider(), EastmoneyAQuoteProvider()]
    us_providers = [FinnhubQuoteProvider()]
    by_market: dict[str, list[tuple[str, Instrument]]] = {}
    for sym in symbols:
        market, code = _split_symbol(sym)
        if market not in ("a", "us"):
            continue
        inst = _instrument_for(market, code)
        if inst is None:
            continue
        by_market.setdefault(market, []).append((sym, inst))

    out: dict[str, float] = {}
    for market, items in by_market.items():
        providers = a_providers if market == "a" else us_providers
        insts = [inst for _, inst in items]
        # 1) try batch on each provider until one returns non-empty
        batch_ok = False
        for prov in providers:
            try:
                quotes = await prov.fetch_batch(insts)
            except Exception:
                continue
            pm = _price_map(quotes)
            if pm:
                for sym, inst in items:
                    if inst.code.upper() in pm and sym not in out:
                        out[sym] = pm[inst.code.upper()]
                batch_ok = True
            # if batch returned anything, stop provider fallback loop
            if pm:
                break
        # 2) per-symbol fallback for still-missing A shares
        if market == "a":
            missing = [(s, i) for s, i in items if s not in out]
            for sym, inst in missing:
                for prov in providers:
                    try:
                        q = await prov.fetch(inst)
                    except Exception:
                        q = None
                    if q is not None and q.price is not None:
                        out[sym] = q.price
                        break
    return out


def main():
    tracker_dir = f"{PROJECT_ROOT}/.local/signal_tracker"
    tracker = SignalTracker(tracker_dir)
    now = datetime.now(timezone.utc)

    delta_map = {"24h": timedelta(hours=24), "1w": timedelta(days=7)}
    due = {w: [] for w in delta_map}
    stock_symbols: set[str] = set()
    crypto_symbols: set[str] = set()
    for window, delta in delta_map.items():
        for sig in tracker.unsettled(window):
            if now < sig.generated_at + delta:
                continue
            if not sig.symbol:
                continue
            due[window].append(sig)
            market, _ = _split_symbol(sig.symbol)
            (stock_symbols if market in ("a", "us") else crypto_symbols).add(sig.symbol)

    # Stock prices via engine providers (P0-5 fix)
    stock_prices = {}
    if stock_symbols:
        try:
            stock_prices = asyncio.run(_fetch_stock_prices(list(stock_symbols)))
        except Exception as e:
            print(f"[signal_settlement] stock price fetch failed: {e!r}")

    # Crypto prices via BTC-only harvester (preserve existing behaviour)
    crypto_prices = {}
    if crypto_symbols:
        try:
            from stocks.engine.intelligence_harvester import IntelligenceHarvester
            harvester = IntelligenceHarvester(max_items_per_source=1)
            async def _get():
                return await harvester._fetch_quotes()
            quotes = asyncio.run(_get()) or {}
            for sym in crypto_symbols:
                q = quotes.get(sym)
                if isinstance(q, dict) and q.get("price") is not None:
                    crypto_prices[sym] = q["price"]
        except Exception as e:
            print(f"[signal_settlement] crypto price fetch failed: {e!r}")

    prices = {**stock_prices, **crypto_prices}
    settled = {"24h": 0, "1w": 0}
    for window, sigs in due.items():
        for sig in sigs:
            current_price = prices.get(sig.symbol)
            if current_price is None:
                continue
            tracker.settle(sig, window, current_price, now=now)
            settled[window] += 1
            print(f"[signal_settlement] Settled {sig.signal_id} ({window}): "
                  f"entry={sig.generation_price} exit={current_price}")

    perf = tracker.performance()
    print(f"[signal_settlement] Done. 24h={settled['24h']} 1w={settled['1w']}. "
          f"Win rate: 24h={perf.get('win_rate_24h','?')} 1w={perf.get('win_rate_1w','?')}")


if __name__ == "__main__":
    main()
