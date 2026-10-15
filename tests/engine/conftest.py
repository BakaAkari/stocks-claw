"""Named fixtures for the evaluation slice, using real production adapters."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from stocks.domain.models import Instrument
from stocks.engine.scheduled_analysis import _track_engine_action_signals
from stocks.engine.target_observation import observe
from stocks.providers.binance_quote import BinanceQuoteProvider


@pytest.fixture
def evaluation_signal(tmp_path):
    generated = datetime(2026, 10, 1, tzinfo=timezone.utc)
    quote = BinanceQuoteProvider()._data_to_quote({
        'symbol': 'BTCUSDT', 'lastPrice': '100', 'priceChange': '1',
        'priceChangePercent': '1', 'openPrice': '99', 'highPrice': '101',
        'lowPrice': '98', 'prevClosePrice': '99', 'volume': '1000',
        'closeTime': int(generated.timestamp() * 1000),
    }, Instrument(code='BTCUSDT', name='Bitcoin', market='crypto'))
    _track_engine_action_signals(
        [{'symbol': 'crypto:BTCUSDT', 'signal': 'wait_for_pullback'}],
        generated.isoformat(), repo_root=tmp_path / 'producer', quotes={'crypto': [quote]})
    return json.loads((tmp_path / 'producer/.local/signal_tracker/signals.jsonl').read_text())


@pytest.fixture
def evaluation_window():
    return {'id': 'raw-1h-v1', 'basis': 'elapsed', 'horizon': 1, 'retry_seconds': 3600}


@pytest.fixture
def observation_factory():
    def make(symbol, target, *, clock=None, price=110):
        instant = datetime.fromisoformat(target['target_at'])
        start = int(instant.timestamp() // 60) * 60000
        if start + 59999 < instant.timestamp() * 1000:
            start += 60000
        payload = [[start, '100', '111', '99', str(price), '1', start + 59999]]
        return observe(symbol, target, clock=clock or instant + timedelta(minutes=5),
                       fetch=lambda _: payload)
    return make
