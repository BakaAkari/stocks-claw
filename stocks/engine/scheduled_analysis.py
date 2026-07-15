"""Scheduled cross-market analysis runs.

This module is deliberately small and file-based. It is designed to be called by
cron/launchd and to hand a structured JSON artifact to the user-facing Agent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from stocks.engine.economic_event_watcher import EconomicEventWatcher
from stocks.engine.hypothesis_tracker import (
    HypothesisStore,
    auto_check_hypotheses,
    format_hypothesis_report,
)
from stocks.engine.intelligence_analyzer import (
    LLMIntelligenceAnalyzer,
    _compute_brief_health,
)
from stocks.engine.intelligence_harvester import IntelligenceHarvester
from stocks.engine.news_intelligence_store import (
    IntelligenceSnapshot,
    NewsIntelligenceStore,
)
from stocks.engine.profile_interpreter import load_computed, merge_with_defaults
from stocks.engine.quant_action import (
    _TAG_TO_BUCKET,
    compute_portfolio_risk,
    finalize_decision,
)
from stocks.engine.risk_warning import assess_risk
from stocks.engine.shadow_account import build_shadow_block, save_snapshot
from stocks.engine.signal_tracker import SignalTracker, TrackedSignal
from stocks.logging_utils import get_logger

logger = get_logger("scheduled_analysis")

SCHEDULED_RUN_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ScheduledSession:
    id: str
    market: str
    exchange_timezone: str
    user_timezone: str
    time: str
    intent: str
    push: str
    enabled: bool
    duplicate_window_minutes: int
    holidays: frozenset[str]
    primary_market: str
    run_every_minutes: Optional[int] = None

    @property
    def exchange_tz(self) -> ZoneInfo:
        return ZoneInfo(self.exchange_timezone)

    @property
    def user_tz(self) -> ZoneInfo:
        return ZoneInfo(self.user_timezone)


@dataclass(frozen=True)
class SessionOccurrence:
    session: ScheduledSession
    market_date: date
    scheduled_for: datetime

    @property
    def run_id(self) -> str:
        scheduled_utc = self.scheduled_for.astimezone(timezone.utc)
        stamp = scheduled_utc.strftime("%Y%m%dT%H%M%SZ")
        return f"{stamp}_{self.session.id}"


def parse_datetime(value: Optional[str], *, default_tz: str = "Asia/Shanghai") -> datetime:
    """Parse an optional ISO datetime into an aware datetime."""
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(default_tz))
    return parsed


def load_scheduled_config(config_dir: Path) -> dict:
    path = config_dir / "scheduled_sessions.json"
    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("scheduled_sessions.json must be an object with schema_version=1")
    return data


class MarketSessionCalendar:
    """Resolve configured market sessions using exchange-local time zones."""

    def __init__(self, config: dict):
        self.config = config
        self.user_timezone = str(config.get("user_timezone") or "Asia/Shanghai")
        self.duplicate_window_minutes = int(config.get("default_duplicate_window_minutes") or 90)
        self.sessions = self._load_sessions(config)

    def find_session(self, session_id: str) -> ScheduledSession:
        for session in self.sessions:
            if session.id == session_id:
                return session
        raise ValueError(f"Unknown scheduled session: {session_id}")

    def due_sessions(self, now: datetime) -> list[SessionOccurrence]:
        occurrences: list[SessionOccurrence] = []
        for session in self.sessions:
            occurrence = self.occurrence_for(session, now)
            if not self._is_market_date(session, occurrence.market_date):
                continue
            local_now = now.astimezone(session.exchange_tz)
            due_until = occurrence.scheduled_for + timedelta(
                minutes=session.duplicate_window_minutes
            )
            if occurrence.scheduled_for <= local_now <= due_until:
                occurrences.append(occurrence)
        return occurrences

    def occurrence_for(
        self,
        session: ScheduledSession | str,
        now: datetime,
    ) -> SessionOccurrence:
        if isinstance(session, str):
            session = self.find_session(session)
        local_now = now.astimezone(session.exchange_tz)
        hour, minute = _parse_hhmm(session.time)
        scheduled_for = datetime.combine(
            local_now.date(),
            time(hour=hour, minute=minute),
            tzinfo=session.exchange_tz,
        )
        run_every = getattr(session, "run_every_minutes", None)
        if run_every:
            midnight = scheduled_for.replace(hour=0, minute=0, second=0, microsecond=0)
            since_midnight = int((local_now - midnight).total_seconds()) // 60
            boundary_minutes = (since_midnight // run_every) * run_every
            scheduled_for = midnight + timedelta(minutes=boundary_minutes)
        return SessionOccurrence(
            session=session,
            market_date=scheduled_for.date(),
            scheduled_for=scheduled_for,
        )

    def is_market_open_for_occurrence(self, occurrence: SessionOccurrence) -> bool:
        return self._is_market_date(occurrence.session, occurrence.market_date)

    def _load_sessions(self, config: dict) -> list[ScheduledSession]:
        sessions: list[ScheduledSession] = []
        markets = config.get("markets") or {}
        if not isinstance(markets, dict):
            return sessions
        for market, market_config in markets.items():
            if not isinstance(market_config, dict) or market_config.get("enabled") is False:
                continue
            exchange_timezone = str(market_config.get("exchange_timezone") or self.user_timezone)
            holidays = frozenset(str(item) for item in market_config.get("holidays") or [])
            for item in market_config.get("sessions") or []:
                if not isinstance(item, dict) or item.get("enabled") is False:
                    continue
                sessions.append(
                    ScheduledSession(
                        id=str(item["id"]),
                        market=str(market),
                        exchange_timezone=exchange_timezone,
                        user_timezone=self.user_timezone,
                        time=str(item["time"]),
                        intent=str(item.get("intent") or item["id"]),
                        push=str(item.get("push") or "normal"),
                        enabled=True,
                        duplicate_window_minutes=int(
                            item.get(
                                "duplicate_window_minutes",
                                self.duplicate_window_minutes,
                            )
                        ),
                        holidays=holidays,
                        primary_market=str(item.get("primary_market") or market),
                        run_every_minutes=item.get("run_every_minutes"),
                    )
                )
        return sessions

    @staticmethod
    def _is_market_date(session: ScheduledSession, market_date: date) -> bool:
        if market_date.weekday() >= 5:
            return False
        return market_date.isoformat() not in session.holidays


class RunArtifactStore:
    """Persist scheduled run artifacts under .local/scheduled_runs."""

    def __init__(self, artifact_dir: str | Path):
        self.artifact_dir = Path(artifact_dir)
        self.latest_dir = self.artifact_dir / "latest"

    def save(self, run: dict) -> dict:
        json_path = self._artifact_path(run, suffix=".json")
        md_path = self._artifact_path(run, suffix=".md")
        json_path.parent.mkdir(parents=True, exist_ok=True)
        self.latest_dir.mkdir(parents=True, exist_ok=True)

        payload = json.dumps(run, ensure_ascii=False, indent=2)
        json_path.write_text(payload + "\n", encoding="utf-8")
        md_path.write_text(format_run_markdown(run) + "\n", encoding="utf-8")
        (self.latest_dir / f"{run['session']}.json").write_text(
            payload + "\n",
            encoding="utf-8",
        )
        return {
            "json_path": str(json_path),
            "markdown_path": str(md_path),
            "latest_path": str(self.latest_dir / f"{run['session']}.json"),
        }

    def latest(self, session_id: str) -> Optional[dict]:
        path = self.latest_dir / f"{session_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def has_run_for_market_date(self, session_id: str, market_date: str) -> Optional[dict]:
        latest = self.latest(session_id)
        if latest and latest.get("market_date") == market_date:
            return latest
        return None

    def _artifact_path(self, run: dict, *, suffix: str) -> Path:
        return (
            self.artifact_dir
            / str(run["market_date"])
            / str(run["market"])
            / str(run["session"])
            / f"{run['run_id']}{suffix}"
        )


class ScheduledAnalysisRunner:
    """Build scheduled analysis artifacts from an engine AnalysisContext."""

    def __init__(
        self,
        engine: Any,
        *,
        config: dict,
        artifact_dir: str | Path,
        event_watcher = None,
        repo_root: Path | None = None,
    ):
        self.engine = engine
        self.config = config
        self.calendar = MarketSessionCalendar(config)
        self.store = RunArtifactStore(artifact_dir)
        self._repo_root = repo_root or Path(artifact_dir).parent.parent if artifact_dir else None
        self.event_watcher: Optional[EconomicEventWatcher] = event_watcher

    async def run_due(
        self,
        *,
        now: Optional[datetime] = None,
        force: bool = False,
    ) -> dict:
        current = now or datetime.now(timezone.utc)

        # 1. Check event triggers FIRST — they take priority over time sessions.
        event_runs: list[dict] = []
        if self.event_watcher is not None:
            event_result = await self._run_event_triggered(current)
            if event_result is not None and event_result.get("runs"):
                event_runs = event_result["runs"]

        # 2. Check time-based sessions.
        due = self.calendar.due_sessions(current)

        # If event already ran an intelligence harvest, skip duplicate session.
        if event_runs:
            due = [
                occ for occ in due
                if occ.session.id != "global_intelligence_watch"
            ]

        time_runs = []
        for occurrence in due:
            time_runs.append(await self.run_occurrence(occurrence, now=current, force=force))

        all_runs = event_runs + time_runs

        if not all_runs:
            return {
                "success": True,
                "status": "skipped_no_due",
                "generated_at": _iso_utc(current),
                "runs": [],
            }

        statuses = {str(item.get("status")) for item in all_runs}
        result = {
            "success": True,
            "status": (
                "ok" if "ok" in statuses else "degraded" if "degraded" in statuses else "skipped"
            ),
            "generated_at": _iso_utc(current),
            "runs": all_runs,
        }
        if event_runs:
            result["event_triggered"] = True
        return result

    async def run_session(
        self,
        session_id: str,
        *,
        now: Optional[datetime] = None,
        force: bool = False,
    ) -> dict:
        current = now or datetime.now(timezone.utc)
        occurrence = self.calendar.occurrence_for(session_id, current)
        return await self.run_occurrence(occurrence, now=current, force=force)

    async def run_occurrence(
        self,
        occurrence: SessionOccurrence,
        *,
        now: datetime,
        force: bool,
    ) -> dict:
        if not self.calendar.is_market_open_for_occurrence(occurrence):
            return _skipped_result("skipped_market_closed", occurrence, now)

        market_date = occurrence.market_date.isoformat()
        existing = self.store.has_run_for_market_date(occurrence.session.id, market_date)
        if existing and not force:
            return {
                "success": True,
                "status": "skipped_duplicate",
                "run_id": occurrence.run_id,
                "existing_run_id": existing.get("run_id"),
                "session": occurrence.session.id,
                "market": occurrence.session.market,
                "market_date": market_date,
                "scheduled_for": occurrence.scheduled_for.isoformat(),
            }
        elif existing and force:
            # 强制覆盖：先删除旧 artifact，避免路径冲突
            old_path = self.store._artifact_path(existing, suffix=".json")
            old_md = self.store._artifact_path(existing, suffix=".md")
            for p in (old_path, old_md):
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass

        if occurrence.session.id == "global_intelligence_watch":
            return await self._run_intelligence(occurrence, now=now)

        context = await self.engine.build_context(
            include_news=True,
            include_quotes=True,
            include_history=True,
        )
        run = build_scheduled_run(
            context.to_dict(),
            occurrence=occurrence,
            generated_at=now,
            config=self.config,
        )

        # Shadow Account: 保存本期建议快照 + 注入诊断
        action_cards = run.get("action_cards") or []
        if action_cards and self._repo_root:
            save_snapshot(
                action_cards,
                run_id=run["run_id"],
                session=run["session"],
                generated_at=run["generated_at"],
                market_date=run["market_date"],
                repo_root=self._repo_root,
            )
            shadow = build_shadow_block(repo_root=self._repo_root)
            if shadow:
                mb = run.setdefault("mandatory_blocks", {})
                mb["shadow_account"] = shadow

        # Hypothesis Tracker: 自动关联本期 action_cards 到相关论点
        if action_cards and self._repo_root:
            try:
                store = HypothesisStore(store_dir=self._repo_root / ".local" / "hypotheses")
                matched = auto_check_hypotheses(store, run["run_id"], action_cards)
                if matched:
                    all_h = store.list_all()
                    report = format_hypothesis_report(all_h)
                    if report:
                        mb = run.setdefault("mandatory_blocks", {})
                        mb["hypothesis_tracker"] = report
            except Exception:
                pass  # 非关键路径

        paths = self.store.save(run)
        return {
            "success": True,
            "status": run["status"],
            "run_id": run["run_id"],
            "session": run["session"],
            "market": run["market"],
            "market_date": run["market_date"],
            "scheduled_for": run["scheduled_for"],
            "paths": paths,
            "notification": run["notification"],
            "session_summary": run["session_summary"],
            "agent_task": run["agent_task"],
        }

    async def check_event_triggers(self, *, now: Optional[datetime] = None) -> dict:
        """Check for calendar event triggers without executing them.

        Dry-run: returns what WOULD trigger, with event details and trigger windows.
        """
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

        if self.event_watcher is None:
            return {
                "success": True,
                "status": "no_watcher",
                "checked_at": _iso_utc(current),
                "message": "EconomicEventWatcher not configured — no event calendar available",
                "triggers": [],
                "upcoming": [],
            }

        check = await self.event_watcher.check(now=current)
        return {
            "success": True,
            "status": "triggered" if check.has_triggers else "no_triggers",
            "checked_at": _iso_utc(current),
            "triggered": [
                {
                    "name": t.event.name,
                    "event_type": t.event.event_type,
                    "market": t.event.market,
                    "scheduled_at": t.scheduled_at.isoformat(),
                    "window_end": t.trigger_window_end.isoformat(),
                    "minutes_since_event": t.minutes_since_event,
                    "reason": t.reason,
                }
                for t in check.triggered
            ],
            "upcoming": [
                {
                    "name": e.name,
                    "event_type": e.event_type,
                    "date": e.date,
                    "status": getattr(e, "status", "scheduled"),
                    "days_until": getattr(e, "days_until", None),
                }
                for e in check.upcoming[:10]
            ],
            "cooldown_active": check.cooldown_active,
            "calendar_quality": check.calendar_quality,
        }

    async def _run_event_triggered(self, now: datetime) -> Optional[dict]:
        """Check for event triggers and run intelligence harvest if any are active.

        Returns a result dict if an event triggered, else None.
        """
        if self.event_watcher is None:
            return None

        check = await self.event_watcher.check(now=now)
        if not check.has_triggers:
            return None

        trigger_names = [t.event.name for t in check.triggered]
        trigger_reasons = [t.reason for t in check.triggered]

        logger.info(
            f"Event trigger(s) detected: {', '.join(trigger_names)}. "
            f"Forcing intelligence harvest."
        )

        # Run intelligence harvest using the global_intelligence_watch session
        occurrence = self.calendar.occurrence_for("global_intelligence_watch", now)
        result = await self._run_intelligence(occurrence, now=now)

        # Mark all triggers as acted upon (cooldown)
        self.event_watcher.mark_all_triggered(check.triggered)

        # Enrich result with event trigger metadata
        result["event_trigger"] = {
            "triggered_by": trigger_names,
            "trigger_reasons": trigger_reasons,
            "checked_at": _iso_utc(check.checked_at),
            "calendar_quality": check.calendar_quality,
        }
        # Override notification to indicate event-driven
        if "notification" in result:
            result["notification"]["reason"] = f"Event-driven: {', '.join(trigger_names)}"
        if "session_summary" in result:
            result["session_summary"]["priority"] = "high"
            result["session_summary"]["headline"] = (
                f"Event-triggered intelligence: {', '.join(trigger_names)}"
            )

        return {
            "success": True,
            "status": "ok_event_triggered",
            "generated_at": _iso_utc(now),
            "triggered_by": trigger_names,
            "runs": [result],
        }

    async def _run_intelligence(self, occurrence: SessionOccurrence, *, now: datetime) -> dict:
        # Run the global_intelligence_watch session.
        repo_root = Path(__file__).resolve().parents[2]
        intelligence_dir = Path(self.config.get("intelligence_dir") or ".local/news_intelligence")
        if not intelligence_dir.is_absolute():
            intelligence_dir = repo_root / intelligence_dir
        store = NewsIntelligenceStore(intelligence_dir)

        from stocks.providers.finnhub_quote import FinnhubQuoteProvider

        finnhub_client = None
        if hasattr(self.engine, "registry"):
            candidate = self.engine.registry.get("finnhub")
            if isinstance(candidate, FinnhubQuoteProvider):
                finnhub_client = candidate
        if finnhub_client is None:
            finnhub_client = FinnhubQuoteProvider()

        fred_cache_dir = repo_root / ".local" / "macro_cache"
        usage_dir = repo_root / ".local" / "usage"
        harvester = IntelligenceHarvester(
            finnhub_client=finnhub_client,
            max_items_per_source=10,
            fred_cache_dir=fred_cache_dir,
        )
        harvester.enable_usage_tracking(usage_dir)
        harvest_result = await harvester.harvest()
        snapshot = IntelligenceSnapshot(
            collected_at=now,
            sources=harvest_result.source_status,
            articles=harvest_result.articles,
            macro=harvest_result.macro,
            quotes=harvest_result.quotes,
            data_quality=harvest_result.data_quality,
            metadata=harvest_result.metadata,
        )
        store.save_snapshot(snapshot)

        recent_paths = store.list_snapshots(
            start=now - timedelta(hours=6),
            end=now,
        )
        recent_snapshots = store.load_snapshots(recent_paths) + [snapshot]
        # Try LLM-driven analysis first, fall back to keyword rules
        holdings = []
        if hasattr(self.engine, '_asset_positions_v2'):
            holdings = [p.instrument_key for p in self.engine._asset_positions_v2 if p.instrument_key]
        llm_cfg = self.engine._config.get("llm", {}) if hasattr(self.engine, "_config") else {}
        paths_cfg = self.engine._config.get("paths", {}) if hasattr(self.engine, "_config") else {}
        analyzer = LLMIntelligenceAnalyzer(
            holdings=holdings,
            fallback_to_rules=True,
            model=llm_cfg.get("analysis_model", "deepseek-v4-flash"),
            max_input_articles=llm_cfg.get("analysis_max_articles", 25),
            timeout=llm_cfg.get("analysis_timeout", 60),
            temperature=llm_cfg.get("analysis_temperature", 0.1),
            env_file_path=paths_cfg.get("secret_env_file"),
            base_url=llm_cfg.get("fallback_base_url"),
        )
        analysis_result = analyzer.analyze(recent_snapshots)
        store.save_clusters(analysis_result.clusters, formed_at=now)
        store.save_signals(analysis_result.signals, generated_at=now)

        # Signal tracking: record signals for backtest
        if analysis_result.signals:
            tracker_dir = repo_root / ".local" / "signal_tracker"
            tracker = SignalTracker(tracker_dir)
            macro_ctx = harvest_result.macro or {}
            tracked = []
            for sig in analysis_result.signals:
                price = None
                quotes_dict = harvest_result.quotes or {}
                if sig.symbol in quotes_dict:
                    q = quotes_dict[sig.symbol]
                    if isinstance(q, dict):
                        price = q.get("price")
                tracked.append(TrackedSignal(
                    signal_id=f"{now.strftime('%Y%m%dT%H%M%S')}_{sig.symbol}_{sig.direction}",
                    generated_at=now,
                    symbol=sig.symbol,
                    direction=sig.direction,
                    rationale=sig.rationale,
                    generation_price=price,
                    confidence=sig.confidence,
                    source=analysis_result.metadata.get("analysis_mode", "unknown"),
                    urgency=sig.urgency,
                    regime={"vix": macro_ctx.get("vix"), "mode": analysis_result.metadata.get("analysis_mode", "unknown")},
                ))
            tracker.record_batch(tracked)
        store.archive_and_purge(now=now)

        run = build_intelligence_run(
            harvest_result.to_dict(),
            analysis_result.to_dict(),
            occurrence=occurrence,
            generated_at=now,
            config=self.config,
            engine_config=self.engine._config,
        )
        paths = self.store.save(run)
        return {
            "success": True,
            "status": run["status"],
            "run_id": run["run_id"],
            "session": run["session"],
            "market": run["market"],
            "market_date": run["market_date"],
            "scheduled_for": run["scheduled_for"],
            "paths": paths,
            "notification": run["notification"],
            "session_summary": run["session_summary"],
            "agent_task": run["agent_task"],
        }

    def latest(self, session_id: str) -> dict:
        latest = self.store.latest(session_id)
        if latest is None:
            return {
                "success": False,
                "error": f"No scheduled run found for session {session_id}",
            }
        return {"success": True, "data": latest}


def _build_rotation_leaders(rotation: dict, max_items: int = 8) -> list[dict]:
    """从轮动数据构建结构化排行榜，供 Agent 做量化选股。

    rotation 来自 ContextBuilder.build_context() 的 rotation 字段，
    其中 items 是已排名的结构化 dict（symbol, name, r5, r20, above_ma20, rank）。
    """
    items = rotation.get("items", [])
    if not items:
        # 回退：用 leaders/laggards 字符串列表
        leaders = rotation.get("leaders", [])
        return [{"symbol": s, "rank": None} for s in leaders[:max_items]]

    result = []
    for item in items[:max_items]:
        result.append({
            "symbol": item.get("symbol"),
            "name": item.get("name"),
            "category": item.get("category"),
            "rank": item.get("rank"),
            "r5": item.get("r5"),
            "r20": item.get("r20"),
            "above_ma20": item.get("above_ma20"),
        })
    return result


def _filter_action_signals_by_market(action_signals: dict, primary_market: str) -> dict:
    """过滤 action_signals.items，只保留与 session 市场相关的标的。"""
    market_prefix = {"cn": "a:", "us": "us:", "crypto": "crypto:"}.get(primary_market, "")
    if not market_prefix:
        return action_signals  # 无法判断，保留全部
    items = action_signals.get("items") or []
    filtered = [i for i in items if str(i.get("symbol", "")).startswith(market_prefix)]
    return {
        **action_signals,
        "items": filtered,
        "counts": _recount_signals(filtered),
    }


def _recount_signals(items: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for item in items:
        sig = item.get("signal", "unknown")
        counts[sig] = counts.get(sig, 0) + 1
    return counts


def _merge_profile_config(base_config):
    """将 computed_profile.json 的参数合并到引擎配置中。

    computed_profile（个性化参数）优先级最高，覆盖 engine.yaml 默认值。
    """
    from pathlib import Path as _Path
    local = _Path(__file__).resolve().parent.parent.parent / ".local"
    computed = load_computed(local / "computed_profile.json")
    if not computed:
        return base_config or {}
    # computed 覆盖 base_config，保证个性化参数不被默认值冲掉
    merged = merge_with_defaults(computed)
    if base_config:
        return {**base_config, **merged}
    return merged


def build_scheduled_run(
    context: dict,
    *,
    occurrence: SessionOccurrence,
    generated_at: datetime,
    config: dict,
) -> dict:
    session = occurrence.session
    generated_at_iso = _iso_utc(generated_at)
    context_quality = context.get("data_quality") or {}
    trigger_reviews = _flatten_trigger_reviews(context.get("recent_advice") or [])
    position_reviews = _build_position_reviews(
        context.get("position_valuations") or [],
        recent_advice=context.get("recent_advice") or [],
    )
    # 提取情报信号供 action_card 冲突检测
    intel_digest = context.get("intelligence_digest") or {}
    intelligence_health = intel_digest.get("intelligence_health") or {
        "status": "missing", "age_minutes": None, "risk_eligible": False
    }
    intelligence_coverage = intel_digest.get("intelligence_coverage") or {
        "field": 0, "directional": 0, "padding": 0,
        "exact": 0, "proxy": 0, "category": 0,
    }
    intel_signals: dict[str, dict] = {
        s.get("symbol", ""): {
            "symbol": s.get("symbol", ""),
            "direction": s.get("direction", ""),
            "urgency": s.get("urgency", "medium"),
            "rationale": s.get("rationale", ""),
        }
        for s in (intel_digest.get("top_signals") or [])
        if s.get("symbol")
    }
    # 提取多维度交叉分析上下文
    market_state = context.get("market_state") or {}
    intel_digest_full = context.get("intelligence_digest") or {}
    event_clusters = intel_digest_full.get("top_clusters") or []
    data_freshness = (context.get("data_quality") or {}).get("quotes", {}).get("freshness", "fresh")
    rotation_data = context.get("rotation") or {}
    rotation_ranks = {
        item["symbol"]: item.get("rank")
        for item in rotation_data.get("items", [])
        if item.get("rank")
    }

    action_cards = _build_action_cards(
        context.get("position_valuations") or [],
        intelligence_signals=intel_signals if intel_signals else None,
        market_state=market_state,
        event_clusters=event_clusters,
        data_freshness=data_freshness,
        rotation_ranks=rotation_ranks if rotation_ranks else None,
        constraints=context.get("portfolio_constraints"),
        portfolio_mapping=context.get("portfolio_mapping"),
        quant_config=_merge_profile_config(
            context.get("engine_config", {}).get("quant_action"),
        ),
    )
    portfolio_risk = _build_portfolio_risk_summary(
        context.get("position_valuations") or [],
        action_cards=action_cards,
    )
    rotation_leaders_data = context.get("rotation", {}).get("items", [])
    capital_allocation = _build_capital_allocation(
        action_cards,
        context.get("position_valuations") or [],
        context.get("portfolio_mapping") or {},
        context.get("liquidity_summary") or {},
        constraints=context.get("portfolio_constraints"),
        rotation_ranks=rotation_ranks if rotation_ranks else None,
        rotation_leaders=rotation_leaders_data,
    )
    session_intent_props = _session_intent_props(session.id)
    filtered_action_signals = _filter_action_signals_by_market(
        context.get("action_signals") or {}, session.primary_market
    )
    action_signal_reviews = _build_action_signal_reviews(
        filtered_action_signals,
        session=session,
        max_primary=8,
        max_cross=2,
        can_recommend_new=session_intent_props.get("can_recommend_new", True),
    )
    priority = _priority(trigger_reviews, position_reviews)
    notification = _notification(
        session=session,
        priority=priority,
        now=generated_at,
        quiet_hours=config.get("quiet_hours") or {},
    )
    status = _run_status(context_quality)
    return {
        "schema_version": SCHEDULED_RUN_SCHEMA_VERSION,
        "run_id": occurrence.run_id,
        "generated_at": generated_at_iso,
        "market": session.market,
        "session": session.id,
        "market_date": occurrence.market_date.isoformat(),
        "exchange_timezone": session.exchange_timezone,
        "user_timezone": session.user_timezone,
        "scheduled_for": occurrence.scheduled_for.isoformat(),
        "status": status,
        "status_reason": _status_reason(status, context_quality),
        "intelligence_health": intelligence_health,
        "intelligence_coverage": intelligence_coverage,
        "source_context": {
            "schema_version": context.get("schema_version"),
            "generated_at": context.get("generated_at"),
        },
        "portfolio_scope": _portfolio_scope(context, session.primary_market),
        "session_summary": {
            "headline": _headline(session.id),
            "priority": priority,
            "push_policy": notification["policy"],
            "intent_props": session_intent_props,
            "market_state_summary": _market_state_summary(context.get("market_state") or {}),
        },
        "position_reviews": position_reviews,
        "action_cards": action_cards,
        "portfolio_risk": portfolio_risk,
        "capital_allocation": capital_allocation,
        "trigger_reviews": trigger_reviews,
        "action_signal_reviews": action_signal_reviews,
        "action_signals": filtered_action_signals,
        "rule_scorecard": context.get("rule_scorecard", {}),
        "data_quality": context_quality,
        "agent_task": build_agent_task(session),
        "write_policy": {
            "may_write_financial_memory": False,
            "requires_user_confirmation": True,
        },
        "notification": notification,
        "risk_assessment": _compute_risk_assessment(context),
        "mandatory_blocks": build_mandatory_blocks(
            _compute_risk_assessment(context),
            capital_allocation.get("constraint_alerts", []),
            context.get("upcoming_events") or [],
            capital_allocation=capital_allocation,
            total_value_cny=capital_allocation.get("total_value_cny", 0),
        ),
        "context_digest": {
            "market_state": context.get("market_state") or {},
            "market_state_summary": _market_state_summary(context.get("market_state") or {}),
            "portfolio_mapping": context.get("portfolio_mapping") or {},
            "exposure_summary": context.get("exposure_summary") or {},
            "liquidity_summary": context.get("liquidity_summary") or {},
            "advice_granularity": context.get("advice_granularity") or {},
            "rotation_leaders": _build_rotation_leaders(
                context.get("rotation") or {}, max_items=8
            ),
            "intelligence_digest": context.get("intelligence_digest") or {},
            "upcoming_events": context.get("upcoming_events") or [],
        },
    }


def _compute_risk_assessment(context: dict) -> dict:
    """Lightweight risk assessment for run reports using intelligence clusters."""
    from stocks.engine.risk_warning import assess_risk

    macro = (context.get("market_state") or {}).get("macro") or context.get("macro") or {}
    intel_digest = context.get("intelligence_digest") or {}
    health = intel_digest.get("intelligence_health") or {}
    if health and not health.get("risk_eligible", False):
        return {
            "level": "normal",
            "triggers": [],
            "recommended_actions": ["情报过期或缺失，不参与风险升级"],
            "suspend_accumulation": False,
            "cash_target_pct": None,
        }
    clusters = intel_digest.get("top_clusters") or []

    geopolitical_crisis = any(
        c.get("theme") == "geopolitics" and c.get("urgency") == "critical"
        for c in clusters
    )

    risk = assess_risk(
        vix=macro.get("vix"),
        cluster_urgencies=[c.get("urgency") for c in clusters],
        negative_cluster_count=sum(1 for c in clusters if c.get("sentiment") == "negative"),
        geopolitical_crisis=geopolitical_crisis,
    )

    return {
        "level": risk.level,
        "triggers": [{"condition": t.condition, "value": t.value} for t in risk.triggers],
        "recommended_actions": risk.recommended_actions,
        "suspend_accumulation": risk.suspend_accumulation,
        "cash_target_pct": risk.cash_target_pct,
    }


def _format_capital_facts(capital_allocation: dict) -> str:
    """将 capital_allocation 化为纯事实块——不含建议、不含解释。

    由 build_mandatory_blocks 调用，LLM 基于这些事实自行生成资金部署建议。
    """
    lines = ["**💰 资金状况**"]
    ca = capital_allocation or {}

    total = ca.get("total_value_cny", 0)
    net = ca.get("net_deployable_cny", 0)
    ratio_pct = round(net / total * 100, 1) if total > 0 else 0
    lines.append(f"总资产 ¥{total:,.0f} ｜ 净可动用 ¥{net:,.0f}（{ratio_pct}%）")

    # Constraint alerts — facts only
    alerts = ca.get("constraint_alerts", [])
    breaches = [a for a in alerts if a.get("severity") == "breach"]
    nears = [a for a in alerts if a.get("severity") == "near"]
    if breaches or nears:
        lines.append("")
        for a in breaches:
            lines.append(f"- 约束偏离：{a['message']}")
        for a in nears:
            lines.append(f"- 约束逼近：{a['message']}")

    # Conflicts — facts only
    conflicts = ca.get("conflicts", [])
    if conflicts:
        reduce_conf = [c for c in conflicts if c.get("signal") in ("reduce", "stop_loss", "take_profit")]
        add_conf = [c for c in conflicts if c.get("signal") == "add"]
        parts = []
        if reduce_conf:
            parts.append(f"{len(reduce_conf)} 个减仓信号 vs 约束要求加仓")
        if add_conf:
            parts.append(f"{len(add_conf)} 个加仓信号 vs 约束要求减仓")
        lines.append(f"- 信号冲突：{'；'.join(parts)}")

    # Reduce proceeds
    reduce_items = ca.get("reduce_items", [])
    if reduce_items:
        total_reduce = sum(r.get("proceeds_cny", 0) for r in reduce_items)
        lines.append(f"- 预计回收：¥{total_reduce:,.0f}（{len(reduce_items)} 笔）")

    # Add candidates — facts only
    add_candidates = ca.get("add_candidates", [])
    if add_candidates:
        lines.append("")
        lines.append("**加仓候选**（按综合得分排序）")
        for i, ad in enumerate(add_candidates[:5]):
            note = f" — {ad['constraint_note']}" if ad.get("constraint_note") not in ("无约束冲突", "") else ""
            lines.append(f"{i+1}. {ad['position_id']} {ad['action']}{note}")

    # Rotation reference — facts only
    idle = ca.get("idle_cash_suggestions", [])
    if idle:
        lines.append("")
        lines.append("**轮动参考**")
        for s in idle[:3]:
            lines.append(f"- #{s.get('rank','?')} {s.get('symbol','?')} {s.get('name','')} "
                       f"（20日 +{s.get('r20','?')}%）")

    return "\n".join(lines)


def build_mandatory_blocks(
    risk_assessment: dict,
    constraint_alerts: list[dict],
    upcoming_events: list[dict],
    *,
    capital_allocation: dict | None = None,
    total_value_cny: float = 0.0,
) -> dict[str, str]:
    """生成 agent 必须原样嵌入的确定性报告段。

    不依赖 LLM 选择性渲染 — 这些文本是系统计算的确定事实。
    """
    blocks: dict[str, str] = {}

    # ── 风险边界段 ──
    ra = risk_assessment or {}
    level = ra.get("level", "normal")
    if level != "normal":
        lines = ["**风险边界**"]
        lines.append(f"风险等级: {level}")
        triggers = ra.get("triggers") or []
        for t in triggers:
            lines.append(f"- 触发: {t.get('condition', '?')} — {t.get('value', '?')}")
        actions = ra.get("recommended_actions") or []
        for a in actions:
            lines.append(f"- 系统建议: {a}")
        if ra.get("suspend_accumulation"):
            lines.append("- 暂停加仓: 是")
        if ra.get("cash_target_pct") is not None:
            lines.append(f"- 现金目标: {ra['cash_target_pct']*100:.0f}%")
        # 72h 内重大事件警告
        if upcoming_events:
            now_utc = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
            urgent = []
            for ev in upcoming_events[:3]:
                timestamp = ev.get("timestamp")
                if timestamp:
                    try:
                        ev_time = __import__('datetime').datetime.fromisoformat(timestamp)
                        hours_left = (ev_time - now_utc).total_seconds() / 3600
                        if 0 < hours_left <= 72:
                            urgent.append(f"{ev.get('title', '?')}（{hours_left:.0f}小时后）")
                    except Exception:
                        pass
            if urgent:
                lines.append(f"- 临近事件: {', '.join(urgent)} — 当前交易逻辑可能被单日逆转")
        blocks["risk_boundary"] = "\n".join(lines)

    # ── 约束偏离段 ──
    breaches = [a for a in constraint_alerts if a.get("severity") == "breach"]
    if breaches:
        lines2 = ["**大类约束偏离**"]
        for a in breaches:
            lines2.append(f"- {a.get('message', '')}")
        blocks["constraint_alerts"] = "\n".join(lines2)

    # ── 资金状况事实段 ──
    if capital_allocation:
        cap_text = _format_capital_facts(capital_allocation)
        if cap_text:
            blocks["capital_facts"] = cap_text

    return blocks


GLOBAL_INTELLIGENCE_WATCH_SCHEMA_VERSION = 1


def build_intelligence_run(
    harvest_result: dict,
    analysis: dict,
    *,
    occurrence: SessionOccurrence,
    generated_at: datetime,
    config: dict,
    engine_config: Optional[dict] = None,
) -> dict:
    """Build a ScheduledAnalysisRun artifact for global_intelligence_watch."""
    session = occurrence.session
    generated_at_iso = _iso_utc(generated_at)
    macro = harvest_result.get("macro") or {}
    quotes = harvest_result.get("quotes") or {}
    clusters = analysis.get("clusters") or []
    signals = analysis.get("signals") or []
    data_quality = analysis.get("data_quality") or harvest_result.get("data_quality") or {}
    status = "ok" if data_quality.get("status") == "ok" else "degraded"

    # ── Intelligence health check (Task 3) ──
    intel_health = _compute_brief_health(
        generated_at, occurrence.scheduled_for,
    )
    health_stale = intel_health["status"] == "stale"

    # ── Stale guard: stale intelligence must not feed into Risk State ──
    if health_stale:
        from stocks.engine.risk_warning import RiskAssessment
        risk = RiskAssessment(
            level="normal",
            triggers=[],
            recommended_actions=["情报已过期（≥48h 无更新），风险评警告停。等待下次情报巡逻"],
            suspend_accumulation=False,
            cash_target_pct=None,
        )
        # Override status to reflect stale
        status = "degraded"
        # Clear stale clusters/signals from risk assessment inputs
        # Risk assessment below will be skipped
        skip_risk = True
    else:
        skip_risk = False

    # Risk assessment
    if not skip_risk:
        risk = assess_risk(
            vix=macro.get("vix"),
            cluster_urgencies=[c.get("urgency") for c in clusters],
            negative_cluster_count=sum(1 for c in clusters if c.get("sentiment") == "negative"),
            geopolitical_crisis=any(
                c.get("theme") == "geopolitics" and c.get("urgency") == "critical"
                for c in clusters
            ),
            config=engine_config.get("risk_warning") if engine_config else None,
        )

    # Non-trading-day downgrade: weekends can't act on signals,
    # so reduce alarm level to avoid unnecessary anxiety.
    if generated_at.weekday() >= 5:  # Saturday=5, Sunday=6
        _DOWNGRADE = {"hedge": "reduce", "reduce": "watch", "watch": "watch", "normal": "normal"}
        downgraded = _DOWNGRADE.get(risk.level, risk.level)
        if downgraded != risk.level:
            from stocks.engine.risk_warning import RiskAssessment
            risk = RiskAssessment(
                level=downgraded,
                triggers=risk.triggers,
                recommended_actions=[
                    f"非交易日降级: {risk.level}→{downgraded}（市场休市，信号无法执行）"
                ] + risk.recommended_actions,
                suspend_accumulation=risk.suspend_accumulation,
                cash_target_pct=risk.cash_target_pct,
            )

    # Silent mode: no signals + no critical clusters + low priority → archive only
    has_critical = any(c.get("urgency") == "critical" for c in clusters)
    has_signals = len(signals) > 0
    if not has_critical and not has_signals and risk.level == "normal":
        notification = {"recommended": False, "policy": "archive_only", "reason": "silent_mode: no critical events or signals"}
    else:
        reason_parts = []
        if has_critical:
            reason_parts.append("critical clusters detected")
        if has_signals:
            reason_parts.append(f"{len(signals)} signals")
        if risk.level != "normal":
            reason_parts.append(f"risk level: {risk.level}")
        if not reason_parts:
            reason_parts.append("hourly patrol")
        notification = {"recommended": True, "policy": "push_now", "reason": "; ".join(reason_parts)}
    return {
        "schema_version": GLOBAL_INTELLIGENCE_WATCH_SCHEMA_VERSION,
        "run_id": occurrence.run_id,
        "generated_at": generated_at_iso,
        "market": session.market,
        "session": session.id,
        "market_date": occurrence.market_date.isoformat(),
        "exchange_timezone": session.exchange_timezone,
        "user_timezone": session.user_timezone,
        "scheduled_for": occurrence.scheduled_for.isoformat(),
        "status": status,
        "status_reason": _intelligence_status_reason(status, data_quality),
        "intelligence_health": intel_health,
        "intelligence_coverage": _compute_signal_coverage_summary(signals),
        "source_context": {
            "sources": harvest_result.get("source_status", {}),
            "article_count": len(harvest_result.get("articles", [])),
        },
        "portfolio_scope": {"primary_market": "global", "included_markets": ["global"]},
        "session_summary": {
            "headline": "Global intelligence hourly patrol: macro, news, and cross-market signals",
            "priority": _intelligence_priority(signals),
            "push_policy": notification["policy"],
            "intent_props": {"can_recommend_new": True, "session_type": "intelligence_patrol"},
            "market_state_summary": {
                "risk_appetite": _risk_appetite_from_macro(macro),
                "vix": macro.get("vix"),
                "top_move": _top_move(quotes),
            },
        },
        "position_reviews": [],
        "trigger_reviews": [],
        "action_signal_reviews": [_signal_to_action_review(s) for s in signals[:10]],
        "data_quality": data_quality,
        "agent_task": build_intelligence_agent_task(session),
        "write_policy": {
            "may_write_financial_memory": False,
            "requires_user_confirmation": True,
        },
        "notification": notification,
        "risk_assessment": {
            "level": risk.level,
            "triggers": [{"condition": t.condition, "value": t.value} for t in risk.triggers],
            "recommended_actions": risk.recommended_actions,
            "suspend_accumulation": risk.suspend_accumulation,
            "cash_target_pct": risk.cash_target_pct,
        },
        "context_digest": {
            "market_state_summary": {
                "risk_appetite": _risk_appetite_from_macro(macro),
                "vix": macro.get("vix"),
                "top_move": _top_move(quotes),
            },
            "market_impact": analysis.get("market_impact", {}),
            "clusters": clusters[:8],
            "signals": signals[:10],
            "macro": macro,
            "quotes": quotes,
            # agent_task 引用 intelligence_digest.top_clusters / top_signals
            "intelligence_digest": {
                "top_clusters": clusters[:8],
                "top_signals": signals[:10],
                "metadata": analysis.get("metadata", {}),
                "cross_cluster_synthesis_cn": analysis.get("metadata", {}).get("cross_cluster", ""),
            },
        },
    }


def build_intelligence_agent_task(session: ScheduledSession) -> dict:
    """构建自包含的情报巡逻 Agent 任务说明书。"""
    return {
        "task_version": 4,
        "language": "zh-CN",
        "audience": "single_user",
        "session_intent": session.intent,
        "primary_market": session.primary_market,
        "must_answer": [
            "本小时最重要的 1-2 个事件是什么（引用 intelligence_digest.top_clusters）",
            "72小时内是否有CPI/FOMC/NFP等重大数据发布？如有，当前所有交易逻辑可能被该数据单日逆转，必须在风险边界段显式警告",
            "它们对 VIX、油、金、美债、美元、中国资产的可能影响",
            "哪些标的出现了可买入/卖出/观察的信号（引用 intelligence_digest.top_signals）",
            "数据质量是否有明显缺口",
        ],
        "must_not_do": [
            "不得承诺收益",
            "不得忽略 data_quality",
            "不得自动保存建议、执行或预测",
            "事件描述必须引用 intelligence_digest.top_clusters 中的实际 summary，不得编造新闻",
            "宏观数据：市场数据(VIX/美债/美元/汇率/黄金/原油)引用 market 层，官方统计(CPI/失业率/联邦基金利率)引用 official 层并标注数据月份。区分日频 vs 月频数据的时效性差异",
            "严禁使用 # | ``` > --- -[ ] HTML 等飞书不兼容的 Markdown 语法",
            "标题用**加粗**，代码用`行内代码`，分隔用空行",
        ],
        "data_reference": {
            "事件": "intelligence_digest.top_clusters[] — theme, summary, sentiment, urgency, affected_markets",
            "信号": "intelligence_digest.top_signals[] — direction, symbol, rationale",
            "宏观": "macro — market(日频: VIX/美债10Y/美元指数/汇率/黄金/原油, 见 data_quality.macro.market) + official(月频: CPI/失业率/联邦基金利率, 见 data_quality.macro.official, 含 next_release 预估)",
            "行情": "quotes — SPY, QQQ, VIXY, GLD, USO, UUP, NVDA 等 ETF/个股报价",
        },
        "output_structure": {
            "max_words": 900,
            "platform": "feishu",
            "format_rules": "仅使用**加粗**、`行内代码`、- 列表、[链接](url) 四种格式。用空行分隔段落。禁用 # | ``` > --- -[ ] HTML。",
            "sections": [
                {"name": "标题", "content": "全球情报小时巡逻 · {collected_at}"},
                {"name": "核心事件", "content": "1-2 个最重要的事件，附来源和影响分析"},
                {"name": "市场快照", "content": "VIX/美债/美元/油/金/比特币当前水平和方向"},
                {"name": "操作信号", "content": "情报管道识别的买入/卖出/观察信号"},
                {"name": "数据边界", "content": "数据时效、来源质量、缺失字段"},
            ],
        },
        "persona": {
            "role": "你是用户的全球市场情报分析师。你的工作是扫描全球新闻和宏观数据，发现可能影响用户组合的事件和趋势。",
            "principles": [
                "只报真正重要的事件，不报噪音",
                "每个事件必须说明'为什么重要'和'可能影响什么'",
                "引用具体数字，不泛泛而谈",
                "没有重要事件时诚实说'本小时无重大事件'，不编造",
            ],
        },
        "adaptability": {
            "silent_when_quiet": "如果无 urgency=critical 的事件且无新信号 → 输出缩至 3-5 句话",
            "detail_when_active": "如果有 critical 事件或多个新信号 → 展开至 900 字",
        },
        "final_analysis_instructions": (
            "阅读 persona 理解你的角色。根据 adaptability 决定输出篇幅。"
            "输出通常含五节（标题→核心事件→市场快照→操作信号→数据边界），"
            "但无重要事件时可大幅压缩。严格遵守 must_not_do。事件和信号必须引用实际数据。"
        ),
    }


def _intelligence_status_reason(status: str, data_quality: dict) -> str:
    if status == "ok":
        return "Intelligence harvest and analysis completed successfully"
    return "; ".join(data_quality.get("errors", [])) or "Degraded without explicit error"


def _compute_signal_coverage_summary(signals: list[dict]) -> dict:
    """Compute intelligence coverage at the artifact summary level.

    Returns a breakdown of signal origin (generation_method) and direction
    distribution — a summary-level view of intelligence coverage before
    per-position matching.
    """
    total = len(signals)
    by_gen: dict[str, int] = {}
    by_direction: dict[str, int] = {}
    urgency_count: dict[str, int] = {}
    for s in signals:
        gm = s.get("generation_method", "rule_fallback")
        by_gen[gm] = by_gen.get(gm, 0) + 1
        d = s.get("direction", "watch")
        by_direction[d] = by_direction.get(d, 0) + 1
        u = s.get("urgency", "medium")
        urgency_count[u] = urgency_count.get(u, 0) + 1
    return {
        "total": total,
        "by_generation_method": by_gen,
        "by_direction": by_direction,
        "by_urgency": urgency_count,
    }


def _intelligence_priority(signals: list[dict]) -> str:
    if any(s.get("urgency") in ("critical", "high") for s in signals):
        return "high"
    if signals:
        return "normal"
    return "low"


def _risk_appetite_from_macro(macro: dict) -> str:
    vix = macro.get("vix")
    if vix is None:
        return "unknown"
    if vix > 25:
        return "risk_off"
    if vix < 15:
        return "risk_on"
    return "neutral"


def _top_move(quotes: dict) -> str:
    moves = []
    for code, quote in quotes.items():
        if isinstance(quote, dict) and quote.get("pct_change") is not None:
            moves.append((code, quote["pct_change"]))
    if not moves:
        return "none"
    moves.sort(key=lambda x: abs(x[1]), reverse=True)
    code, pct = moves[0]
    return f"{code}: {pct:+.2f}%"


def _signal_to_action_review(signal: dict) -> dict:
    return {
        "symbol": signal.get("symbol", ""),
        "name": signal.get("name", ""),
        "signal": signal.get("direction", "watch"),
        "action_hint": signal.get("rationale", ""),
        "scope": "global",
        "source": "intelligence",
        "urgency": signal.get("urgency", "medium"),
    }


def _build_persona() -> dict:
    """从 computed_profile 构建风格化 persona。"""
    from pathlib import Path as _Path
    local = _Path(__file__).resolve().parent.parent.parent / ".local"
    computed = load_computed(local / "computed_profile.json")

    base = {
        "role": "你是用户的私人投资分析师，不是市场播报员。你了解用户的每一笔持仓、成本和偏好。",
        "principles": [
            "用第一人称对用户说话",
            "决策给选项，不给命令",
            "没变化就直说——不要为了凑字数编内容",
            "引用具体数字",
            "主动提醒风险",
            "引用上次建议（如果相关）",
        ],
    }

    if computed and computed.get("style_summary"):
        summary = computed["style_summary"]
        params = computed.get("params", {})
        style_rules = [f"用户交易风格: {summary}"]

        # 根据参数生成风格化指令
        stop = params.get("stop_loss_pct")
        if stop is not None and stop < -15:
            style_rules.append(f"用户容忍较大回撤（止损线 {stop}%），不必在小幅浮亏时催促止损")
        elif stop is not None and stop > -8:
            style_rules.append(f"用户偏好严格风控（止损线 {stop}%），浮亏接近止损线时主动提醒")

        if not params.get("chase_enabled", False):
            style_rules.append("用户不追涨——轮动排名靠前但已拉升的标的只做参考，不建议追高买入")

        ladder = params.get("add_ladder", [0.02])
        if len(ladder) > 1 or ladder[0] > 0.03:
            style_rules.append(f"用户偏好分批建仓（{len(ladder)}档），加仓建议分档给出")

        tp = params.get("take_profit_levels")
        if tp:
            max_tp = max(lvl[0] for lvl in tp)
            if max_tp > 40:
                style_rules.append("用户偏好让利润奔跑，止盈提醒偏保守、偏延迟")
            elif max_tp < 20:
                style_rules.append("用户偏好落袋为安，止盈提醒偏积极")

        base["principles"] = style_rules + base["principles"]

    return base


def build_agent_task(session: ScheduledSession) -> dict:
    """构建自包含的 Agent 任务说明书。

    产出的 agent_task 对象包含 Agent 所需的全部指令，
    不依赖任何外部 prompt。任何 Agent 读取 JSON 后，
    严格按此任务说明书执行即可生成完整分析推送。
    """
    # ── 情报 brief 读取指令（所有 session 类型共用） ──
    _intel_brief_task = (
        "【情报要点】读取 .local/intelligence/latest_brief.json"
        " — 提取 2-3 条与用户持仓最相关的事件，每条标注来源标题和发布时间。"
        "结合 clusters.portfolio_relevance 字段判断哪些事件影响用户组合"
    )

    must_answer_by_intent = {
        "pre_open_plan": [
            "今天重点盯哪些已有持仓和触发器",
            _intel_brief_task,
            "【前瞻展望】综合分析以下四层信息，给出未来1-2周最值得关注的板块和方向（不少于3个）：",
            "  (a) intelligence_digest.top_clusters — 情报管道识别到的事件主题和影响",
            "  (b) intelligence_digest.top_signals — 情报管道给出的方向性信号",
            "  (c) rotation_leaders — 轮动排名领涨的板块和标的",
            "  (d) exposure_summary.top — 组合当前暴露分布和潜在缺口",
            "每个方向写明：依据（引用具体数据）→ 对应标的 → 与现有组合的关系",
            "哪些候选方向只适合观察,不能追",
            "数据质量是否足以形成盘前计划",
            "【资金部署】读取 mandatory_blocks.capital_facts 和 capital_allocation 全文：基于约束偏离、净可动用金额、回收预估、加仓候选和轮动参考，结合 persona 风格和 intelligence_digest 方向，给出'今天钱往哪放'的具体判断——是保持现金、等回收后补特定板块，还是有可立即执行的加仓方向。必须引用 capital_facts 中的具体数字，不笼统说'关注'或'观望'",
            "【风险边界】读取 risk_assessment：如果 level 不是 normal，显式报告。如果 suspend_accumulation=true，盘前计划必须标注'暂停开新仓'",
        ],
        "open_watch": [
            "开盘后已有持仓是否出现异常跳空、破位或过热",
            _intel_brief_task,
            "是否需要等待收盘确认",
            "数据质量是否足以支持盘中判断",
        ],
        "pre_close_decision": [
            "收盘前已有持仓是否需要动",
            "哪些触发器已经触发或接近触发",
            _intel_brief_task,
            "【前瞻展望】综合分析 intelligence_digest + rotation_leaders + exposure_summary，给出未来1-2周最值得关注的板块和方向（不少于3个），每个方向写明依据和对应标的",
            "是否有不应追高或不应补弱的标的",
            "【资金部署】读取 mandatory_blocks.capital_facts 和 capital_allocation 全文：基于约束偏离、净可动用金额、回收预估、加仓候选和轮动参考，结合 persona 风格和 intelligence_digest 方向，给出'今天钱往哪放'的具体判断——是保持现金、等回收后补特定板块，还是有可立即执行的加仓方向。必须引用 capital_facts 中的具体数字，不笼统说'关注'或'观望'",
        ],
        "after_close_review": [
            "今天触发器和持仓事实如何复盘",
            _intel_brief_task,
            "明天开盘前重点看什么",
            "【资金部署】读取 mandatory_blocks.capital_facts 和 capital_allocation 全文：基于约束偏离、净可动用金额、回收预估、加仓候选和轮动参考，结合 persona 风格和 intelligence_digest 方向，给出'今天钱往哪放'的具体判断——是保持现金、等回收后补特定板块，还是有可立即执行的加仓方向。必须引用 capital_facts 中的具体数字，不笼统说'关注'或'观望'",
            "是否需要让用户补录数据或确认长期记录",
            "【风险边界】读取 risk_assessment 和 mandatory_blocks.risk_boundary：如果 mandatory_blocks.risk_boundary 存在，必须原样嵌入报告末尾（用**加粗**标题，- 列表）。不得改写、省略或合并到其他段。情报管道中的地缘政治事件，如果触发了 risk_assessment，必须在风险边界段报告",
        ],
        "morning_close_check": [
            "上午走势小结：哪些持仓触发了信号或接近触发",
            _intel_brief_task,
            "午间休市前是否需要提前减仓或止盈",
            "午后重点关注哪些持仓和方向",
            "数据质量是否足以支持午间判断",
        ],
        "afternoon_open_check": [
            "午间有无重大消息改变上午盘前计划",
            "下午开盘是否有跳空或异常波动",
            _intel_brief_task,
            "是否需要在收盘前提前行动",
            "上午未触发但下午可能触发的持仓",
        ],
        "mid_session_check": [
            "盘中波动是否改变已有计划",
            _intel_brief_task,
            "是否存在 critical 风险",
            "是否应保持只生成不推送",
        ],
    }

    return {
        "task_version": 4,
        "language": "zh-CN",
        "audience": "single_user",
        "session_intent": session.intent,
        "primary_market": session.primary_market,

        # ── Agent 必须回答的问题 ──
        "must_answer": must_answer_by_intent.get(
            session.intent,
            ["已有持仓是否需要动", "数据质量是否足以支持动作"],
        ),

        # ── 硬性禁止（违反 = 错误） ──
        "must_not_do": [
            "不得承诺收益",
            "不得忽略 data_quality",
            "不得建议动用 rebalance_eligible=false 的资产",
            "不得把代理 ETF 价格触发器套到场外基金",
            "不得自动保存建议、执行或预测",
            # 数据忠实性
            "market_state.risk_appetite 写什么报什么，严禁自编'避险情绪升温''风险偏好回升'",
            "risk_appetite 缺失时写'系统未判断风险偏好'，不得填空",
            "valuation_input.method=manual_amount 的持仓必须标注'手工估值（非实时）'，严禁说'无浮动'",
            # 触发器完整性
            "action_cards 中 signal=stop_loss 的持仓必须出现在推送最前，标注'硬止损触发'",
            "loss_level=severe 的持仓必须标注'严重亏损阈值'",
            "触发器必须按严重度从高到低列出，不得只报轻的漏重的",
            # 飞书格式（违反会导致整条消息变成纯文本）"
            "严禁使用 # 号标题（用**加粗**代替）",
            "严禁使用 | 表格",
            "严禁使用 ``` 代码块（用`行内代码`代替）",
            "严禁使用 > 引用",
            "严禁使用 --- 分隔线",
            "严禁使用 - [ ] 任务列表",
            "严禁使用任何 HTML 标签",
            # 情报来源约束
            "情报引用必须标注来源标题和发布时间，严禁自编'市场传闻''据悉''有消息称'",
            "情报分析只能基于 intelligence_digest 和 .local/intelligence/latest_brief.json 中已有的事实数据",
            # 数据缺口时效
            "数据缺口首次出现时标注'新增'；连续出现时注明'持续N次'；超过7天的缺口降级为脚注，超过30天不再显示",
            "场外基金手工估值等结构性缺口只在一处集中说明。市场宏观数据(VIX/美债/汇率)为日频，官方统计(CPI/失业率)为月频——引用时区分标注各自的 as_of，不混为一谈",
            # 资产分层报告（硬性）
            "action_cards 的 routing 字段决定报告层级: full→可操作, fund→高门槛(T+2/更高阈值), precious→有价差, info_only→只读, skip→跳过",
            "routing=fund 的资产可以报告止盈/止损建议，但必须标注'T+2到账'、'以收盘净值为准'、'登录平台操作'，不得用'一键操作'语气",
            "routing=precious 的资产可以报告止盈/止损建议，但必须标注'有买卖价差'、'登录平台确认后操作'",
            "routing=info_only 的资产的唯一合法操作是'持有，有开放期限制'，不得建议减仓/加仓",
            "routing=full 的资产正常报告操作建议",
            "routing=skip 的资产不在报告中出现",
            # 扫描池完整性（硬性）
            "action_signals 中 signal=accumulate_candidate 的标的必须全部在前瞻展望段列出，标注当前价格和触发理由",
            "action_signals 中 signal=wait_for_pullback 的标的至少列出前 3 个",
            "accumulate_candidate 必须按综合得分分三档展示：≥0.4 强推荐（最多3个）、0.2-0.4 可关注（最多3个）、<0.2 弱信号（仅列名），不得平铺10个",
            "若 capital_allocation.constraint_alerts 中某大类超限，标注'等减仓后再考虑'；低于下限则标注'优先填补缺口'",
            "不得只挑自己喜欢的板块展示——必须按系统给出的信号原样报告",
            # 分歧处理
            "若 action_cards 中任一卡的 dissent 非空，必须在持仓动作段单独讨论分歧场景：说明哪个信号源（技术/情报/因子）与最终结论方向相反、可能的影响、用户应关注什么信号来裁决",
        ],

        # ── 数据字段引用指南 ──
        "data_reference": {
            "持仓动作": "action_cards[] — 逐持仓的动作信号。routing: full=可操作, fund=场外基金(T+2/高阈值), precious=贵金属(有价差), info_only=银行理财(只读), skip=跳过。所有非full持仓标注操作约束",
            "驱动向量": "action_cards 中每卡的 drivers[] — 技术面/情报/因子各自的独立信号，dissent=非空表示有信号源与最终结论冲突，confidence=high/medium/low",
            "方向信号": "action_signals — items[] 全量扫描池(61个标的)及 counts 汇总；ranked 是排序结果。accumulate_candidate=可加仓, wait_for_pullback=等回调, neutral_hold=观望, avoid_catching_falling_knife=勿抄底",
            "风险仪表盘": "portfolio_risk.scenario — global_risk_off / china_shock / inflation_commodity 三个多因子情景",
            "持仓事实": "position_reviews[] — 逐持仓估值、盈亏、session_facts（含 severe_loss 标注）",
            "情报": "intelligence_digest — top_clusters（事件聚类）、top_signals（方向信号）",
            "情报brief": ".local/intelligence/latest_brief.json — clusters（含portfolio_relevance）、signals、macro（每小时更新）",
            "事件日历": "upcoming_events[] — 未来72小时内的重大数据发布时间（CPI/FOMC/NFP等），每个事件标注距现在的剩余小时数。如果72小时内有事件，风险边界段必须显式标注'X小时后CPI发布，当前通胀交易逻辑可能单日逆转'",
            "轮动": "rotation_leaders — 轮动排名领涨的板块和标的",
            "组合": "exposure_summary.top — 组合暴露分布和潜在缺口",
            "资金部署": "capital_facts(mandatory_blocks) + capital_allocation — constraint_alerts(大类超限)、conflicts(信号方向冲突)、reduce_items(减仓回收)、add_candidates(加仓优先级)、net_deployable_cny(净可动用)、idle_cash_suggestions(轮动参考)。capital_facts 是系统计算的确定事实，资金部署建议必须在报告中独立成段",
            "风险等级": "risk_assessment — level(hedge/reduce/watch/normal)、triggers(触发条件)、recommended_actions(建议操作)、suspend_accumulation(是否暂停加仓)、cash_target_pct(现金目标比例)",
            "必修文本": "mandatory_blocks — risk_boundary(风险边界)、constraint_alerts(约束偏离)和capital_facts(资金状况事实)是系统计算的确定事实，必须原样嵌入报告末尾，不得改写或省略。基于 capital_facts 在报告前半部独立成段给出资金部署建议",
        },

        # ── 输出格式 ──
        "output_structure": {
            "max_words": 1200,
            "platform": "feishu",
            "format_rules": "仅使用**加粗**、`行内代码`、- 列表、[链接](url) 四种格式。用空行分隔段落。禁用 # | ``` > --- -[ ] HTML。",
            "sections": [
                {
                    "name": "标题",
                    "content": "{session_display_name} · {market_date}",
                },
                {
                    "name": "一句话执行结论",
                    "content": "以 action_cards 的止损/止盈信号为最高优先级，给出当前最关键的 1 个动作判断",
                },
                {
                    "name": "风险边界",
                    "content": "读取 risk_assessment。如果 level!=normal，必须显式报告风险等级、触发原因和系统建议操作。如果 suspend_accumulation=true，必须标注'暂停加仓'。如果 72 小时内有 CPI/FOMC/NFP，显式警告'X小时后CPI发布，当前交易逻辑可能单日逆转'",
                },
                {
                    "name": "持仓动作",
                    "content": "列出所有触发动作的持仓，按优先级: stop_loss > take_profit > reduce > add。routing=full 的资产正常写操作建议；routing=fund 的资产标注'T+2到账，以收盘净值为准，登录平台操作'；routing=precious 的资产标注'有买卖价差，登录平台确认后操作'。routing=info_only/skip 不写入此段。",
                },
                {
                    "name": "非可操作持仓",
                    "content": "列出 routing=info_only 的持仓（仅有银行理财等），只报告状态不输出操作。routing=skip 的不出现。",
                },
                {
                    "name": "资金部署",
                    "content": "基于 mandatory_blocks.capital_facts 的事实（约束偏离、净可动用、回收预估、轮动参考），生成资金部署判断：现金层建议（持有/部署/等回收）、可操作方向（按约束优先级 + 轮动 + persona 过滤）、需等待的条件（什么信号出现后才能动）。必须引用具体数字，不笼统说'关注'",
                },
                {
                    "name": "情报要点",
                    "content": "引用 .local/intelligence/latest_brief.json 中 2-3 条与用户持仓最相关的事件，每条标注来源和发布时间",
                },
                {
                    "name": "前瞻展望",
                    "content": "第一部分：扫描池分级展示。accumulate_candidate 按综合得分分三档：≥0.4 强推荐（可分批布局）| 0.2-0.4 可关注（回踩再确认）| <0.2 弱信号（仅供参考）。每档不超过 3 个，超出部分只列名字不加详述。wait_for_pullback 只列出前 3 个。第二部分：结合 capital_facts.constraint_alerts + 资金部署段的结论——如果某大类超限标注'等减仓后再考虑'；低于下限标注'优先填补缺口'。第三部分：综合 intelligence_digest + exposure_summary 的方向判断",
                },
                {
                    "name": "风险与数据边界",
                    "content": "引用 portfolio_risk.scenario 的多因子情景；标注 data_quality 的缺口（stale 行情、手工估值、单源风险）",
                },
                {
                    "name": "尾部",
                    "content": "以上仅为数据摘要，不构成投资建议",
                },
            ],
        },

        # ── 分析师人格（塑造输出风格，由 computed_profile 驱动） ──
        "persona": _build_persona(),

        # ── 自适应输出（根据内容调整篇幅） ──
        "adaptability": {
            "silent_when_nothing": "如果 action_cards 全部是 hold + 无触发器 fired + 无情报变化 → 输出可缩至 3-5 句话，不强制展开所有六节",
            "loud_when_critical": "如果有 stop_loss 或 loss_level=severe 或 intelligence urgency=critical → 把这些放在最前面，篇幅可扩展到 1500 字",
            "forward_outlook_minimum": "即使当日无持仓动作，前瞻展望段仍必须给出至少 1-2 个方向（基于 intelligence_digest + rotation_leaders）",
        },

        # ── 最终指令（一行总结，Agent 也可只读此字段快速理解任务） ──
        "final_analysis_instructions": (
            "阅读 persona 理解你的角色。根据 adaptability 决定输出篇幅。"
            "输出通常含六节（标题→执行结论→持仓动作→前瞻展望→风险边界→免责），"
            "但当无变化时可大幅压缩。严格遵守 must_not_do。数据引用以 data_reference 为准。"
        ),
    }


def format_run_markdown(run: dict) -> str:
    lines = [
        f"# {run['session']} {run['market_date']}",
        "",
        f"- run_id: `{run['run_id']}`",
        f"- status: `{run['status']}`",
        f"- priority: `{run['session_summary']['priority']}`",
        f"- push_policy: `{run['session_summary']['push_policy']}`",
        f"- headline: {run['session_summary']['headline']}",
        "",
        "## Market Focus",
        f"- primary_market: `{run['portfolio_scope'].get('primary_market')}`",
        f"- included_markets: {run['portfolio_scope'].get('included_markets', [])}",
        "",
        "## Market State Summary",
    ]
    summary = run.get("session_summary", {}).get("market_state_summary") or {}
    if summary:
        lines.append(f"- 风险偏好: {summary.get('risk_appetite', 'unknown')}")
        lines.append(f"- {summary.get('vix', 'VIX unknown')}")
        lines.append(f"- 龙头动作: {summary.get('top_move', 'none')}")
    else:
        lines.append("- 本轮无市场状态摘要")
    lines.append("")
    lines.append("## Agent Task")
    for item in run.get("agent_task", {}).get("must_answer", []):
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Action Signals")
    for item in run.get("action_signal_reviews", [])[:10]:
        scope = "*本地" if item.get("scope") == "primary" else "跨市场"
        lines.append(
            f"- {scope} {item.get('symbol')} {item.get('signal')}: {item.get('action_hint')}"
        )
    lines.append("")
    lines.append("## Trigger Reviews")
    trigger_reviews = run.get("trigger_reviews", [])
    if trigger_reviews:
        for item in trigger_reviews[:12]:
            status = item.get("status", "unknown")
            target = item.get("instrument", item.get("target", "unknown"))
            ttype = item.get("type", "unknown")
            level = item.get("level")
            lines.append(f"- {target} {ttype}={level} status={status}")
    else:
        lines.append("- 本轮没有已存档建议的触发器可核对")
    lines.append("")

    lines.append("## Position Reviews")
    for item in run.get("position_reviews", [])[:12]:
        pnl = item.get("pnl", {}).get("pnl_pct")
        pnl_text = f", pnl={pnl:+.2f}%" if isinstance(pnl, (int, float)) else ""
        level = item.get("loss_level", "normal")
        level_tag = {
            "severe": " [SEVERE_LOSS]",
            "high": " [HIGH_LOSS]",
            "warn": " [WARN_LOSS]",
        }.get(level, "")
        lines.append(
            f"- {item.get('display_name')} ({item.get('instrument_key') or 'manual'})"
            f"{pnl_text}{level_tag}"
        )
    # ── Mandatory blocks ──
    mb = run.get("mandatory_blocks") or {}
    if mb.get("risk_boundary"):
        lines.append("")
        lines.append(mb["risk_boundary"])
    if mb.get("constraint_alerts"):
        lines.append("")
        lines.append(mb["constraint_alerts"])
    if mb.get("shadow_account"):
        lines.append("")
        lines.append(mb["shadow_account"])
    if mb.get("hypothesis_tracker"):
        lines.append("")
        lines.append(mb["hypothesis_tracker"])
    return "\n".join(lines)


def resolve_artifact_dir(config: dict, *, repo_root: Path) -> Path:
    raw = str(config.get("artifact_dir") or ".local/scheduled_runs")
    path = Path(raw)
    if path.is_absolute():
        return path
    return repo_root / path


def _parse_hhmm(value: str) -> tuple[int, int]:
    hour, sep, minute = value.partition(":")
    if not sep:
        raise ValueError(f"Invalid session time: {value}")
    return int(hour), int(minute)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _skipped_result(status: str, occurrence: SessionOccurrence, now: datetime) -> dict:
    return {
        "success": True,
        "status": status,
        "run_id": occurrence.run_id,
        "generated_at": _iso_utc(now),
        "session": occurrence.session.id,
        "market": occurrence.session.market,
        "market_date": occurrence.market_date.isoformat(),
        "scheduled_for": occurrence.scheduled_for.isoformat(),
    }


def _flatten_trigger_reviews(recent_advice: list[dict]) -> list[dict]:
    reviews = []
    for advice in recent_advice:
        for item in advice.get("trigger_review") or []:
            review = dict(item)
            review.setdefault("advice_id", advice.get("id") or advice.get("created_at"))
            review.setdefault("advice_summary", advice.get("summary"))
            reviews.append(review)
    return reviews


def _build_position_reviews(
    position_valuations: list[dict], *, recent_advice: list[dict] | None = None
) -> list[dict]:
    LOSS_WARN_THRESHOLD = -8.0
    LOSS_HIGH_THRESHOLD = -15.0
    LOSS_SEVERE_THRESHOLD = -25.0
    position_triggers = _collect_position_triggers(recent_advice or [])
    reviews = []
    for item in position_valuations:
        liquidity = item.get("liquidity") or {}
        flags = list(item.get("flags") or [])
        facts = _position_facts(item)
        pnl_pct = item.get("pnl_pct")
        loss_level = "normal"
        if isinstance(pnl_pct, (int, float)):
            if pnl_pct <= LOSS_SEVERE_THRESHOLD:
                loss_level = "severe"
            elif pnl_pct <= LOSS_HIGH_THRESHOLD:
                loss_level = "high"
            elif pnl_pct <= LOSS_WARN_THRESHOLD:
                loss_level = "warn"
        if loss_level == "warn":
            facts.append(f"浮亏 {pnl_pct:.2f}% 已超过 {LOSS_WARN_THRESHOLD}% 警示阈值")
        elif loss_level == "high":
            facts.append(f"浮亏 {pnl_pct:.2f}% 已超过 {LOSS_HIGH_THRESHOLD}% 高亏损阈值")
        elif loss_level == "severe":
            facts.append(
                f"浮亏 {pnl_pct:.2f}% 已超过 {LOSS_SEVERE_THRESHOLD}% 严重阈值,需要专项复盘"
            )
        reviews.append(
            {
                "position_id": item.get("position_id"),
                "display_name": item.get("display_name"),
                "instrument_key": item.get("instrument_key"),
                "account_id": item.get("account_id"),
                "advice_granularity": item.get("advice_granularity"),
                "valuation": {
                    "market_value_cny": item.get("market_value_cny"),
                    "price": item.get("price"),
                    "price_as_of": item.get("as_of"),
                    "price_source": item.get("price_source"),
                    "stale": "stale_quote" in flags or "stale_manual" in flags,
                },
                "pnl": {
                    "cost_amount": item.get("cost_amount"),
                    "pnl_pct": item.get("pnl_pct"),
                    "unrealized_pnl_cny": item.get("unrealized_pnl_cny"),
                },
                "liquidity": {
                    "rebalance_eligible": liquidity.get("rebalance_eligible"),
                    "tradable": liquidity.get("tradable"),
                    "tier": liquidity.get("tier"),
                },
                "proxy": item.get("proxy"),
                "flags": flags,
                "missing_fields": item.get("missing_fields") or [],
                "session_facts": facts,
                "loss_level": loss_level,
                "trigger_reviews": position_triggers.get(item.get("instrument_key") or "", []),
            }
        )
    reviews.sort(
        key=lambda r: (
            {"severe": 0, "high": 1, "warn": 2, "normal": 3}.get(r.get("loss_level", "normal"), 3),
            r.get("display_name") or "",
        )
    )
    return reviews


def _collect_position_triggers(recent_advice: list[dict]) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for advice in recent_advice:
        for trigger in advice.get("trigger_review") or []:
            key = str(trigger.get("instrument", ""))
            if not key:
                continue
            summary = {
                "advice_id": advice.get("id") or advice.get("created_at"),
                "advice_summary": advice.get("summary"),
                "type": trigger.get("type"),
                "level": trigger.get("level"),
                "action": trigger.get("action"),
                "status": trigger.get("status"),
                "observed": trigger.get("observed"),
            }
            result.setdefault(key, []).append(summary)
    return result





def _build_action_cards(
    position_valuations: list[dict],
    intelligence_signals: Optional[dict[str, dict]] = None,
    *,
    market_state: Optional[dict] = None,
    event_clusters: Optional[list[dict]] = None,
    data_freshness: str = "fresh",
    rotation_ranks: Optional[dict[str, int]] = None,
    constraints: Optional[dict] = None,
    portfolio_mapping: Optional[dict] = None,
    quant_config: Optional[dict] = None,
) -> list[dict]:
    """为每个持仓计算量化行动卡 — 通过 finalize_decision 一次性裁决。

    优先级链：stop_loss → constraint_override → intel_override →
              macro/event overlay → routing_downgrade → data_freshness
    """
    from stocks.engine.quant_action import QuantActionEngine

    ratios = (portfolio_mapping or {}).get("ratios", {}) if portfolio_mapping else {}
    cards = []

    for item in position_valuations:
        pid = item.get("position_id", "")
        valuation_method = item.get("valuation_method", "")

        # 跳过已清仓持仓（finalize_decision 也会处理，但这里提前过滤减少调用）
        mv = item.get("market_value_cny")
        if mv is None or mv <= 0:
            qty = (item.get("holding") or {}).get("quantity", 0) if item.get("holding") else 0
            classification = item.get("classification") or {}
            cards.append({
                "position_id": pid,
                "display_name": item.get("display_name", ""),
                "instrument_key": item.get("instrument_key", ""),
                "product_type": classification.get("product_type", ""),
                "routing": "skip",
                "account_type": (item.get("account") or {}).get("type", ""),
                "signal": "hold",
                "action": "已清仓，无持仓" if qty == 0 else "持仓为零，无需操作",
                "ratio": 0.0, "facts": [], "stop_price": None, "target_prices": [],
                "position_limit_pct": 5.0, "current_weight_pct": 0.0,
                "risk_to_stop_pct": None, "risk_amount_cny": None,
                "intelligence_conflict": "none",
                "drivers": [], "dissent": None, "confidence": "high",
                "raw_signal": "", "raw_ratio": 0.0, "raw_action": "",
                "evidence_status": "ok",
            })
            continue

        # ── 技术面信号 ──
        indicators = item.get("indicators") or {}
        if valuation_method == "fund_nav":
            indicators = {}  # 代理价格 ≠ 基金净值
        engine = QuantActionEngine(indicators, config=quant_config)
        tech = engine.review_position(
            position_id=pid,
            price=item.get("price"),
            cost=item.get("cost_amount"),
            pnl_pct=item.get("pnl_pct"),
            one_day_change_pct=item.get("one_day_change_pct"),
            current_weight_pct=item.get("portfolio_weight"),
            quantity=(item.get("holding") or {}).get("quantity") if item.get("holding") else None,
        )

        # ── 逐持仓 freshness（从 evidence 取，不再使用全局单一值）──
        position_freshness = (
            item.get("evidence", {}).get("price_freshness") or data_freshness
        )
        # ── 确定性最终决策 ──
        rotation_symbol = item.get("instrument_key") or ""
        decision = finalize_decision(
            tech=tech,
            position=item,
            market_state=market_state,
            event_clusters=event_clusters,
            intelligence_signals=intelligence_signals,
            rotation_ranks=rotation_ranks,
            rotation_symbol=rotation_symbol,
            data_freshness=position_freshness,
            constraints=constraints,
            portfolio_ratios=ratios,
        )

        # ── 账本元数据（LLM 需要区分场内/场外/银行理财）──
        classification = item.get("classification") or {}
        product_type = classification.get("product_type", "")
        account_type = (item.get("account") or {}).get("type", "")
        # 路由推导（与 _PRODUCT_TYPE_RULES 同步）
        # full / fund / precious / info_only / skip
        _routing_map = {
            "qdii_fund": "fund", "feeder_fund": "fund",
            "mixed_fund": "fund", "fixed_income_plus_fund": "fund",
            "precious_metal_account": "precious",
            "bank_wealth_management": "info_only",
            "money_market_fund": "skip", "cash": "skip", "cash_equivalent": "skip",
            "insurance_policy": "skip",
        }
        routing = _routing_map.get(product_type, "")
        if not routing:
            # ── 回退：product_type 未标注时，从 account_id 推导 ──
            aid = (item.get("account") or {}).get("account_id", "") or account_type or ""
            if "alipay" in aid:
                routing = "fund"         # 支付宝持仓默认场外基金
            elif "ccb" in aid:
                # 建行持仓：按 position_id 精修
                pid_lower = (item.get("position_id") or "").lower()
                if "gold" in pid_lower or "precious" in pid_lower:
                    routing = "precious"     # 贵金属
                elif "wmp" in pid_lower or "wealth" in pid_lower:
                    routing = "info_only"    # 银行理财
                elif "mm" in pid_lower or "cash" in pid_lower:
                    routing = "skip"         # 货基/现金
                else:
                    routing = "fund"
            elif "boc" in aid:
                routing = "info_only"    # 中银保险默认只读
            else:
                routing = "full"

        cards.append({
            "position_id": decision.position_id,
            "display_name": item.get("display_name", ""),
            "instrument_key": item.get("instrument_key", ""),
            "product_type": product_type,
            "routing": routing,
            "account_type": account_type,
            "signal": decision.signal,
            "action": decision.action,
            "ratio": decision.ratio,
            "facts": decision.facts,
            "stop_price": decision.stop_price,
            "target_prices": decision.target_prices,
            "position_limit_pct": decision.position_limit_pct,
            "current_weight_pct": decision.current_weight_pct,
            "risk_to_stop_pct": decision.risk_to_stop_pct,
            "risk_amount_cny": decision.risk_amount_cny,
            "intelligence_conflict": decision.intelligence_conflict,
            "drivers": decision.drivers,
            "dissent": decision.dissent,
            "confidence": decision.confidence,
            # ── Task 2: 数据异常守门 ──
            "raw_signal": getattr(decision, 'raw_signal', ''),
            "raw_ratio": getattr(decision, 'raw_ratio', 0.0),
            "raw_action": getattr(decision, 'raw_action', ''),
            "evidence_status": getattr(decision, 'evidence_status', 'ok'),
        })

    return cards


def _build_capital_allocation(
    action_cards: list[dict],
    position_valuations: list[dict],
    portfolio_mapping: dict,
    liquidity_summary: dict,
    *,
    constraints: Optional[dict] = None,
    rotation_ranks: Optional[dict[str, int]] = None,
    rotation_leaders: Optional[list[dict]] = None,
) -> dict:
    """组合级资金分配提示 — 交易分析师视角。

    输出：约束告警 → 冲突检测 → 减仓回收 → 加仓排序(含约束惩罚) →
    闲置资金建议(轮动候选) → 净可动用 → 优先摘要。
    """
    total_value = sum(
        item.get("market_value_cny") or 0.0 for item in position_valuations
    )
    if total_value <= 0:
        return {
            "total_value_cny": 0, "constraint_alerts": [], "conflicts": [],
            "reduce_proceeds_cny": 0, "add_candidates": [],
            "idle_cash_suggestions": [], "net_deployable_cny": 0,
            "priority_summary": "无有效持仓数据",
        }

    constraints = constraints or {}
    rotation_ranks = rotation_ranks or {}
    rotation_leaders = rotation_leaders or []

    # ── 1. 约束检查 ──
    constraint_alerts = []
    ratios = portfolio_mapping.get("ratios", {})
    for bucket_name, rule in constraints.items():
        if not isinstance(rule, dict):
            continue
        min_pct = rule.get("min")
        max_pct = rule.get("max")
        actual = ratios.get(bucket_name)
        if actual is None:
            continue
        actual_pct = actual * 100
        if max_pct is not None and actual_pct > max_pct * 100:
            constraint_alerts.append({
                "bucket": bucket_name, "severity": "breach",
                "actual_pct": round(actual_pct, 1),
                "limit_pct": round(max_pct * 100, 1),
                "direction": "reduce",
                "message": f"{bucket_name} 占比 {actual_pct:.1f}%，超出上限 {max_pct*100:.0f}%",
            })
        elif max_pct is not None and actual_pct > max_pct * 100 * 0.85:
            constraint_alerts.append({
                "bucket": bucket_name, "severity": "near",
                "actual_pct": round(actual_pct, 1),
                "limit_pct": round(max_pct * 100, 1),
                "direction": "caution",
                "message": f"{bucket_name} 占比 {actual_pct:.1f}%，逼近上限 {max_pct*100:.0f}%",
            })
        elif min_pct is not None and actual_pct < min_pct * 100:
            constraint_alerts.append({
                "bucket": bucket_name, "severity": "breach",
                "actual_pct": round(actual_pct, 1),
                "limit_pct": round(min_pct * 100, 1),
                "direction": "increase",
                "message": f"{bucket_name} 占比 {actual_pct:.1f}%，低于下限 {min_pct*100:.0f}%",
            })

    # ── 约束方向 → 超限大类集合 ──
    over_limit_buckets = {a["bucket"] for a in constraint_alerts if a["direction"] == "reduce"}
    under_limit_buckets = {a["bucket"] for a in constraint_alerts if a["direction"] == "increase"}

    # ── 2. 冲突检测：约束方向 vs 持仓信号方向 ──
    conflicts = []
    for card in action_cards:
        sig = card.get("signal", "hold")
        if sig in ("hold", "wait"):
            continue
        card_dir = "reduce" if sig in ("reduce", "stop_loss", "take_profit") else "add"
        # 找到持仓的曝光标签
        pv = next((p for p in position_valuations if p.get("position_id") == card["position_id"]), {})
        klass = pv.get("classification", {})
        tags = klass.get("exposure_tags", [])
        # 找到标签对应的约束大类
        matched_buckets = set()
        for tag in tags:
            b = _TAG_TO_BUCKET.get(tag)
            if b:
                matched_buckets.add(b)
        # 检测冲突
        for b in matched_buckets:
            if b in under_limit_buckets and card_dir == "reduce":
                conflicts.append({
                    "position_id": card["position_id"],
                    "signal": sig,
                    "bucket": b,
                    "conflict": f"{b}不足应加仓，但该持仓信号为{sig}（减仓）",
                })
            if b in over_limit_buckets and card_dir == "add":
                conflicts.append({
                    "position_id": card["position_id"],
                    "signal": sig,
                    "bucket": b,
                    "conflict": f"{b}超限应减仓，但该持仓信号为{sig}（加仓）",
                })

    # ── 3. 减仓回收 ──
    reduce_proceeds = 0.0
    reduce_items = []
    for card in action_cards:
        if card["signal"] in ("reduce", "stop_loss", "take_profit") and card.get("ratio", 0) > 0:
            mv = 0.0
            for pv in position_valuations:
                if pv.get("position_id") == card["position_id"]:
                    mv = pv.get("market_value_cny") or 0.0
                    break
            proceeds = mv * abs(card["ratio"])
            reduce_proceeds += proceeds
            reduce_items.append({
                "position_id": card["position_id"],
                "signal": card["signal"],
                "ratio": card["ratio"],
                "market_value_cny": round(mv, 2),
                "proceeds_cny": round(proceeds, 2),
            })

    # ── 4. 可动用资金 ──
    liq_buckets = liquidity_summary.get("buckets", {})
    available_cash = (
        liq_buckets.get("cash_or_t0", {}).get("value_cny", 0)
        + liq_buckets.get("t1_t2", {}).get("value_cny", 0)
    )
    safety_buffer = total_value * 0.05
    net_deployable = max(0, available_cash + reduce_proceeds - safety_buffer)

    # ── 5. 加仓候选：约束感知排序 ──
    pid_to_inst = {}
    pid_to_tags = {}
    for pv in position_valuations:
        pid = pv.get("position_id", "")
        ik = pv.get("instrument_key", "")
        if ik:
            pid_to_inst[pid] = ik
        pid_to_tags[pid] = (pv.get("classification", {}).get("exposure_tags") or [])

    add_candidates = []
    for card in action_cards:
        if card["signal"] != "add":
            continue
        # Skip trivial allocations below ¥800 threshold
        mv = 0.0
        for pv in position_valuations:
            if pv.get("position_id") == card["position_id"]:
                mv = pv.get("market_value_cny") or 0.0
                break
        alloc_amount = mv * abs(card.get("ratio", 0))
        if alloc_amount < 800:
            card["facts"].append(f"分配金额 ¥{alloc_amount:.0f} 低于 ¥800 有效下限，仅作观察不执行")
            card["ratio"] = 0.0
            card["signal"] = "hold"
            card["action"] = "持仓观察（加仓信号有效但金额低于执行下限）"
        strength = abs(card.get("ratio", 0))
        tags = pid_to_tags.get(card["position_id"], [])
        # 约束惩罚/奖励：每个匹配的约束大类只生效一次（去重）
        constraint_penalty = 1.0
        penalty_reasons = []
        matched_buckets = set()
        for tag in tags:
            b = _TAG_TO_BUCKET.get(tag)
            if b and b not in matched_buckets:
                matched_buckets.add(b)
                if b in over_limit_buckets:
                    constraint_penalty *= 0.2
                    penalty_reasons.append(f"{b}超限")
                elif b in under_limit_buckets:
                    constraint_penalty *= 1.5
                    penalty_reasons.append(f"{b}不足")
        # 轮动加分
        ik = pid_to_inst.get(card["position_id"], "")
        rank = rotation_ranks.get(ik)
        rotation_bonus = 1.0
        if rank is not None:
            if rank <= 3:
                rotation_bonus = 2.0
            elif rank <= 5:
                rotation_bonus = 1.5
        score = strength * constraint_penalty * rotation_bonus
        add_candidates.append({
            "position_id": card["position_id"],
            "action": card["action"],
            "ratio": card["ratio"],
            "position_limit_pct": card.get("position_limit_pct", 5.0),
            "current_weight_pct": card.get("current_weight_pct") or 0,
            "priority_score": round(score, 4),
            "constraint_note": "；".join(penalty_reasons) if penalty_reasons else "无约束冲突",
            "facts": card.get("facts", [])[:2],
        })

    add_candidates.sort(key=lambda x: x["priority_score"], reverse=True)

    # ── 6. 闲置资金建议：轮动领涨候选 ──
    idle_cash_suggestions = []
    add_total_need = sum(
        abs(c["ratio"]) * total_value for c in add_candidates
    )
    idle_cash = net_deployable - add_total_need
    if idle_cash > total_value * 0.05 and rotation_leaders:
        for leader in rotation_leaders[:5]:
            sym = leader.get("symbol", "")
            rank_val = leader.get("rank")
            if rank_val and rank_val <= 5:
                idle_cash_suggestions.append({
                    "symbol": sym,
                    "name": leader.get("name", ""),
                    "rank": rank_val,
                    "r20": leader.get("r20"),
                    "category": leader.get("category", ""),
                    "rationale": f"轮动排名 #{rank_val}，20日收益 {leader.get('r20', '?')}%，可作为{under_limit_buckets or '权益'}配置候补",
                })

    # ── 7. 优先级摘要（含执行排序建议）──
    priority_summary = []
    if constraint_alerts:
        breaches = [a for a in constraint_alerts if a["severity"] == "breach"]
        if breaches:
            priority_summary.append(f"首要：解决 {', '.join(a['bucket'] for a in breaches)} 约束偏离")
    if conflicts:
        # 区分冲突类型，给出排序建议
        reduce_conflicts = [c for c in conflicts if c["signal"] in ("reduce", "stop_loss", "take_profit")]
        add_conflicts = [c for c in conflicts if c["signal"] == "add"]
        priority_summary.append(f"注意：{len(conflicts)} 个持仓信号与约束方向冲突")
        if reduce_conflicts and add_conflicts:
            priority_summary.append(
                "排序建议：先执行减仓信号回收资金→再按约束方向加仓→最后补齐不足大类"
            )
        elif reduce_conflicts and constraint_alerts:
            priority_summary.append(
                "排序建议：先执行减仓→回笼资金后等方向明确再补仓，不宜同时加减"
            )
    if add_candidates:
        top = add_candidates[0]
        penalty = "（约束受限）" if top["constraint_note"] != "无约束冲突" else ""
        priority_summary.append(f"优先加仓：{top['position_id']}{penalty}")
    if idle_cash_suggestions:
        priority_summary.append(f"闲置资金({idle_cash:,.0f}CNY)：关注轮动领涨 {idle_cash_suggestions[0]['symbol']}")

    return {
        "total_value_cny": round(total_value, 2),
        "constraint_alerts": constraint_alerts,
        "conflicts": conflicts,
        "reduce_items": reduce_items,
        "available_cash_cny": round(available_cash, 2),
        "reduce_proceeds_cny": round(reduce_proceeds, 2),
        "net_deployable_cny": round(net_deployable, 2),
        "add_candidates": add_candidates[:5],
        "idle_cash_suggestions": idle_cash_suggestions[:3],
        "priority_summary": "；".join(priority_summary) if priority_summary else "无优先动作",
    }


def _build_portfolio_risk_summary(
    position_valuations: list[dict], *, action_cards: list[dict],
) -> dict:
    """组合风险仪表盘。action_cards 作为唯一决策源——不再独立计算。"""
    total_value = sum(item.get("market_value_cny") or 0.0 for item in position_valuations)
    return compute_portfolio_risk(
        action_cards, total_value, position_valuations=position_valuations,
    )


def _position_facts(item: dict) -> list[str]:
    facts: list[str] = []
    pnl_pct = item.get("pnl_pct")
    if isinstance(pnl_pct, (int, float)):
        if pnl_pct >= 20:
            facts.append(f"浮盈 {pnl_pct:.2f}% 已超过 20%")
        elif pnl_pct <= -8:
            facts.append(f"浮亏 {pnl_pct:.2f}% 已超过 8%")
    # Intraday / recent drop check for profit-taking on winners
    one_day_change = item.get("one_day_change_pct")
    if isinstance(one_day_change, (int, float)) and one_day_change < -2.0:
        if isinstance(pnl_pct, (int, float)) and pnl_pct > 3.0:
            facts.append(f"单日下跌 {one_day_change:.2f}%，浮盈 {pnl_pct:.2f}%，建议考虑适度锁定浮盈")
        else:
            facts.append(f"单日下跌 {one_day_change:.2f}%，趋势转弱")
    if "missing_quote" in set(item.get("flags") or []):
        facts.append("缺最新行情,本轮估值有降级")
    if item.get("advice_granularity") == "fixed":
        facts.append("固定或锁定资产,不得给调仓动作")
    proxy = item.get("proxy") or {}
    if proxy.get("signal"):
        facts.append(f"代理 {proxy.get('instrument_key')} 信号为 {proxy.get('signal')}")
    return facts


def _build_action_signal_reviews(
    action_signals: dict,
    *,
    session: ScheduledSession,
    max_primary: int = 8,
    max_cross: int = 2,
    can_recommend_new: bool = True,
) -> list[dict]:
    items = []
    for item in action_signals.get("items") or []:
        signal = item.get("signal")
        if signal in {None, "neutral_hold", "no_data"}:
            continue
        if not can_recommend_new and signal in {"accumulate_candidate", "rotation_candidate"}:
            continue
        symbol = str(item.get("symbol") or "")
        scope = (
            "primary" if _symbol_matches_market(symbol, session.primary_market) else "cross_market"
        )
        items.append(
            {
                "symbol": symbol,
                "name": item.get("name"),
                "category": item.get("category"),
                "pool": item.get("pool"),
                "universe": item.get("universe"),
                "signal": signal,
                "action_hint": item.get("action_hint"),
                "reasons": item.get("reasons") or [],
                "scope": scope,
                "as_of": item.get("as_of"),
                "rank": item.get("rank"),
                "score": item.get("_score"),
            }
        )
    # 排序：同 scope 内按 rank（越小越前）→ 无 rank 按 symbol
    items.sort(key=lambda item: (
        item["scope"] != "primary",
        item.get("rank") is None,
        item.get("rank") or 999,
        item["symbol"],
    ))
    primary = [item for item in items if item["scope"] == "primary"][:max_primary]
    cross = [item for item in items if item["scope"] == "cross_market"][:max_cross]
    return primary + cross


def _symbol_matches_market(symbol: str, market: str) -> bool:
    if market == "cn":
        return symbol.startswith("a:")
    if market == "us":
        return symbol.startswith("us:")
    return False


def _priority(trigger_reviews: list[dict], position_reviews: list[dict]) -> str:
    if any(item.get("status") == "fired" for item in trigger_reviews):
        return "critical"
    for item in position_reviews:
        loss_level = item.get("loss_level", "normal")
        liquidity = item.get("liquidity") or {}
        if loss_level in {"high", "severe"} and liquidity.get("rebalance_eligible") is not False:
            return "critical"
    return "normal"


def _notification(
    *,
    session: ScheduledSession,
    priority: str,
    now: datetime,
    quiet_hours: dict,
) -> dict:
    quiet_blocked = _quiet_hours_blocked(now, quiet_hours) and priority != "critical"
    if session.push == "disabled":
        recommended = False
        policy = "generate_only"
    elif session.push == "critical_only" and priority != "critical":
        recommended = False
        policy = "generate_only"
    elif quiet_blocked:
        recommended = False
        policy = "defer_until_quiet_hours_end"
    elif session.push == "digest":
        recommended = True
        policy = "digest"
    else:
        recommended = True
        policy = "push_now"
    return {
        "recommended": recommended,
        "urgency": priority,
        "quiet_hours_blocked": quiet_blocked,
        "policy": policy,
    }


def _quiet_hours_blocked(now: datetime, quiet_hours: dict) -> bool:
    if not quiet_hours.get("enabled"):
        return False
    tz = ZoneInfo(str(quiet_hours.get("timezone") or "Asia/Shanghai"))
    local_now = now.astimezone(tz)
    start_h, start_m = _parse_hhmm(str(quiet_hours.get("start") or "00:00"))
    end_h, end_m = _parse_hhmm(str(quiet_hours.get("end") or "07:30"))
    start = time(start_h, start_m)
    end = time(end_h, end_m)
    current = local_now.time()
    if start <= end:
        return start <= current < end
    return current >= start or current < end


def _run_status(data_quality: dict) -> str:
    degraded_sections = []
    for key in ("asset_completeness", "quotes", "history_backfill", "rotation", "action_signals"):
        status = (data_quality.get(key) or {}).get("status")
        if status in {"blocked", "failed", "no_data"}:
            return "degraded"
        if status in {"degraded", "partial"}:
            degraded_sections.append(key)
    return "degraded" if degraded_sections else "ok"


def _status_reason(status: str, data_quality: dict) -> Optional[str]:
    if status == "ok":
        return None
    parts = []
    for key, value in data_quality.items():
        if isinstance(value, dict) and value.get("status") not in {None, "ok", "fresh"}:
            parts.append(f"{key}:{value.get('status')}")
    return "; ".join(parts) or "data_quality degraded"


def _portfolio_scope(context: dict, primary_market: str) -> dict:
    instrument_keys = [
        item.get("instrument_key")
        for item in context.get("position_valuations") or []
        if item.get("instrument_key")
    ]
    return {
        "account_ids": sorted(
            {
                item.get("account_id")
                for item in context.get("position_valuations") or []
                if item.get("account_id")
            }
        ),
        "position_ids": [
            item.get("position_id")
            for item in context.get("position_valuations") or []
            if item.get("position_id")
        ],
        "instrument_keys": instrument_keys,
        "included_markets": sorted({key.split(":", 1)[0] for key in instrument_keys}),
        "primary_market": primary_market,
    }


def _headline(session_id: str) -> str:
    return {
        "cn_pre_open": "A 股盘前:重点检查隔夜影响、持仓触发器和当天计划",
        "cn_open_watch": "A 股开盘观察:只处理跳空、破位和过热风险",
        "cn_pre_close": "A 股收盘前:优先回答已有持仓是否需要条件式处理",
        "cn_after_close": "A 股盘后:复盘收盘事实、触发器和明日计划",
        "us_pre_open": "美股盘前:聚焦 IBKR 持仓、事件和隔夜风险",
        "us_open_watch": "美股开盘观察:确认早盘波动是否改变计划",
        "us_mid_session": "美股盘中:只检查高波动或 critical 风险",
        "us_pre_close": "美股收盘前:默认生成,仅 critical 建议即时推送",
        "us_after_close": "美股盘后:复盘今日盈亏与触发器事实,不做新建议",
        "cn_morning_close": "A 股午前:检查上午走势和午后应对计划",
        "cn_afternoon_open": "A 股午后开盘:检查午间变化和下午方向",
    }.get(session_id, f"{session_id}: scheduled analysis")


# 将与 session 相关的市场焦点和属性抽象为单独体
_SESSION_INTENT_PROPERTIES = {
    "cn_pre_open": {
        "focus": "a+中国/香港市场",
        "can_recommend_new": True,
        "can_review_closed": False,
    },
    "cn_open_watch": {
        "focus": "a+中国/香港市场",
        "can_recommend_new": False,
        "can_review_closed": False,
    },
    "cn_pre_close": {
        "focus": "a+中国/香港市场",
        "can_recommend_new": True,
        "can_review_closed": False,
    },
    "cn_morning_close": {
        "focus": "a+中国/香港市场",
        "can_recommend_new": True,
        "can_review_closed": False,
    },
    "cn_afternoon_open": {
        "focus": "a+中国/香港市场",
        "can_recommend_new": True,
        "can_review_closed": False,
    },
    "cn_after_close": {
        "focus": "a+中国/香港市场",
        "can_recommend_new": False,
        "can_review_closed": True,
    },
    "us_pre_open": {"focus": "us+欧美市场", "can_recommend_new": True, "can_review_closed": False},
    "us_open_watch": {
        "focus": "us+欧美市场",
        "can_recommend_new": False,
        "can_review_closed": False,
    },
    "us_mid_session": {
        "focus": "us+欧美市场",
        "can_recommend_new": False,
        "can_review_closed": False,
    },
    "us_pre_close": {"focus": "us+欧美市场", "can_recommend_new": True, "can_review_closed": False},
    "us_after_close": {
        "focus": "us+欧美市场",
        "can_recommend_new": False,
        "can_review_closed": True,
    },
}


def _market_state_summary(market_state: dict) -> dict:
    risk = market_state.get("risk_appetite", "unknown")
    vix = market_state.get("vix")
    major_moves = market_state.get("major_moves", [])
    breadth = market_state.get("breadth")
    if isinstance(major_moves, list) and major_moves:
        top = major_moves[0]
        move_text = f"{top.get('symbol', '')} {top.get('change_pct', 0):+.2f}%"
    else:
        move_text = "无明显美股龙头"
    vix_text = f"VIX {vix:.2f}" if isinstance(vix, (int, float)) else "VIX 未知"
    risk_text = str(risk) if risk else "风险偏好未知"
    return {
        "risk_appetite": risk_text,
        "vix": vix_text,
        "top_move": move_text,
        "breadth": breadth,
        "source": market_state.get("source", "market_state"),
    }


def _session_intent_props(session_id: str) -> dict:
    return _SESSION_INTENT_PROPERTIES.get(
        session_id,
        {"focus": "cross_market", "can_recommend_new": True, "can_review_closed": False},
    )
