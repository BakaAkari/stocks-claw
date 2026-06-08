"""显式数据持久化 — 仅保存用户请求的数据"""

import json
import os
from datetime import datetime
from typing import Optional
from stocks.domain.models import AnalysisContext


class DataPersistence:
    """数据持久化器 — 将 AnalysisContext 保存为 JSON 快照，支持加载最近记录"""

    def __init__(self, base_dir: str = "./data/snapshots"):
        self.base_dir = base_dir
        os.makedirs(base_dir, exist_ok=True)

    def save_context(self, context: AnalysisContext, label: Optional[str] = None) -> str:
        """保存 AnalysisContext 到文件，返回文件路径"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if label:
            filename = f"{timestamp}_{label}.json"
        else:
            filename = f"{timestamp}.json"

        filepath = os.path.join(self.base_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(context.to_dict(), f, ensure_ascii=False, indent=2)

        return filepath

    def load_recent(self, count: int = 5) -> list[dict]:
        """加载最近 N 次保存的上下文摘要（仅返回元信息，非完整数据）"""
        files = self._list_snapshot_files()
        recent = []

        for filepath in files[:count]:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                summary = {
                    "file": os.path.basename(filepath),
                    "generated_at": data.get("generated_at", "unknown"),
                    "asset_count": data.get("asset_count", 0),
                    "news_count": data.get("news_count", 0),
                    "schema_version": data.get("schema_version", 0),
                    "llm_enhancer_enabled": data.get("llm_enhancer_enabled", False),
                }
                recent.append(summary)
            except Exception:
                continue

        return recent

    def list_snapshots(self) -> list[str]:
        """列出所有快照文件（按时间倒序）"""
        return [os.path.basename(f) for f in self._list_snapshot_files()]

    def _list_snapshot_files(self) -> list[str]:
        """获取目录下所有 JSON 快照文件的完整路径，按修改时间倒序"""
        if not os.path.isdir(self.base_dir):
            return []

        files = [
            os.path.join(self.base_dir, f)
            for f in os.listdir(self.base_dir)
            if f.endswith(".json")
        ]
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return files
