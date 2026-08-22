"""Scheduled cross-market analysis runs.

This module is deliberately small and file-based. It is designed to be called by
cron/launchd and to hand a structured JSON artifact to the user-facing Agent.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from stocks.engine.advisory_mainline import build_advisory_outlook
from stocks.engine.economic_event_watcher import EconomicEventWatcher
from stocks.engine.forecasts import build_forecast_candidates
from stocks.engine.hypothesis_tracker import (
    HypothesisStore,
    auto_check_hypotheses,
    format_hypothesis_report,
)
from stocks.engine.intelligence_analyzer import LLMIntelligenceAnalyzer
from stocks.engine.intelligence_harvester import IntelligenceHarvester
from stocks.engine.news_intelligence_store import (
    IntelligenceSnapshot,
    NewsIntelligenceStore,
)
from stocks.engine.outcome_attribution import save_portfolio_snapshots
from stocks.engine.outlook_delta import OutlookDeltaState, compute_outlook_delta
from stocks.engine.outlook_evidence import (
    OBSERVATION_OUTLOOK_SESSIONS,
    PRIMARY_OUTLOOK_SESSIONS,
    build_outlook_evidence,
    evidence_hash,
)
from stocks.engine.outlook_synthesizer import OutlookSynthesizer
from stocks.engine.outlook_validation import sanitize_unavailable_outlook
from stocks.engine.presentation import build_user_view
from stocks.engine.profile_interpreter import load_computed, merge_with_defaults
from stocks.engine.quant_action import (
    _TAG_TO_BUCKET,
    compute_portfolio_risk,
    finalize_decision,
)
from stocks.engine.risk_warning import assess_risk
from stocks.engine.shadow_account import build_shadow_block, save_snapshot
from stocks.engine.signal_tracker import SignalTracker, TrackedSignal
from stocks.engine.window_delta import WindowDelta, compute_window_delta
from stocks.logging_utils import get_logger

logger = get_logger("scheduled_analysis")

# ── Phase 2: 可执行性辅助函数 ──

_ACCOUNT_ID_TO_INSTITUTION = {
    # 当前资产文件账户 ID（权威来源是 assets 文件 accounts 段，
    # 经 context_builder 透传；此表仅为缺失元数据时的兜底）。
    "cn_broker": "brokerage",
    "ibkr": "brokerage",
    "alipay": "fund_platform",
    "ccb": "bank",
    "bochk_life": "insurance",
    # 历史 ID（2026-07-06 资产文件改版前），保留兼容旧快照。
    "a_stock": "brokerage",
    "boc_life": "insurance",
}


def _institution_type_for_account_id(account_id: str) -> str:
    """Infer institution_type from account_id when not present in position metadata."""
    return _ACCOUNT_ID_TO_INSTITUTION.get(account_id, "")

# ── Phase 2: 可执行性辅助函数 ──

_PLATFORM_DISPLAY = {
    "brokerage": "证券账户",
    "fund_platform": "支付宝",
    "bank": "银行理财",
    "insurance": "保险账户",
}

_OPERATION_CHANNEL = {
    ("fund_platform", "alipay"): "打开支付宝 → 理财 → 按名称/代码搜索",
    ("bank", "ccb"): "打开建行 APP → 理财/基金 → 查看开放期",
    ("insurance", "boc_life"): "联系香港中银人寿顾问或登录中银人寿 APP",
    ("brokerage", "a_stock"): "通过东方财富/华泰等中信建投交易软件",
    ("brokerage", "ibkr"): "登录 Interactive Brokers (IBKR) 账户",
}


def _platform_display(institution_type: str, account_id: str) -> str:
    """Return a human-readable platform name based on institution type and account id."""
    if institution_type == "brokerage":
        if account_id == "ibkr":
            return "IBKR"
        if account_id in ("a_stock", "cn_broker"):
            return "A股证券账户"
        return "证券账户"
    if institution_type == "fund_platform":
        return "支付宝"
    if institution_type == "bank":
        return "建行 APP"
    if institution_type == "insurance":
        return "中银人寿"
    return "待确认平台"


def _settlement_timing_for_institution(institution_type: str, routing: str) -> str:
    """Card-level settlement fallback aligned with engine.yaml execution_rules.

    Adversarial review P1-5: this function used to be a second, contradictory
    settlement authority (fund_platform -> "T+1" while the config resolves
    T+2; unknown institutions defaulted to a dangerous "T+1"). It now emits
    only the canonical token vocabulary used by execution_rules and fails
    closed to "" for anything it cannot map. Approved actions always get
    their settlement from resolve_execution(); this fallback is card
    metadata only.
    """
    if routing in ("info_only", "skip"):
        return ""
    if institution_type == "brokerage":
        return "T+1"
    if institution_type == "fund_platform":
        return "T+2"
    if institution_type == "bank":
        return "periodic_open"
    if institution_type == "insurance":
        return "locked"
    return ""


def _operation_channel(institution_type: str, account_id: str, routing: str) -> str:
    """Return concrete operation-channel hint for the platform."""
    if routing in ("info_only", "skip"):
        if institution_type == "insurance":
            return _OPERATION_CHANNEL.get(("insurance", "boc_life"), "联系对应机构顾问")
        return "当前不形成交易动作，继续持有"
    channel = _OPERATION_CHANNEL.get((institution_type, account_id))
    if channel:
        return channel
    if institution_type == "brokerage":
        return "登录证券账户执行"
    if institution_type == "fund_platform":
        return "打开支付宝 → 理财 → 按名称/代码搜索"
    if institution_type == "bank":
        return "打开建行 APP → 理财/基金"
    return "在对应平台执行"


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
    delta_silent_when_unchanged: bool = False
    # ── 会话级展示/行为覆盖（来自 scheduled_sessions.json 的 session 配置）──
    # headline/focus/can_recommend_new/can_review_closed 缺省时回退到
    # _headline()/_session_intent_props() 的内置默认表；ignore_weekend=True
    # 表示该会话在周末也照常运行（仅假期跳过）。
    headline: Optional[str] = None
    focus: Optional[str] = None
    can_recommend_new: Optional[bool] = None
    can_review_closed: Optional[bool] = None
    ignore_weekend: bool = False

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
        if run_every and local_now >= scheduled_for:
            since_start = int((local_now - scheduled_for).total_seconds()) // 60
            boundary_minutes = (since_start // run_every) * run_every
            scheduled_for = scheduled_for + timedelta(minutes=boundary_minutes)
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
                        delta_silent_when_unchanged=bool(
                            item.get("delta_silent_when_unchanged", False)
                        ),
                        # 会话级覆盖字段：从 session 配置原样透传，缺省回退到默认表
                        headline=item.get("headline"),
                        focus=item.get("focus"),
                        can_recommend_new=item.get("can_recommend_new"),
                        can_review_closed=item.get("can_review_closed"),
                        ignore_weekend=bool(item.get("ignore_weekend", False)),
                    )
                )
        return sessions

    @staticmethod
    def _is_market_date(session: ScheduledSession, market_date: date) -> bool:
        # ignore_weekend=True 的会话（如全球情报巡逻）周末照常运行，仅跳过假期
        if not session.ignore_weekend and market_date.weekday() >= 5:
            return False
        return market_date.isoformat() not in session.holidays


class RunArtifactStore:
    """Persist scheduled run artifacts under .local/scheduled_runs."""

    _TRUSTED_FIELDS = frozenset({
        "window_delta", "portfolio_decision", "risk_state",
        "data_boundaries", "research_candidates",
    })
    # 可信交易产物必须使用的 agent_task.task_version（与 build_agent_task 对齐）
    _TRUSTED_CONTRACT_TASK_VERSION = 5

    def __init__(self, artifact_dir: str | Path):
        self.artifact_dir = Path(artifact_dir)
        self.latest_dir = self.artifact_dir / "latest"

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def _validate_trusted_contract(self, run: dict) -> None:
        if run.get("market") not in {"cn", "us"}:
            return
        task_version = (run.get("agent_task") or {}).get("task_version")
        missing = self._TRUSTED_FIELDS - set(run)
        if task_version != self._TRUSTED_CONTRACT_TASK_VERSION or missing:
            raise ValueError(
                "trading artifacts must use trusted v5 contract; "
                f"task_version={task_version}, missing={sorted(missing)}"
            )

    def save(self, run: dict) -> dict:
        self._validate_trusted_contract(run)
        json_path = self._artifact_path(run, suffix=".json")
        md_path = self._artifact_path(run, suffix=".md")
        json_path.parent.mkdir(parents=True, exist_ok=True)
        self.latest_dir.mkdir(parents=True, exist_ok=True)

        payload = json.dumps(run, ensure_ascii=False, indent=2)
        self._atomic_write(json_path, payload + "\n")
        self._atomic_write(md_path, format_run_markdown(run) + "\n")
        self._atomic_write(
            self.latest_dir / f"{run['session']}.json",
            payload + "\n",
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

    def has_run_for_occurrence(self, session_id: str, run_id: str) -> Optional[dict]:
        latest = self.latest(session_id)
        if latest and latest.get("run_id") == run_id:
            return latest
        return None

    def find_previous_for_session(
        self, session_id: str, market: str, *, market_date: str = ""
    ) -> Optional[dict]:
        """Return prior same-session artifact, else latest same-market window."""
        same_session = self.latest(session_id)
        if (
            same_session
            and same_session.get("market") == market
            and (not market_date or same_session.get("market_date") == market_date)
        ):
            return same_session
        best: Optional[dict] = None
        best_generated = ""
        for path in self.latest_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict) or data.get("market") != market:
                continue
            if market_date and data.get("market_date") != market_date:
                continue
            generated = data.get("generated_at") or ""
            if generated > best_generated:
                best = data
                best_generated = generated
        return best

    def find_latest_two_primary(self, market: str) -> list[dict]:
        """Return the two most recent primary-window artifacts for *market*.

        Walks the dated artifact directory tree, collects all JSON artifacts
        whose ``session`` field is in ``PRIMARY_OUTLOOK_SESSIONS``, whose
        ``market`` matches, whose top-level ``status`` is ``"ok"``, and
        whose ``structured_outlook.status`` is ``"ok"``.  Excludes files
        with ``_push_payload`` in their name as well as all files under a
        ``latest/`` directory.  Deduplicates by ``run_id``.  Returns the two
        most recent by ``generated_at``, oldest first.
        """
        candidates: list[dict] = []
        seen_run_ids: set[str] = set()
        glob_expr = f"**/{market}/*/*.json"
        for path in sorted(self.artifact_dir.glob(glob_expr)):
            rel = path.relative_to(self.artifact_dir)
            if "_push_payload" in path.name:
                continue
            if "latest" in rel.parts:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            if data.get("market") != market:
                continue
            if data.get("session") not in PRIMARY_OUTLOOK_SESSIONS:
                continue
            if data.get("status") != "ok":
                continue
            so = data.get("structured_outlook") or {}
            if not isinstance(so, dict) or so.get("status") != "ok":
                continue
            run_id = data.get("run_id")
            if run_id and run_id in seen_run_ids:
                continue
            if run_id:
                seen_run_ids.add(run_id)
            candidates.append(data)

        candidates.sort(
            key=lambda d: d.get("generated_at") or "", reverse=True,
        )
        top_two = candidates[:2]
        top_two.reverse()
        return top_two


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
        outlook_synthesizer = None,
    ):
        self.engine = engine
        self.config = config
        self.calendar = MarketSessionCalendar(config)
        self.store = RunArtifactStore(artifact_dir)
        self._repo_root = repo_root or Path(artifact_dir).parent.parent if artifact_dir else None
        self.event_watcher: Optional[EconomicEventWatcher] = event_watcher
        self.outlook_synthesizer = outlook_synthesizer
        self._outlook_delta_state: OutlookDeltaState | None = None

    def _get_synthesizer(self) -> OutlookSynthesizer:
        """Return the outlook synthesizer, constructing a default if none was injected."""
        if self.outlook_synthesizer is None:
            self.outlook_synthesizer = OutlookSynthesizer(self.config)
        return self.outlook_synthesizer

    def _get_delta_state(self) -> OutlookDeltaState:
        """Return or create the shared outlook delta state file."""
        if self._outlook_delta_state is None:
            artifact_dir = self.store.artifact_dir
            state_path_env = self.config.get("outlook_delta_state_path")
            if state_path_env:
                state_path = Path(state_path_env)
            else:
                # Production artifact_dir is often .local/scheduled_runs; parent
                # is already .local -- avoid .local/.local/outlook_delta_state.json.
                if artifact_dir.parent.name == ".local":
                    state_path = artifact_dir.parent / "outlook_delta_state.json"
                else:
                    state_path = artifact_dir.parent / ".local" / "outlook_delta_state.json"
            self._outlook_delta_state = OutlookDeltaState(state_path)
        return self._outlook_delta_state

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
        if occurrence.session.run_every_minutes:
            existing = self.store.has_run_for_occurrence(occurrence.session.id, occurrence.run_id)
        else:
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
        context_dict = context.to_dict()
        previous_run = self.store.find_previous_for_session(
            occurrence.session.id, occurrence.session.market,
            market_date=market_date,
        )
        run = build_scheduled_run(
            context_dict,
            occurrence=occurrence,
            generated_at=now,
            config=self.config,
            previous_run=previous_run,
            attach_user_view=False,
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

        # Decision Attribution: save snapshots for each approved/suppressed action
        portfolio_decision = run.get("portfolio_decision") or {}
        if portfolio_decision.get("approved_actions") and self._repo_root:
            try:
                save_portfolio_snapshots(
                    portfolio_decision,
                    generated_at=run.get("generated_at", ""),
                    repo_root=self._repo_root,
                )
            except Exception:
                logger.exception("Failed to save decision snapshots")

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

        # Outlook synthesis for primary windows; delta for observation windows.
        # These feed the single build_user_view() call below — no field is
        # ever patched into user_view after it is built.
        session_id = run["session"]
        structured_outlook_for_view: Optional[dict] = None
        outlook_delta_for_view: Optional[dict] = None
        if session_id in PRIMARY_OUTLOOK_SESSIONS:
            mainline_enabled = bool(
                (self.config.get("llm") or {}).get("advisory_mainline", {}).get("enabled", True)
            )
            if mainline_enabled:
                # M2 advisory mainline: LLM Investment Analyst drives the
                # structured_outlook; failures degrade to 研判待复核.
                try:
                    outlook = await asyncio.to_thread(
                        build_advisory_outlook,
                        context,
                        session_id=session_id,
                        market=occurrence.session.primary_market,
                        config=self.config,
                        now=run["generated_at"],
                    )
                    run["structured_outlook"] = outlook
                    run["forecast_candidates"] = build_forecast_candidates(outlook)
                    if isinstance(outlook.get("advisory_receipt"), dict):
                        run["advisory_receipt"] = outlook["advisory_receipt"]
                    structured_outlook_for_view = outlook
                except Exception:
                    logger.exception("Advisory mainline failed for %s", session_id)
                    run["structured_outlook"] = sanitize_unavailable_outlook(
                        ["Outlook synthesis failed"], generated_at=run["generated_at"],
                    )
                    run["forecast_candidates"] = []
                    structured_outlook_for_view = run["structured_outlook"]
            else:
                # Legacy constrained OutlookSynthesizer path (evidence + hash).
                try:
                    evidence = build_outlook_evidence(
                        context_dict, run, session_id=session_id,
                        generated_at=run["generated_at"],
                    )
                    # Write evidence meta *before* generation so it is preserved
                    # even when synthesis fails
                    run["outlook_evidence_meta"] = {
                        "hash": evidence_hash(evidence),
                        "confidence_cap": evidence["confidence_cap"],
                    }
                    outlook = await asyncio.to_thread(
                        self._get_synthesizer().generate, evidence,
                        now=run["generated_at"],
                    )
                    run["structured_outlook"] = outlook
                    run["forecast_candidates"] = build_forecast_candidates(outlook)
                    structured_outlook_for_view = outlook
                except Exception:
                    logger.exception("Outlook synthesis failed for %s", session_id)
                    run["structured_outlook"] = sanitize_unavailable_outlook(
                        ["Outlook synthesis failed"], generated_at=run["generated_at"],
                    )
                    run["forecast_candidates"] = []
                    structured_outlook_for_view = run["structured_outlook"]
        elif session_id in OBSERVATION_OUTLOOK_SESSIONS:
            # Observation window: compute delta from latest two primary outlooks
            try:
                primaries = self.store.find_latest_two_primary(run["market"])
                if len(primaries) >= 2:
                    delta = compute_outlook_delta(primaries[0], primaries[1])
                    if delta:
                        state = self._get_delta_state()
                        if state.should_emit(run["market"], delta):
                            outlook_delta_for_view = delta
            except Exception:
                logger.exception("Outlook delta failed for %s", session_id)

        # Single authoritative build of user_view for this run — constructed
        # exactly once, after outlook/delta are known, never mutated after.
        run["portfolio_decision"]["user_view"] = build_user_view(
            run["portfolio_decision"],
            context_dict.get("position_valuations") or [],
            run.get("position_reviews") or [],
            run.get("research_candidates") or [],
            run.get("risk_state") or {},
            data_boundaries=run.get("data_boundaries") or {},
            session_id=run["session"],
            session_intent=occurrence.session.intent,
            primary_market=occurrence.session.primary_market,
            structured_outlook=structured_outlook_for_view,
            outlook_delta=outlook_delta_for_view,
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


def _vix_levels() -> dict:
    """VIX 风险分层阈值：权威来源 engine.yaml quant_action.vix_levels。
    缺失 = 部署事故（配置随 repo 分发），fail-closed。"""
    global _CACHED_VIX_LEVELS
    if _CACHED_VIX_LEVELS is not None:
        return _CACHED_VIX_LEVELS
    from stocks.engine.config_loader import load_engine_config
    lv = ((load_engine_config() or {}).get("quant_action") or {}).get("vix_levels")
    if not isinstance(lv, dict) or not all(k in lv for k in ("hedge", "reduce", "watch")):
        raise RuntimeError("engine.yaml quant_action.vix_levels 缺失或缺键")
    _CACHED_VIX_LEVELS = dict(lv)
    return _CACHED_VIX_LEVELS


_CACHED_VIX_LEVELS: dict | None = None


def _risk_evidence_keys(*, vix, clusters: list[dict]) -> list[str]:
    keys: list[str] = []
    if isinstance(vix, (int, float)):
        lv = _vix_levels()
        if vix > lv["hedge"]:
            keys.append("macro:vix_hedge")
        elif vix > lv["reduce"]:
            keys.append("macro:vix_reduce")
        elif vix > lv["watch"]:
            keys.append("macro:vix_watch")
    for index, cluster in enumerate(clusters):
        if cluster.get("urgency") not in {"critical", "high"}:
            continue
        identity = (cluster.get("cluster_id") or cluster.get("id") or cluster.get("url")
                    or cluster.get("theme") or index)
        keys.append(f"cluster:{identity}")
    return sorted(set(keys))


def _resolve_risk_state_path(config: dict, *, repo_root: Optional[Path] = None) -> Path:
    """Resolve the persistent risk-state path, anchored to the repo root.

    Relative ``risk_state.state_path`` / ``artifact_dir`` values must NOT be
    resolved against the process CWD: scheduled runs can be launched by
    agents or cron jobs whose CWD differs from the repo, which would
    silently split the risk state into multiple divergent files.
    """
    root = repo_root or Path(__file__).resolve().parents[2]
    risk_cfg = (config or {}).get("risk_state") or {}
    raw = risk_cfg.get("state_path")
    if not raw:
        artifact_dir = Path(str((config or {}).get("artifact_dir") or ".local/scheduled_runs"))
        if not artifact_dir.is_absolute():
            artifact_dir = root / artifact_dir
        return artifact_dir.parent / "risk_state.json"
    path = Path(str(raw))
    if not path.is_absolute():
        path = root / path
    return path


def _persist_risk_state(assessment: dict, *, generated_at: datetime, config: dict) -> dict:
    from stocks.engine.risk_state import RiskObservation, RiskStateStore

    risk_cfg = (config or {}).get("risk_state") or {}
    observation = RiskObservation(
        candidate_level=assessment.get("level", "normal"),
        evidence_keys=tuple(assessment.get("evidence_keys") or ()),
        observed_at=generated_at,
        expires_at=generated_at + timedelta(
            minutes=int(risk_cfg.get("critical_ttl_minutes", 360))
        ),
    )
    state_path = _resolve_risk_state_path(config)
    state = RiskStateStore(path=state_path, config=risk_cfg).update(observation)
    result = state.to_dict()
    result["recommended_actions"] = assessment.get("recommended_actions", [])
    result["triggers"] = assessment.get("triggers", [])
    return result


def _evidence_holding(pv: dict) -> dict:
    """Copy holding facts (quantity, unit) from a position_valuations record.

    ``position_valuations`` records carry either a nested ``holding`` dict or
    a flat ``quantity``/``unit`` pair depending on producer; both are copied
    verbatim, never recomputed. A missing quantity stays missing (empty dict)
    so downstream minimum-unit logic sees it as absent rather than zero.
    """
    holding = pv.get("holding")
    if isinstance(holding, dict):
        return dict(holding)
    quantity = pv.get("quantity")
    if quantity is None:
        return {}
    return {"quantity": quantity, "unit": pv.get("unit", "")}


def _build_adjudicator_evidences(pv_list: list[dict]) -> dict[str, dict]:
    """Build the complete authoritative evidence the adjudicator needs per position.

    Copies fields straight from the single ``position_valuations`` record
    without recomputation: position_id, instrument_key, holding, valuation
    method, market value, classification, liquidity (tier/redemption_rule/
    lockup_until/maturity_date/tradable/rebalance_eligible all pass through
    inside the liquidity dict), and evidence (price freshness/data anomalies).
    """
    evidences: dict[str, dict] = {}
    for pv in pv_list:
        pid = pv.get("position_id", "")
        if not pid:
            continue
        evidences[pid] = {
            "position_id": pid,
            "instrument_key": pv.get("instrument_key", ""),
            "account_id": pv.get("account_id", ""),
            "holding": _evidence_holding(pv),
            "valuation_method": pv.get("valuation_method", ""),
            "classification": pv.get("classification", {}),
            "liquidity": pv.get("liquidity", {}),
            "market_value_cny": pv.get("market_value_cny", 0.0),
            "evidence": pv.get("evidence", {}),
            "product_type": (pv.get("classification") or {}).get("product_type", ""),
        }
    return evidences


def build_scheduled_run(
    context: dict,
    *,
    occurrence: SessionOccurrence,
    generated_at: datetime,
    config: dict,
    previous_run: Optional[dict] = None,
    attach_user_view: bool = True,
    structured_outlook: Optional[dict] = None,
    outlook_delta: Optional[dict] = None,
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
    # E3(2026-08-12): weak 信号只进 LLM 面展示, 排除出确定性面 —
    # 不驱动 action card direction。只消费 passed。
    intel_signals: dict[str, dict] = {
        s.get("symbol", ""): {
            "symbol": s.get("symbol", ""),
            "direction": s.get("direction", ""),
            "urgency": s.get("urgency", "medium"),
            "rationale": s.get("rationale", ""),
        }
        for s in (intel_digest.get("top_signals") or [])
        if s.get("symbol") and not s.get("weak")
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
    from stocks.engine.portfolio_adjudicator import (
        adjudicate_portfolio,
        build_capital_allocation_with_suppression,
        build_cash_schedule,
    )

    capital_allocation = build_capital_allocation_with_suppression(
        action_cards,
        context.get("position_valuations") or [],
        context.get("portfolio_mapping") or {},
        context.get("liquidity_summary") or {},
        constraints=context.get("portfolio_constraints"),
        rotation_ranks=rotation_ranks if rotation_ranks else None,
        rotation_leaders=rotation_leaders_data,
        context_config=context.get("engine_config", {}).get("portfolio_layering"),
    )
    total_value = sum(
        item.get("market_value_cny") or 0.0
        for item in (context.get("position_valuations") or [])
    )
    cash_schedule = build_cash_schedule(
        context.get("position_valuations") or [], [], total_value
    )
    # Build the adjudicator bridge by copying the single authoritative
    # position_valuations records; do not recompute or silently default facts.
    pv_list = context.get("position_valuations") or []
    evidences = _build_adjudicator_evidences(pv_list)
    # Adjudicate portfolio actions against the persistent risk state.
    risk_assessment = _compute_risk_assessment(context)
    risk_state = _persist_risk_state(
        risk_assessment, generated_at=generated_at,
        config=context.get("engine_config") or config,
    )
    try:
        portfolio_decision = adjudicate_portfolio(
            action_cards,
            evidences,
            context.get("portfolio_constraints") or {},
            risk_state,
            cash_schedule,
            run_id=occurrence.run_id,
            execution_rules=(context.get("engine_config") or {}).get("execution_rules"),
        )
    except Exception:
        from stocks.engine.portfolio_adjudicator import (
            PortfolioDecision,
            make_decision_id,
        )

        logger.exception("Portfolio adjudication failed")
        portfolio_decision = PortfolioDecision(
            status="review_required",
            decision_id=make_decision_id(
                occurrence.run_id, "portfolio", "adjudication_failed", 0.0
            ),
            unresolved_conflicts=[{
                "code": "adjudication_failed",
                "message": "组合裁决失败，所有动作均未获批，需人工复核",
            }],
            cash_schedule=cash_schedule,
        )
    # P3-1: 顶层 cash_schedule 必须与裁决器结果一致。build_cash_schedule
    # 在 scheduled_analysis 这里用空 approved_sales 调用,得到的是"未裁决"
    # 毛值(strategic_exit 含待结算);adjudicate_portfolio 内部用真实
    # approved_sales 重算过(净额)。两份并存且不一致(8/6 实测顶层
    # 523,472 vs 裁决 486,622),下游消费方可能拿错份 → 用裁决结果覆盖。
    if getattr(portfolio_decision, "cash_schedule", None):
        cash_schedule = portfolio_decision.cash_schedule
    session_intent_props = _session_intent_props(session)
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
    fired_triggers = [
        f"{item.get('type', '')}:{item.get('instrument', '')}"
        for item in trigger_reviews if item.get("status") == "fired"
    ]
    priority = _priority(
        risk_state=risk_state,
        portfolio_decision=portfolio_decision.to_dict(),
        fired_triggers=fired_triggers,
        trigger_reviews=trigger_reviews,
        position_reviews=position_reviews,
    )
    status = _run_status(context_quality, primary_market=session.primary_market)
    research_candidates = _build_research_candidates(
        context.get("action_signals") or {},
        risk_state,
        session,
        blocked_symbols=_blocked_symbols(context.get("position_valuations") or []),
        data_quality=context_quality,
    )
    # P0-1: 引擎动作信号接入 signal_tracker(反馈闭环),失败不阻断主流程。
    # repo_root 由 helper 内部 resolve,避免调用处作用域依赖。
    _track_engine_action_signals(
        research_candidates,
        generated_at_iso,
    )
    data_boundaries = {
        "data_quality": context_quality,
        "source_context": {
            "schema_version": context.get("schema_version"),
            "generated_at": context.get("generated_at"),
        },
    }
    portfolio_decision_dict = portfolio_decision.to_dict() if portfolio_decision else {}
    # 注: build_user_view 在 window_delta 计算后调用一次(见下文),
    # 以携带 P2-4 窗口级风险迁移信息 —— 保持"恰好一次"。
    run = {
        "schema_version": SCHEDULED_RUN_SCHEMA_VERSION,
        "run_id": occurrence.run_id,
        "generated_at": generated_at_iso,
        # P0-2 fix: 记录生产代码版本(commit hash + dirty flag),让 artifact 可追溯、
        # 跨窗口可比。修复前同一持仓同一天相邻窗口常因盘中部署而用不同版本代码裁决
        # (cn=需人工确认 / us=直接执行), 无版本记录则无法定位该漂移。
        "code_version": _code_version(),
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
            "headline": _headline(session),
            "priority": "normal",
            "push_policy": "push_now",
            "intent_props": session_intent_props,
            "market_state_summary": _market_state_summary(context.get("market_state") or {}),
        },
        "position_reviews": position_reviews,
        "action_cards": action_cards,
        "portfolio_risk": portfolio_risk,
        "capital_allocation": capital_allocation,
        "cash_schedule": cash_schedule,
        "portfolio_decision": portfolio_decision_dict,
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
        "risk_assessment": risk_assessment,
        "risk_state": risk_state,
        "data_boundaries": data_boundaries,
        "research_candidates": research_candidates,
        "mandatory_blocks": build_mandatory_blocks(
            risk_state,
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
        "execution_review": _build_execution_review_summary(
            context.get("recent_advice") or []
        ),
    }
    window_delta = compute_window_delta(
        previous_run, run, session_id=session.id, market=session.market
    )
    # P2-4: 风险状态表述基准消歧。build_user_view 需要在 window_delta
    # 已知后重建一次,让 user_view.risk.window_level_change 携带窗口级
    # 迁移信息(与观察级 transition_key 并存时供渲染层消歧)。
    if attach_user_view and window_delta is not None:
        portfolio_decision_dict["user_view"] = build_user_view(
            portfolio_decision_dict,
            context.get("position_valuations") or [],
            position_reviews,
            research_candidates,
            risk_state,
            data_boundaries=data_boundaries,
            session_id=session.id,
            session_intent=session.intent,
            structured_outlook=structured_outlook,
            outlook_delta=outlook_delta,
            window_delta=window_delta.to_dict() if hasattr(window_delta, "to_dict") else None,
        )
    notification = _notification(
        session=session, priority=priority, now=generated_at,
        quiet_hours=config.get("quiet_hours") or {}, window_delta=window_delta,
    )
    run["window_delta"] = window_delta.to_dict()
    run["notification"] = notification
    run["session_summary"]["priority"] = priority
    run["session_summary"]["push_policy"] = notification["policy"]
    return run


def _compute_risk_assessment(context: dict) -> dict:
    """Lightweight risk assessment for run reports using intelligence clusters."""
    from stocks.engine.risk_warning import assess_risk

    macro = (context.get("market_state") or {}).get("macro") or context.get("macro") or {}
    intel_digest = context.get("intelligence_digest") or {}
    health = intel_digest.get("intelligence_health") or {}
    intelligence_eligible = not health or health.get("risk_eligible", False)
    clusters = (intel_digest.get("top_clusters") or []) if intelligence_eligible else []

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
        "evidence_keys": _risk_evidence_keys(
            vix=macro.get("vix"), clusters=clusters,
        ),
        "triggers": [
            {
                "name": t.condition,
                "condition": t.condition,
                "value": t.value,
                "severity": t.severity,
            }
            for t in risk.triggers
        ],
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

    # Producer-side intelligence is generated in this run. Staleness is
    # evaluated later by trading-session consumers against latest_brief provenance.
    intel_health = {"status": "ok", "age_minutes": 0.0, "risk_eligible": True}

    # Risk assessment
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

    persistent_risk_assessment = {
        "level": risk.level,
        "evidence_keys": _risk_evidence_keys(vix=macro.get("vix"), clusters=clusters),
        "triggers": [{"condition": item.condition, "value": item.value} for item in risk.triggers],
        "recommended_actions": risk.recommended_actions,
        "suspend_accumulation": risk.suspend_accumulation,
        "cash_target_pct": risk.cash_target_pct,
    }
    risk_state = _persist_risk_state(
        persistent_risk_assessment, generated_at=generated_at,
        config=engine_config or config,
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

    risk_assessment = {
        "level": risk.level,
        "evidence_keys": _risk_evidence_keys(
            vix=macro.get("vix"), clusters=clusters,
        ),
        "triggers": [{"condition": item.condition, "value": item.value}
                     for item in risk.triggers],
        "recommended_actions": risk.recommended_actions,
        "suspend_accumulation": risk.suspend_accumulation,
        "cash_target_pct": risk.cash_target_pct,
    }
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
        "risk_assessment": risk_assessment,
        "risk_state": risk_state,
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
        "task_version": 5,
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
            "信号": "intelligence_digest.top_signals[] — direction, symbol, rationale, "
                "adjudication(passed/weak), weak(bool)。weak=True 表示低置信/弱情报，"
                "只能作为观察线索，不得作为确认方向或买卖依据；只有 passed 信号可"
                "作为方向性信号引用",
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
    lv = _vix_levels()
    if vix > lv["reduce"]:
        return "risk_off"
    if vix < lv["risk_on_below"]:
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

        ladder = params.get("ma20_pullback_add_ratios", [0.02])
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


def _blocked_symbols(position_valuations: list[dict]) -> set[str]:
    """Collect instrument_keys whose position-level data anomaly blocks action."""
    blocked = set()
    for item in position_valuations:
        if (item.get("evidence") or {}).get("data_anomalies"):
            key = item.get("instrument_key") or ""
            if key:
                blocked.add(key)
    return blocked


def _research_market(symbol: str) -> str:
    """Extract the market prefix from an instrument symbol (a:513770 -> a).

    P1-15: research candidates must be freshness-gated per market like
    executable actions are. The symbol format is the same one the fetcher
    uses (market:symbol); an unknown or empty prefix fails closed to '' so
    callers treat the candidate as unverifiable.
    """
    value = str(symbol or "")
    if ":" not in value:
        return ""
    market, _, _ = value.partition(":")
    return market.strip().lower()


# P2-1: technical-indicator freshness tolerance (calendar days). The
# research candidates quote prices/MA/RSI computed from the HistoryCache
# daily bars, whose as_of is the last bar's timestamp -- independent of the
# realtime quotes layer. If a candidate's as_of is older than this window,
# the quote_stale downgrade applies even when quotes.by_market says fresh
# (the 2026-08-06 adversarial check: A-share bars stopped at 07-23 while
# quotes looked fresh, so two-week-old prices were shown as current).
# Must stay in sync with history_provider.warm_history_cache stale_days.
_HISTORY_STALE_DAYS = 4


def _indicator_as_of_stale(as_of: object) -> bool:
    """P2-1: True when a candidate's technical-indicator as_of (last daily
    bar timestamp) is explicitly older than the tolerance window.

    Missing/unparseable as_of returns False here: an unknown-age candidate
    is not *proven* stale, so we do not downgrade it (the realtime quotes
    gate already covers the market-level staleness). Only an explicitly
    old indicator timestamp trips the technical-indicator gate -- this is
    the 2026-08-06 adversarial case where quotes said fresh but the daily
    bars feeding price/MA/RSI had stopped updating two weeks earlier.
    """
    if not as_of:
        return False
    try:
        ts = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(days=_HISTORY_STALE_DAYS)
    return bool(ts < cutoff)




def _track_engine_action_signals(
    candidates: list[dict],
    generated_at: str,
    repo_root: Path | None = None,
) -> None:
    """P0-1: 把引擎 research 候选(accumulate/left_bottom/wait_for_pullback 等)
    记录到 SignalTracker,让 A股/美股动作信号开始积累结算胜率。

    之前 tracker 只记录 intelligence 的 LLM/fallback 信号(主要是 BTC),
    引擎给用户的股票动作信号从不进 tracker —— 反馈闭环断在源头。
    这里补齐: 每个候选 = 一个方向性信号(direction 由信号类型映射),
    以 symbol 的现价作为 generation_price。

    失败只告警不阻断主流程(与 intelligence 追踪同风格)。
    """
    if not candidates:
        return
    from stocks.engine.signal_tracker import SignalTracker, TrackedSignal
    try:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        try:
            now = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass
        if repo_root is None:
            repo_root = Path(__file__).resolve().parents[2]
        tracker = SignalTracker(repo_root / ".local" / "signal_tracker")
        # 方向映射: accumulate/rotation/left_bottom/wait_for_pullback = 观察买入意图;
        # reduce_risk = 减仓意图; 其余跳过或标记 hold。
        direction_map = {
            "accumulate_candidate": "buy",
            "rotation_candidate": "buy",
            "left_bottom_candidate": "buy",
            "wait_for_pullback": "buy",
            "reduce_risk": "sell",
            "avoid_catching_falling_knife": "hold",
        }
        tracked: list[TrackedSignal] = []
        for c in candidates:
            signal = str(c.get("signal") or "")
            direction = direction_map.get(signal)
            if direction is None or direction == "hold":
                continue
            symbol = str(c.get("symbol") or "")
            if not symbol:
                continue
            price = c.get("price")
            if price is None:
                continue
            tracked.append(TrackedSignal(
                signal_id=(
                    f"{now.strftime('%Y%m%dT%H%M%S')}_{symbol.replace(':', '_')}_{direction}"
                ),
                generated_at=now,
                symbol=symbol,
                direction=direction,
                rationale=str(c.get("action_hint") or "")[:200],
                generation_price=float(price),
                confidence=float(c.get("score") or 0) if c.get("score") is not None else None,
                source="engine_action",
                urgency="medium",
                regime={"vix": None, "mode": "engine"},
            ))
        if tracked:
            tracker.record_batch(tracked)
            get_logger("scheduled_analysis").info(
                f"tracked {len(tracked)} engine action signals"
            )
    except Exception as exc:  # noqa: BLE001 - 追踪失败不影响主流程
        get_logger("scheduled_analysis").warning(
            f"track_engine_action_signals failed: {exc}"
        )


def _build_research_candidates(
    action_signals: dict,
    risk_state: dict,
    session: ScheduledSession,
    *,
    blocked_symbols: set[str] | None = None,
    data_quality: dict | None = None,
) -> list[dict]:
    """Build research_candidates from action_signals, filtered by risk state.

    research_only signals go here, never into the action section.
    When suspend_accumulation is active, candidates must note reassessment
    condition, and accumulation-oriented candidates lose their sizing
    guidance (P1-15: "暂停加仓" plus a "分批布局 2-4%" hint is a
    contradiction).

    Freshness gate (P1-15): a candidate whose market quotes are
    stale/missing is marked quote_stale so render layers downgrade it to
    pure observation instead of presenting price/MA20-based setup claims.
    The 2026-08-05 us_after_close report listed three A-share ETF
    candidates with precise prices while data_notes said A-share quotes
    were stale -- the candidate pipeline must not contradict its own data
    boundary notes.

    Diversity rule: one signal class (e.g., left_bottom_candidate) must not
    monopolize the display list. We round-robin across signal types so the user
    sees a mix of layouts: trend/rotation, left-bottom, and pullback ideas.
    """
    from stocks.engine.action_signals import _research_sizing_hint

    suspend = risk_state.get("suspend_accumulation", False)
    risk_level = risk_state.get("level", "normal")

    # P1-15: per-market quote freshness, same shape presentation._is_executable
    # consumes (quotes.by_market.<market>.freshness). Missing market entry
    # fails closed to stale. Single source of truth: presentation.STALE_FRESHNESS.
    from stocks.engine.presentation import STALE_FRESHNESS

    by_market = ((data_quality or {}).get("quotes") or {}).get("by_market") or {}

    # 左侧分批接货比例(支撑位档位表): 从 computed_profile 个性化参数读取。
    _batch_ratios: list[float] = [0.40, 0.35, 0.25]
    try:
        from stocks.engine.profile_interpreter import load_computed, merge_with_defaults
        from pathlib import Path as _P
        _merged = merge_with_defaults(load_computed(_P(__file__).resolve().parent.parent.parent / ".local" / "computed_profile.json"))
        _br = _merged.get("left_batch_plan_ratios")
        if isinstance(_br, (list, tuple)) and _br:
            _batch_ratios = [float(x) for x in _br if isinstance(x, (int, float))]
    except Exception:
        pass

    candidates = []
    for item in (action_signals.get("items") or []):
        signal = item.get("signal", "")
        # Only research-only signals go here (not actionable signals)
        if signal in {
            "neutral_hold", "no_data", None, "reduce", "stop_loss", "take_profit",
            "add", "accumulate", "hard_stop_loss", "mid_stop_loss", "lock_profit",
        }:
            continue
        symbol = str(item.get("symbol") or "")
        if symbol and (blocked_symbols and symbol in blocked_symbols):
            continue
        candidate = {
            "symbol": symbol,
            "name": item.get("name"),
            "signal": signal,
            "action_hint": item.get("action_hint"),
            "reasons": (item.get("reasons") or [])[:2],
            "priority": "research_only",
            "score": item.get("_score"),
            "rank": item.get("rank"),
            # P0-1: generation_price 供 signal_tracker 记录(反馈闭环)。
            "price": item.get("price"),
            # P0(左侧): 左侧位置指标(布林位置/RSI/量比),渲染层据此呈现位置卡。
            "price_position": item.get("price_position"),
            "rsi_14": item.get("rsi_14"),
            "volume_ratio": item.get("volume_ratio"),
            # P1(左侧): 技术位供分批档位表(MA20/布林下轨/MA60)。
            "ma_20": item.get("ma_20"),
            "ma_60": item.get("ma_60"),
            "bollinger_lower": item.get("bollinger_lower"),
            # P2-1: technical-indicator as_of from the HistoryCache daily
            # bars (last bar timestamp), distinct from the realtime quotes
            # layer. Used by the freshness gate below; also surfaced so the
            # render layer can annotate staleness per candidate.
            "as_of": item.get("as_of"),
            # 左侧分批接货比例(支撑位档位表), 渲染层按实际档位对齐。
            "batch_ratios": _batch_ratios,
        }
        if suspend:
            candidate["reassess_after"] = f"风险解除后再评估（当前状态: {risk_level}）"
            candidate["condition"] = "risk_suspend_accumulation"
        # P1-15: freshness gate. A stale candidate is downgraded to pure
        # observation: no setup tag, no sizing guidance, reasons replaced by
        # the data-boundary note so the report never quotes precise prices it
        # just called stale.
        market = _research_market(symbol)
        quote_item = by_market.get(market) or {}
        quote_stale = str(quote_item.get("freshness") or "") in STALE_FRESHNESS
        # P2-1: the realtime quotes layer can look fresh while the daily
        # bars feeding price/MA/RSI stopped updating. Treat a candidate whose
        # indicator as_of is older than the tolerance window as stale too.
        tech_stale = _indicator_as_of_stale(candidate.get("as_of"))
        if quote_stale or tech_stale:
            candidate["quote_stale"] = True
            candidate["condition"] = "quote_stale"
            candidate["setup_tag"] = "观察"
            if quote_stale:
                candidate["reasons"] = ["行情数据过时，暂不评估技术面，待数据恢复后复核"]
                candidate["sizing_hint"] = "行情数据过时，不提供仓位/止损建议，待数据恢复后评估"
            else:
                candidate["reasons"] = ["技术指标数据停留较早，暂不评估技术面，待数据更新后复核"]
                candidate["sizing_hint"] = "技术指标数据过时，不提供仓位/止损建议，待数据更新后评估"
        else:
            # Inject human-readable sizing + stop-loss + risk conflict guidance.
            # In a suspend state _research_sizing_hint strips accumulation
            # phrasing (布局/试仓/轮入/建仓/分批) but PRESERVES the stop-loss
            # line — pausing new entries must not erase existing-position risk
            # protection. (This replaces a hardcoded generic sentence that
            # wiped the stop-loss guidance for every candidate.)
            candidate["sizing_hint"] = _research_sizing_hint(signal, risk_level, suspend)
        candidates.append(candidate)

    # Round-robin across signal types to avoid a single theme (e.g., deep
    # oversold) dominating the research list. Within each type, prefer higher
    # scores and lower ranks.
    order = [
        "accumulate_candidate", "rotation_candidate", "left_bottom_candidate",
        "wait_for_pullback", "reduce_risk", "avoid_catching_falling_knife",
    ]
    by_signal: dict[str, list[dict]] = {s: [] for s in order}
    for c in candidates:
        by_signal.setdefault(c.get("signal"), []).append(c)
    # P-market-focus: 候选 round-robin 按主市场优先（与 presentation 动作排序对齐），
    # 避免跨市场候选（如 A股 ETF 轮动 score 普遍高于美股）占满 8 个名额、挤出主市场候选。
    primary_market = str(getattr(session, "primary_market", "") or "")

    def _market_rank(symbol: str) -> int:
        m = _research_market(symbol)
        if primary_market == "us":
            return 0 if m == "us" else 1
        if primary_market == "cn":
            return 0 if m == "a" else 1
        return 1

    for group in by_signal.values():
        group.sort(
            key=lambda x: (
                _market_rank(str(x.get("symbol") or "")),
                -(x.get("score") or 0),
                x.get("rank") or 999,
                str(x.get("symbol") or ""),
            )
        )
    diverse: list[dict] = []
    while len(diverse) < 8 and any(by_signal.values()):
        for s in order:
            if len(diverse) >= 8:
                break
            if by_signal[s]:
                diverse.append(by_signal[s].pop(0))
    return diverse


def build_agent_task(session: ScheduledSession) -> dict:
    """v5 dual-layer report contract: trade card first, assistant second."""
    is_watch = session.intent in {"open_watch", "mid_session_check"}
    is_pre_close = session.intent == "pre_close_decision"
    delivery_policy = (
        "观察窗口：若 window_delta.material=false、没有获批动作且没有新增待人工确认事项，最终只输出 [SILENT]。"
        if is_watch else
        "主计划/复盘窗口：没有获批动作时仍输出“今日无需操作”，并展示1-2个关键原因。"
    )
    if is_pre_close:
        delivery_policy += " 收盘前窗口若既无获批动作也无新增冲突，可输出 [SILENT]。"
    return {
        "task_version": 5,
        "language": "zh-CN",
        "audience": "single_user",
        "session_intent": session.intent,
        "primary_market": session.primary_market,
        "must_answer": [
            "【交易指令卡】第一段只读取 portfolio_decision.user_view.instruction_card；原样表达状态、最多3个动作、比例、预计金额、取消条件、到账和下一检查点",
            "没有获批动作时，主窗口写“今日无需操作”并列出 instruction_card.no_action_reasons 中1-2个关键原因",
            "【私人投资助理】第二段只读取 portfolio_decision.user_view.assistant_brief（含 outlook 研判和 outlook_delta 观测变化），解释原因、当前不能做什么、四类资金、风险状态和观察候选",
            delivery_policy,
        ],
        "must_not_do": [
            "不得承诺收益",
            "数据质量、风险触发原因和信号分类已确定性写入 user_view；不得回读原始字段补充数字或结论",
            "不得向用户展示 position_id、decision_id、内部哈希、原始异常码、英文 signal/risk/liquidity 枚举",
            "所有标的必须使用 portfolio_decision.user_view 中的真实名称 + 公开代码；不得自行用代理标的代码替代基金代码",
            "动作卡必须显示结构化比例 + 预计金额；amount_is_estimate=true 时必须标注“估算”",
            "不得从 rationale、facts 或自由文本提取数字作为动作比例或金额",
            "suppressed_actions、unresolved_conflicts、research_candidates 不得变成新的交易动作",
            "私人投资助理只能解释，不能新增动作、金额或交易时机",
            "research_only 信号不得写入交易指令卡动作列表",
            "不得自动保存建议、执行或预测",
            "严禁使用 # 号标题（用**加粗**代替）",
            "严禁使用 | 表格、```代码块、>引用、---分隔线、任务列表或HTML",
        ],
        "data_reference": {
            "window_delta": "仅供生成器确定观察窗口静默；不得进入用户正文",
            "portfolio_decision": "用户正文唯一来源为 portfolio_decision.user_view（含 assistant_brief.outlook 和 outlook_delta）",
            "risk_state": "仅供生成器构建 user_view；消费端不得直接读取",
            "data_boundaries": "仅供生成器构建 user_view.data_notes；消费端不得直接读取",
            "research_candidates": "仅供生成器构建 user_view.research；消费端不得直接读取",
        },
        "output_structure": {
            "max_words": 900,
            "platform": "feishu",
            "format_rules": "仅使用**加粗**、`行内代码`、- 列表、[链接](url)和空行。",
            "sections": [
                {
                    "name": "交易指令卡",
                    "content": "必须位于最上方，只列可执行动作。读取 instruction_card.actions，每动作呈现：标的 + 方向(止盈/减仓/买入) + 最终比例 + 预计金额 + 可达平台。状态为需要操作时列全部可执行动作；actions_overflow 用一行'另有N项已获批可执行'概括，不逐条展开；状态为今日无需操作时列1-2个原因；状态为等待人工确认时说明冲突但不得生成交易动作。",
                },
                {
                    "name": "私人投资助理",
                    "content": "紧接交易指令卡。读取 assistant_brief，按下列顺序、且只讲决策结论：1)多空结论+关键证伪/验证条件一句话(outlook.near_term.direction + validation/falsification)；2)为什么是这样(动作的论断式理由,不罗列指标数值)；3)当前不要做什么(do_not_do)；4)资金：现在能用多少+卖出后才能用多少(各一句)；5)风险状态一句；6)仅供观察的候选(≤3组,每组一句话+标的名单)；7)下一检查点。",
                },
            ],
            "render_discipline": [
                "只输出对用户决策有用的结论，不逐条罗列 MA/RSI/布林/量比/偏离等指标数值、不罗列 evidence_summary/source_refs/rule_scorecard/shadow_account 统计(累计分析次数/历史建议分布/历史胜率)。这些是系统内部分析用数据，保留在 artifact 供深挖，不进报告正文。",
                "多空对抗分析只给结论+证伪/验证条件一句话，不展开多空两栏的长篇逻辑。",
                "同一动作不在'为什么'与'明日计划'重复展开——'为什么'交代原因即可。",
                "观察候选按动作语义分组：原因相同/相近的标的合并为一行'同类候选：A/B/C'，只保留一组完整原因+完整标的名单，不逐条重复模板话术。",
            ],
        },
        "persona": _build_persona(),
        "adaptability": {
            "delivery_policy": delivery_policy,
            "card_first": "无论篇幅长短，交易指令卡必须是第一段，私人投资助理必须紧接第二段",
            "no_new_actions": "私人投资助理不得新增 portfolio_decision.user_view.instruction_card.actions 之外的动作",
        },
        "final_analysis_instructions": (
            "严格按 portfolio_decision.user_view 输出双层报告：交易指令卡在上，私人投资助理紧接在下。"
            "只使用确定性人类展示字段，不输出内部代号，不进行二次计算。"
            "报告是给人看的决策简报，不是系统数据导出：只呈现'该做什么/该信什么/什么会改判'，"
            "技术指标数值、证据明细、历史统计仅作为内部判断素材，不得逐条罗列进正文。"
        ),
    }

def _build_execution_review_summary(recent_advice: list[dict]) -> dict:
    """从 recent_advice 的 execution_review 汇总执行状态。"""
    all_reviews: list[dict] = []
    status_counts: dict[str, int] = {}
    for advice in recent_advice:
        for review in (advice.get("execution_review") or []):
            all_reviews.append(review)
            st = review.get("status", "unknown")
            status_counts[st] = status_counts.get(st, 0) + 1
    return {
        "total": len(all_reviews),
        "status_counts": dict(status_counts),
        "reviews": all_reviews,
    }



def _code_version() -> dict:
    """返回生产代码版本快照: git commit hash、dirty flag、包版本。

    供 artifact 'code_version' 字段用于跨窗口可追溯与版本漂移定位。
    无法获取 git 信息时返回 unknown,不阻断生成。
    """
    import subprocess
    commit = None
    dirty = None
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=str(Path(__file__).resolve().parents[2]),
        ).stdout.strip() or None
        dirty_out = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
            cwd=str(Path(__file__).resolve().parents[2]),
        ).stdout.strip()
        dirty = bool(dirty_out)
    except Exception:
        commit, dirty = None, None
    try:
        from stocks import __version__
        ver = __version__
    except Exception:
        ver = None
    return {
        "commit": commit or "unknown",
        "dirty": dirty if dirty is not None else None,
        "version": ver,
    }

def format_run_markdown(run: dict) -> str:
    """Render the deterministic trade-card-first human report."""
    decision = run.get("portfolio_decision") or {}
    view = decision.get("user_view") or {}
    card = view.get("instruction_card") or {}
    assistant = view.get("assistant_brief") or {}
    session_labels = {
        "cn_post_open": "A股开盘后", "cn_after_close": "A股盘后复盘",
        "us_post_open": "美股开盘后", "us_after_close": "美股盘后复盘",
        "global_intelligence_watch": "每日全球情报",
    }
    session_label = session_labels.get(str(run.get("session") or ""), "交易窗口")
    lines = [
        f"**{session_label} · {run.get('market_date', '')}**",
        "",
        "**交易指令卡**",
        f"- **{card.get('status_label', '等待人工确认')}**",
    ]
    actions = card.get("actions") or []
    if actions:
        for action in actions[:3]:
            ratio = float(action.get("ratio") or 0.0)
            lines.append(
                f"- **{action.get('action_label', '待确认动作')}｜{action.get('display_label', '未命名持仓')}**"
            )
            lines.append(f"  - 比例: {ratio * 100:.0f}%")
            amount = action.get("estimated_amount_cny")
            if amount is None:
                lines.append("  - 预计金额: 金额待确认")
            else:
                estimate = "（估算）" if action.get("amount_is_estimate") else ""
                lines.append(f"  - 预计金额: ¥{float(amount):,.0f}{estimate}")
            # P1-2/P2-3 fix: 呈现触发依据(decision_reason)与操作平台/渠道——
            # Kari"以明确止盈/止损点为决策依据",只见指令不见依据等于把答案藏进抽屉。
            decision_reason = action.get("decision_reason")
            if decision_reason:
                lines.append(f"  - 依据: {decision_reason}")
            # 跨市场/休市执行时点: 例如 A股盘后窗口推的美股减仓, 美股可能尚未开盘,
            # 如实告知"何时能执行", 避免把"过期/休市"动作当成"现在就能执行"。
            session_note = action.get("market_session_note")
            if session_note:
                lines.append(f"  - 执行时点: {session_note}")
            platform = action.get("platform")
            channel = action.get("operation_channel")
            if platform:
                channel_txt = f"（{channel}）" if channel and channel != platform else ""
                lines.append(f"  - 平台: {platform}{channel_txt}")
            lines.append(f"  - 取消条件: {action.get('cancel_condition', '条件不再成立时取消')}")
            lines.append(f"  - 到账: {action.get('settlement_display', '到账时间待确认')}")
            lines.append(f"  - 下次检查: {action.get('next_checkpoint', '下一交易窗口复核')}")
    else:
        for reason in (card.get("no_action_reasons") or ["当前没有满足执行条件的获批动作"])[0:2]:
            lines.append(f"- 原因: {reason}")
        lines.append(f"- 下次检查: {card.get('next_checkpoint', '下一交易窗口复核')}")
    # P0-3 fix: 被展示名额(actions[:3])截断的可执行动作不得静默消失——显式提示用户
    # 还有几项未展示，避免 Kari 看不到完整的止盈/减仓指令。
    overflow = int(card.get("actions_overflow") or 0)
    if overflow > 0:
        lines.append(f"- 注: 另有 {overflow} 项已获批可执行动作未在此完整展示，请以下一窗口/完整复核为准")
    # P0-3 fix: 被执行/数据门禁暂缓但仍获批的动作(如场外基金无实时行情)须呈现，
    # 不得静默丢弃——Kari 明确要求"系统有问题就告诉我系统有问题，不要掩盖"。
    for ref in (card.get("suppressed_actions_reference") or [])[:3]:
        ratio = ref.get("ratio")
        ratio_txt = f" {ratio * 100:.0f}%" if isinstance(ratio, (int, float)) else ""
        amount = ref.get("estimated_amount_cny")
        amount_txt = f"，金额 {float(amount):,.0f} 元" if amount is not None else ""
        note = f"（{ref.get('amount_blocked_reason')}）" if ref.get("amount_blocked_reason") else ""
        # 2026-08-14: 区分"已获批可执行但超展示名额"与"被门禁暂缓"——两者措辞不同,
        # 把可执行卖出标成"暂缓待人工核实"会让 Kari 误以为不用执行, 掩盖真实动作。
        if ref.get("executable"):
            reason = ref.get("deferred_reason") or ""
            s_note = ref.get("market_session_note") or ""
            s_note_txt = f"；{s_note}" if s_note else ""
            lines.append(
                f"- **{ref.get('signal_type', '动作')}｜{ref.get('display_label', '未命名持仓')}**"
                f"{ratio_txt}{amount_txt}{note}（已获批可执行，超出展示名额待核对{s_note_txt}）"
            )
        else:
            reason = ref.get("deferred_reason") or ""
            lines.append(
                f"- **{ref.get('signal_type', '动作')}｜{ref.get('display_label', '未命名持仓')}**"
                f"{ratio_txt}{amount_txt}{note}（暂缓：{reason or '待人工核实'}）"
            )

    lines.extend(["", "**私人投资助理**", "", "**为什么这样安排**"])
    for reason in (assistant.get("why") or ["当前决策以组合裁决结果为准"])[0:5]:
        lines.append(f"- {reason}")

    conflicts = assistant.get("conflict_summary") or []
    if conflicts:
        lines.extend(["", "**待人工确认的信号分类**"])
        for item in conflicts:
            lines.append(f"- {item.get('action_label', '待确认动作')}: {int(item.get('count') or 0)} 项")

    lines.extend(["", "**现在不要做什么**"])
    do_not_do = assistant.get("do_not_do") or []
    if do_not_do:
        for item in do_not_do[:5]:
            lines.append(f"- {item}")
    else:
        lines.append("- 无额外禁止事项")

    lines.extend(["", "**资金状态**"])
    cash = assistant.get("cash") or {}
    for key in ("available_now", "confirmed_settling", "planned_release", "strategic_exit", "locked", "safety_buffer"):
        item = cash.get(key) or {}
        lines.append(f"- {item.get('label', '资金待确认')}: ¥{float(item.get('amount_cny') or 0.0):,.0f}")
    # P0-4 fix: "到账途中/卖出后可用"含已批准但尚未确认成交的卖出估算。
    # 引擎 stateless，无法知道 Kari 是否已执行卖出；必须诚实标注"估算、以成交为准"，
    # 否则 Kari 会把建议回款当成真金在途（Kari 核心原则：系统有问题就说，不要掩盖）。
    if cash.get("pending_sell"):
        lines.append("- 注: “到账途中/卖出后可用”含已批准但尚未确认成交的卖出估算，实际到账以成交为准")

    risk = assistant.get("risk") or {}
    lines.extend(["", "**组合与风险**"])
    lines.append(f"- 当前状态: {risk.get('label', '风险状态待确认')}（{risk.get('transition', '状态待确认')}）")
    if risk.get("suspend_accumulation"):
        lines.append("- 当前暂停加仓")
    for reason in risk.get("reasons") or []:
        lines.append(f"- 触发原因: {reason}")
    lines.append(f"- 解除条件: {risk.get('release_condition', '等待风险条件明确')}")

    data_notes = assistant.get("data_notes") or []
    if data_notes:
        lines.extend(["", "**数据说明**"])
        for note in data_notes:
            lines.append(f"- {note}")
    # P0-5 fix: 反馈闭环回流——把 signal_tracker 真实结算胜率轻量呈现给用户,
    # 让 Kari 知道系统在自我跟踪"这类信号历史表现"(诚实标注样本量,不编造)。
    # 2026-08-15: 叠加样本质量标注——对抗性校验发现, 结算器修复后短期内积累的
    # 样本看似量大, 实则被少数标的与单日行情主导, 对左侧交易者还存在"24h上涨率"
    # 与"分批左侧加仓"的方向错配。因此当样本未达到可信标准(trustable=False)时,
    # 必须诚实声明"当前胜率尚不足以作为信任/降权依据", 而非把它当成定论。
    _fb = (run.get("rule_scorecard") or {}).get("feedback") or {}
    _fb_eng = (_fb.get("by_source_direction_window") or {}).get("engine_action/buy/24h")
    if _fb and _fb.get("total_settled"):
        if data_notes:
            pass
        else:
            lines.extend(["", "**数据说明**"])
        _fbtxt = ""
        _quality_note = ""
        if _fb_eng and _fb_eng.get("total"):
            _fbtxt = f"；引擎买入信号24h胜率 {_fb_eng.get('ok')}/{_fb_eng.get('total')}={_fb_eng.get('win_rate',0)*100:.0f}%"
            _sq = (_fb_eng.get("sample_quality") or {})
            if not _sq.get("trustable"):
                # 样本不足或单日主导: 明确这不是可信任的胜率, 避免误导左侧决策
                _quality_note = f"。该胜率{_sq.get('note') or '样本质量不足'}"
        lines.append(f"- 反馈闭环: 已跟踪 _fb_total 条历史信号结算{_fbtxt}（用于持续校准，不构成单一信号依据）".replace(
            "_fb_total", str(_fb.get("total_settled"))) + _quality_note)

    lines.extend(["", "**仅供观察**"])
    research = assistant.get("research") or []
    if research:
        for item in research[:8]:
            lines.append(f"- **{item.get('display_label', '未命名标的')}**: {item.get('action_hint', '仅供观察')}")
            # P1-6 fix: 渲染 sizing/止损纪律(仓位/止损位)。左侧交易者"以明确止盈/止损
            # 点为决策依据",试仓的仓位与止损是 Kari 需要的最低信息;这些已存在 JSON 的
            # sizing_hint, 之前渲染层丢弃了它。
            sizing = item.get("sizing_hint")
            if sizing:
                lines.append(f"  - 仓位/止损: {sizing}")
            if item.get("reassess_after"):
                lines.append(f"  - 再评估: {item['reassess_after']}")
    else:
        lines.append("- 暂无需要重点跟踪的研究候选")
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
                "evidence": item.get("evidence") or {},
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
            horizon=str(item.get("horizon") or "long"),
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
        # 路由推导：直接消费 engine.yaml quant_action.product_type_rules 的
        # mode 字段，单一权威（2026-08-22 配置化后消除此处的同步副本）。
        from stocks.engine.quant_action import _load_product_type_rules
        routing = (_load_product_type_rules().get(product_type) or {}).get("mode", "")
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

        account_id = (item.get("account") or {}).get("account_id", "") or item.get("account_id", "")
        institution_type = (item.get("account") or {}).get("institution_type", "") or _institution_type_for_account_id(account_id)
        cards.append({
            "position_id": decision.position_id,
            "display_name": item.get("display_name", ""),
            "instrument_key": item.get("instrument_key", ""),
            "product_type": product_type,
            "routing": routing,
            "account_type": account_type,
            "account_id": account_id,
            "institution_type": institution_type,
            "platform_display": _platform_display(institution_type, account_id),
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
            # ── Phase 2: 结算和平台信息 ──
            "settlement_timing": getattr(decision, 'settlement_timing', '') or _settlement_timing_for_institution(institution_type, routing),
            "operation_channel": _operation_channel(institution_type, account_id, routing),
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
    context_config: Optional[dict] = None,
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
    # 最小加仓金额阈值（CNY），从 engine.yaml portfolio_layering.min_add_amount_cny 读取。
    min_add_amount_cny = float(
        (context_config or {}).get("min_add_amount_cny", 800.0)
    )

    # 可用资金池
    available_cash = liq_buckets.get("cash_or_t0", {}).get("value_cny", 0)
    strategic_exit_value = liq_buckets.get("t1_t2", {}).get("value_cny", 0)
    safety_buffer = total_value * 0.05
    net_deployable = max(0, available_cash + reduce_proceeds - safety_buffer)

    # ── M4: 分池可动用资金。隔离池的现金与减仓回款只计入本池，
    # 任何加仓建议的出资计算不得跨池。
    from stocks.engine.constraint_model import ConstraintModel as _CM

    constraint_model = _CM.from_config(constraints or {})
    pool_of_pid: dict[str, str] = {}
    pool_totals: dict[str, float] = {}
    pool_cash: dict[str, float] = {}
    for pv in position_valuations:
        pid = pv.get("position_id", "")
        pool = constraint_model.pool_of(pid, str(pv.get("account_id") or ""))
        pool_of_pid[pid] = pool
        mv = pv.get("market_value_cny") or 0.0
        pool_totals[pool] = pool_totals.get(pool, 0.0) + mv
        tier = ((pv.get("liquidity") or {}).get("tier") or "")
        tradable = (pv.get("liquidity") or {}).get("tradable")
        if tier in ("cash", "t0") and tradable is not False:
            pool_cash[pool] = pool_cash.get(pool, 0.0) + mv
    pool_proceeds: dict[str, float] = {}
    for item in reduce_items:
        pool = pool_of_pid.get(item["position_id"], "domestic")
        pool_proceeds[pool] = pool_proceeds.get(pool, 0.0) + item["proceeds_cny"]
    pools_funding: dict[str, dict] = {}
    if constraint_model.has_pools:
        for pool in sorted(pool_totals):
            pool_safety = pool_totals[pool] * 0.05
            deployable = max(
                0.0,
                pool_cash.get(pool, 0.0) + pool_proceeds.get(pool, 0.0) - pool_safety,
            )
            pools_funding[pool] = {
                "label": constraint_model.pool_label(pool),
                "isolated": constraint_model.is_isolated(pool),
                "total_value_cny": round(pool_totals[pool], 2),
                "available_cash_cny": round(pool_cash.get(pool, 0.0), 2),
                "reduce_proceeds_cny": round(pool_proceeds.get(pool, 0.0), 2),
                "safety_buffer_cny": round(pool_safety, 2),
                "net_deployable_cny": round(deployable, 2),
            }

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
        # Skip trivial allocations below configured threshold (do NOT mutate card)
        mv = 0.0
        for pv in position_valuations:
            if pv.get("position_id") == card["position_id"]:
                mv = pv.get("market_value_cny") or 0.0
                break
        alloc_amount = mv * abs(card.get("ratio", 0))
        if alloc_amount < min_add_amount_cny:
            # Suppression only — original card kept intact.
            # Suppression record is produced by build_capital_allocation_with_suppression.
            continue
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
        cand_pool = pool_of_pid.get(card["position_id"], "domestic")
        add_candidates.append({
            "position_id": card["position_id"],
            "action": card["action"],
            "ratio": card["ratio"],
            "position_limit_pct": card.get("position_limit_pct", 5.0),
            "current_weight_pct": card.get("current_weight_pct") or 0,
            "priority_score": round(score, 4),
            "constraint_note": "；".join(penalty_reasons) if penalty_reasons else "无约束冲突",
            "facts": card.get("facts", [])[:2],
            # M4: 加仓的出资来源仅限本池可动用资金（隔离池不跨池）
            "pool": cand_pool,
            "pool_label": constraint_model.pool_label(cand_pool),
            "funding_deployable_cny": (
                pools_funding.get(cand_pool, {}).get("net_deployable_cny")
                if pools_funding else round(net_deployable, 2)
            ),
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
        "strategic_exit_value_cny": round(strategic_exit_value, 2),
        "reduce_proceeds_cny": round(reduce_proceeds, 2),
        "net_deployable_cny": round(net_deployable, 2),
        "add_candidates": add_candidates[:5],
        "idle_cash_suggestions": idle_cash_suggestions[:3],
        "priority_summary": "；".join(priority_summary) if priority_summary else "无优先动作",
        # M4: 分池资金（定义了 pools 时存在；隔离池资金不跨池出资）
        "pools": pools_funding,
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


def _priority(
    risk_state: Optional[dict] = None,
    portfolio_decision: Optional[dict] = None,
    fired_triggers: Optional[list[str]] = None,
    trigger_reviews: Optional[list[dict]] = None,
    position_reviews: Optional[list[dict]] = None,
) -> str:
    """Compute session priority based on risk state, portfolio decision, and triggers.

    Priority rules:
    - Critical: risk hedge/escalation, approved stop_loss/urgent actions, fired stop-loss triggers
    - High: risk reduce/watch, review_required portfolio decision
    - Normal: everything else (normal risk + no approved urgent, even if manual gold high loss)
    """
    risk_state = risk_state or {}
    portfolio_decision = portfolio_decision or {}
    fired_triggers = fired_triggers or []
    trigger_reviews = trigger_reviews or []
    position_reviews = position_reviews or []

    # 1. Only a new hedge escalation is critical; a persistent unchanged hedge
    # remains high and is handled by WindowDelta without repeated alarm spam.
    risk_level = risk_state.get("level", "normal")
    risk_transition = risk_state.get("transition", "")
    if risk_level == "hedge" and risk_transition == "escalated":
        return "critical"

    # 2. Approved stop_loss or urgent reduce -> critical
    for action in portfolio_decision.get("approved_actions") or []:
        sig = action.get("signal", "")
        ratio = abs(action.get("ratio", 0.0) or 0.0)
        if sig in ("stop_loss",) or (sig == "reduce" and ratio >= 0.5):
            return "critical"

    # 3. Only stop-loss / urgent fired triggers are critical.
    for item in trigger_reviews:
        if item.get("status") != "fired":
            continue
        trigger_type = str(item.get("type") or item.get("trigger_type") or "")
        urgency = str(item.get("urgency") or "")
        if "stop_loss" in trigger_type or urgency == "critical":
            return "critical"
    if any(str(item).startswith("stop_loss") for item in fired_triggers):
        return "critical"

    # 4. Persistent hedge/reduce risk -> high
    if risk_level in ("hedge", "reduce"):
        return "high"

    # 5. review_required -> high
    if portfolio_decision.get("status") == "review_required":
        return "high"

    # 6. High/severe loss with rebalance eligibility (from position_reviews)
    for item in position_reviews:
        loss_level = item.get("loss_level", "normal")
        liquidity = item.get("liquidity") or {}
        if loss_level in {"high", "severe"} and liquidity.get("rebalance_eligible") is not False:
            return "high"

    return "normal"


def _notification(
    *,
    session: ScheduledSession,
    priority: str,
    now: datetime,
    quiet_hours: dict,
    window_delta: Optional[WindowDelta] = None,
) -> dict:
    # Delta-driven: if no material change and session is silent-when-unchanged, archive_only
    if (
        priority != "critical"
        and window_delta is not None
        and not window_delta.material
        and not window_delta.first_in_session
        and session.delta_silent_when_unchanged
    ):
        return {
            "recommended": False,
            "urgency": priority,
            "quiet_hours_blocked": False,
            "policy": "archive_only",
            "delta_driven": True,
        }
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


def _run_status(data_quality: dict, *, primary_market: str = "") -> str:
    degraded_sections = []
    market_key = {"cn": "a", "us": "us", "crypto": "crypto"}.get(primary_market)
    for key in ("asset_completeness", "quotes", "history_backfill", "rotation", "action_signals"):
        section = data_quality.get(key) or {}
        status = section.get("status")
        global_severe = status in {"blocked", "failed", "no_data"}
        if key == "quotes" and market_key and not global_severe:
            market_quality = (section.get("by_market") or {}).get(market_key) or {}
            market_status = market_quality.get("status")
            market_freshness = market_quality.get("freshness")
            if market_status:
                status = market_status
            if market_freshness in {"stale", "old", "unknown", "missing"}:
                status = "degraded"
        if status in {"blocked", "failed", "no_data"}:
            return "degraded"
        if status in {"degraded", "partial", "stale_fallback"}:
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


_DEFAULT_HEADLINES = {
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
}


def _headline(session: ScheduledSession | str) -> str:
    """会话摘要标题。

    优先使用 session 配置中的 headline 覆盖字段；未配置时回退到内置默认表，
    最后兜底为 ``{session_id}: scheduled analysis``。接受 ScheduledSession
    或纯 session_id 字符串（向后兼容）。
    """
    if isinstance(session, str):
        session_id = session
        configured = None
    else:
        session_id = session.id
        configured = session.headline
    if configured:
        return configured
    return _DEFAULT_HEADLINES.get(session_id, f"{session_id}: scheduled analysis")


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
    "us_post_open": {"focus": "us+欧美市场", "can_recommend_new": True, "can_review_closed": False},
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


def _session_intent_props(session: ScheduledSession | str) -> dict:
    """会话意图属性（focus / can_recommend_new / can_review_closed）。

    以内置默认表为基底，再用 session 配置中的 focus/can_recommend_new/
    can_review_closed 覆盖字段逐项覆写（未配置的字段保持默认值）。
    接受 ScheduledSession 或纯 session_id 字符串（向后兼容）。
    """
    if isinstance(session, str):
        session_id = session
        focus = can_recommend_new = can_review_closed = None
    else:
        session_id = session.id
        focus = session.focus
        can_recommend_new = session.can_recommend_new
        can_review_closed = session.can_review_closed
    props = dict(
        _SESSION_INTENT_PROPERTIES.get(
            session_id,
            {"focus": "cross_market", "can_recommend_new": True, "can_review_closed": False},
        )
    )
    if focus is not None:
        props["focus"] = focus
    if can_recommend_new is not None:
        props["can_recommend_new"] = bool(can_recommend_new)
    if can_review_closed is not None:
        props["can_review_closed"] = bool(can_review_closed)
    return props
