#!/usr/bin/env python3
"""Run one independent, read-only IBKR disaster & sentinel alert cycle."""
from __future__ import annotations

import argparse
import fcntl
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.engine.disaster_alert_service import run_alert_cycle
from stocks.providers.ibkr_alerts import fetch_snapshot

DEFAULT_CONFIG = ROOT / "stocks/config/disaster_alerts.json"
DEFAULT_DATA_DIR = ROOT / ".local/disaster_alerts"


def _load_json(path: Path, default=None):
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _merge_rules(config: dict, sentinels_file_arg: Path | None = None) -> dict:
    rules = dict(config.get("rules") or {})
    sentinels: dict = {}
    sentinels_path = sentinels_file_arg
    if sentinels_path is None and config.get("sentinels_file"):
        raw_path = Path(str(config["sentinels_file"]))
        sentinels_path = raw_path if raw_path.is_absolute() else (ROOT / raw_path)
    if sentinels_path is not None and sentinels_path.exists():
        loaded = _load_json(sentinels_path, {})
        if isinstance(loaded, dict):
            nested = loaded.get("sentinels")
            if isinstance(nested, dict):
                sentinels.update(nested)
            else:
                sentinels.update(loaded)
    if isinstance(rules.get("sentinels"), dict):
        sentinels.update(rules["sentinels"])
    if sentinels:
        rules["sentinels"] = sentinels
    return rules


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--snapshot-file", type=Path)
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--journal-file", type=Path)
    parser.add_argument("--sentinels-file", type=Path)
    parser.add_argument("--lock-file", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = _load_json(args.config)
    state_path = args.state_file or DEFAULT_DATA_DIR / "state.json"
    journal_path = args.journal_file or DEFAULT_DATA_DIR / "runs.jsonl"
    history_path = DEFAULT_DATA_DIR / "history.json"
    lock_path = args.lock_file or state_path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        if not config.get("enabled", False):
            return 0
        try:
            if args.snapshot_file:
                snapshot = _load_json(args.snapshot_file)
            else:
                history_cache = _load_json(history_path)
                snapshot, history_cache = fetch_snapshot(
                    host=config["host"],
                    port=int(config["port"]),
                    client_id=int(config["client_id"]),
                    expected_account=config["expected_account"],
                    history_cache=history_cache,
                    timeout=int(config.get("timeout_seconds", 25)),
                    history_max_age_seconds=int(config.get("history_max_age_seconds", 72000)),
                    market_data_type=int(config.get("market_data_type", 3)),
                )
                _write_json(history_path, history_cache)
                if snapshot.get("errors"):
                    snapshot["system_errors"] = snapshot["errors"]
        except Exception as exc:
            snapshot = {
                "positions": [],
                "net_liquidation": None,
                "data_as_of": datetime.now(timezone.utc).isoformat(),
                "system_errors": [{"message": f"{type(exc).__name__}: {exc}"}],
            }
        output = run_alert_cycle(
            snapshot,
            state_path,
            journal_path,
            config=_merge_rules(config, args.sentinels_file),
        )
        if output:
            print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
