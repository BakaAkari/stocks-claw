"""Manual E3 observation worker. Explicit paths; never uses legacy settlements."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from stocks.engine.evaluation_ledger import EvaluationLedger
from stocks.engine.observation_pipeline import collect_observation


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--signals',required=True)
    parser.add_argument('--database',required=True)
    parser.add_argument('--windows',required=True,help='JSON market -> window definitions')
    args=parser.parse_args()
    windows=json.loads(Path(args.windows).read_text())
    ledger=EvaluationLedger(args.database)
    counts={}
    for line in Path(args.signals).read_text().splitlines():
        if not line.strip():continue
        signal=json.loads(line)
        market=signal['symbol'].split(':',1)[0]
        for window in windows.get(market,[]):
            status=collect_observation(ledger,signal,window)
            counts[status]=counts.get(status,0)+1
    print(json.dumps(counts,sort_keys=True))

if __name__=='__main__':main()
