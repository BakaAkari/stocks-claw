"""
Fallback Tracker — 数据源 fallback 链可观测性

记录每次数据请求的完整 fallback 链路，生成数据健康度报告。
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class FallbackRecord:
    """单次请求的 fallback 链路记录。"""
    symbol: str
    market: str
    data_type: str                # quote / history / macro
    requested_sources: list[str]  # 尝试顺序
    used_source: str
    failed_sources: list[str]     # 尝试过但失败的源
    failure_reasons: dict[str, str]  # source → reason
    timestamp: str
    latency_ms: float


class FallbackTracker:
    """fallback 链路追踪器。轻量、低开销、只附加。"""

    def __init__(self, store_dir: Path | None = None, max_records: int = 5000):
        if store_dir is None:
            store_dir = Path(__file__).resolve().parents[2] / ".local" / "fallback_logs"
        self._dir = Path(store_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._log_path = self._dir / "records.jsonl"
        self._max_records = max_records

    def record(
        self,
        symbol: str,
        market: str,
        data_type: str,
        requested_sources: list[str],
        used_source: str,
        failed_sources: list[str] | None = None,
        failure_reasons: dict[str, str] | None = None,
        latency_ms: float = 0,
    ) -> None:
        rec = FallbackRecord(
            symbol=symbol, market=market, data_type=data_type,
            requested_sources=requested_sources, used_source=used_source,
            failed_sources=failed_sources or [],
            failure_reasons=failure_reasons or {},
            timestamp=datetime.now(timezone.utc).isoformat(),
            latency_ms=latency_ms,
        )
        line = json.dumps({
            "symbol": rec.symbol, "market": rec.market, "data_type": rec.data_type,
            "requested": rec.requested_sources, "used": rec.used_source,
            "failed": rec.failed_sources, "reasons": rec.failure_reasons,
            "ts": rec.timestamp, "latency_ms": rec.latency_ms,
        }, ensure_ascii=False)
        with open(self._log_path, "a") as f:
            f.write(line + "\n")

        # 简单裁剪旧记录
        self._prune()

    def _prune(self) -> None:
        try:
            lines = self._log_path.read_text().splitlines()
            if len(lines) > self._max_records:
                self._log_path.write_text("\n".join(lines[-self._max_records // 2:]) + "\n")
        except OSError:
            pass

    def health_report(self, *, days: int = 7) -> dict:
        """生成数据源健康度报告。"""
        if not self._log_path.exists():
            return {"status": "no_data", "sources": {}, "summary": "无 fallback 记录"}

        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
        source_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "success": 0, "failures": {}, "avg_latency_ms": 0.0})
        total_requests = 0
        total_failures = 0

        for line in self._log_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                ts = datetime.fromisoformat(rec["ts"]).timestamp()
            except (ValueError, KeyError):
                ts = 0
            if ts < cutoff:
                continue

            total_requests += 1
            used = rec["used"]
            stats = source_stats[used]
            stats["total"] += 1
            stats["success"] += 1
            stats["avg_latency_ms"] = (stats["avg_latency_ms"] * (stats["success"] - 1) + rec.get("latency_ms", 0)) / stats["success"]

            for failed in rec.get("failed", []):
                total_failures += 1
                fstats = source_stats[failed]
                fstats["total"] += 1
                reason = rec.get("reasons", {}).get(failed, "unknown")
                fstats["failures"][reason] = fstats["failures"].get(reason, 0) + 1

        health = {}
        for source, stats in source_stats.items():
            total = stats["total"]
            success = stats["success"]
            health[source] = {
                "total_requests": total,
                "success_rate": success / total if total > 0 else 0,
                "avg_latency_ms": round(stats["avg_latency_ms"], 1),
                "common_failures": sorted(stats["failures"].items(), key=lambda x: -x[1])[:3],
            }

        return {
            "status": "ok",
            "days": days,
            "total_requests": total_requests,
            "total_failures": total_failures,
            "sources": health,
            "summary": f"{days}天内 {total_requests} 次请求，{total_failures} 次 fallback",
        }
