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

from stocks.engine.intelligence_analyzer import IntelligenceAnalyzer
from stocks.engine.intelligence_harvester import IntelligenceHarvester
from stocks.engine.news_intelligence_store import (
    IntelligenceSnapshot,
    NewsIntelligenceStore,
)
from stocks.engine.quant_action import QuantActionEngine, compute_portfolio_risk

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
    ):
        self.engine = engine
        self.config = config
        self.calendar = MarketSessionCalendar(config)
        self.store = RunArtifactStore(artifact_dir)

    async def run_due(
        self,
        *,
        now: Optional[datetime] = None,
        force: bool = False,
    ) -> dict:
        current = now or datetime.now(timezone.utc)
        due = self.calendar.due_sessions(current)
        if not due:
            return {
                "success": True,
                "status": "skipped_no_due",
                "generated_at": _iso_utc(current),
                "runs": [],
            }
        runs = []
        for occurrence in due:
            runs.append(await self.run_occurrence(occurrence, now=current, force=force))
        statuses = {str(item.get("status")) for item in runs}
        return {
            "success": True,
            "status": (
                "ok" if "ok" in statuses else "degraded" if "degraded" in statuses else "skipped"
            ),
            "generated_at": _iso_utc(current),
            "runs": runs,
        }

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
        harvester = IntelligenceHarvester(
            finnhub_client=finnhub_client,
            max_items_per_source=10,
            fred_cache_dir=fred_cache_dir,
        )
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
        analyzer = IntelligenceAnalyzer(lookback_hours=6)
        analysis_result = analyzer.analyze(recent_snapshots)
        store.save_clusters(analysis_result.clusters, formed_at=now)
        store.save_signals(analysis_result.signals, generated_at=now)
        store.archive_and_purge(now=now)

        run = build_intelligence_run(
            harvest_result.to_dict(),
            analysis_result.to_dict(),
            occurrence=occurrence,
            generated_at=now,
            config=self.config,
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
    action_cards = _build_action_cards(context.get("position_valuations") or [])
    portfolio_risk = _build_portfolio_risk_summary(context.get("position_valuations") or [])
    session_intent_props = _session_intent_props(session.id)
    action_signal_reviews = _build_action_signal_reviews(
        context.get("action_signals") or {},
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
        "trigger_reviews": trigger_reviews,
        "action_signal_reviews": action_signal_reviews,
        "action_signals": context.get("action_signals") or {},
        "rule_scorecard": context.get("rule_scorecard", {}),
        "data_quality": context_quality,
        "agent_task": build_agent_task(session),
        "write_policy": {
            "may_write_financial_memory": False,
            "requires_user_confirmation": True,
        },
        "notification": notification,
        "context_digest": {
            "market_state": context.get("market_state") or {},
            "market_state_summary": _market_state_summary(context.get("market_state") or {}),
            "portfolio_mapping": context.get("portfolio_mapping") or {},
            "exposure_summary": context.get("exposure_summary") or {},
            "liquidity_summary": context.get("liquidity_summary") or {},
            "advice_granularity": context.get("advice_granularity") or {},
            "rotation_leaders": (context.get("rotation") or {}).get("leaders", [])[:8],
            "intelligence_digest": context.get("intelligence_digest") or {},
        },
    }


GLOBAL_INTELLIGENCE_WATCH_SCHEMA_VERSION = 1


def build_intelligence_run(
    harvest_result: dict,
    analysis: dict,
    *,
    occurrence: SessionOccurrence,
    generated_at: datetime,
    config: dict,
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
    notification = {"recommended": True, "policy": "push_now", "reason": "hourly patrol"}
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
        },
    }


def build_intelligence_agent_task(session: ScheduledSession) -> dict:
    return {
        "task_version": 2,
        "language": "zh-CN",
        "audience": "single_user",
        "session_intent": session.intent,
        "primary_market": session.primary_market,
        "must_answer": [
            "本小时最重要的 1-2 个事件是什么",
            "它们对 VIX、油、金、美债、美元、中国资产的可能影响",
            "哪些非持仓标的出现了可买入/卖出/观察的信号",
            "数据质量是否有明显缺口",
        ],
        "must_not_do": [
            "不得承诺收益",
            "不得忽略 data_quality",
            "不得自动保存建议、执行或预测",
        ],
        "output_style": {
            "max_words": 900,
            "prefer_actionable_bullets": True,
            "include_data_boundary": True,
            "include_market_focus": True,
        },
        "final_analysis_instructions": (
            "先给出本小时最重要的 1-2 个结论,再列出市场影响与操作信号,最后说明数据边界。"
        ),
    }


