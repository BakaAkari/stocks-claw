"""Exercise manual worker/report commands against a producer-created frozen task."""

import hashlib
import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from stocks.engine.evaluation_contract import utc
from stocks.engine.evaluation_ledger import EvaluationLedger
from stocks.engine.evaluation_task_sync import schedule_signal
from stocks.engine.observation_pipeline import collect_observation

ROOT = Path(__file__).resolve().parents[2]


def test_worker_and_readonly_report_cli_need_no_external_signals(tmp_path, evaluation_signal, evaluation_window, observation_factory):
    ledger = EvaluationLedger(tmp_path / 'ledger.db')
    key, _ = schedule_signal(ledger, evaluation_signal, evaluation_window)
    collect_observation(ledger, key, now=utc(key[2]) + timedelta(minutes=5), provider=observation_factory)
    worker = subprocess.run([sys.executable, str(ROOT / 'scripts/collect_target_observations.py'),
                             '--database', ledger.path], check=True, capture_output=True, text=True)
    assert json.loads(worker.stdout)['worker'] == {'not_claimed': 1}
    before = hashlib.sha256(Path(ledger.path).read_bytes()).hexdigest()
    output = tmp_path / 'report.json'
    subprocess.run([sys.executable, str(ROOT / 'scripts/build_evaluation_report.py'),
                    '--database', ledger.path, '--output', str(output)], check=True, capture_output=True)
    report = json.loads(output.read_text())
    assert len(report['results']) == 1
    assert report['results'][0]['signal'] == evaluation_signal
    assert hashlib.sha256(Path(ledger.path).read_bytes()).hexdigest() == before
