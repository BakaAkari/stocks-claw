from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / 'logs'
LOG_PATH = LOG_DIR / 'stocks.jsonl'


def log_event(event: str, **fields: Any) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        'ts': datetime.now().isoformat(timespec='seconds'),
        'event': event,
        **fields,
    }
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')
