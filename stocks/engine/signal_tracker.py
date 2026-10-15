"""Append-only producer events and read compatibility for legacy signal ledgers.

Versioned events retain identity, semantics and entry evidence. The old
settlement helpers remain diagnostic-only and cannot mutate versioned events.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
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


def signal_event_id(
    source: str, generated_at: datetime, symbol: str, semantics: str, *, origin="production",
) -> str:
    """A stable identity for a producer event, independent of its observed price."""
    if generated_at.tzinfo is None:
        raise ValueError("generated_at timezone required")
    key = [origin, source, generated_at.astimezone(timezone.utc).isoformat(), symbol, semantics]
    digest = hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()
    return f"signal-v1:{digest}"


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

    # Missing on legacy rows: never reconstruct provenance from today's quotes.
    entry_snapshot: Optional[dict] = None
    scoring_status: str = "unscorable"
    scoring_reason: Optional[str] = "legacy_missing_snapshot"
    schema_version: int = 1
    signal_type: str = "unknown"
    action_semantics: str = "unknown"
    strategy_id: str = "unknown"
    strategy_version: str = "unknown"
    forecast_horizon: Optional[str] = None
    provenance: dict = field(default_factory=dict)

    # Settlement
    price_24h: Optional[float] = None
    price_1w: Optional[float] = None
    correct_24h: Optional[bool] = None
    correct_1w: Optional[bool] = None
    settled_24h: bool = False
    settled_1w: bool = False

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "signal_id": self.signal_id,
            "signal_type": self.signal_type,
            "action_semantics": self.action_semantics,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "forecast_horizon": self.forecast_horizon,
            "provenance": self.provenance,
            "generated_at": self.generated_at.isoformat(),
            "symbol": self.symbol,
            "direction": self.direction,
            "rationale": self.rationale if self.schema_version >= 1 else self.rationale[:200],
            "generation_price": self.generation_price,
            "confidence": self.confidence,
            "source": self.source,
            "urgency": self.urgency,
            "regime": self.regime,
            "entry_snapshot": self.entry_snapshot,
            "scoring_status": self.scoring_status,
            "scoring_reason": self.scoring_reason,
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

    # Used only by the disabled legacy settlement implementation.
    MIN_PRICE_CHANGE = 0.003

    def record(self, signal: TrackedSignal) -> bool:
        """Append an immutable event once; reject conflicting reuse of its ID.

        Repeated judgments are retained. Statistical independence belongs in
        metrics, not in destructive symbol/direction filtering at ingestion.
        """
        payload = signal.to_dict()
        if not signal.signal_id:
            raise ValueError("signal_id required")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        # The lock covers both lookup and append, including other processes.
        with open(self.signals_file, "a+", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            stream.seek(0)
            for line in stream:
                try:
                    existing = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(existing, dict) or existing.get("signal_id") != signal.signal_id:
                    continue
                if json.dumps(existing, ensure_ascii=False, sort_keys=True) != encoded:
                    raise ValueError(f"conflicting signal_id: {signal.signal_id}")
                return False
            stream.seek(0, os.SEEK_END)
            stream.write(encoded + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return True

    def record_batch(self, signals: list[TrackedSignal]) -> int:
        return sum(self.record(signal) for signal in signals)

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
                    if (not data.get(field_settled, False)
                            and data.get("scoring_status") == "scorable"
                            and (data.get("entry_snapshot") or {}).get("scoring_status") == "scorable"):
                        results.append(self._from_dict(data))
        except OSError:
            pass
        return results

    # 各窗口的最大有效结算延迟(小时)。超出说明是补跑/补结算, exit 价已不是
    # 信号窗口内的价格, 判定无意义 -> 标记 stale_window, 不进胜率样本。
    # 24h: 窗口24h + cron 6h粒度×4 + 余量 = 48h 内有效; 1w: 7d + 余量 = 10d 内有效。
    MAX_SETTLE_LAG_HOURS = {"24h": 48, "1w": 10 * 24}

    def settle(self, signal: TrackedSignal, window: str, price: float, now: Optional[datetime] = None) -> TrackedSignal:
        """Settle a signal against a current price.

        精度修复(2026-08-14): 只有满足 ①entry 有价 ②exit 有价 ③|涨跌幅|>=MIN_PRICE_CHANGE
        ④结算延迟未超窗(并非补跑) 的结算才算"有效方向判定"并进入胜率样本。无效记录
        写 invalid_reason + correct=None, 保留审计但不污染胜率分母。
        """
        if signal.schema_version >= 1:
            raise ValueError("versioned signal events require the evaluation ledger")
        invalid_reason = None
        correct = self._is_correct(signal.direction, signal.generation_price, price)
        # 结算延迟(now - generated_at), 超窗则判定无效(补跑价无窗口意义)
        if now is not None and signal.generated_at is not None:
            lag_h = (now - signal.generated_at).total_seconds() / 3600.0
            if lag_h > self.MAX_SETTLE_LAG_HOURS.get(window, 48):
                invalid_reason = "stale_window"
        if signal.generation_price is None or signal.generation_price == 0:
            invalid_reason = "missing_entry_price"
        elif price is None or price == 0:
            invalid_reason = "missing_exit_price"
        elif invalid_reason is None:
            change = abs(price / signal.generation_price - 1)
            if change < self.MIN_PRICE_CHANGE:
                invalid_reason = "below_min_change"  # 波动太小, 方向判定接近噪声

        if window == "24h":
            signal.price_24h = price
            signal.correct_24h = correct
            signal.settled_24h = True
        elif window == "1w":
            signal.price_1w = price
            signal.correct_1w = correct
            signal.settled_1w = True

        settlement = {
            "signal_id": signal.signal_id,
            "window": window,
            "settled_at": (now or datetime.now(timezone.utc)).isoformat(),
            "generation_price": signal.generation_price,
            "settlement_price": price,
            "direction": signal.direction,
            "correct": correct,
            "invalid": invalid_reason,
            "abs_pct_change": round(abs(price / signal.generation_price - 1) * 100, 4)
            if signal.generation_price else None,
        }
        try:
            with open(self.settlements_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(settlement, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning(f"SignalTracker: failed to write settlement: {exc}")

        # 写回 signals 文件的 settled 标记，消除重复结算。
        # 修复前 settle 只追加 settlements 文件，signals 行的 settled_24h/settled_1w
        # 从未更新——unsettled() 每 6h cron 会把同一信号重复 settle，
        # settlements 文件重复行直接污染胜率样本分母。
        self._mark_settled_in_signals_file(signal, window)

        return signal

    def _mark_settled_in_signals_file(self, signal: TrackedSignal, window: str) -> None:
        """Rewrite signals file with the settled flag updated for this signal_id.

        JSONL 无就地更新，读全量→改目标行→原子替换。信号文件量级小
        （每日新增个位数行），全量重写代价可忽略。
        """
        import os
        import tempfile
        field_settled = f"settled_{window}"
        try:
            if not self.signals_file.exists():
                return
            lines = self.signals_file.read_text(encoding="utf-8").splitlines()
            changed = False
            out_lines = []
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    out_lines.append(line)
                    continue
                try:
                    data = json.loads(stripped)
                except json.JSONDecodeError:
                    out_lines.append(line)
                    continue
                if data.get("signal_id") == signal.signal_id and not data.get(field_settled):
                    data[field_settled] = True
                    if window == "24h":
                        data["price_24h"] = signal.price_24h
                        data["correct_24h"] = signal.correct_24h
                    elif window == "1w":
                        data["price_1w"] = signal.price_1w
                        data["correct_1w"] = signal.correct_1w
                    out_lines.append(json.dumps(data, ensure_ascii=False))
                    changed = True
                else:
                    out_lines.append(line)
            if not changed:
                return
            fd, tmp = tempfile.mkstemp(
                prefix=".signals.", suffix=".tmp", dir=self.signals_file.parent
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write("\n".join(out_lines) + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, self.signals_file)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        except OSError as exc:
            logger.warning(f"SignalTracker: failed to mark settled in signals file: {exc}")

    def performance(self) -> dict:
        """Compute aggregate performance across all settled signals.

        精度修复(2026-08-14): 胜率分母只统计"有效方向判定"(invalid 为空且 correct 非 None),
        不再把 entry 缺失/波动过小的记录混入分母, 避免系统性压低或虚抬胜率。
        """
        total = 0
        wins_24h = 0
        wins_1w = 0
        invalid_count = 0

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
                    if s.get("invalid"):
                        invalid_count += 1
                        continue  # 无效判定, 不进胜率样本
                    if s.get("correct") is not None:
                        if s["window"] == "24h":
                            wins_24h += 1 if s.get("correct") else 0
                        elif s["window"] == "1w":
                            wins_1w += 1 if s.get("correct") else 0
        except OSError:
            pass

        valid_24h = sum(
            1 for s in self._iter_settlements("24h")
            if not s.get("invalid") and s.get("correct") is not None
        )
        valid_1w = sum(
            1 for s in self._iter_settlements("1w")
            if not s.get("invalid") and s.get("correct") is not None
        )

        return {
            "total_settlements": total,
            "valid_24h": valid_24h,
            "valid_1w": valid_1w,
            "invalid_count": invalid_count,
            "settled_24h": valid_24h,
            "settled_1w": valid_1w,
            "wins_24h": wins_24h,
            "wins_1w": wins_1w,
            "win_rate_24h": round(wins_24h / valid_24h, 3) if valid_24h > 0 else None,
            "win_rate_1w": round(wins_1w / valid_1w, 3) if valid_1w > 0 else None,
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
            schema_version=data.get("schema_version", 0),
            signal_id=data.get("signal_id", ""),
            signal_type=data.get("signal_type", "unknown"),
            action_semantics=data.get("action_semantics", "unknown"),
            strategy_id=data.get("strategy_id", "unknown"),
            strategy_version=data.get("strategy_version", "unknown"),
            forecast_horizon=data.get("forecast_horizon"),
            provenance=data.get("provenance", {}),
            generated_at=datetime.fromisoformat(data["generated_at"]),
            symbol=data.get("symbol", ""),
            direction=data.get("direction", ""),
            rationale=data.get("rationale", ""),
            generation_price=data.get("generation_price"),
            confidence=data.get("confidence", 0.0),
            source=data.get("source", ""),
            urgency=data.get("urgency", "medium"),
            regime=data.get("regime", {}),
            entry_snapshot=data.get("entry_snapshot"),
            scoring_status=data.get("scoring_status", "unscorable"),
            scoring_reason=data.get("scoring_reason", "legacy_missing_snapshot"),
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
