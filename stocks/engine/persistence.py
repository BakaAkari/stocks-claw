"""最小化分析快照持久化。"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from stocks.domain.models import (
    AdviceRecord,
    AnalysisContext,
    ExecutionRecord,
    ForecastRecord,
)


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
    ):
        self.base_dir = Path(base_dir)
        self.enabled = enabled
        self.max_snapshots = max(1, int(max_snapshots))
        self.advice_dir = Path(advice_dir) if advice_dir else self.base_dir.parent / "advice"
        self.max_advice_records = max(1, int(max_advice_records))
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
        """保存一条确认过的建议摘要，并按 max_advice_records 滚动清理。"""
        self.advice_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{self._safe_timestamp(record.created_at)}.json"
        filepath = self.advice_dir / filename
        if filepath.exists():
            filename = f"{self._safe_timestamp(datetime.now().isoformat())}.json"
            filepath = self.advice_dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(record.to_dict(), f, ensure_ascii=False, indent=2)

        self._trim_advice()
        return str(filepath)

    def load_recent_advice(self, count: int = 3) -> list[dict]:
        """读取最近 N 条确认建议摘要。"""
        return [record.to_dict() for record in self._load_advice_records()[:count]]

    def list_advice(self) -> list[dict]:
        """列出所有确认建议摘要（按 created_at 倒序）。"""
        return [record.to_dict() for record in self._load_advice_records()]

    def save_execution(self, record: ExecutionRecord) -> str:
        """保存一条确认执行记录，并按 max_execution_records 滚动清理。"""
        self.execution_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{self._safe_timestamp(record.recorded_at)}_{record.id}.json"
        filepath = self.execution_dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(record.to_dict(), f, ensure_ascii=False, indent=2)

        self._trim_executions()
        return str(filepath)

    def list_executions(self) -> list[dict]:
        """列出所有执行记录（按 recorded_at 倒序）。"""
        return [record.to_dict() for record in self._load_execution_records()]

    def save_forecast(self, record: ForecastRecord) -> str:
        """保存一条确认预测记录或结算后的预测记录，并按上限滚动清理。"""
        self.forecast_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{self._safe_timestamp(record.created_at)}_{record.id}.json"
        filepath = self.forecast_dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(record.to_dict(), f, ensure_ascii=False, indent=2)

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
        records.sort(key=lambda record: record.created_at, reverse=True)
        return records

    def _list_advice_files(self) -> list[Path]:
        if not self.advice_dir.is_dir():
            return []
        files = list(self.advice_dir.glob("*.json"))
        files.sort(key=lambda path: path.name, reverse=True)
        return files

    def _trim_advice(self) -> None:
        self._trim_record_files(
            self._list_advice_files(),
            keep=self.max_advice_records,
            sort_key=lambda data: data.get("created_at", ""),
        )

    def _load_execution_records(self) -> list[ExecutionRecord]:
        records: list[ExecutionRecord] = []
        for path in self._list_execution_files():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    records.append(ExecutionRecord.from_dict(json.load(f)))
            except Exception:
                continue
        records.sort(key=lambda record: (record.recorded_at, record.id), reverse=True)
        return records

    def _list_execution_files(self) -> list[Path]:
        if not self.execution_dir.is_dir():
            return []
        files = list(self.execution_dir.glob("*.json"))
        files.sort(key=lambda path: path.name, reverse=True)
        return files

    def _trim_executions(self) -> None:
        self._trim_record_files(
            self._list_execution_files(),
            keep=self.max_execution_records,
            sort_key=lambda data: data.get("recorded_at", ""),
        )

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
    def _safe_timestamp(value: str) -> str:
        return value.replace(":", "").replace("+", "_").replace("/", "_").replace("\\", "_")
