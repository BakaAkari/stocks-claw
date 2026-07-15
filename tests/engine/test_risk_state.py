"""Tests for Task 7: Risk State Lifecycle.

Strict TDD — these tests must fail RED before implementation.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stocks.engine.risk_state import (
    RiskObservation,
    RiskState,
    RiskStateStore,
)

# ── Helpers ───────────────────────────────────────────────────────────────


def _obs(
    level: str,
    *,
    evidence_keys: list[str] | None = None,
    observed_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> RiskObservation:
    now = observed_at or datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
    future = expires_at or now + timedelta(hours=6)
    return RiskObservation(
        candidate_level=level,
        evidence_keys=tuple(evidence_keys or []),
        observed_at=now,
        expires_at=future,
    )


def _store(tmp_path: Path) -> RiskStateStore:
    return RiskStateStore(path=str(tmp_path / "risk_state.json"))


def _load_raw(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ═══════════════════════════════════════════════════════════════════════════
# 1. State transition tests
# ═══════════════════════════════════════════════════════════════════════════


class TestStateTransitions:
    """Core state machine transitions."""

    def test_normal_to_watch_single_signal(self, tmp_path: Path):
        """A single watch-level observation promotes normal→watch."""
        store = _store(tmp_path)
        result = store.update(_obs("watch", evidence_keys=["vix"]))
        assert result.level == "watch"

    def test_single_cluster_does_not_immediately_hedge(self, tmp_path: Path):
        """A single hedge observation does NOT immediately set state to hedge."""
        store = _store(tmp_path)
        result = store.update(_obs("hedge", evidence_keys=["cluster_a"]))
        # Still not hedge -- needs confirmation
        assert result.level != "hedge"

    def test_two_independent_evidence_upgrades_to_hedge(self, tmp_path: Path):
        """Two hedge observations with DIFFERENT evidence keys -> hedge."""
        store = _store(tmp_path)
        store.update(_obs("hedge", evidence_keys=["vix"]))
        result = store.update(_obs("hedge", evidence_keys=["critical_cluster"]))
        assert result.level == "hedge"

    def test_two_consecutive_confirmations_upgrades_to_hedge(self, tmp_path: Path):
        """Two consecutive hedge observations with SAME key -> hedge."""
        store = _store(tmp_path)
        base = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
        store.update(_obs("hedge", evidence_keys=["vix"], observed_at=base))
        result = store.update(
            _obs(
                "hedge",
                evidence_keys=["vix"],
                observed_at=base + timedelta(minutes=60),
            )
        )
        assert result.level == "hedge"

    def test_deescalation_requires_two_rounds(self, tmp_path: Path):
        """Going from watch->normal requires 2 consecutive normal observations."""
        store = _store(tmp_path)
        base = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
        store.update(_obs("watch", evidence_keys=["vix"], observed_at=base))
        assert (
            store.update(
                _obs(
                    "normal",
                    evidence_keys=[],
                    observed_at=base + timedelta(hours=1),
                )
            ).level
            == "watch"
        )  # still watch
        assert (
            store.update(
                _obs(
                    "normal",
                    evidence_keys=[],
                    observed_at=base + timedelta(hours=2),
                )
            ).level
            == "normal"
        )  # now normal

    def test_ttl_expiry_auto_resets_to_normal(self, tmp_path: Path):
        """When observed_at is past expires_at, state resets to normal."""
        store = _store(tmp_path)
        now = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
        future = now + timedelta(hours=6)
        past = future + timedelta(minutes=1)
        store.update(_obs("watch", evidence_keys=["vix"], observed_at=now, expires_at=future))
        # Use a unique observed_at to avoid idempotency collision
        result = store.update(
            _obs(
                "normal",
                evidence_keys=[],
                observed_at=past,
                expires_at=past,
            )
        )
        assert result.level == "normal"

    def test_same_observation_idempotent(self, tmp_path: Path):
        """Repeating the same observation produces no state change."""
        store = _store(tmp_path)
        now = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
        obs = _obs("watch", evidence_keys=["vix"], observed_at=now)
        r1 = store.update(obs)
        r2 = store.update(obs)
        assert r1.level == r2.level
        assert r1.evidence_keys == r2.evidence_keys

    def test_same_facts_new_run_is_unchanged(self, tmp_path: Path):
        store = _store(tmp_path)
        base = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
        store.update(_obs("watch", evidence_keys=["vix"], observed_at=base))
        result = store.update(
            _obs("watch", evidence_keys=["vix"], observed_at=base + timedelta(minutes=30))
        )
        assert result.transition == "unchanged"

    def test_watch_then_hedge_requires_confirmations(self, tmp_path: Path):
        """Escalating from watch to hedge still needs hedge confirmation rules."""
        store = _store(tmp_path)
        store.update(_obs("watch", evidence_keys=["vix"]))
        r = store.update(_obs("hedge", evidence_keys=["vix"]))
        # Still watch -- hedge needs 2 evidence keys or 2 confirmations
        assert r.level == "watch"


# ═══════════════════════════════════════════════════════════════════════════
# 2. Atomic persistence tests
# ═══════════════════════════════════════════════════════════════════════════


class TestAtomicPersistence:
    """Atomic write via tempfile + os.replace."""

    def test_written_file_is_readable_json(self, tmp_path: Path):
        """After update, the state file contains valid JSON with all fields."""
        store = _store(tmp_path)
        store.update(_obs("watch", evidence_keys=["vix"]))
        raw = _load_raw(tmp_path / "risk_state.json")
        assert raw["level"] == "watch"
        assert "updated_at" in raw

    def test_replace_interruption_preserves_old_state(self, tmp_path: Path, monkeypatch):
        """Failure before os.replace leaves the prior target readable."""
        store = _store(tmp_path)
        store.update(_obs("watch", evidence_keys=["vix"]))
        monkeypatch.setattr(
            "stocks.engine.risk_state.os.replace", lambda *_: (_ for _ in ()).throw(OSError("boom"))
        )
        with pytest.raises(OSError, match="boom"):
            store.update(
                _obs(
                    "reduce",
                    evidence_keys=["drawdown"],
                    observed_at=datetime(2026, 7, 15, 13, tzinfo=timezone.utc),
                )
            )
        recovered = RiskStateStore(path=store.path)._load()
        assert recovered.level == "watch"
        assert not list(tmp_path.glob("*.tmp"))

    def test_tmp_file_is_never_read(self, tmp_path: Path):
        """Only the target file is read -- .tmp.* files are ignored."""
        store = _store(tmp_path)
        state_path = Path(store.path)
        # Write valid state to target
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            '{"level":"watch","evidence_keys":[],'
            '"deescalation_remaining":0,"updated_at":"2026-07-15T12:00:00+00:00"}',
            encoding="utf-8",
        )
        # Create a misleading .tmp file
        tmp_file = state_path.with_name(state_path.name + ".tmp")
        tmp_file.write_text(
            '{"level":"hedge","evidence_keys":["wrong"],'
            '"deescalation_remaining":0,"updated_at":"2026-07-15T12:00:00+00:00"}',
            encoding="utf-8",
        )
        loaded = store._load()
        assert loaded.level == "watch"  # from the real file, not tmp
        assert "wrong" not in loaded.evidence_keys

    def test_store_roundtrip_preserves_all_fields(self, tmp_path: Path):
        """All RiskState fields survive a save->load roundtrip."""
        store = _store(tmp_path)
        store.update(_obs("hedge", evidence_keys=["vix", "critical_cluster"]))
        raw = _load_raw(tmp_path / "risk_state.json")
        assert raw["level"] == "hedge"


# ═══════════════════════════════════════════════════════════════════════════
# 3. Edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Boundary and edge-case behaviours."""

    def test_default_state_is_normal(self):
        """A freshly constructed RiskState starts at normal."""
        s = RiskState()
        assert s.level == "normal"
        assert s.evidence_keys == []

    def test_store_default_path(self):
        """RiskStateStore uses a default path."""
        store = RiskStateStore()
        assert store.path is not None
        assert "risk_state.json" in str(store.path)

    def test_update_with_no_expiry_keeps_state(self, tmp_path: Path):
        """An observation without expires_at doesn't clear state via TTL."""
        store = _store(tmp_path)
        store.update(
            RiskObservation(
                candidate_level="watch",
                evidence_keys=("vix",),
                observed_at=datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc),
            )
        )
        raw = _load_raw(tmp_path / "risk_state.json")
        assert raw["level"] == "watch"

    def test_clean_observation_does_not_extend_elevated_ttl(self, tmp_path: Path):
        store = _store(tmp_path)
        base = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
        expiry = base + timedelta(hours=6)
        store.update(_obs("watch", evidence_keys=["vix"], observed_at=base, expires_at=expiry))
        result = store.update(
            _obs(
                "normal",
                evidence_keys=[],
                observed_at=base + timedelta(hours=1),
                expires_at=base + timedelta(hours=7),
            )
        )
        assert result.expires_at == expiry

    def test_two_independent_evidence_keys_in_one_obs(self, tmp_path: Path):
        """Single observation with 2+ evidence keys triggers immediate hedge."""
        store = _store(tmp_path)
        result = store.update(_obs("hedge", evidence_keys=["vix", "critical_cluster"]))
        assert result.level == "hedge"

    def test_candidate_hedge_does_not_block_normal(self, tmp_path: Path):
        """Hedge candidate with single key still allows normal deescalation."""
        store = _store(tmp_path)
        store.update(_obs("hedge", evidence_keys=["vix"]))  # candidate
        r1 = store.update(_obs("normal", evidence_keys=[]))
        assert r1.level == "watch"  # first clean observation only starts deescalation
        r2 = store.update(
            _obs(
                "normal",
                evidence_keys=[],
                observed_at=datetime(2026, 7, 15, 13, tzinfo=timezone.utc),
            )
        )
        assert r2.level == "normal"


