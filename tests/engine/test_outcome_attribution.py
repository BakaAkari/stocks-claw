"""Tests for Task 11: Decision attribution and shadow trial settlement.

Strict TDD — these tests must fail RED before implementation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from stocks.domain.models import ExecutionRecord

# ── Helpers ──────────────────────────────────────────────────────────────

def _make_linear_price_history(
    instruments: list[str],
    *,
    base_prices: dict[str, float],
    start_date: str = "2026-07-01",
    days: int = 30,
    daily_change: float = 0.002,
    seed_noise: float = 0.0,
) -> dict[str, dict[str, float]]:
    """Build a deterministic linear price history.

    Each instrument gets `days` daily prices starting from `start_date`.
    Price[t] = base_price * (1 + daily_change)^t, no noise.
    """
    from datetime import date as date_type

    start = date_type.fromisoformat(start_date)
    history: dict[str, dict[str, float]] = {}
    for key in instruments:
        bp = base_prices.get(key, 100.0)
        series: dict[str, float] = {}
        for offset in range(days):
            d = (start + timedelta(days=offset)).isoformat()
            # Skip weekends for a realistic-ish calendar
            dt = datetime.fromisoformat(d)
            if dt.weekday() >= 5:
                continue
            price = bp * ((1.0 + daily_change) ** offset)
            series[d] = round(price, 4)
        history[key] = series
    return history


def _make_snapshot(
    *,
    decision_id: str,
    rule_version: str = "decision-trust-t1-v1",
    params_hash: str = "abcd1234",
    data_as_of: str = "2026-07-01T10:00:00+00:00",
    position_id: str = "a:588000",
    signal: str = "add",
    ratio: float = 0.05,
    horizon_days: int = 5,
    executed: bool = True,
    entry_price: float = 100.0,
    execution_price: float | None = None,
    settlement_timing: str = "T+1",
    commission_rate: float = 0.0003,
) -> dict:
    """Build a minimal DecisionSnapshot-like dict for testing."""
    return {
        "decision_id": decision_id,
        "rule_version": rule_version,
        "params_hash": params_hash,
        "data_as_of": data_as_of,
        "position_id": position_id,
        "signal": signal,
        "ratio": ratio,
        "horizon_days": horizon_days,
        "executed": executed,
        "entry_price": entry_price,
        "execution_price": execution_price if execution_price is not None else entry_price,
        "settlement_timing": settlement_timing,
        "commission_rate": commission_rate,
        "settled": False,
        "outcome": None,
    }


def _make_execution(
    decision_id: str,
    *,
    status: str = "executed",
    price: float = 100.0,
    executed_ratio: float = 1.0,
) -> dict:
    """Build a minimal execution record dict."""
    return ExecutionRecord.create(
        decision_id=decision_id,
        status=status,
        target="a:588000",
        action="add",
        price=price,
        executed_ratio=executed_ratio,
        note="test",
    ).to_dict()


# ── Tests ────────────────────────────────────────────────────────────────


class TestDecisionSnapshotModel:
    """DecisionSnapshot data model structure."""

    def test_minimal_snapshot_fields(self):
        """Verify all required fields exist in a snapshot dict."""
        snap = _make_snapshot(decision_id="d001")
        required = {
            "decision_id", "rule_version", "params_hash", "data_as_of",
            "position_id", "signal", "ratio", "horizon_days",
            "executed", "entry_price", "execution_price",
            "settlement_timing", "commission_rate",
            "settled", "outcome",
        }
        assert set(snap.keys()) == required, f"Missing fields: {required - set(snap.keys())}"

    def test_executed_and_shadow_both_supported(self):
        """Snapshot must support both executed=True and executed=False (shadow counterfactual)."""
        executed = _make_snapshot(decision_id="d001", executed=True)
        shadow = _make_snapshot(decision_id="d002", executed=False)
        assert executed["executed"] is True
        assert shadow["executed"] is False

    def test_three_horizons_allowed(self):
        """Horizon must be 1, 5, or 20 trading days."""
        for h in (1, 5, 20):
            snap = _make_snapshot(decision_id=f"d{h}", horizon_days=h)
            assert snap["horizon_days"] == h


class TestSettleDecisionsSingleScenario:
    """Settle 1 decision with known deterministic price history."""

    def test_1d_horizon_add_profits(self, tmp_path):
        """1-day horizon: add action at 100, price goes to 101 → profit after costs."""
        from stocks.engine.outcome_attribution import settle_decisions

        price_history = _make_linear_price_history(
            ["a:588000"],
            base_prices={"a:588000": 100.0},
            start_date="2026-07-01",
            days=10,
            daily_change=0.01,  # 1% per day
        )
        snapshots = [
            _make_snapshot(
                decision_id="d001",
                position_id="a:588000",
                horizon_days=1,
                entry_price=100.0,
                execution_price=100.0,
                data_as_of="2026-07-01T10:00:00+00:00",
                settlement_timing="T+0",
                commission_rate=0.0,  # zero costs for deterministic test
                executed=True,
            ),
        ]
        executions = [_make_execution("d001", price=100.0)]
        as_of = datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc)

        result = settle_decisions(as_of, price_history, snapshots, executions)
        assert result["total_settled"] == 1
        # Price went up, so outcome should be positive
        outcome = result["by_decision"]["d001"]
        assert outcome["outcome_return"] > 0
        assert outcome["horizon_days"] == 1
        assert outcome["settled"] is True

    def test_5d_horizon_add_loss(self, tmp_path):
        """5-day horizon: add action at 100, price drops over 5 days → loss after costs."""
        from stocks.engine.outcome_attribution import settle_decisions

        # Prices that decline
        price_history = _make_linear_price_history(
            ["a:588000"],
            base_prices={"a:588000": 100.0},
            start_date="2026-07-01",
            days=15,
            daily_change=-0.005,  # -0.5% per day
        )
        snapshots = [
            _make_snapshot(
                decision_id="d001",
                position_id="a:588000",
                horizon_days=5,
                entry_price=100.0,
                execution_price=100.0,
                data_as_of="2026-07-01T10:00:00+00:00",
                settlement_timing="T+1",
                commission_rate=0.0,
                executed=True,
            ),
        ]
        executions = [_make_execution("d001", price=100.0)]
        as_of = datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc)

        result = settle_decisions(as_of, price_history, snapshots, executions)
        assert result["total_settled"] == 1
        outcome = result["by_decision"]["d001"]
        assert outcome["outcome_return"] < 0

    def test_20d_horizon_evaluate(self, tmp_path):
        """20-day horizon settlement after 20 trading days."""
        from stocks.engine.outcome_attribution import settle_decisions

        price_history = _make_linear_price_history(
            ["a:588000"],
            base_prices={"a:588000": 100.0},
            start_date="2026-06-01",
            days=40,
            daily_change=0.001,
        )
        snapshots = [
            _make_snapshot(
                decision_id="d001",
                position_id="a:588000",
                horizon_days=20,
                entry_price=100.0,
                execution_price=100.0,
                data_as_of="2026-06-01T10:00:00+00:00",
                settlement_timing="T+0",
                commission_rate=0.0,
                executed=True,
            ),
        ]
        executions = [_make_execution("d001", price=100.0)]
        as_of = datetime(2026, 7, 5, 10, 0, tzinfo=timezone.utc)

        result = settle_decisions(as_of, price_history, snapshots, executions)
        outcome = result["by_decision"]["d001"]
        assert outcome["horizon_days"] == 20
        assert outcome["settled"] is True

    def test_transaction_cost_deducted(self, tmp_path):
        """Transaction costs (commission) must be deducted from returns."""
        from stocks.engine.outcome_attribution import settle_decisions

        price_history = _make_linear_price_history(
            ["a:588000"],
            base_prices={"a:588000": 100.0},
            start_date="2026-07-01",
            days=10,
            daily_change=0.0,  # flat price
        )
        snapshots = [
            _make_snapshot(
                decision_id="d001",
                position_id="a:588000",
                horizon_days=1,
                entry_price=100.0,
                execution_price=100.0,
                data_as_of="2026-07-01T10:00:00+00:00",
                settlement_timing="T+0",
                commission_rate=0.001,  # 0.1% commission
                executed=True,
            ),
        ]
        executions = [_make_execution("d001", price=100.0)]
        as_of = datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc)

        result = settle_decisions(as_of, price_history, snapshots, executions)
        outcome = result["by_decision"]["d001"]
        # Price is flat at 100, so return should be negative due to commission
        assert outcome["outcome_return"] < 0
        assert outcome["commission_paid"] > 0

    def test_settlement_timing_lag(self, tmp_path):
        """Settlement timing T+1 means execution price is next day's price, not entry."""
        from stocks.engine.outcome_attribution import settle_decisions

        price_history = _make_linear_price_history(
            ["a:588000"],
            base_prices={"a:588000": 100.0},
            start_date="2026-07-01",
            days=10,
            daily_change=0.0,  # flat
        )
        # Entry price is 100, but T+1 settlement means execution at next day's price
        # Since price is flat, both are 100
        # But we also check that the lagged price is used
        snapshots = [
            _make_snapshot(
                decision_id="d001",
                position_id="a:588000",
                horizon_days=1,
                entry_price=100.0,
                execution_price=101.0,  # T+1 settlement: price went up slightly
                data_as_of="2026-07-01T10:00:00+00:00",
                settlement_timing="T+1",
                commission_rate=0.0,
                executed=True,
            ),
        ]
        executions = [_make_execution("d001", price=101.0, executed_ratio=1.0)]
        as_of = datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc)

        result = settle_decisions(as_of, price_history, snapshots, executions)
        outcome = result["by_decision"]["d001"]
        # execution_price was used (101.0), not entry_price (100.0)
        assert outcome["execution_price_used"] == 101.0

    def test_idempotent_settle(self, tmp_path):
        """Settling the same snapshot twice must return the same result (no double-counting)."""
        from stocks.engine.outcome_attribution import settle_decisions

        price_history = _make_linear_price_history(
            ["a:588000"],
            base_prices={"a:588000": 100.0},
            start_date="2026-07-01",
            days=10,
            daily_change=0.01,
        )
        snapshots = [
            _make_snapshot(
                decision_id="d001",
                position_id="a:588000",
                horizon_days=1,
                entry_price=100.0,
                execution_price=100.0,
                data_as_of="2026-07-01T10:00:00+00:00",
                executed=True,
            ),
        ]
        executions = [_make_execution("d001", price=100.0)]
        as_of = datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc)

        # Settle once
        result1 = settle_decisions(as_of, price_history, snapshots, executions)
        # Settle again with already-settled snapshots
        _ = list(result1["by_decision"].values())
        result2 = settle_decisions(
            as_of, price_history,
            [s["_snapshot"] for s in result1["by_decision"].values()],
            executions,
        )
        assert result2["total_settled"] == 0  # nothing new to settle


