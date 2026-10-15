"""E3 observation collection into E2's atomic store; no return/win scoring."""
from datetime import datetime, timezone
from stocks.engine.exchange_targets import target_after
from stocks.engine.target_observation import observe
from stocks.engine.evaluation_runner import run_evaluation


def collect_observation(ledger, signal, window, *, now=None, provider=observe):
    now = now or datetime.now(timezone.utc)
    if signal.get('scoring_status') != 'scorable' or signal.get('entry_snapshot',{}).get('scoring_status') != 'scorable':
        return 'unscorable'
    market = signal['symbol'].split(':',1)[0]
    generated = datetime.fromisoformat(signal['generated_at'])
    target = target_after(generated,market,window['horizon'],basis=window['basis'])
    policy = 'observation-v1:'+window['id']
    def evaluate():
        obs = provider(signal['symbol'],target,clock=now)
        obs['target_definition']=target
        if obs['status']=='observed': return obs
        deadline=datetime.fromisoformat(target['target_at']).timestamp()+window['retry_seconds']
        if now.timestamp()>=deadline:
            return dict(obs,status='expired',expiry_reason='observation_retry_deadline')
        return None
    return run_evaluation(ledger,signal['signal_id'],policy,target['target_at'],evaluate,
                          clock=lambda:now.timestamp())
