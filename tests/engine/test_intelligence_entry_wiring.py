"""Exercise real collector/parser outputs through the production event writer."""
import io
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from stocks.engine.intelligence_analyzer import IntelligenceAnalyzer, LLMIntelligenceAnalyzer
from stocks.engine.intelligence_harvester import IntelligenceHarvester
from stocks.engine.scheduled_analysis import _track_intelligence_signals
from stocks.errors import ProviderDataError

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)


def _rows(root):
    return [json.loads(line) for line in (root / '.local/signal_tracker/signals.jsonl').read_text().splitlines()]


def _record(root, signals, quotes, mode='llm'):
    _track_intelligence_signals(signals, NOW, repo_root=root, quotes=quotes,
                                metadata={'analysis_mode': mode})


def test_real_rule_producer_resolves_instrument_from_quote(tmp_path):
    quote = {'instrument': {'market': 'us', 'code': 'GLD'}, 'price': 10,
             'pct_change': 2, 'source': 'fixture', 'as_of': NOW.isoformat(), 'stale': False}
    signals = IntelligenceAnalyzer()._generate_signals(
        [], SimpleNamespace(macro={}, quotes={'GLD': quote}))
    assert signals[0].symbol == 'GLD'  # Actual upstream contract, not a prefixed fixture.
    _record(tmp_path, signals, {'GLD': quote}, 'rule_fallback')
    row = _rows(tmp_path)[0]
    assert row['symbol'] == 'us:GLD'
    assert row['action_semantics'] == 'buy'
    assert row['signal_type'] == 'intelligence_signal'
    assert row['forecast_horizon'] == signals[0].horizon
    assert row['strategy_id'] == 'intelligence:rule_fallback'
    assert row['strategy_version'] == 'unknown'
    assert row['schema_version'] == 1
    assert row['scoring_status'] == 'observation_only'
    assert row['entry_snapshot']['scoring_status'] == 'scorable'


@pytest.mark.parametrize('market,code,missing', [
    ('a', '518880', False), ('us', 'NEM', False), ('crypto', 'BTCUSDT', False),
    ('us', 'NEM', True),
])
def test_real_llm_parser_records_canonical_and_unavailable_entries(tmp_path, market, code, missing):
    signals = LLMIntelligenceAnalyzer.__new__(LLMIntelligenceAnalyzer)._parse_signals({
        'signals': [{'symbol': f'{market}:{code}', 'direction': 'watch', 'horizon': 'long_term'}]})
    quote = {'instrument': {'market': market, 'code': code}, 'price': 10,
             'source': 'fixture', 'as_of': NOW.isoformat(), 'stale': False}
    _record(tmp_path, signals, {} if missing else {code: quote})
    row = _rows(tmp_path)[0]
    assert row['action_semantics'] == 'watch'
    assert row['forecast_horizon'] == 'long_term'
    assert row['scoring_status'] == ('unscorable' if missing else 'observation_only')
    assert row['generation_price'] == (None if missing else 10)
    if missing:
        assert row['scoring_reason'] == 'missing_quote'
    else:
        assert row['entry_snapshot']['as_of'] == quote['as_of']


async def test_real_intelligence_btc_collector_to_event(tmp_path):
    payload = {'symbol': 'BTCUSDT', 'lastPrice': '60000', 'priceChange': '100',
               'priceChangePercent': '1', 'openPrice': '59000', 'highPrice': '61000',
               'lowPrice': '58000', 'prevClosePrice': '59900', 'volume': '123',
               'closeTime': 1783042009009}
    with patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(payload).encode())):
        quote = await IntelligenceHarvester.__new__(IntelligenceHarvester)._fetch_binance_btc()
    signals = LLMIntelligenceAnalyzer.__new__(LLMIntelligenceAnalyzer)._parse_signals({
        'signals': [{'symbol': 'BTCUSDT', 'direction': 'buy'}]})
    _record(tmp_path, signals, {'BTCUSDT': quote})
    row = _rows(tmp_path)[0]
    assert row['symbol'] == 'crypto:BTCUSDT'
    assert row['scoring_status'] == 'observation_only'
    entry = row['entry_snapshot']
    assert entry['as_of'] == '2026-07-03T01:26:49.009000+00:00'
    assert entry['stale'] is False
    assert entry['currency'] == 'USDT'
    assert entry['adjustment'] == 'not_applicable'
    assert entry['price_type'] == 'last_trade'
    assert entry['price_basis'] == 'raw_quoted_price'


async def test_collector_never_invents_missing_source_time():
    with patch('urllib.request.urlopen', return_value=io.BytesIO(b'{"lastPrice":"60000"}')):
        with pytest.raises(ProviderDataError):
            await IntelligenceHarvester.__new__(IntelligenceHarvester)._fetch_binance_btc()