def _intelligence_status_reason(status: str, data_quality: dict) -> str:
    if status == "ok":
        return "Intelligence harvest and analysis completed successfully"
    return "; ".join(data_quality.get("errors", [])) or "Degraded without explicit error"


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


def build_agent_task(session: ScheduledSession) -> dict:
    must_answer_by_intent = {
        "pre_open_plan": [
            "今天重点盯哪些已有持仓和触发器",
            "哪些候选方向只适合观察,不能追",
            "数据质量是否足以形成盘前计划",
        ],
        "open_watch": [
            "开盘后已有持仓是否出现异常跳空、破位或过热",
            "是否需要等待收盘确认",
            "数据质量是否足以支持盘中判断",
        ],
        "pre_close_decision": [
            "收盘前已有持仓是否需要动",
            "哪些触发器已经触发或接近触发",
            "是否有不应追高或不应补弱的标的",
        ],
        "after_close_review": [
            "今天触发器和持仓事实如何复盘",
            "明天开盘前重点看什么",
            "是否需要让用户补录数据或确认长期记录",
        ],
        "mid_session_check": [
            "盘中波动是否改变已有计划",
            "是否存在 critical 风险",
            "是否应保持只生成不推送",
        ],
    }
    return {
        "task_version": 2,
        "language": "zh-CN",
        "audience": "single_user",
        "session_intent": session.intent,
        "primary_market": session.primary_market,
        "must_answer": must_answer_by_intent.get(
            session.intent,
            ["已有持仓是否需要动", "数据质量是否足以支持动作"],
        ),
        "must_not_do": [
            "不得承诺收益",
            "不得忽略 data_quality",
            "不得建议动用 rebalance_eligible=false 的资产",
            "不得把代理 ETF 价格触发器套到场外基金",
            "不得自动保存建议、执行或预测",
        ],
        "output_style": {
            "max_words": 900,
            "prefer_actionable_bullets": True,
            "include_data_boundary": True,
            "include_market_focus": True,
        },
        "final_analysis_instructions": ("先给一句话执行结论,再按持仓列出动作,最后说明数据边界。"),
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


def _build_action_cards(position_valuations: list[dict]) -> list[dict]:
    """为每个持仓计算量化行动卡。"""

    cards = []
    for item in position_valuations:
        indicators = item.get("indicators") or {}
        engine = QuantActionEngine(indicators)
        review = engine.review_position(
            position_id=item.get("position_id", ""),
            price=item.get("price"),
            cost=item.get("cost_amount"),
            pnl_pct=item.get("pnl_pct"),
            one_day_change_pct=item.get("one_day_change_pct"),
            current_weight_pct=item.get("portfolio_weight"),
            quantity=(item.get("holding") or {}).get("quantity") if item.get("holding") else None,
        )
        cards.append({
            "position_id": review.position_id,
            "signal": review.signal,
            "action": review.action,
            "ratio": review.ratio,
            "facts": review.facts,
            "stop_price": review.stop_price,
            "target_prices": review.target_prices,
            "position_limit_pct": review.position_limit_pct,
            "current_weight_pct": review.current_weight_pct,
            "risk_to_stop_pct": review.risk_to_stop_pct,
            "risk_amount_cny": review.risk_amount_cny,
        })
    return cards


def _build_portfolio_risk_summary(position_valuations: list[dict]) -> dict:
    """组合风险仪表盘。"""

    total_value = sum(item.get("market_value_cny") or 0.0 for item in position_valuations)
    reviews = []
    for item in position_valuations:
        indicators = item.get("indicators") or {}
        engine = QuantActionEngine(indicators)
        review = engine.review_position(
            position_id=item.get("position_id", ""),
            price=item.get("price"),
            cost=item.get("cost_amount"),
            pnl_pct=item.get("pnl_pct"),
            one_day_change_pct=item.get("one_day_change_pct"),
            current_weight_pct=item.get("portfolio_weight"),
            quantity=(item.get("holding") or {}).get("quantity") if item.get("holding") else None,
        )
        reviews.append(review)
    return compute_portfolio_risk(reviews, total_value, position_valuations=position_valuations)


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
            }
        )
    items.sort(key=lambda item: (item["scope"] != "primary", item["symbol"]))
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
