"""最小化分析快照持久化。"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from stocks.domain.models import AnalysisContext


class DataPersistence:
    """数据持久化器 — 将 AnalysisContext 保存为 JSON 快照，支持加载最近记录"""

    def __init__(
        self,
        base_dir: str = "./data/snapshots",
        *,
        enabled: bool = True,
        max_snapshots: int = 30,
    ):
        self.base_dir = Path(base_dir)
        self.enabled = enabled
        self.max_snapshots = max(1, int(max_snapshots))
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

    def _list_snapshot_files(self) -> list[Path]:
        """获取目录下所有 JSON 快照文件的完整路径，按修改时间倒序"""
        if not self.base_dir.is_dir():
            return []
        files = list(self.base_dir.glob("*.json"))
        files.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
        return files

    def _trim_snapshots(self) -> None:
        for path in self._list_snapshot_files()[self.max_snapshots:]:
            path.unlink(missing_ok=True)
