"""Explicit manual synchronization and worker; no cron or legacy settlement use."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stocks.engine.decision_observation import schedule_decision_observation
from stocks.engine.evaluation_ledger import EvaluationLedger
from stocks.engine.evaluation_task_sync import sync_signal_events
from stocks.engine.observation_pipeline import collect_due
from stocks.engine.outcome_attribution import load_decision_snapshots


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--signals', help='New producer events; omit to consume already frozen tasks')
    source.add_argument('--recommendations', help='Frozen recommendation directory; select one with --decision-id')
    parser.add_argument('--decision-id', help='Exact recommendation decision ID')
    parser.add_argument('--windows', help='Explicit JSON market -> window definitions, required with a source')
    args = parser.parse_args()
    if bool(args.signals or args.recommendations) != bool(args.windows):
        parser.error('a source and --windows must be supplied together')
    if bool(args.recommendations) != bool(args.decision_id):
        parser.error('--recommendations and --decision-id must be supplied together')
    ledger = EvaluationLedger(args.database)
    summary = {}
    if args.signals:
        windows = json.loads(Path(args.windows).read_text())
        summary['sync'] = sync_signal_events(args.signals, ledger, windows=windows)
    if args.recommendations:
        matches = [s for s in load_decision_snapshots(snaps_dir=args.recommendations)
                   if s.get('schema_version') == 2 and s.get('decision_id') == args.decision_id]
        if len(matches) != 1:
            parser.error('recommendation missing or ambiguous')
        snapshot = matches[0]
        market = str(snapshot.get('instrument_id') or '').split(':')[0]
        windows = json.loads(Path(args.windows).read_text()).get(market, [])
        if not windows:
            parser.error('no explicit window configured for this recommendation market')
        scheduled = [schedule_decision_observation(ledger, snapshot, window) for window in windows]
        summary['sync'] = {'decision_id': args.decision_id,
                           'created': sum(created for _, created in scheduled),
                           'existing': sum(not created for _, created in scheduled)}
    summary['worker'] = collect_due(ledger)
    print(json.dumps(summary, sort_keys=True))


if __name__ == '__main__':
    main()
