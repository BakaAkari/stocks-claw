"""Historical replay through the production signal and observation pipeline.

This command uses public Binance prices and actual rule code. Its event is an
explicit historical replay, not a claim that production emitted a past signal.
All artifacts go to the required, initially empty --output-dir.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stocks.domain.models import Instrument
from stocks.engine.action_signals import compute_action_signals
from stocks.engine.exchange_targets import target_after
from stocks.engine.history_provider import BinanceKLineProvider
from stocks.engine.rotation import compute_rotation
from stocks.engine.target_observation import observe

REPLAYABLE_ACTIONS = frozenset(
    {
        "accumulate_candidate",
        "rotation_candidate",
        "left_bottom_candidate",
        "wait_for_pullback",
        "reduce_risk",
    }
)


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _prepare_output(path: Path) -> Path:
    output = path.expanduser().resolve()
    repository = Path(__file__).resolve().parents[1]
    for private in (repository / ".local", repository / ".secret"):
        if output == private or private in output.parents:
            raise ValueError("smoke output must be outside production private directories")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("--output-dir must be an empty or new directory")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _select_candidate(frame, instrument, *, now, replay_days, hours):
    """Choose the latest eligible real rule output without examining exit prices."""
    symbol = f"{instrument.market}:{instrument.code}"
    attempts = []
    ordered = frame.sort_values("timestamp").reset_index(drop=True)
    for offset in range(min(replay_days, len(ordered))):
        prefix = ordered.iloc[: len(ordered) - offset].copy()
        cutoff = prefix.iloc[-1]["timestamp"].to_pydatetime()
        if cutoff.tzinfo is None:
            raise ValueError("history provider returned a timestamp without timezone")
        cutoff = cutoff.astimezone(timezone.utc)
        # Allow the target minute bar to finish; never advance a simulated clock
        # into the future to make an incomplete live observation look settled.
        if cutoff + timedelta(hours=hours, minutes=1) > now:
            continue
        frames = {symbol: prefix}
        instruments = {symbol: instrument}
        rotation = compute_rotation(frames, instruments)
        actions = compute_action_signals(frames, instruments, rotation)
        items = actions.get("items", [])
        attempts.append(
            {"cutoff": cutoff.isoformat(), "actions": [row["signal"] for row in items]}
        )
        for candidate in items:
            if candidate.get("signal") in REPLAYABLE_ACTIONS:
                return candidate, cutoff, prefix, rotation, actions, attempts
    raise RuntimeError(
        "No eligible historical rule candidate; no signal was fabricated. "
        + json.dumps(attempts, ensure_ascii=False)
    )


async def _replay_inputs(args, output):
    instrument = Instrument(
        code=args.symbol, name=args.symbol, market="crypto", category="crypto"
    )
    history = await BinanceKLineProvider().fetch(instrument, lookback_days=args.lookback_days)
    if history.empty:
        raise RuntimeError("Binance history unavailable; cannot run a real-price smoke")
    now = datetime.now(timezone.utc)
    candidate, cutoff, prefix, rotation, actions, attempts = _select_candidate(
        history,
        instrument,
        now=now,
        replay_days=args.replay_days,
        hours=args.hours,
    )
    prefix.to_json(output / "history-input.json", orient="records", date_format="iso")
    _write_json(output / "rotation.json", rotation)
    _write_json(output / "action-signals.json", actions)
    _write_json(output / "candidate-search.json", attempts)
    # Use the same target descriptor and historical provider as the worker for
    # the entry observation, retaining its actual source and bar timestamps.
    entry_target = target_after(cutoff - timedelta(hours=1), "crypto", 1, basis="elapsed")
    entry = observe(f"crypto:{args.symbol}", entry_target, clock=now)
    _write_json(output / "entry-observation.json", entry)
    if entry.get("status") != "observed":
        raise RuntimeError(f"entry observation unavailable: {entry.get('reason')}")
    if datetime.fromisoformat(entry["observed_at"]) != cutoff:
        raise RuntimeError("entry bar does not match the historical rule cutoff")
    return candidate, cutoff, entry, now


def _read_report(database):
    from stocks.engine.evaluation_metrics import build_report

    before = hashlib.sha256(database.read_bytes()).hexdigest()
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        report = build_report(db)
    if hashlib.sha256(database.read_bytes()).hexdigest() != before:
        raise RuntimeError("report generation changed the database")
    return report


def _run_frozen_pipeline(args, output, candidate, cutoff, entry, replay_created_at):
    from stocks.engine.entry_snapshot import entry_quote_from_observation
    from stocks.engine.evaluation_ledger import EvaluationLedger
    from stocks.engine.evaluation_task_sync import sync_signal_events
    from stocks.engine.observation_pipeline import collect_due
    from stocks.engine.scheduled_analysis import _track_engine_action_signals

    repository = Path(__file__).resolve().parents[1]
    source_files = (
        "stocks/engine/action_signals.py",
        "stocks/engine/indicators.py",
        "stocks/engine/rotation.py",
        "stocks/engine/scheduled_analysis.py",
        "stocks/engine/entry_snapshot.py",
        "stocks/engine/target_observation.py",
        "stocks/config/engine.yaml",
    )
    evidence_files = ("history-input.json", "rotation.json", "action-signals.json")
    provenance = {
        "origin": "historical_replay",
        "decision_as_of": cutoff.isoformat(),
        "replay_created_at": replay_created_at.isoformat(),
        "code_files_sha256": {
            name: hashlib.sha256((repository / name).read_bytes()).hexdigest()
            for name in source_files
        },
        "input_files_sha256": {
            name: hashlib.sha256((output / name).read_bytes()).hexdigest()
            for name in evidence_files
        },
    }
    quote = entry_quote_from_observation(entry, replay_as_of=cutoff)
    _track_engine_action_signals(
        [candidate],
        cutoff.isoformat(),
        repo_root=output,
        quotes={candidate["symbol"]: quote},
        provenance=provenance,
    )
    signals = output / ".local" / "signal_tracker" / "signals.jsonl"
    if not signals.exists():
        raise RuntimeError("production signal recorder did not emit the replay event")
    emitted = [json.loads(line) for line in signals.read_text().splitlines() if line.strip()]
    if len(emitted) != 1:
        raise RuntimeError(f"expected exactly one producer event, got {len(emitted)}")
    event = emitted[0]
    if event.get("action_semantics") != candidate["signal"]:
        raise RuntimeError("production recorder did not preserve the original action semantics")
    if event.get("provenance") != provenance:
        raise RuntimeError("production recorder did not preserve the replay provenance")
    windows = {
        "crypto": [
            {
                "id": f"smoke-raw-price-{args.hours}h-v1",
                "basis": "elapsed",
                "horizon": args.hours,
                "retry_seconds": 3600,
            }
        ]
    }
    _write_json(output / "windows.json", windows)
    database = output / "evaluation.sqlite3"
    ledger = EvaluationLedger(database)
    first_sync = sync_signal_events(signals, ledger, windows=windows)
    if len(ledger.keys()) != 1:
        raise RuntimeError("sync did not create exactly one frozen task")
    first_worker = collect_due(ledger)
    first_report = _read_report(database)
    _write_json(output / "report.json", first_report)
    if len(first_report.get("results", [])) != 1:
        raise RuntimeError("worker did not persist exactly one terminal evaluation result")
    result = first_report["results"][0]
    if result.get("status") != "evaluated":
        raise RuntimeError(f"historical observation could not be evaluated: {result.get('reason')}")
    expected_return = result["exit"]["price"] / result["entry"]["price"] - 1
    if not math.isclose(result["price_return"], expected_return, rel_tol=1e-12, abs_tol=1e-12):
        raise RuntimeError("result is not the raw quoted-price change")
    if result.get("scoring_status") != "observation_only" or result.get("outcome") is not None:
        raise RuntimeError("price observation was incorrectly promoted into strategy scoring")
    second_sync = sync_signal_events(signals, ledger, windows=windows)
    second_worker = collect_due(ledger)
    second_report = _read_report(database)
    if first_report != second_report or len(ledger.keys()) != 1:
        raise RuntimeError("repeating sync and worker changed the frozen report")
    summary = {
        "status": "ok",
        "scope": "historical_replay_of_production_rule_code_with_real_public_prices",
        "production_signal_emitted_at_historical_time": False,
        "source_module": "stocks.engine.action_signals.compute_action_signals",
        "symbol": candidate["symbol"],
        "action_semantics": candidate["signal"],
        "provenance": provenance,
        "first_sync": first_sync,
        "first_worker": first_worker,
        "second_sync": second_sync,
        "second_worker": second_worker,
        "idempotent": True,
        "result_count": len(first_report["results"]),
        "result_id": result["result_id"],
        "price_return": result["price_return"],
        "currency": result["currency"],
        "output_dir": str(output),
    }
    _write_json(output / "smoke-result.json", summary)
    print(json.dumps(summary, ensure_ascii=False, allow_nan=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--lookback-days", type=int, default=100)
    parser.add_argument("--replay-days", type=int, default=20)
    parser.add_argument("--hours", type=int, default=1)
    args = parser.parse_args()
    if not args.symbol.isalnum() or args.symbol != args.symbol.upper():
        parser.error("--symbol must be an uppercase Binance symbol such as BTCUSDT")
    if not 60 <= args.lookback_days <= 500:
        parser.error("--lookback-days must be between 60 and 500")
    if not 1 <= args.replay_days <= args.lookback_days - 30:
        parser.error("--replay-days must leave at least 30 bars for the rule engine")
    if args.hours < 1:
        parser.error("--hours must be positive")
    output = _prepare_output(args.output_dir)
    candidate, cutoff, entry, replay_created_at = asyncio.run(_replay_inputs(args, output))
    _run_frozen_pipeline(args, output, candidate, cutoff, entry, replay_created_at)


if __name__ == "__main__":
    main()
