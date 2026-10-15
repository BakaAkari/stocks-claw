"""Export frozen evaluation results without reading mutable signal inputs."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stocks.engine.evaluation_metrics import build_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    database, output = Path(args.database).resolve(), Path(args.output).resolve()
    if output == database or (output.exists() and database.exists() and output.samefile(database)):
        raise ValueError('output must not overwrite database')
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        report = build_report(db)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'results': len(report['results']), 'tasks': len(report['tasks']), 'output': str(output)}))


if __name__ == '__main__':
    main()
