"""Task 3: Intelligence trust — provenance, health, coverage, unified matching.

Tests cover:
  1. match_intelligence — exact, proxy, exposure_tag, category_padding matching
  2. _compute_coverage — 6 dimensions, padding ≠ directional
  3. _compute_brief_health — stale at 48h, ok at 47h, boundary at 48h
  4. IntelConflictRule — caution/override from matched signals (not dict parse)
  5. _build_drivers — provenance in driver, unavailable when stale
  6. _detect_dissent — non-null when intelligence direction differs from final
  7. build_brief — pure function with injected now, provenance fields
  8. Stale guard in build_intelligence_run — signals don't enter risk state
  9. Positive event + reduce → conflict=caution + dissent non-empty
 10. category_padding does not count as directional, does not trigger conflict
 11. Provenance fields on IntelligenceSignal (generation_method, match_method)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from stocks.engine.intelligence_analyzer import (
    MatchedSignal,
    _compute_brief_health,
    _compute_coverage,
    _intel_consensus_direction_from_matched,
    match_intelligence,
)
from stocks.engine.news_intelligence_store import IntelligenceSignal
from stocks.engine.quant_action import _build_drivers, _detect_dissent

# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def sample_signals() -> list[IntelligenceSignal]:
    now = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
    return [
        IntelligenceSignal(
            symbol="NVDA",
            name="NVIDIA",
            direction="buy",
            horizon="short_term",
            rationale="AI demand remains strong",
            falsification="Demand weakens",
            risk_source="llm_analysis",
            confidence=0.8,
            urgency="high",
            generated_at=now,
            generation_method="llm",
            match_method="exact",
            source_as_of=now,
        ),
        IntelligenceSignal(
            symbol="GLD",
            name="SPDR Gold Shares",
            direction="buy",
            horizon="short_term",
            rationale="Safe-haven demand",
            falsification="Yields rise",
            risk_source="llm_analysis",
            confidence=0.7,
            urgency="medium",
            generated_at=now,
            generation_method="rule_fallback",
            match_method="proxy",
            source_as_of=now,
        ),
    ]


@pytest.fixture
def sample_position() -> dict:
    return {
        "instrument_key": "us:NVDA",
        "classification": {
            "exposure_tags": ["tech", "ai", "semiconductor"],
            "product_type": "stock",
        },
    }


# ============================================================
# 1. match_intelligence — exact, proxy, exposure_tag, padding
# ============================================================


class TestMatchIntelligence:
    def test_exact_match(self, sample_signals, sample_position):
        """Exact suffix match: NVDA signal → us:NVDA position."""
        matched = match_intelligence(sample_position, sample_signals)
        nvda = [m for m in matched if m.matched_symbol == "NVDA"]
        assert len(nvda) == 1
        assert nvda[0].match_method == "exact"
        assert nvda[0].direction == "buy"

    def test_proxy_match(self, sample_signals):
        """Proxy match: GLD signal → NEM position via _INTEL_SIGNAL_PROXY."""
        position = {
            "instrument_key": "us:NEM",
            "classification": {"exposure_tags": ["gold", "mining"]},
        }
        matched = match_intelligence(position, sample_signals)
        gld = [m for m in matched if m.matched_symbol == "GLD"]
        assert len(gld) == 1
        assert gld[0].match_method == "proxy"

    def test_exposure_tag_fallback(self):
        """When no exact/proxy match, exposure tag should fall through to padding."""
        now = datetime.now(timezone.utc)
        sig = IntelligenceSignal(
            symbol="AAPL", name="Apple Inc", direction="buy",
            horizon="short_term", rationale="Strong earnings",
            falsification="", risk_source="", confidence=0.8,
            urgency="medium", generated_at=now,
        )
        position = {
            "instrument_key": "us:QQQ",
            "classification": {"exposure_tags": ["tech", "nasdaq100"]},
        }
        matched = match_intelligence(position, [sig])
        padding = [m for m in matched if m.generation_method == "category_padding"]
        assert len(padding) == 2
        assert all(p.match_method == "category" for p in padding)

    def test_category_padding_not_directional(self):
        """Category padding should produce neutral direction, match_method='category'."""
        now = datetime.now(timezone.utc)
        sig = IntelligenceSignal(
            symbol="VIX", name="VIX index", direction="sell",
            horizon="short_term", rationale="Fear elevated",
            falsification="", risk_source="", confidence=0.8,
            urgency="high", generated_at=now,
        )
        position = {
            "instrument_key": "us:SPY",
            "classification": {"exposure_tags": ["gold", "mining"]},
        }
        matched = match_intelligence(position, [sig])
        padding = [m for m in matched if m.generation_method == "category_padding"]
        assert len(padding) == 2
        assert all(p.direction == "neutral" for p in padding)
        assert all(p.match_method == "category" for p in padding)


# ============================================================
# 2. _compute_coverage — 6 dimensions, padding ≠ directional
# ============================================================


class TestComputeCoverage:
    def test_six_dimensions_present(self):
        """Coverage dict must have field, directional, padding, exact, proxy, category."""
        matched = []
        coverage = _compute_coverage(matched)
        for key in ("field", "directional", "padding", "exact", "proxy", "category"):
            assert key in coverage, f"Missing coverage dimension: {key}"

    def test_padding_not_directional(self):
        """Category padding contributes to field+padding, NOT to directional."""
        now = datetime.now(timezone.utc)
        matched = [
            MatchedSignal(
                matched_symbol="gold", direction="neutral",
                rationale="padding", generation_method="category_padding",
                match_method="category", source_as_of=now,
            ),
        ]
        coverage = _compute_coverage(matched)
        assert coverage["field"] == 1
        assert coverage["directional"] == 0
        assert coverage["padding"] == 1
        assert coverage["category"] == 1

    def test_mixed_coverage(self):
        """Mixed exact + padding: directional counts only exact, padding separate."""
        now = datetime.now(timezone.utc)
        matched = [
            MatchedSignal(
                matched_symbol="NVDA", direction="buy",
                rationale="AI demand", generation_method="llm",
                match_method="exact", source_as_of=now,
            ),
            MatchedSignal(
                matched_symbol="gold", direction="neutral",
                rationale="padding", generation_method="category_padding",
                match_method="category", source_as_of=now,
            ),
            MatchedSignal(
                matched_symbol="mining", direction="neutral",
                rationale="padding", generation_method="category_padding",
                match_method="category", source_as_of=now,
            ),
        ]
        coverage = _compute_coverage(matched)
        assert coverage["field"] == 3
        assert coverage["directional"] == 1
        assert coverage["padding"] == 2
        assert coverage["exact"] == 1
        assert coverage["category"] == 2
        assert coverage["proxy"] == 0


# ============================================================
# 3. _compute_brief_health — stale at 48h, ok before
# ============================================================


class TestComputeBriefHealth:
    def test_fresh_under_48h(self):
        """Under 48 hours → status=ok, risk_eligible=True."""
        now = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
        recent = now - timedelta(hours=47)
        result = _compute_brief_health(now, recent)
        assert result["status"] == "ok"
        assert result["risk_eligible"] is True
        assert result["age_minutes"] < 48 * 60

    def test_stale_at_48h_plus_epsilon(self):
        """Just over 48 hours → status=stale, risk_eligible=False."""
        now = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
        old = now - timedelta(hours=48, minutes=1)
        result = _compute_brief_health(now, old)
        assert result["status"] == "stale"
        assert result["risk_eligible"] is False

    def test_boundary_exactly_48h(self):
        """Exactly 48h reaches the stale boundary."""
        now = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
        old = now - timedelta(hours=48)
        result = _compute_brief_health(now, old)
        assert result["status"] == "stale"
        assert result["risk_eligible"] is False
        assert result["age_minutes"] == 48 * 60

    def test_custom_max_age(self):
        """Custom max_age_hours should be respected."""
        now = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
        old = now - timedelta(hours=2)
        result = _compute_brief_health(now, old, max_age_hours=1.0)
        assert result["status"] == "stale"
        assert result["risk_eligible"] is False

    def test_future_brief_returns_ok(self):
        """If brief appears to be from the future, age=0 → ok."""
        now = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
        future = now + timedelta(hours=1)
        result = _compute_brief_health(now, future)
        assert result["status"] == "ok"
        assert result["age_minutes"] == 0.0


# ============================================================
# 4. _intel_consensus_direction_from_matched — shared by consumers
# ============================================================


class TestIntelConsensusDirectionFromMatched:
    def test_bullish_majority(self):
        now = datetime.now(timezone.utc)
        matched = [
            MatchedSignal("NVDA", "buy", "AI", "llm", "exact", now),
            MatchedSignal("AMD", "sell", "bad", "llm", "exact", now),
            MatchedSignal("INTC", "buy", "good", "llm", "exact", now),
        ]
        assert _intel_consensus_direction_from_matched(matched) == "bullish"

    def test_bearish_majority(self):
        now = datetime.now(timezone.utc)
        matched = [
            MatchedSignal("NVDA", "sell", "bad", "llm", "exact", now),
            MatchedSignal("AMD", "sell", "bad", "llm", "exact", now),
        ]
        assert _intel_consensus_direction_from_matched(matched) == "bearish"

    def test_tie_returns_neutral(self):
        now = datetime.now(timezone.utc)
        matched = [
            MatchedSignal("NVDA", "buy", "good", "llm", "exact", now),
            MatchedSignal("AMD", "sell", "bad", "llm", "exact", now),
        ]
        assert _intel_consensus_direction_from_matched(matched) == "neutral"

    def test_category_padding_ignored(self):
        """Category-padding signals with neutral direction are skipped."""
        now = datetime.now(timezone.utc)
        matched = [
            MatchedSignal("NVDA", "buy", "good", "llm", "exact", now),
            MatchedSignal("gold", "neutral", "padding", "category_padding", "category", now),
            MatchedSignal("mining", "neutral", "padding", "category_padding", "category", now),
        ]
        assert _intel_consensus_direction_from_matched(matched) == "bullish"


# ============================================================
# 5. _build_drivers — provenance, unavailable when stale
# ============================================================


class TestBuildDrivers:
    def test_intelligence_driver_has_provenance(self, sample_signals, sample_position):
        """Intelligence driver should include match_count, generation_methods, match_methods."""
        from stocks.engine.quant_action import QuantReview

        tech = QuantReview(
            position_id="test", signal="hold", action="持有",
            ratio=0.0, facts=["无技术动作"],
            stop_price=None, target_prices=[], position_limit_pct=10.0,
            current_weight_pct=1.0, risk_to_stop_pct=None, risk_amount_cny=None,
        )
        signals_dict = {s.symbol: s.to_dict() for s in sample_signals}
        drivers = _build_drivers(
            tech=tech, signal="hold", action="持有", votes=[],
            intelligence_signals=signals_dict, position=sample_position,
        )
        intel = next(d for d in drivers if d["source"] == "intelligence")
        assert intel["signal"] != "unavailable"
        assert "provenance" in intel
        assert intel["provenance"]["match_count"] >= 1
        assert "llm" in intel["provenance"]["generation_methods"]
        assert "exact" in intel["provenance"]["match_methods"]

    def test_intelligence_unavailable_when_no_match(self):
        """No matching signals → intelligence driver shows unavailable."""
        from stocks.engine.quant_action import QuantReview

        tech = QuantReview(
            position_id="test", signal="hold", action="持有",
            ratio=0.0, facts=["无技术动作"],
            stop_price=None, target_prices=[], position_limit_pct=10.0,
            current_weight_pct=1.0, risk_to_stop_pct=None, risk_amount_cny=None,
        )
        position = {
            "instrument_key": "us:FAKE",
            "classification": {"exposure_tags": []},
        }
        drivers = _build_drivers(
            tech=tech, signal="hold", action="持有", votes=[],
            intelligence_signals={}, position=position,
        )
        intel = next(d for d in drivers if d["source"] == "intelligence")
        assert intel["signal"] == "unavailable"
        assert intel["provenance"]["match_count"] == 0


# ============================================================
# 6. _detect_dissent — non-null when intel conflicts with final
# ============================================================


class TestDetectDissent:
    def test_dissent_detected_when_intel_bullish_final_bearish(self):
        """Intel bullish + final bearish → dissent non-null."""
        drivers = [
            {"source": "technical", "signal": "reduce", "direction": "bearish", "reasons": ["tech"]},
            {"source": "intelligence", "signal": "bullish", "direction": "bullish", "reasons": ["NVDA: buy"]},
        ]
        dissent = _detect_dissent(drivers, "reduce")
        assert dissent is not None
        assert dissent["source"] == "intelligence"

    def test_no_dissent_when_aligned(self):
        """Intel bullish + final bullish → no dissent."""
        drivers = [
            {"source": "technical", "signal": "add", "direction": "bullish", "reasons": ["tech"]},
            {"source": "intelligence", "signal": "bullish", "direction": "bullish", "reasons": ["NVDA: buy"]},
        ]
        dissent = _detect_dissent(drivers, "add")
        assert dissent is None

    def test_dissent_null_when_intel_unavailable(self):
        """Unavailable intelligence → no dissent."""
        drivers = [
            {"source": "technical", "signal": "add", "direction": "bullish", "reasons": ["tech"]},
            {"source": "intelligence", "signal": "unavailable", "direction": "unavailable", "reasons": [], "provenance": {"match_count": 0}},
        ]
        dissent = _detect_dissent(drivers, "add")
        assert dissent is None


# ============================================================
# 7. build_brief — pure function with injected now
# ============================================================


class TestBuildBrief:
    def test_provenance_fields_present(self):
        """build_brief output must include source_run_id, source_generated_at, brief_generated_at."""
        from scripts.intelligence_brief import build_brief

        now = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
        data = {
            "run_id": "test_run_001",
            "scheduled_for": "2026-07-15T11:00:00",
            "context_digest": {
                "clusters": [],
                "signals": [],
                "macro": {},
                "quotes": {},
                "intelligence_digest": {"metadata": {}},
            },
            "data_quality": {"status": "ok", "errors": []},
        }
        brief = build_brief(data, now=now)
        assert "source_run_id" in brief
        assert "source_generated_at" in brief
        assert "brief_generated_at" in brief
        assert brief["source_run_id"] == "test_run_001"
        assert brief["source_generated_at"] == "2026-07-15T11:00:00"
        assert brief["brief_generated_at"] == "2026-07-15T12:00:00"

    def test_default_now_when_not_injected(self):
        """When now is not passed, build_brief should still work."""
        from scripts.intelligence_brief import build_brief

        data = {
            "run_id": "test",
            "scheduled_for": "2026-07-15T10:00:00",
            "context_digest": {
                "clusters": [], "signals": [],
                "macro": {}, "quotes": {},
                "intelligence_digest": {"metadata": {}},
            },
            "data_quality": {"status": "ok", "errors": []},
        }
        brief = build_brief(data)
        assert "brief_generated_at" in brief


# ============================================================
# 8. build_intelligence_run — health + coverage in artifact
# ============================================================


class TestBuildIntelligenceRun:
    def test_health_and_coverage_in_artifact(self):
        """build_intelligence_run output must contain intelligence_health and intelligence_coverage."""
        from stocks.engine.scheduled_analysis import (
            ScheduledSession,
            SessionOccurrence,
            build_intelligence_run,
        )

        now = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
        occurrence = SessionOccurrence(
            session=ScheduledSession(
                id="global_intelligence_watch",
                market="global", exchange_timezone="America/New_York",
                user_timezone="Asia/Shanghai",
                time="09:00", intent="intelligence_patrol", push="normal",
                enabled=True, duplicate_window_minutes=90,
                holidays=frozenset(), primary_market="global",
            ),
            market_date=now.date(),
            scheduled_for=now,
        )
        harvest = {
            "macro": {}, "quotes": {}, "articles": [],
            "source_status": {}, "data_quality": {"status": "ok", "errors": []},
        }
        analysis = {
            "clusters": [], "signals": [],
            "data_quality": {"status": "ok", "errors": []},
            "metadata": {},
        }
        run = build_intelligence_run(
            harvest, analysis,
            occurrence=occurrence, generated_at=now,
            config={"user_timezone": "Asia/Shanghai"},
        )
        assert "intelligence_health" in run
        assert run["intelligence_health"]["status"] == "ok"
        assert run["intelligence_health"]["risk_eligible"] is True
        assert "intelligence_coverage" in run
        assert run["intelligence_coverage"]["total"] == 0

    def test_stale_guard_bypasses_risk_state(self):
        """When scheduled_for is 48h+ before generated_at, risk is downgraded to normal."""
        from stocks.engine.scheduled_analysis import (
            ScheduledSession,
            SessionOccurrence,
            build_intelligence_run,
        )

        now = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
        stale_scheduled = now - timedelta(hours=49)
        occurrence = SessionOccurrence(
            session=ScheduledSession(
                id="global_intelligence_watch",
                market="global", exchange_timezone="America/New_York",
                user_timezone="Asia/Shanghai",
                time="09:00", intent="intelligence_patrol", push="normal",
                enabled=True, duplicate_window_minutes=90,
                holidays=frozenset(), primary_market="global",
            ),
            market_date=stale_scheduled.date(),
            scheduled_for=stale_scheduled,
        )
        harvest = {
            "macro": {"vix": 35}, "quotes": {},
            "articles": [], "source_status": {},
            "data_quality": {"status": "ok", "errors": []},
        }
        analysis = {
            "clusters": [
                {"theme": "geopolitics", "urgency": "critical", "sentiment": "negative"},
            ],
            "signals": [
                {"symbol": "VIX", "direction": "sell", "urgency": "high",
                 "rationale": "test", "generation_method": "llm"},
            ],
            "data_quality": {"status": "ok", "errors": []},
            "metadata": {},
        }
        run = build_intelligence_run(
            harvest, analysis,
            occurrence=occurrence, generated_at=now,
            config={"user_timezone": "Asia/Shanghai"},
        )
        assert run["intelligence_health"]["status"] == "stale"
        assert run["intelligence_health"]["risk_eligible"] is False
        assert run["risk_assessment"]["level"] == "normal"
        assert "过期" in run["risk_assessment"]["recommended_actions"][0]


# ============================================================
# 9. Positive event + reduce → conflict=caution + dissent non-empty
# ============================================================


class TestConflictAndDissent:
    def test_positive_event_plus_reduce_causes_caution(self):
        """Intel says buy + tech says reduce → IntelConflictRule returns caution."""
        from stocks.engine.factor_rules import FactorVote, IntelConflictRule

        now = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
        rule = IntelConflictRule()
        sig = IntelligenceSignal(
            symbol="NVDA", name="NVIDIA", direction="buy",
            horizon="short_term", rationale="AI demand strong",
            falsification="", risk_source="llm_analysis",
            confidence=0.8, urgency="medium", generated_at=now,
        )
        signals_dict = {sig.symbol: sig.to_dict()}
        position = {
            "instrument_key": "us:NVDA",
            "classification": {"exposure_tags": ["tech", "ai"]},
        }
        vote = rule.evaluate(
            position,
            current_signal="reduce",
            current_ratio=1.0,
            intelligence_signals=signals_dict,
        )
        assert isinstance(vote, FactorVote)
        assert vote.conflict_type == "caution"

    def test_unrelated_critical_signal_does_not_upgrade_matched_medium_conflict(self):
        """Only matched signal urgency may determine caution vs override."""
        from stocks.engine.factor_rules import IntelConflictRule

        now = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
        matched = IntelligenceSignal(
            symbol="NVDA", name="NVIDIA", direction="buy",
            horizon="short_term", rationale="AI demand strong",
            falsification="", risk_source="llm_analysis",
            confidence=0.8, urgency="medium", generated_at=now,
        )
        unrelated = IntelligenceSignal(
            symbol="USO", name="Oil", direction="sell",
            horizon="short_term", rationale="Oil shock",
            falsification="", risk_source="llm_analysis",
            confidence=0.9, urgency="critical", generated_at=now,
        )
        vote = IntelConflictRule().evaluate(
            {"instrument_key": "us:NVDA", "classification": {"exposure_tags": ["tech", "ai"]}},
            current_signal="reduce", current_ratio=1.0,
            intelligence_signals={
                matched.symbol: matched.to_dict(),
                unrelated.symbol: unrelated.to_dict(),
            },
        )
        assert vote.conflict_type == "caution"
        assert vote.signal_override == ""

    def test_positive_event_plus_reduce_dissent_non_null(self):
        """Intel bullish + tech reduce → dissent must be present."""
        from stocks.engine.quant_action import QuantReview

        now = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
        sig = IntelligenceSignal(
            symbol="NVDA", name="NVIDIA", direction="buy",
            horizon="short_term", rationale="AI demand strong",
            falsification="", risk_source="llm_analysis",
            confidence=0.8, urgency="medium", generated_at=now,
        )
        tech = QuantReview(
            position_id="test", signal="reduce", action="减仓",
            ratio=1.0, facts=["技术减仓"],
            stop_price=None, target_prices=[], position_limit_pct=10.0,
            current_weight_pct=1.0, risk_to_stop_pct=None, risk_amount_cny=None,
        )
        position = {
            "instrument_key": "us:NVDA",
            "classification": {"exposure_tags": ["tech", "ai"]},
        }
        drivers = _build_drivers(
            tech=tech, signal="reduce", action="减仓",
            votes=[], intelligence_signals={sig.symbol: sig.to_dict()},
            position=position,
        )
        dissent = _detect_dissent(drivers, "reduce")
        assert dissent is not None


# ============================================================
# 10. category_padding only field/padding/category, NOT directional
# ============================================================


class TestCategoryPaddingDoesNotTriggerConflict:
    def test_padding_only_signals_no_conflict(self):
        """IntelConflictRule with only padding signals → no conflict."""
        from stocks.engine.factor_rules import IntelConflictRule

        now = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
        rule = IntelConflictRule()
        position = {
            "instrument_key": "us:FAKE",
            "classification": {"exposure_tags": ["gold", "mining"]},
        }
        sig = IntelligenceSignal(
            symbol="VIX", name="VIX index", direction="sell",
            horizon="short_term", rationale="Fear",
            falsification="", risk_source="",
            confidence=0.8, urgency="medium", generated_at=now,
        )
        vote = rule.evaluate(
            position,
            current_signal="add",
            current_ratio=1.0,
            intelligence_signals={sig.symbol: sig.to_dict()},
        )
        assert vote.conflict_type == "none"
        assert vote.direction == "add"


# ============================================================
# 11. Provenance fields on IntelligenceSignal
# ============================================================


class TestIntelligenceSignalProvenance:
    def test_defaults(self):
        """IntelligenceSignal defaults for generation_method, match_method."""
        now = datetime.now(timezone.utc)
        sig = IntelligenceSignal(
            symbol="TEST", name="Test", direction="hold",
            horizon="short_term", rationale="test",
            falsification="", risk_source="",
            confidence=0.5, urgency="medium",
            generated_at=now,
        )
        assert sig.generation_method == "rule_fallback"
        assert sig.match_method == "unmatched"
        assert sig.source_as_of is None

    def test_to_dict_includes_provenance(self):
        """to_dict should include generation_method, match_method, source_as_of."""
        now = datetime.now(timezone.utc)
        sig = IntelligenceSignal(
            symbol="TEST", name="Test", direction="buy",
            horizon="short_term", rationale="test",
            falsification="", risk_source="",
            confidence=0.8, urgency="high",
            generated_at=now,
            generation_method="llm",
            match_method="proxy",
            source_as_of=now,
        )
        d = sig.to_dict()
        assert d["generation_method"] == "llm"
        assert d["match_method"] == "proxy"
        assert "source_as_of" in d

    def test_round_trip(self):
        """from_dict(to_dict(signal)) should preserve provenance fields."""
        now = datetime.now(timezone.utc)
        original = IntelligenceSignal(
            symbol="TEST", name="Test", direction="buy",
            horizon="short_term", rationale="test",
            falsification="", risk_source="",
            confidence=0.8, urgency="high",
            generated_at=now,
            generation_method="llm",
            match_method="proxy",
            source_as_of=now,
        )
        d = original.to_dict()
        restored = IntelligenceSignal.from_dict(d)
        assert restored.generation_method == "llm"
        assert restored.match_method == "proxy"
        assert restored.source_as_of == now


# ============================================================
# 12. Trading-session consumer guard for latest_brief.json
# ============================================================


class TestTradingSessionBriefHealth:
    def test_stale_digest_suppresses_intelligence_in_scheduled_run(self, tmp_path):
        import json

        from stocks.engine.context_builder import ContextBuilder
        from stocks.engine.scheduled_analysis import (
            ScheduledSession,
            SessionOccurrence,
            build_scheduled_run,
        )

        repo_root = tmp_path
        intelligence_store = repo_root / ".local" / "news_intelligence"
        signals_dir = intelligence_store / "signals" / "2026-07-13"
        events_dir = intelligence_store / "events" / "2026-07-13"
        signals_dir.mkdir(parents=True)
        events_dir.mkdir(parents=True)
        (signals_dir / "signal.json").write_text(json.dumps({
            "generated_at": "2026-07-13T09:00:00+00:00",
            "signals": [{
                "symbol": "NVDA", "name": "NVIDIA", "direction": "buy",
                "horizon": "short_term", "rationale": "AI demand",
                "falsification": "demand weakens", "risk_source": "test",
                "confidence": 0.8, "urgency": "medium",
                "generated_at": "2026-07-13T09:00:00+00:00",
                "generation_method": "llm",
                "source_as_of": "2026-07-13T09:00:00+00:00"
            }]
        }))
        (events_dir / "event_cluster.json").write_text(json.dumps({
            "formed_at": "2026-07-13T09:00:00+00:00",
            "clusters": [{
                "theme": "technology", "summary": "AI demand",
                "affected_markets": ["us"], "affected_symbols": ["NVDA"],
                "sentiment": "positive", "urgency": "critical",
                "confidence": 0.8
            }]
        }))
        brief_dir = repo_root / ".local" / "intelligence"
        brief_dir.mkdir(parents=True)
        (brief_dir / "latest_brief.json").write_text(json.dumps({
            "source_run_id": "old_watch",
            "source_generated_at": "2026-07-13T09:00:00+00:00",
            "brief_generated_at": "2026-07-13T09:05:00+00:00"
        }))

        builder = object.__new__(ContextBuilder)
        builder._config = {"intelligence_dir": str(intelligence_store)}
        positions = [{
            "instrument_key": "us:NVDA",
            "classification": {"exposure_tags": ["tech", "ai"]},
        }]
        digest = builder._build_intelligence_digest(
            repo_root=repo_root,
            generated_at="2026-07-15T10:00:00+00:00",
            positions=positions,
        )

        assert digest["intelligence_health"]["status"] == "stale"
        assert digest["intelligence_health"]["risk_eligible"] is False
        assert digest["source_run_id"] == "old_watch"
        assert digest["brief_generated_at"] == "2026-07-13T09:05:00+00:00"
        assert digest["top_signals"] == []
        assert digest["top_clusters"] == []
        assert digest["intelligence_coverage"]["field"] >= 1
        assert digest["intelligence_coverage"]["directional"] >= 1

        now = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
        occurrence = SessionOccurrence(
            session=ScheduledSession(
                id="us_pre_close", market="us",
                exchange_timezone="America/New_York",
                user_timezone="Asia/Shanghai", time="15:30",
                intent="pre_close_decision", push="normal", enabled=True,
                duplicate_window_minutes=90, holidays=frozenset(),
                primary_market="us",
            ),
            market_date=now.date(), scheduled_for=now,
        )
        context = {
            "schema_version": 12, "generated_at": now.isoformat(),
            "data_quality": {"quotes": {"freshness": "current"}},
            "position_valuations": [{
                "position_id": "us_nvda", "display_name": "NVIDIA",
                "instrument_key": "us:NVDA", "market_value_cny": 10000.0,
                "price": 90.0, "cost_amount": 100.0, "pnl_pct": -10.5,
                "portfolio_weight": 1.0, "indicators": {},
                "classification": {"product_type": "stock", "exposure_tags": ["tech", "ai"]},
                "liquidity": {"rebalance_eligible": True, "tradable": True},
                "holding": {"quantity": None},
                "evidence": {"price_freshness": "current"},
            }],
            "intelligence_digest": digest, "market_state": {},
            "rotation": {}, "portfolio_mapping": {"ratios": {}},
            "liquidity_summary": {}, "action_signals": {},
        }
        run = build_scheduled_run(
            context, occurrence=occurrence, generated_at=now,
            config={"user_timezone": "Asia/Shanghai", "quiet_hours": {"enabled": False}},
        )
        card = run["action_cards"][0]
        intel_driver = next(d for d in card["drivers"] if d["source"] == "intelligence")
        assert run["intelligence_health"]["status"] == "stale"
        assert run["intelligence_coverage"]["directional"] >= 1
        assert intel_driver["signal"] == "unavailable"
        assert card["intelligence_conflict"] == "none"
        assert card["dissent"] is None
        assert run["risk_assessment"]["level"] == "normal"
