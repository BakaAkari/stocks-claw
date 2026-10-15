"""E4 reproducible results from unique observations; no strategy feedback."""
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime

VERSION = 'directional-price-v1'


def number(x):
    return not isinstance(x, bool) and isinstance(x, (int, float)) and math.isfinite(x)


def evaluate(signal, observation, policy):
    result = dict(evaluator_version=VERSION, status='unavailable', reason=None,
                  gross_return=None, net_return=None, outcome=None,
                  neutral_band=policy['neutral_band'], entry=signal.get('entry_snapshot'),
                  exit=observation, signal=signal, policy=policy)
    def reject(reason):
        return dict(result, reason=reason)
    band=policy['neutral_band']
    if not number(band) or band<0: raise ValueError('invalid neutral band')
    if signal.get('signal_type')!='short_term_directional': return reject('unsupported_signal_semantics')
    if signal.get('direction') not in ('buy','sell'): return reject('unsupported_direction')
    entry=signal.get('entry_snapshot') or {}
    if signal.get('scoring_status')!='scorable' or entry.get('scoring_status')!='scorable': return reject('unscorable_entry')
    if observation.get('status')!='observed': return reject(observation.get('reason') or observation.get('status') or 'missing_observation')
    if entry.get('instrument_id') != signal.get('symbol'):
        return reject('entry_symbol_mismatch')
    if observation.get('symbol')!=signal.get('symbol'): return reject('symbol_mismatch')
    if entry.get('adjustment') is None or entry.get('adjustment')!=observation.get('adjustment'): return reject('price_basis_unverified')
    if not entry.get('currency') or entry.get('currency')!=observation.get('currency'): return reject('currency_unverified')
    if signal.get('corporate_actions_checked') is not True: return reject('corporate_actions_unverified')
    for obj in (entry,observation):
        if not number(obj.get('price')) or obj['price']<=0: return reject('invalid_price')
        if not obj.get('source'): return reject('missing_source')
    try:
        start=datetime.fromisoformat(entry['as_of'].replace('Z','+00:00'))
        end=datetime.fromisoformat(observation['observed_at'].replace('Z','+00:00'))
        if start.tzinfo is None or end.tzinfo is None or end<=start: return reject('invalid_price_times')
    except (KeyError,ValueError,TypeError): return reject('invalid_price_times')
    gross=(observation['price']/entry['price']-1)*(1 if signal['direction']=='buy' else -1)
    costs=policy.get('round_trip_cost_rate')
    if costs is not None and (not number(costs) or costs<0): raise ValueError('invalid costs')
    net=None if costs is None else gross-costs
    value=net if net is not None else gross
    return dict(result,status='evaluated',gross_return=gross,net_return=net,
                outcome='neutral' if abs(value)<=band else ('win' if value>0 else 'loss'),
                outcome_basis='net' if net is not None else 'gross',
                net_unavailable_reason='costs_not_provided' if net is None else None,
                return_semantics='directional_price_return_not_executed_trade_pnl')


def summarize(results, min_samples):
    if isinstance(min_samples,bool) or not isinstance(min_samples,int) or min_samples<1: raise ValueError('invalid min_samples')
    groups=defaultdict(list)
    for row in results:
        s=row['signal']
        key=tuple(str(s.get(k,'unknown')) for k in ('strategy','strategy_version','source','symbol','market_regime'))+(row['policy_id'],)
        groups[key].append(row)
    output=[]
    for key,rows in sorted(groups.items()):
        valid=[r for r in rows if r['status']=='evaluated']
        wins=sum(r['outcome']=='win' for r in valid);losses=sum(r['outcome']=='loss' for r in valid)
        n=len(valid); sufficient=n>=min_samples
        gross=[r['gross_return'] for r in valid];net=[r['net_return'] for r in valid if r['net_return'] is not None]
        output.append(dict(group=list(key),total=len(rows),valid=n,unavailable=len(rows)-n,
            unavailable_reasons=dict(Counter(r['reason'] for r in rows if r['status']!='evaluated')),
            wins=wins,losses=losses,neutral=n-wins-losses,
            win_rate=wins/(wins+losses) if wins+losses>=min_samples else None,
            neutral_rate=(n-wins-losses)/n if n else None,
            mean_gross_return=sum(gross)/n if n else None,
            mean_net_return=sum(net)/len(net) if net else None,net_sample_count=len(net),
            expectancy=sum(net)/n if sufficient and len(net)==n else None,
            max_drawdown=None,max_drawdown_reason='no_portfolio_equity_path_or_intrawindow_prices',
            verdict='descriptive_only' if sufficient else 'insufficient_evidence'))
    return output


def build_report(db, signals, policy, min_samples):
    """Read-only source join; conflicting signal IDs never select first/last."""
    index={};conflicts=set()
    for s in signals:
        sid=s['signal_id']
        if sid in index and index[sid]!=s: conflicts.add(sid)
        index[sid]=s
    results=[]
    for sid,pid,target,state,payload in db.execute('SELECT signal_id,policy_id,target_at,state,result FROM tasks ORDER BY signal_id,policy_id,target_at'):
        if not pid.startswith('observation-v1:'): continue
        signal=index.get(sid,{'signal_id':sid})
        obs=json.loads(payload) if payload else {'status':state,'reason':'observation_pending'}
        result=evaluate(signal,obs,policy)
        if sid in conflicts or sid not in index:
            result.update(status='unavailable',reason='conflicting_signal_id' if sid in conflicts else 'missing_signal',gross_return=None,net_return=None,outcome=None)
        if obs.get('target_at')!=target:
            result.update(status='unavailable',reason='target_mismatch',gross_return=None,net_return=None,outcome=None)
        result.update(task_key=[sid,pid,target],policy_id=pid)
        result['result_id']=hashlib.sha256(json.dumps([result['task_key'],VERSION,policy,signal,obs],sort_keys=True,allow_nan=False).encode()).hexdigest()
        results.append(result)
    return dict(schema_version=1,evaluator_version=VERSION,feedback_enabled=False,
                results=results,groups=summarize(results,min_samples))
