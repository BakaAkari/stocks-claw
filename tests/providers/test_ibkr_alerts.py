from stocks.providers.ibkr_alerts import select_quote


def test_select_quote_prefers_real_time_last_and_close():
    quote = select_quote({4: 101.5, 1: 101.4, 2: 101.6, 9: 100.0})

    assert quote == {"price": 101.5, "prev_close": 100.0, "delayed": False}


def test_select_quote_marks_delayed_data_explicitly():
    quote = select_quote({68: 80.0, 75: 100.0})

    assert quote["price"] == 80.0
    assert quote["prev_close"] == 100.0
    assert quote["delayed"] is True


def test_select_quote_uses_midpoint_when_last_is_unavailable():
    quote = select_quote({1: 99.0, 2: 101.0, 9: 100.0})

    assert quote["price"] == 100.0
    assert quote["delayed"] is False


def test_select_quote_rejects_non_positive_prices():
    quote = select_quote({4: -1.0, 9: 100.0, 1: -1.0, 2: -1.0})

    assert quote["price"] == 100.0
    assert quote["prev_close"] == 100.0


def test_fetch_snapshot_uses_public_disconnect_for_cleanup(monkeypatch):
    import threading

    import stocks.providers.ibkr_alerts as module

    class FakeApp:
        def __init__(self):
            self.ready = threading.Event()
            self.positions_done = threading.Event()
            self.summary_done = threading.Event()
            self.ready.set()
            self.positions_done.set()
            self.summary_done.set()
            self.accounts = ["U16075508"]
            self.positions = []
            self.net_liquidation = 10000
            self.errors = []
            self.quotes = {}
            self.histories = {}
            self.disconnected = False

        def connect(self, host, port, client_id):
            return None

        def isConnected(self):
            return True

        def run(self):
            return None

        def reqPositions(self):
            return None

        def reqAccountSummary(self, *args):
            return None

        def reqMarketDataType(self, market_data_type):
            return None

        def disconnect(self):
            self.disconnected = True

    fake = FakeApp()
    monkeypatch.setattr(module, "IbkrAlertApp", lambda: fake)

    snapshot, _ = module.fetch_snapshot("test", 1, 1, "U16075508")

    assert snapshot["net_liquidation"] == 10000
    assert fake.disconnected is True


def test_delayed_subscription_notice_does_not_finish_quote_early():
    import threading

    from stocks.providers.ibkr_alerts import IbkrAlertApp

    app = IbkrAlertApp()
    app.quotes[10001] = {"ticks": {}, "done": threading.Event(), "error": None}

    app.error(10001, 10167, "Displaying delayed market data")

    assert app.quotes[10001]["done"].is_set() is False
    assert app.quotes[10001]["error"] is None
    assert app.errors == []
