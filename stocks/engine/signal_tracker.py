"""Signal tracking and backtest loop.

Records generated signals with generation-time context, then settles them
against future prices to compute win rates. Provides performance feedback
for LLM prompt optimization.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from stocks.logging_utils import get_logger

logger = get_logger("signal_tracker")

SETTLE_WINDOWS = [
    ("24h", timedelta(hours=24)),
    ("1w", timedelta(days=7)),
]


@dataclass
class TrackedSignal:
    signal_id: str
    generated_at: datetime
    symbol: str
    direction: str
    rationale: str
    generation_price: Optional[float]
    confidence: float
    source: str
    urgency: str
    regime: dict = field(default_factory=dict)

    # Settlement
    price_24h: Optional[float] = None
    price_1w: Optional[float] = None
    correct_24h: Optional[bool] = None
    correct_1w: Optional[bool] = None
    settled_24h: bool = False
    settled_1w: bool = False

    def to_dict(self) -> dict:
        return {
            "signal_id": self.signal_id,
            "generated_at": self.generated_at.isoformat(),
            "symbol": self.symbol,
            "direction": self.direction,
            "rationale": self.rationale[:200],
            "generation_price": self.generation_price,
            "confidence": self.confidence,
            "source": self.source,
            "urgency": self.urgency,
            "regime": self.regime,
            "price_24h": self.price_24h,
            "price_1w": self.price_1w,
            "correct_24h": self.correct_24h,
            "correct_1w": self.correct_1w,
            "settled_24h": self.settled_24h,
            "settled_1w": self.settled_1w,
        }


class SignalTracker:
    """Tracks signals from generation through settlement to performance stats."""

    def __init__(self, tracker_dir: str | Path):
        self.dir = Path(tracker_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.signals_file = self.dir / "signals.jsonl"
        self.settlements_file = self.dir / "settlements.jsonl"

    def record(self, signal: TrackedSignal) -> None:
        """Write a new signal to the tracking file."""
        try:
            with open(self.signals_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(signal.to_dict(), ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning(f"SignalTracker: failed to record signal: {exc}")

    def record_batch(self, signals: list[TrackedSignal]) -> None:
        for s in signals:
            self.record(s)

    def unsettled(self, window: str = "24h") -> list[TrackedSignal]:
        """Return signals that haven't been settled for the given window."""
        field_settled = f"settled_{window}"
        results = []
        try:
            if not self.signals_file.exists():
                return results
            with open(self.signals_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not data.get(field_settled, False):
                        results.append(self._from_dict(data))
        except OSError:
            pass
        return results

    def settle(self, signal: TrackedSignal, window: str, price: float, now: Optional[datetime] = None) -> TrackedSignal:
        """Settle a signal against a current price."""
        if window == "24h":
            signal.price_24h = price
            signal.correct_24h = self._is_correct(signal.direction, signal.generation_price, price)
            signal.settled_24h = True
        elif window == "1w":
            signal.price_1w = price
            signal.correct_1w = self._is_correct(signal.direction, signal.generation_price, price)
            signal.settled_1w = True

        settlement = {
            "signal_id": signal.signal_id,
            "window": window,
            "settled_at": (now or datetime.now(timezone.utc)).isoformat(),
            "generation_price": signal.generation_price,
            "settlement_price": price,
            "direction": signal.direction,
            "correct": self._is_correct(signal.direction, signal.generation_price, price),
        }
        try:
            with open(self.settlements_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(settlement, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning(f"SignalTracker: failed to write settlement: {exc}")

        return signal

    def performance(self) -> dict:
        """Compute aggregate performance across all settled signals."""
        total = 0
        wins_24h = 0
        wins_1w = 0


        if not self.settlements_file.exists():
            return {"total": 0, "win_rate_24h": None, "win_rate_1w": None}

        try:
            with open(self.settlements_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        s = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    total += 1
                    if s.get("correct"):
                        if s["window"] == "24h":
                            wins_24h += 1
                        elif s["window"] == "1w":
                            wins_1w += 1
        except OSError:
            pass

        settled_24h = sum(1 for _ in self._iter_settlements("24h"))
        settled_1w = sum(1 for _ in self._iter_settlements("1w"))

        return {
            "total_settlements": total,
            "settled_24h": settled_24h,
            "settled_1w": settled_1w,
            "wins_24h": wins_24h,
            "wins_1w": wins_1w,
            "win_rate_24h": round(wins_24h / settled_24h, 3) if settled_24h > 0 else None,
            "win_rate_1w": round(wins_1w / settled_1w, 3) if settled_1w > 0 else None,
        }

    def performance_context(self) -> str:
        """Human-readable performance summary for LLM prompt feedback."""
        perf = self.performance()
        if perf["total_settlements"] < 5:
            return ""

        parts = [f"信号回测: {perf['total_settlements']} 次结算"]
        if perf["win_rate_24h"] is not None:
            parts.append(f"24h胜率={perf['win_rate_24h']:.0%}")
        if perf["win_rate_1w"] is not None:
            parts.append(f"1周胜率={perf['win_rate_1w']:.0%}")
        return " | ".join(parts)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _is_correct(direction: str, entry_price: Optional[float], exit_price: float) -> Optional[bool]:
        if entry_price is None or entry_price == 0:
            return None
        change = (exit_price - entry_price) / entry_price
        if direction in ("buy", "accumulate", "long"):
            return change > 0
        elif direction in ("sell", "reduce", "short"):
            return change < 0
        return None

    @staticmethod
    def _from_dict(data: dict) -> TrackedSignal:
        return TrackedSignal(
            signal_id=data.get("signal_id", ""),
            generated_at=datetime.fromisoformat(data["generated_at"]),
            symbol=data.get("symbol", ""),
            direction=data.get("direction", ""),
            rationale=data.get("rationale", ""),
            generation_price=data.get("generation_price"),
            confidence=data.get("confidence", 0.0),
            source=data.get("source", ""),
            urgency=data.get("urgency", "medium"),
            regime=data.get("regime", {}),
            price_24h=data.get("price_24h"),
            price_1w=data.get("price_1w"),
            correct_24h=data.get("correct_24h"),
            correct_1w=data.get("correct_1w"),
            settled_24h=data.get("settled_24h", False),
            settled_1w=data.get("settled_1w", False),
        )

    def _iter_settlements(self, window: str):
        if not self.settlements_file.exists():
            return
        try:
            with open(self.settlements_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        s = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if s.get("window") == window:
                        yield s
        except OSError:
            pass
