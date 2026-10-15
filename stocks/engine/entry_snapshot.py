"""Strict generation-time identity and quote provenance. No provider guessing."""
import math
from copy import deepcopy
from datetime import datetime

MARKETS = frozenset({'a', 'us', 'crypto', 'hk'})


def entry_quote_from_observation(observation, *, replay_as_of):
    """Adapt historical provider evidence at an explicit replay decision cutoff.

    The freshness statement is relative to that cutoff, never to wall-clock
    time. This adapter does not recreate a signal that existed in production.
    """
    if observation.get('status') != 'observed':
        raise ValueError('entry observation must be observed')
    from stocks.engine.target_observation import validate_observation

    identity = canonical_identity(observation.get('symbol'))
    reason = validate_observation(identity, observation.get('target_definition'), observation)
    if reason:
        raise ValueError('invalid entry observation: ' + reason)
    observed = datetime.fromisoformat(str(observation.get('observed_at')).replace('Z', '+00:00'))
    cutoff = datetime.fromisoformat(str(replay_as_of).replace('Z', '+00:00'))
    if observed.tzinfo is None or cutoff.tzinfo is None or observed != cutoff:
        raise ValueError('replay cutoff must equal aware observed_at')
    market, code = identity.split(':', 1)
    return {
        'instrument': {'market': market, 'code': code},
        'price': observation.get('price'),
        'source': observation.get('source'),
        'as_of': observation.get('source_as_of') or observation['observed_at'],
        'source_as_of': observation.get('source_as_of'),
        'observed_at': observation['observed_at'],
        'stale': False,
        'freshness_context': 'historical_replay',
        'currency': observation.get('currency'),
        'adjustment': observation.get('adjustment'),
        'price_basis': observation.get('price_basis'),
        'price_type': observation.get('price_type'),
        'source_payload_sha256': observation.get('source_payload_sha256'),
        'entry_observation': deepcopy(observation),
    }


def select_entry(symbol, quotes, market=None):
    """Resolve a bare code only from unique, explicit quote instruments.

    Dictionary keys and ticker spelling never establish a market. Duplicate
    observations remain ambiguous even when they name the same instrument.
    """
    observations = []
    for value in (quotes or {}).values():
        for quote in (value if isinstance(value, list) else [value]):
            q = quote.to_dict() if hasattr(quote, 'to_dict') else quote
            if not isinstance(q, dict) or not isinstance(q.get('instrument'), dict):
                continue
            inst = q['instrument']
            try:
                quote_identity = canonical_identity(inst.get('code'), inst.get('market'))
            except ValueError:
                continue
            observations.append((quote_identity, q))
    try:
        identity = canonical_identity(symbol, market)
    except ValueError:
        result = build_entry_snapshot(symbol, market=market)
        if (market is not None or not isinstance(symbol, str) or ':' in symbol
                or not symbol or symbol != symbol.strip()):
            return result
        identities = {key for key, q in observations if q['instrument']['code'] == symbol}
        if len(identities) != 1:
            if identities:
                result['reason'] = 'ambiguous_identity'
            return result
        identity = identities.pop()
    matches = [q for key, q in observations if key == identity]
    result = build_entry_snapshot(identity, matches[0] if len(matches) == 1 else None)
    if len(matches) != 1:
        result['reason'] = 'ambiguous_quotes' if matches else 'missing_quote'
    return result


def canonical_identity(symbol, market=None):
    if not isinstance(symbol, str) or not symbol or symbol != symbol.strip():
        raise ValueError('invalid_symbol')
    if ':' in symbol:
        parts = symbol.split(':')
        if len(parts) != 2 or not parts[1]:
            raise ValueError('invalid_symbol')
        prefix, code = parts
        if market is not None and market != prefix:
            raise ValueError('market_mismatch')
        market = prefix
    else:
        code = symbol
    if market not in MARKETS or any(c.isspace() for c in code):
        raise ValueError('unknown_identity')
    return f'{market}:{code}'


def build_entry_snapshot(symbol, quote=None, market=None):
    """Preserve supplied values; reject missing lineage rather than inventing it.

    A false stale flag is provider evidence only, not independent validation.
    Currency, adjustment and price type are never inferred from the ticker.
    """
    q = quote.to_dict() if hasattr(quote, 'to_dict') else (quote or {})
    result = {'instrument_id': None, 'price': q.get('price'),
              'source': q.get('source'), 'as_of': q.get('as_of'),
              'price_type': q.get('price_type') or 'unknown', 'stale': q.get('stale'),
              'currency': q.get('currency'), 'adjustment': q.get('adjustment'),
              'price_basis': q.get('price_basis'),
              'source_as_of': q.get('source_as_of'),
              'observed_at': q.get('observed_at'),
              'freshness_context': q.get('freshness_context'),
              'source_payload_sha256': q.get('source_payload_sha256'),
              'entry_observation': deepcopy(q.get('entry_observation')),
              'scoring_status': 'unscorable', 'reason': None}
    try:
        identity = canonical_identity(symbol, market)
        result['instrument_id'] = identity
        inst = q.get('instrument') if isinstance(q.get('instrument'), dict) else {}
        quote_id = canonical_identity(inst.get('code'), inst.get('market'))
        if identity != quote_id:
            raise ValueError('quote_identity_mismatch')
    except ValueError as exc:
        result['reason'] = str(exc)
        return result
    price = q.get('price')
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        result['reason'] = 'missing_or_invalid_price'
    elif not isinstance(q.get('source'), str) or not q['source'].strip():
        result['reason'] = 'missing_source'
    else:
        try:
            ts = datetime.fromisoformat(str(q.get('as_of')).replace('Z', '+00:00'))
            if ts.tzinfo is None or ts.utcoffset() is None:
                raise ValueError('timezone_required')
        except (TypeError, ValueError):
            result['reason'] = 'missing_or_invalid_as_of'
        else:
            if q.get('stale') is not False:
                result['reason'] = 'stale_or_unknown_freshness'
            else:
                result['scoring_status'] = 'scorable'
    return result
