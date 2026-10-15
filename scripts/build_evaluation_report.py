"""Read-only E4 report; explicit input/output paths; no feedback side effects."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path
from collections import Counter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from stocks.engine.evaluation_metrics import build_report


def main():
    p=argparse.ArgumentParser()
    for name in ('database','signals','policy','output'): p.add_argument('--'+name,required=True)
    p.add_argument('--legacy-settlements')
    args=p.parse_args()
    inputs=[Path(v).resolve() for v in (args.database,args.signals,args.policy,args.legacy_settlements) if v]
    output=Path(args.output).resolve()
    if output in inputs: raise ValueError('output must not overwrite input')
    cfg=json.loads(Path(args.policy).read_text())
    signals=[json.loads(x) for x in Path(args.signals).read_text().splitlines() if x.strip()]
    with sqlite3.connect(Path(args.database).resolve().as_uri()+'?mode=ro',uri=True) as db:
        report=build_report(db,signals,cfg['evaluation'],cfg['min_samples'])
    if args.legacy_settlements:
        rows=[json.loads(x) for x in Path(args.legacy_settlements).read_text().splitlines() if x.strip()]
        keys=Counter((r.get('signal_id'),r.get('window')) for r in rows)
        report['legacy_diagnostic']={'rows':len(rows),'unique_signal_windows':len(keys),
          'duplicate_extra_rows':sum(n-1 for n in keys.values()),
          'comparable':False,'reason':'legacy_time_price_and_semantics_not_equivalent; never pooled'}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'results':len(report['results']),'groups':len(report['groups']),'output':str(output)}))

if __name__=='__main__': main()