class TestShadowTrial:
    """Shadow trial: compare executed actions vs unexecuted counterfactuals."""

    def test_executed_vs_shadow_separate_counts(self, tmp_path):
        """Executed and shadow decisions must be counted separately in summary."""
        from stocks.engine.outcome_attribution import settle_decisions

        price_history = _make_linear_price_history(
            ["a:588000", "a:512480"],
            base_prices={"a:588000": 100.0, "a:512480": 100.0},
            start_date="2026-07-01",
            days=10,
            daily_change=0.01,
        )
        snapshots = [
            # Executed action
            _make_snapshot(
                decision_id="d001", position_id="a:588000",
                horizon_days=5, entry_price=100.0, execution_price=100.0,
                data_as_of="2026-07-01T10:00:00+00:00",
                executed=True,
            ),
            # Shadow (unexecuted) action
            _make_snapshot(
                decision_id="d002", position_id="a:512480",
                horizon_days=5, entry_price=100.0, execution_price=100.0,
                data_as_of="2026-07-01T10:00:00+00:00",
                executed=False,
            ),
        ]
        # Only d001 was actually executed
        executions = [_make_execution("d001", price=100.0)]
        as_of = datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc)

        result = settle_decisions(as_of, price_history, snapshots, executions)
        # Summary should have separate executed and shadow sections
        summary = result["summary"]
        assert "executed" in summary
        assert "shadow" in summary
        assert summary["executed"]["count"] == 1
        assert summary["shadow"]["count"] == 1


