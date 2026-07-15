"""News intelligence storage: hourly snapshots, event clusters, signals and archive."""

from __future__ import annotations

import gzip
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class IntelligenceSnapshot:
    """Raw data collected during one global_intelligence_watch run."""

    collected_at: datetime
    sources: dict
    articles: list[dict]
    macro: dict
    quotes: dict
    data_quality: dict
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "collected_at": self.collected_at.isoformat(),
            "sources": self.sources,
            "articles": self.articles,
            "macro": self.macro,
            "quotes": self.quotes,
            "data_quality": self.data_quality,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "IntelligenceSnapshot":
        return cls(
            collected_at=_parse_iso(data.get("collected_at", "")),
            sources=data.get("sources", {}),
            articles=data.get("articles", []),
            macro=data.get("macro", {}),
            quotes=data.get("quotes", {}),
            data_quality=data.get("data_quality", {}),
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True)
class EventCluster:
    """Aggregated event cluster produced by IntelligenceAnalyzer."""

    cluster_id: str
    theme: str
    event_type: str
    summary: str
    articles: list[dict]
    affected_markets: list[str]
    affected_symbols: list[str]
    sentiment: str
    urgency: str
    confidence: float
    formed_at: datetime

    def to_dict(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "theme": self.theme,
            "event_type": self.event_type,
            "summary": self.summary,
            "articles": self.articles,
            "affected_markets": self.affected_markets,
            "affected_symbols": self.affected_symbols,
            "sentiment": self.sentiment,
            "urgency": self.urgency,
            "confidence": self.confidence,
            "formed_at": self.formed_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EventCluster":
        return cls(
            cluster_id=data["cluster_id"],
            theme=data["theme"],
            event_type=data["event_type"],
            summary=data["summary"],
            articles=data.get("articles", []),
            affected_markets=data.get("affected_markets", []),
            affected_symbols=data.get("affected_symbols", []),
            sentiment=data.get("sentiment", "unknown"),
            urgency=data.get("urgency", "medium"),
            confidence=data.get("confidence", 0.0),
            formed_at=_parse_iso(data.get("formed_at", "")),
        )


@dataclass(frozen=True)
class IntelligenceSignal:
    """Action-oriented signal generated from event clusters and market data.

    Task 3 provenance fields:
      generation_method - llm / rule_fallback / category_padding
      match_method      - unmatched until a position matcher resolves exact/proxy/exposure_tag/category
      source_as_of      - when the source data was observed
    """

    symbol: str
    name: str
    direction: str  # buy / sell / hold / watch
    horizon: str  # short_term / medium_term / long_term
    rationale: str
    falsification: str
    risk_source: str
    confidence: float
    urgency: str
    generated_at: datetime
    generation_method: str = "rule_fallback"
    match_method: str = "unmatched"
    source_as_of: "Optional[datetime]" = None

    def to_dict(self) -> dict:
        d = {
            "symbol": self.symbol,
            "name": self.name,
            "direction": self.direction,
            "horizon": self.horizon,
            "rationale": self.rationale,
            "falsification": self.falsification,
            "risk_source": self.risk_source,
            "confidence": self.confidence,
            "urgency": self.urgency,
            "generated_at": self.generated_at.isoformat(),
            "generation_method": self.generation_method,
            "match_method": self.match_method,
        }
        if self.source_as_of is not None:
            d["source_as_of"] = self.source_as_of.isoformat()
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "IntelligenceSignal":
        source_as_of = None
        raw = data.get("source_as_of")
        if raw:
            source_as_of = _parse_iso(raw)
        return cls(
            symbol=data["symbol"],
            name=data["name"],
            direction=data.get("direction", "watch"),
            horizon=data.get("horizon", "short_term"),
            rationale=data.get("rationale", ""),
            falsification=data.get("falsification", ""),
            risk_source=data.get("risk_source", ""),
            confidence=data.get("confidence", 0.0),
            urgency=data.get("urgency", "medium"),
            generated_at=_parse_iso(data.get("generated_at", "")),
            generation_method=data.get("generation_method", "rule_fallback"),
            match_method=data.get("match_method", "unmatched"),
            source_as_of=source_as_of,
        )


class NewsIntelligenceStore:
    """File-backed store for news intelligence snapshots, clusters and signals."""

    def __init__(
        self,
        root_dir: str | Path,
        *,
        online_days: int = 7,
        archive_days: int = 30,
    ):
        self.root = Path(root_dir)
        self.online_days = online_days
        self.archive_days = archive_days
        self.hourly_dir = self.root / "hourly"
        self.events_dir = self.root / "events"
        self.signals_dir = self.root / "signals"
        self.archive_dir = self.root / "archive"
        for d in (self.hourly_dir, self.events_dir, self.signals_dir, self.archive_dir):
            d.mkdir(parents=True, exist_ok=True)

    def save_snapshot(self, snapshot: IntelligenceSnapshot) -> Path:
        path = self._snapshot_path(snapshot.collected_at)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def save_clusters(self, clusters: list[EventCluster], *, formed_at: datetime) -> Path:
        path = self._events_path(formed_at)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "formed_at": formed_at.isoformat(),
            "clusters": [c.to_dict() for c in clusters],
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def save_signals(self, signals: list[IntelligenceSignal], *, generated_at: datetime) -> Path:
        path = self._signals_path(generated_at)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": generated_at.isoformat(),
            "signals": [s.to_dict() for s in signals],
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def latest_snapshot(self, before: Optional[datetime] = None) -> Optional[IntelligenceSnapshot]:
        candidates = self.list_snapshots(before=before)
        if not candidates:
            return None
        return IntelligenceSnapshot.from_dict(
            json.loads(candidates[-1].read_text(encoding="utf-8"))
        )

    def latest_clusters(self, before: Optional[datetime] = None) -> Optional[dict]:
        candidates = self._list_day_files(self.events_dir, before=before)
        if not candidates:
            return None
        return json.loads(candidates[-1].read_text(encoding="utf-8"))

    def latest_signals(self, before: Optional[datetime] = None) -> Optional[dict]:
        candidates = self._list_day_files(self.signals_dir, before=before)
        if not candidates:
            return None
        return json.loads(candidates[-1].read_text(encoding="utf-8"))

    def list_snapshots(
        self,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        before: Optional[datetime] = None,
    ) -> list[Path]:
        return self._list_hourly_files(self.hourly_dir, start=start, end=end, before=before)

    def load_snapshots(self, paths: list[Path]) -> list[IntelligenceSnapshot]:
        snapshots = []
        for path in paths:
            try:
                snapshots.append(
                    IntelligenceSnapshot.from_dict(json.loads(path.read_text(encoding="utf-8")))
                )
            except (json.JSONDecodeError, KeyError, OSError):
                continue
        return snapshots

    def archive_and_purge(self, now: Optional[datetime] = None) -> dict:
        now = now or datetime.now(timezone.utc)
        online_cutoff = now - timedelta(days=self.online_days)
        archive_cutoff = now - timedelta(days=self.archive_days)
        archived = 0
        deleted = 0
        for source_dir in (self.hourly_dir, self.events_dir, self.signals_dir):
            for path in source_dir.rglob("*.json"):
                try:
                    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                except OSError:
                    continue
                if mtime < archive_cutoff:
                    path.unlink(missing_ok=True)
                    deleted += 1
                elif mtime < online_cutoff:
                    rel = path.relative_to(self.root)
                    archive_path = self.archive_dir / f"{rel}.gz"
                    archive_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(path, "rb") as src, gzip.open(archive_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    path.unlink(missing_ok=True)
                    archived += 1
        return {"archived": archived, "deleted": deleted}

    def _snapshot_path(self, dt: datetime) -> Path:
        day = _day_str(dt)
        stamp = dt.strftime("%H%M%S")
        return self.hourly_dir / day / f"{stamp}.json"

    def _events_path(self, dt: datetime) -> Path:
        day = _day_str(dt)
        return self.events_dir / day / "event_cluster.json"

    def _signals_path(self, dt: datetime) -> Path:
        day = _day_str(dt)
        return self.signals_dir / day / "signal.json"

    def _list_day_files(self, directory: Path, *, before: Optional[datetime] = None) -> list[Path]:
        paths: list[Path] = []
        for day_dir in sorted(directory.iterdir()):
            if not day_dir.is_dir():
                continue
            for path in sorted(day_dir.iterdir()):
                if path.suffix != ".json":
                    continue
                if before is not None:
                    try:
                        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                    except OSError:
                        continue
                    if mtime >= before:
                        continue
                paths.append(path)
        return paths

    def _list_hourly_files(
        self,
        directory: Path,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        before: Optional[datetime] = None,
    ) -> list[Path]:
        paths: list[Path] = []
        for day_dir in sorted(directory.iterdir()):
            if not day_dir.is_dir():
                continue
            day = datetime.strptime(day_dir.name, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if start is not None and day.date() < start.date():
                continue
            if end is not None and day.date() > end.date():
                continue
            for path in sorted(day_dir.iterdir()):
                if path.suffix != ".json":
                    continue
                if before is not None:
                    try:
                        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                    except OSError:
                        continue
                    if mtime >= before:
                        continue
                paths.append(path)
        return paths


def _day_str(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _parse_iso(value: str) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
