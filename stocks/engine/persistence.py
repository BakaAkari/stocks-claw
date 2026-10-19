"""最小化分析快照持久化。"""

import fcntl
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from stocks.domain.models import (
    AdviceRecord,
    AnalysisContext,
    ExecutionRecord,
    ForecastRecord,
    UserDecisionRecord,
)
from stocks.engine.atomic_io import atomic_write_json


class DataPersistence:
    """数据持久化器 — 将 AnalysisContext 保存为 JSON 快照，支持加载最近记录"""

    def __init__(
        self,
        base_dir: str = "./data/snapshots",
        *,
        enabled: bool = True,
        max_snapshots: int = 30,
        advice_dir: str | None = None,
        max_advice_records: int = 30,
        execution_dir: str | None = None,
        max_execution_records: int = 200,
        forecast_dir: str | None = None,
        max_forecast_records: int = 200,
        decision_dir: str | None = None,
    ):
        self.base_dir = Path(base_dir)
        self.enabled = enabled
        self.max_snapshots = max(1, int(max_snapshots))
        self.advice_dir = Path(advice_dir) if advice_dir else self.base_dir.parent / "advice"
        # Retained as constructor compatibility only; confirmed financial history never rolls over.
        self.max_advice_records = max(1, int(max_advice_records))
        self.decision_dir = (
            Path(decision_dir) if decision_dir else self.advice_dir.parent / "user_decisions"
        )
        self.execution_dir = (
            Path(execution_dir) if execution_dir else self.base_dir.parent / "executions"
        )
        self.max_execution_records = max(1, int(max_execution_records))
        self.forecast_dir = (
            Path(forecast_dir) if forecast_dir else self.base_dir.parent / "forecasts"
        )
        self.max_forecast_records = max(1, int(max_forecast_records))
        if self.enabled:
            self.base_dir.mkdir(parents=True, exist_ok=True)

    def save_context(
        self,
        context: AnalysisContext,
        label: Optional[str] = None,
    ) -> Optional[str]:
        """保存最小上下文快照，并按 max_snapshots 滚动清理。"""
        if not self.enabled:
            return None
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        if label:
            filename = f"{timestamp}_{label}.json"
        else:
            filename = f"{timestamp}.json"

        filepath = self.base_dir / filename
        snapshot = {
            "generated_at": context.generated_at,
            "asset_count": context.asset_count,
            "portfolio_summary": {
                "ratios": context.portfolio_mapping.ratios,
                "dominant_layers": context.portfolio_mapping.dominant_layers,
                "growth_exposure": context.portfolio_mapping.growth_exposure,
                "buffer_strength": context.portfolio_mapping.buffer_strength,
                "liquidity_status": context.portfolio_mapping.liquidity_status,
            },
            "market_state": context.market_state.to_dict(),
            "drift_checks": [item.to_dict() for item in context.drift_checks],
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)

        self._trim_snapshots()
        return str(filepath)

    def load_recent(self, count: int = 5) -> list[dict]:
        """加载最近 N 次保存的上下文摘要（仅返回元信息，非完整数据）"""
        files = self._list_snapshot_files()
        recent = []

        for filepath in files[:count]:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                summary = {"file": filepath.name, **data}
                recent.append(summary)
            except Exception:
                continue

        return recent

    def list_snapshots(self) -> list[str]:
        """列出所有快照文件（按时间倒序）"""
        return [path.name for path in self._list_snapshot_files()]

    def save_advice(self, record: AdviceRecord) -> str:
        """Save once by stable ID; feedback projection is separate from immutable advice."""
        return self._save_record_once(
            self.advice_dir, record.to_dict(), timestamp=record.created_at,
            legacy_id=record.created_at, mutable_fields={"feedback", "feedback_history"},
        )

    def load_recent_advice(self, count: int = 3) -> list[dict]:
        """读取最近 N 条确认建议摘要。"""
        return [record.to_dict() for record in self._load_advice_records()[:count]]

    def update_advice_feedback(self, advice_ref: str, feedback: dict) -> dict:
        """Append legacy feedback history and update its latest projection atomically.

        Stable ID is preferred; legacy timestamp references remain supported.
        This API does not invent per-action UserDecisionRecords or actual fills.
        """
        if not isinstance(feedback, dict) or not feedback.get("status"):
            raise ValueError("feedback must be an object with a status")
        with self._record_lock(self.advice_dir):
            files = self._list_advice_files()
            if not files:
                raise ValueError("advice ledger is empty")
            contents = [(path, json.loads(path.read_text(encoding="utf-8"))) for path in files]
            ref = str(advice_ref).strip()
            if not ref:
                raise ValueError("advice_ref is required")
            if ref == "latest":
                target, data = max(contents, key=lambda item: self._time_sort_key(item[1].get("created_at", "")))
            else:
                matches = [(path, data) for path, data in contents if data.get("id") == ref]
                if not matches:
                    safe_ref = self._safe_timestamp(ref)
                    matches = [(path, data) for path, data in contents if (
                        path.stem.startswith(safe_ref)
                        or str(data.get("created_at", "")).startswith(ref)
                    )]
                if not matches:
                    raise ValueError(f"no advice record matches ref '{advice_ref}'")
                if len(matches) > 1:
                    raise ValueError(f"advice ref '{advice_ref}' is ambiguous ({len(matches)} matches)")
                target, data = matches[0]
            history = list(data.get("feedback_history", []))
            if not history and data.get("feedback") is not None:
                history.append(dict(data["feedback"]))
            history.append(dict(feedback))
            data["feedback_history"] = history
            data["feedback"] = dict(feedback)
            record = AdviceRecord.from_dict(data)
            # Keep unrecognized legacy metadata rather than rewrite it away.
            self._atomic_json(target, data)
            return record.to_dict()

    def list_advice(self) -> list[dict]:
        """列出所有确认建议摘要（按 created_at 倒序）。"""
        return [record.to_dict() for record in self._load_advice_records()]

    def save_execution(self, record: ExecutionRecord) -> str:
        """Append one confirmed execution; identical IDs retry without overwriting."""
        return self._save_record_once(
            self.execution_dir, record.to_dict(), timestamp=record.recorded_at,
        )

    def save_decision(self, record: UserDecisionRecord) -> str:
        """Append one confirmed user decision independently of execution facts."""
        return self._save_record_once(
            self.decision_dir, record.to_dict(), timestamp=record.recorded_at,
        )

    def list_decisions(self) -> list[dict]:
        records = []
        if not self.decision_dir.is_dir():
            return records
        for path in self.decision_dir.glob("*.json"):
            try:
                records.append(UserDecisionRecord.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except (ValueError, TypeError, OSError):
                continue
        records.sort(key=lambda record: (self._time_sort_key(record.recorded_at), record.id), reverse=True)
        return [record.to_dict() for record in records]

    def list_executions(self) -> list[dict]:
        """列出所有执行记录（按 recorded_at 倒序）。"""
        return [record.to_dict() for record in self._load_execution_records()]

    def list_executions_checked(self) -> dict:
        """Read all valid records and expose every unreadable/invalid ledger file.

        Reconciliation callers must treat errors as evidence gaps, never as an
        empty ledger. The legacy list_executions API retains its old behavior.
        """
        records: list[dict] = []
        errors: list[dict] = []
        for path in self._list_execution_files():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("execution record must be an object")
                records.append(ExecutionRecord.from_dict(data).to_dict())
            except (OSError, ValueError, TypeError, AttributeError, OverflowError) as exc:
                errors.append({"file": str(path), "reason": f"{type(exc).__name__}: {exc}"})
        records.sort(key=lambda record: (
            self._time_sort_key(record["recorded_at"]), record["id"],
        ), reverse=True)
        return {"records": records, "errors": errors}

    def find_executions_by_decision_id(self, decision_id: str) -> list[dict]:
        """按 decision_id 查找执行记录。"""
        return [
            record.to_dict()
            for record in self._load_execution_records()
            if record.decision_id == decision_id
        ]

    def find_executions_by_run_id(self, run_id: str) -> list[dict]:
        """Match recorded source_run_id exactly; a decision hash is not a run prefix."""
        return [
            record.to_dict() for record in self._load_execution_records()
            if record.source_run_id == run_id
        ]

    def save_forecast(self, record: ForecastRecord) -> str:
        """保存一条确认预测记录或结算后的预测记录，并按上限滚动清理。"""
        self.forecast_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{self._safe_timestamp(record.created_at)}_{record.id}.json"
        filepath = self.forecast_dir / filename

        atomic_write_json(filepath, record.to_dict())

        self._trim_forecasts()
        return str(filepath)

    def list_forecasts(self) -> list[dict]:
        """列出所有预测记录（按 created_at 倒序）。"""
        return [record.to_dict() for record in self._load_forecast_records()]

    def _list_snapshot_files(self) -> list[Path]:
        """获取目录下所有 JSON 快照文件的完整路径，按文件名时间戳倒序。"""
        if not self.base_dir.is_dir():
            return []
        files = list(self.base_dir.glob("*.json"))
        files.sort(key=lambda path: path.name, reverse=True)
        return files

    def _trim_snapshots(self) -> None:
        for path in self._list_snapshot_files()[self.max_snapshots :]:
            path.unlink(missing_ok=True)

    def _load_advice_records(self) -> list[AdviceRecord]:
        records: list[AdviceRecord] = []
        for path in self._list_advice_files():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    records.append(AdviceRecord.from_dict(json.load(f)))
            except Exception:
                continue
        records.sort(key=lambda record: self._time_sort_key(record.created_at), reverse=True)
        return records

    def _list_advice_files(self) -> list[Path]:
        if not self.advice_dir.is_dir():
            return []
        files = list(self.advice_dir.glob("*.json"))
        files.sort(key=lambda path: path.name, reverse=True)
        return files

    def _trim_advice(self) -> None:
        """Compatibility no-op: user-confirmed advice history must not be deleted."""

    def _load_execution_records(self) -> list[ExecutionRecord]:
        records: list[ExecutionRecord] = []
        for path in self._list_execution_files():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    records.append(ExecutionRecord.from_dict(json.load(f)))
            except Exception:
                continue
        records.sort(key=lambda record: (self._time_sort_key(record.recorded_at), record.id), reverse=True)
        return records

    def _list_execution_files(self) -> list[Path]:
        if not self.execution_dir.is_dir():
            return []
        files = list(self.execution_dir.glob("*.json"))
        files.sort(key=lambda path: path.name, reverse=True)
        return files

    def _trim_executions(self) -> None:
        """Compatibility no-op: user-confirmed fills must not be deleted."""

    def _load_forecast_records(self) -> list[ForecastRecord]:
        records: list[ForecastRecord] = []
        for path in self._list_forecast_files():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    records.append(ForecastRecord.from_dict(json.load(f)))
            except Exception:
                continue
        records.sort(key=lambda record: (record.created_at, record.id), reverse=True)
        return records

    def _list_forecast_files(self) -> list[Path]:
        if not self.forecast_dir.is_dir():
            return []
        files = list(self.forecast_dir.glob("*.json"))
        files.sort(key=lambda path: path.name, reverse=True)
        return files

    def _trim_forecasts(self) -> None:
        self._trim_record_files(
            self._list_forecast_files(),
            keep=self.max_forecast_records,
            sort_key=lambda data: data.get("created_at", ""),
        )

    @staticmethod
    def _trim_record_files(paths: list[Path], *, keep: int, sort_key) -> None:
        ranked: list[tuple[str, str, Path]] = []
        for path in paths:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    sort_value = sort_key(json.load(f))
            except Exception:
                sort_value = ""
            ranked.append((str(sort_value or ""), path.name, path))

        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        for _, _, path in ranked[keep:]:
            path.unlink(missing_ok=True)

    @staticmethod
    def _time_sort_key(value: str) -> float:
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            return timestamp.timestamp()
        except (TypeError, ValueError, AttributeError):
            return float("-inf")

    @staticmethod
    @contextmanager
    def _record_lock(directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / ".ledger.lock").open("a", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _atomic_json(filepath: Path, data: dict) -> None:
        encoded = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
        descriptor, temp_name = tempfile.mkstemp(prefix=".pending-", dir=filepath.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, filepath)
        finally:
            Path(temp_name).unlink(missing_ok=True)

    def _save_record_once(
        self, directory: Path, data: dict, *, timestamp: str,
        legacy_id: str | None = None, mutable_fields: set[str] | None = None,
    ) -> str:
        """Lock identity lookup plus atomic append, including simultaneous retries."""
        identity = data.get("id") or legacy_id
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError("stable record id is required")
        mutable_fields = mutable_fields or set()
        immutable = {key: value for key, value in data.items() if key not in mutable_fields}
        canonical = json.dumps(immutable, sort_keys=True, ensure_ascii=False, allow_nan=False)
        with self._record_lock(directory):
            for path in directory.glob("*.json"):
                try:
                    existing = json.loads(path.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    continue
                if not isinstance(existing, dict):
                    continue
                existing_id = existing.get("id") or (
                    existing.get("created_at") if legacy_id is not None else None
                )
                if existing_id != identity:
                    continue
                original = {key: value for key, value in existing.items() if key not in mutable_fields}
                if json.dumps(original, sort_keys=True, ensure_ascii=False, allow_nan=False) != canonical:
                    raise ValueError(f"record id '{identity}' already exists with different content")
                return str(path)
            # Hash IDs to keep user-supplied values out of filesystem paths.
            token = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            filepath = directory / f"{self._safe_timestamp(timestamp)}_{token}.json"
            if filepath.exists():
                raise ValueError(f"record id '{identity}' collides with an unreadable record")
            self._atomic_json(filepath, data)
            return str(filepath)

    @staticmethod
    def _safe_timestamp(value: str) -> str:
        return value.replace(":", "").replace("+", "_").replace("/", "_").replace("\\", "_")