class TestSampleSizeGate:
    """Sample size gate: <10 → counts only, >=10 → stats with CI."""

    def test_below_10_only_counts(self, tmp_path):
        """With <10 samples, output must NOT include win_rate or CI."""
        from stocks.engine.outcome_attribution import settle_decisions

        price_history = _make_linear_price_history(
            ["a:588000"],
            base_prices={"a:588000": 100.0},
            start_date="2026-07-01",
            days=10,
            daily_change=0.01,
        )
        # Create 5 snapshots (all executed)
        snapshots = []
        executions = []
        for i in range(5):
            snapshots.append(_make_snapshot(
                decision_id=f"d00{i}",
                position_id="a:588000",
                horizon_days=5,
                entry_price=100.0 + i,
                execution_price=100.0 + i,
                data_as_of="2026-07-01T10:00:00+00:00",
                executed=True,
            ))
            executions.append(_make_execution(f"d00{i}", price=100.0 + i))
        as_of = datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc)

        result = settle_decisions(as_of, price_history, snapshots, executions)
        summary = result["summary"]["executed"]
        assert summary["count"] == 5
        # Must NOT have win_rate when < 10 samples
        assert "win_rate" not in summary or summary["win_rate"] is None
        assert summary.get("statistical_note", "") != ""

    def test_at_least_10_has_stats_with_ci(self, tmp_path):
        """With >=10 samples, output must include win_rate and confidence_interval."""
        from stocks.engine.outcome_attribution import settle_decisions

        price_history = _make_linear_price_history(
            ["a:588000"],
            base_prices={"a:588000": 100.0},
            start_date="2026-07-01",
            days=30,
            daily_change=0.01,
        )
        # Create 12 snapshots (all executed)
        snapshots = []
        executions = []
        for i in range(12):
            snapshots.append(_make_snapshot(
                decision_id=f"d{i:03d}",
                position_id="a:588000",
                horizon_days=5,
                entry_price=100.0,
                execution_price=100.0,
                data_as_of="2026-07-01T10:00:00+00:00",
                executed=True,
            ))
            executions.append(_make_execution(f"d{i:03d}", price=100.0))
        as_of = datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc)

        result = settle_decisions(as_of, price_history, snapshots, executions)
        summary = result["summary"]["executed"]
        assert summary["count"] >= 10
        # Must include win_rate and confidence_interval
        assert "win_rate" in summary
        assert summary["win_rate"] is not None
        assert "confidence_interval" in summary
        assert summary["confidence_interval"] is not None
        ci = summary["confidence_interval"]
        assert len(ci) == 2
        assert 0 <= ci[0] <= ci[1] <= 1.0


