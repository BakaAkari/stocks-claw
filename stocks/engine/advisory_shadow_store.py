"""Persist shadow advisory artifacts without affecting production.

Shadow artifacts are never delivered to the user. They are used to compare the
new advisory path against the existing production path and to build evidence
before switching.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from stocks.domain.advisory_models import (
    AdvisoryValidationReceipt,
    InvestmentAdvisory,
    UnifiedAnalysisSnapshot,
)
from stocks.logging_utils import get_logger

logger = get_logger("advisory_shadow_store")

DEFAULT_SHADOW_DIR = Path(".local/advisory_shadow")


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_hash(value: Any) -> str:
    h = hashlib.sha256()
    h.update(str(value).encode("utf-8"))
    return h.hexdigest()[:24]


class AdvisoryShadowStore:
    """Store a single shadow run: snapshot, advisory, receipt, and comparison key."""

    def __init__(self, root_dir: Path | str = DEFAULT_SHADOW_DIR) -> None:
        self.root = Path(root_dir)
        _ensure_dir(self.root)

    def _run_dir(self, run_id: str) -> Path:
        run_path = self.root / run_id
        _ensure_dir(run_path)
        return run_path

    def _write_json(self, run_id: str, name: str, data: dict[str, Any]) -> Path:
        run_dir = self._run_dir(run_id)
        path = run_dir / f"{name}.json"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def save(
        self,
        run_id: str,
        snapshot: UnifiedAnalysisSnapshot,
        advisory: InvestmentAdvisory,
        receipt: AdvisoryValidationReceipt,
        *,
        production_decision_id: str = "",
        production_artifact_path: str = "",
    ) -> dict[str, Any]:
        """Persist a complete shadow run and return its manifest."""
        manifest = {
            "run_id": run_id,
            "saved_at": _iso_utc(),
            "snapshot_id": snapshot.snapshot_id,
            "advisory_id": advisory.advisory_id,
            "snapshot_hash": _content_hash(asdict(snapshot)),
            "advisory_hash": _content_hash(asdict(advisory)),
            "receipt_hash": _content_hash(asdict(receipt)),
            "receipt_status": receipt.status,
            "production_decision_id": production_decision_id,
            "production_artifact_path": production_artifact_path,
            "files": {},
        }

        manifest["files"]["snapshot"] = str(
            self._write_json(run_id, "snapshot", asdict(snapshot))
        )
        manifest["files"]["advisory"] = str(
            self._write_json(run_id, "advisory", asdict(advisory))
        )
        manifest["files"]["receipt"] = str(
            self._write_json(run_id, "receipt", asdict(receipt))
        )
        manifest_path = self._write_json(run_id, "manifest", manifest)
        manifest["files"]["manifest"] = str(manifest_path)

        logger.info(
            "saved shadow run",
            extra={
                "run_id": run_id,
                "snapshot_id": snapshot.snapshot_id,
                "advisory_id": advisory.advisory_id,
            },
        )
        return manifest

    def load_manifest(self, run_id: str) -> Optional[dict[str, Any]]:
        path = self._run_dir(run_id) / "manifest.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_runs(self) -> list[str]:
        """Return run IDs sorted by directory mtime."""
        if not self.root.exists():
            return []
        runs = [
            d.name
            for d in self.root.iterdir()
            if d.is_dir() and (d / "manifest.json").exists()
        ]
        runs.sort()
        return runs

    def load(self, run_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Load raw snapshot, advisory, receipt dicts for a run."""
        run_dir = self._run_dir(run_id)
        snapshot = json.loads((run_dir / "snapshot.json").read_text(encoding="utf-8"))
        advisory = json.loads((run_dir / "advisory.json").read_text(encoding="utf-8"))
        receipt = json.loads((run_dir / "receipt.json").read_text(encoding="utf-8"))
        return snapshot, advisory, receipt
