"""Strict generation-time identity and quote provenance. No provider guessing."""
from datetime import datetime
import math

MARKETS = frozenset({'a', 'us', 'crypto', 'hk'})


def select_entry(symbol, quotes, market=None):
    """Select only by explicit instrument identity, never a bare dict key."""
    try:
        identity = canonical_identity(symbol, market)
    except ValueError:
        return build_entry_snapshot(symbol, market=market)
    matches = []
    for value in (quotes or {}).values():
        for quote in (value if isinstance(value, list) else [value]):
            q = quote.to_dict() if hasattr(quote, 'to_dict') else quote
            if not isinstance(q, dict):
                continue
            inst = q.get('instrument') or {}
            try:
                if canonical_identity(inst.get('code'), inst.get('market')) == identity:
                    matches.append(q)
            except ValueError:
                continue
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

    price_type is the schema field Quote.price, not a claim of last/close/NAV.
    A false stale flag is provider evidence only, not independent validation.
    """
    q = quote.to_dict() if hasattr(quote, 'to_dict') else (quote or {})
    result = {'instrument_id': None, 'price': q.get('price'),
              'source': q.get('source'), 'as_of': q.get('as_of'),
              'price_type': 'quote.price', 'stale': q.get('stale'),
              'scoring_status': 'unscorable', 'reason': None}
    try:
        identity = canonical_identity(symbol, market)
        result['instrument_id'] = identity
        inst = q.get('instrument') or {}
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