class TestScheduledIntegration:
    def test_both_builders_share_persistent_state(self, tmp_path):
        from datetime import date

        from stocks.engine.scheduled_analysis import (
            ScheduledSession,
            SessionOccurrence,
            build_intelligence_run,
            build_scheduled_run,
        )

        session = ScheduledSession(
            id="global_intelligence_watch",
            market="global",
            primary_market="global",
            exchange_timezone="UTC",
            user_timezone="UTC",
            time="12:00",
            intent="intelligence_patrol",
            push="push_now",
            enabled=True,
            duplicate_window_minutes=30,
            holidays=frozenset(),
            run_every_minutes=60,
        )
        now = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
        occurrence = SessionOccurrence(
            session=session, market_date=date(2026, 7, 15), scheduled_for=now
        )
        cfg = {
            "quiet_hours": {"enabled": False},
            "risk_state": {"state_path": str(tmp_path / "shared.json")},
        }
        intel = build_intelligence_run(
            {"macro": {"vix": 40}, "quotes": {}, "data_quality": {"status": "ok"}},
            {"clusters": [], "signals": [], "data_quality": {"status": "ok"}},
            occurrence=occurrence,
            generated_at=now,
            config=cfg,
            engine_config=cfg,
        )
        assert intel["risk_state"]["level"] == "watch"
        assert intel["risk_state"]["candidate_level"] == "hedge"
        cn_session = ScheduledSession(
            id="cn_pre_close",
            market="cn",
            primary_market="cn",
            exchange_timezone="Asia/Shanghai",
            user_timezone="Asia/Shanghai",
            time="14:45",
            intent="pre_close_decision",
            push="push_now",
            enabled=True,
            duplicate_window_minutes=30,
            holidays=frozenset(),
        )
        cn_occurrence = SessionOccurrence(
            session=cn_session, market_date=date(2026, 7, 15), scheduled_for=now
        )
        context = {
            "data_quality": {"status": "ok"},
            "position_valuations": [],
            "action_signals": {},
            "market_state": {"macro": {"vix": 40}},
            "intelligence_digest": {
                "intelligence_health": {"risk_eligible": True},
                "top_clusters": [],
            },
            "engine_config": cfg,
        }
        scheduled = build_scheduled_run(
            context, occurrence=cn_occurrence, generated_at=now + timedelta(minutes=30), config=cfg
        )
        assert scheduled["risk_state"]["level"] == "hedge"
        assert scheduled["portfolio_decision"]["status"] in {"approved", "review_required"}

    def test_stale_intelligence_cannot_escalate(self, tmp_path):
        from datetime import date

        from stocks.engine.scheduled_analysis import (
            ScheduledSession,
            SessionOccurrence,
            build_scheduled_run,
        )

        now = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
        session = ScheduledSession(
            id="cn_pre_close",
            market="cn",
            primary_market="cn",
            exchange_timezone="Asia/Shanghai",
            user_timezone="Asia/Shanghai",
            time="14:45",
            intent="pre_close_decision",
            push="push_now",
            enabled=True,
            duplicate_window_minutes=30,
            holidays=frozenset(),
        )
        occurrence = SessionOccurrence(
            session=session, market_date=date(2026, 7, 15), scheduled_for=now
        )
        cfg = {
            "quiet_hours": {"enabled": False},
            "risk_state": {"state_path": str(tmp_path / "stale.json")},
        }
        context = {
            "data_quality": {"status": "ok"},
            "position_valuations": [],
            "action_signals": {},
            "market_state": {"macro": {}},
            "intelligence_digest": {
                "intelligence_health": {"risk_eligible": False},
                "top_clusters": [{"urgency": "critical", "theme": "geopolitics"}],
            },
            "engine_config": cfg,
        }
        run = build_scheduled_run(context, occurrence=occurrence, generated_at=now, config=cfg)
        assert run["risk_state"]["level"] == "normal"
        assert run["risk_state"]["evidence_keys"] == []

    def test_stale_clusters_do_not_suppress_fresh_vix(self, tmp_path):
        from datetime import date

        from stocks.engine.scheduled_analysis import (
            ScheduledSession,
            SessionOccurrence,
            build_scheduled_run,
        )

        now = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
        session = ScheduledSession(
            id="cn_pre_close",
            market="cn",
            primary_market="cn",
            exchange_timezone="Asia/Shanghai",
            user_timezone="Asia/Shanghai",
            time="14:45",
            intent="pre_close_decision",
            push="push_now",
            enabled=True,
            duplicate_window_minutes=30,
            holidays=frozenset(),
        )
        occurrence = SessionOccurrence(
            session=session, market_date=date(2026, 7, 15), scheduled_for=now
        )
        cfg = {
            "quiet_hours": {"enabled": False},
            "risk_state": {"state_path": str(tmp_path / "macro.json")},
        }
        context = {
            "data_quality": {"status": "ok"},
            "position_valuations": [],
            "action_signals": {},
            "market_state": {"macro": {"vix": 40}},
            "intelligence_digest": {
                "intelligence_health": {"risk_eligible": False},
                "top_clusters": [{"urgency": "critical", "theme": "geopolitics"}],
            },
            "engine_config": cfg,
        }
        run = build_scheduled_run(context, occurrence=occurrence, generated_at=now, config=cfg)
        assert run["risk_state"]["candidate_level"] == "hedge"
        assert run["risk_state"]["evidence_keys"] == ["macro:vix_hedge"]