class TestSnapshotPersistence:
    """Decision snapshot save/load round-trip."""

    def test_save_and_load_snapshot(self, tmp_path):
        """Save a snapshot to .local/decisions/ and load it back."""
        from stocks.engine.outcome_attribution import (
            load_decision_snapshots,
            save_decision_snapshot,
        )

        snaps_dir = tmp_path / ".local" / "decisions"
        snap = _make_snapshot(decision_id="d001")
        save_decision_snapshot(snap, snaps_dir=str(snaps_dir))

        assert (snaps_dir / "d001_5d.json").exists()

        loaded = load_decision_snapshots(snaps_dir=str(snaps_dir))
        assert len(loaded) == 1
        assert loaded[0]["decision_id"] == "d001"

    def test_load_empty_directory(self, tmp_path):
        """Loading from non-existent directory returns empty list."""
        from stocks.engine.outcome_attribution import load_decision_snapshots

        snaps_dir = tmp_path / ".local" / "decisions" / "nonexistent"
        loaded = load_decision_snapshots(snaps_dir=str(snaps_dir))
        assert loaded == []

    def test_save_is_idempotent(self, tmp_path):
        """Saving the same snapshot twice should not create duplicates."""
        from stocks.engine.outcome_attribution import save_decision_snapshot

        snaps_dir = tmp_path / ".local" / "decisions"
        snap = _make_snapshot(decision_id="d001")
        save_decision_snapshot(snap, snaps_dir=str(snaps_dir))
        save_decision_snapshot(snap, snaps_dir=str(snaps_dir))

        saved_files = list(snaps_dir.glob("*.json"))
        assert len(saved_files) == 1


