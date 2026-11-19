"""Read-only IBKR account, quote and daily-bar adapter for disaster alerts."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.wrapper import EWrapper

INFO_CODES = {2100, 2101, 2102, 2104, 2106, 2107, 2108, 2119, 2158, 2176, 10089, 10167}
SUMMARY_REQ_ID = 9100
QUOTE_REQ_BASE = 10_000
HISTORY_REQ_BASE = 20_000


def _positive(ticks: dict[int, float], tick_type: int) -> float | None:
    value = ticks.get(tick_type)
    return value if value is not None and value > 0 else None


def select_quote(ticks: dict[int, float]) -> dict[str, Any]:
    """Select current/close prices without hiding delayed market data."""
    previous_close = _positive(ticks, 9) or _positive(ticks, 75)
    for tick_type, delayed in ((4, False), (68, True), (9, False), (75, True)):
        price = _positive(ticks, tick_type)
        if price is not None:
            return {"price": price, "prev_close": previous_close, "delayed": delayed}
    for bid_type, ask_type, delayed in ((1, 2, False), (66, 67, True)):
        bid = _positive(ticks, bid_type)
        ask = _positive(ticks, ask_type)
        if bid is not None and ask is not None:
            return {"price": (bid + ask) / 2, "prev_close": previous_close, "delayed": delayed}
    return {"price": None, "prev_close": previous_close, "delayed": False}


def _contract(position: dict[str, Any]) -> Contract:
    contract = Contract()
    contract.conId = int(position["conid"])
    contract.symbol = position["symbol"]
    contract.secType = position.get("sec_type", "STK")
    contract.exchange = "SMART"
    contract.currency = position.get("currency", "USD")
    return contract


def _history_is_fresh(entry: dict[str, Any] | None, now: float, max_age_seconds: int) -> bool:
    if not entry or len(entry.get("bars", [])) < 50:
        return False
    try:
        fetched_at = datetime.fromisoformat(entry["fetched_at"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return False
    return 0 <= now - fetched_at <= max_age_seconds


class IbkrAlertApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.ready = threading.Event()
        self.positions_done = threading.Event()
        self.summary_done = threading.Event()
        self.accounts: list[str] = []
        self.positions: list[dict[str, Any]] = []
        self.net_liquidation: float | None = None
        self.quotes: dict[int, dict[str, Any]] = {}
        self.histories: dict[int, dict[str, Any]] = {}
        self.errors: list[dict[str, Any]] = []

    def nextValidId(self, orderId):
        self.ready.set()

    def managedAccounts(self, accountsList):
        self.accounts = [account for account in accountsList.split(",") if account]

    def position(self, account, contract, position, avgCost):
        self.positions.append({
            "account": account,
            "conid": int(contract.conId),
            "symbol": contract.symbol,
            "sec_type": contract.secType,
            "currency": contract.currency,
            "quantity": float(position),
            "avg_cost": float(avgCost),
        })

    def positionEnd(self):
        self.positions_done.set()

    def accountSummary(self, reqId, account, tag, value, currency):
        if tag == "NetLiquidation" and currency in ("USD", "BASE"):
            self.net_liquidation = float(value)

    def accountSummaryEnd(self, reqId):
        self.summary_done.set()

    def tickPrice(self, reqId, tickType, price, attrib):
        if reqId in self.quotes and price > 0:
            self.quotes[reqId]["ticks"][int(tickType)] = float(price)

    def tickSnapshotEnd(self, reqId):
        if reqId in self.quotes:
            self.quotes[reqId]["done"].set()

    def historicalData(self, reqId, bar):
        if reqId in self.histories:
            self.histories[reqId]["bars"].append({
                "date": bar.date,
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": float(bar.volume),
            })

    def historicalDataEnd(self, reqId, start, end):
        if reqId in self.histories:
            self.histories[reqId]["done"].set()

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        if int(errorCode) in INFO_CODES:
            return
        error = {"req_id": reqId, "code": int(errorCode), "message": errorString}
        self.errors.append(error)
        for collection in (self.quotes, self.histories):
            if reqId in collection:
                collection[reqId]["error"] = error
                collection[reqId]["done"].set()


def fetch_snapshot(host: str, port: int, client_id: int, expected_account: str,
                   history_cache: dict[str, Any] | None = None, timeout: int = 25,
                   history_max_age_seconds: int = 20 * 3600,
                   market_data_type: int = 3) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch one read-only snapshot; all connection/data failures remain explicit."""
    history_cache = dict(history_cache or {})
    app = IbkrAlertApp()
    app.connect(host, port, client_id)
    if not app.isConnected():
        raise ConnectionError(f"cannot connect to IBKR at {host}:{port}")
    thread = threading.Thread(target=app.run, daemon=True)
    thread.start()
    received_at = datetime.now(timezone.utc).isoformat()
    try:
        if not app.ready.wait(timeout):
            raise TimeoutError("IBKR API did not become ready")
        app.reqPositions()
        app.reqAccountSummary(SUMMARY_REQ_ID, "All", "NetLiquidation")
        if not app.positions_done.wait(timeout) or not app.summary_done.wait(timeout):
            raise TimeoutError("IBKR positions/account summary did not complete")
        if expected_account not in app.accounts:
            raise RuntimeError(f"expected account {expected_account} is not managed by this login")
        if any(position["account"] != expected_account for position in app.positions):
            raise RuntimeError("positions contain an unexpected account")
        if app.net_liquidation is None:
            raise RuntimeError("NetLiquidation was not returned")

        quote_requests = {}
        app.reqMarketDataType(market_data_type)
        for index, position in enumerate(app.positions):
            req_id = QUOTE_REQ_BASE + index
            quote_requests[position["symbol"]] = req_id
            app.quotes[req_id] = {"ticks": {}, "done": threading.Event(), "error": None}
            app.reqMktData(req_id, _contract(position), "", True, False, [])
        deadline = time.monotonic() + timeout
        for req_id, quote in app.quotes.items():
            quote["done"].wait(max(0.1, deadline - time.monotonic()))

        for position in app.positions:
            symbol = position["symbol"]
            quote_state = app.quotes[quote_requests[symbol]]
            position.update(select_quote(quote_state["ticks"]))
            position["data_as_of"] = received_at
            if quote_state["error"]:
                position["quote_error"] = quote_state["error"]
            cached = history_cache.get(symbol)
            position["bars"] = cached.get("bars", []) if cached else []

        now = time.time()
        history_positions = [
            position for position in app.positions
            if not _history_is_fresh(history_cache.get(position["symbol"]), now, history_max_age_seconds)
        ]
        for start in range(0, len(history_positions), 5):
            batch = history_positions[start:start + 5]
            request_ids = {}
            for offset, position in enumerate(batch):
                req_id = HISTORY_REQ_BASE + start + offset
                request_ids[req_id] = position
                app.histories[req_id] = {"bars": [], "done": threading.Event(), "error": None}
                app.reqHistoricalData(
                    req_id, _contract(position), "", "3 M", "1 day", "TRADES",
                    1, 1, False, [],
                )
            deadline = time.monotonic() + timeout
            for req_id in request_ids:
                app.histories[req_id]["done"].wait(max(0.1, deadline - time.monotonic()))
            for req_id, position in request_ids.items():
                history = app.histories[req_id]
                if history["bars"]:
                    bars = history["bars"]
                    position["bars"] = bars
                    history_cache[position["symbol"]] = {
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "bars": bars,
                    }
                elif history["error"]:
                    position["history_error"] = history["error"]

        snapshot = {
            "account": expected_account,
            "net_liquidation": app.net_liquidation,
            "positions": app.positions,
            "data_as_of": received_at,
            "errors": app.errors,
        }
        return snapshot, history_cache
    finally:
        app.disconnect()
        thread.join(timeout=3)