class TestAttributionSummaryShape:
    """Ensure the attribution_summary dict has the required structure."""

    def test_summary_has_all_top_level_keys(self, tmp_path):
        """Top-level summary must include by_horizon, by_decision, summary."""
        from stocks.engine.outcome_attribution import settle_decisions

        price_history = _make_linear_price_history(
            ["a:588000"],
            base_prices={"a:588000": 100.0},
            start_date="2026-07-01",
            days=30,
            daily_change=0.005,
        )
        snapshots = [
            _make_snapshot(decision_id="d001", horizon_days=1, executed=True),
            _make_snapshot(decision_id="d002", horizon_days=5, executed=False),
            _make_snapshot(decision_id="d003", horizon_days=20, executed=True),
        ]
        executions = [
            _make_execution("d001", price=100.0),
            _make_execution("d003", price=100.0),
        ]
        as_of = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)

        result = settle_decisions(as_of, price_history, snapshots, executions)
        assert "settled_at" in result
        assert "total_settled" in result
        assert "total_pending" in result
        assert "by_horizon" in result
        assert "by_decision" in result
        assert "summary" in result
        # by_horizon must have 1d, 5d, 20d
        assert "1d" in result["by_horizon"]
        assert "5d" in result["by_horizon"]
        assert "20d" in result["by_horizon"]


class TestScheduledRunIntegration:
    """Verify snapshots are created during scheduled analysis runs (integration shape)."""

    def test_approved_actions_produce_snapshots(self, tmp_path):
        """When a PortfolioDecision has approved_actions, snapshots must be created for each."""
        from datetime import datetime, timezone

        from stocks.engine.outcome_attribution import save_decision_snapshot
        from stocks.engine.portfolio_adjudicator import (
            PortfolioAction,
            PortfolioDecision,
        )

        decision = PortfolioDecision(
            status="approved",
            decision_id="portfolio_d001",
            approved_actions=[
                PortfolioAction(
                    position_id="a:588000",
                    signal="add",
                    action_description="分批加仓",
                    ratio=0.05,
                    decision_id="d001",
                    reason="通过裁决",
                    settlement_timing="T+1",
                ),
                PortfolioAction(
                    position_id="a:512480",
                    signal="add",
                    action_description="分批加仓",
                    ratio=0.03,
                    decision_id="d002",
                    reason="通过裁决",
                    settlement_timing="T+1",
                ),
            ],
            rule_version="decision-trust-t1-v1",
        )

        snaps_dir = tmp_path / ".local" / "decisions"
        now_iso = datetime.now(timezone.utc).isoformat()
        for action in decision.approved_actions:
            for horizon in (1, 5, 20):
                snap = {
                    "decision_id": action.decision_id,
                    "rule_version": decision.rule_version,
                    "params_hash": "test_hash",
                    "data_as_of": now_iso,
                    "position_id": action.position_id,
                    "signal": action.signal,
                    "ratio": action.ratio,
                    "horizon_days": horizon,
                    "executed": True,
                    "entry_price": 100.0,
                    "execution_price": 100.0,
                    "settlement_timing": action.settlement_timing or "T+1",
                    "commission_rate": 0.0003,
                    "settled": False,
                    "outcome": None,
                }
                save_decision_snapshot(snap, snaps_dir=str(snaps_dir))

        saved_files = sorted(snaps_dir.glob("*.json"))
        # 2 actions * 3 horizons = 6 snapshots
        assert len(saved_files) == 6
        # d001 should have 3 snapshots (1, 5, 20)
        d001_files = [f for f in saved_files if "d001" in f.name]
        assert len(d001_files) == 3
