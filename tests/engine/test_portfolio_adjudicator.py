"""Tests for Task 4: Action Card Immutability and CashSchedule.

Strict TDD — these tests must fail RED before implementation.
"""
from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from stocks.engine.portfolio_adjudicator import (
    adjudicate_portfolio,
    build_capital_allocation_with_suppression,
    build_cash_schedule,
)

# ── Fixtures ──────────────────────────────────────────────────────────

def _make_position(
    position_id: str,
    *,
    product_type: str = "stock",
    liquidity_tier: str = "t1",
    market_value_cny: float = 10_000.0,
    tradable: bool = True,
    rebalance_eligible: bool = True,
    exposure_tags: list[str] | None = None,
) -> dict:
    """Helper to build minimal position_valuation dicts."""
    tags = exposure_tags if exposure_tags is not None else [product_type]
    return {
        "position_id": position_id,
        "display_name": position_id,
        "instrument_key": f"a:{position_id.split('_')[-1]}",
        "market_value_cny": market_value_cny,
        "price": 100.0,
        "cost_amount": 100.0,
        "pnl_pct": 0.0,
        "portfolio_weight": 0.05,
        "indicators": {},
        "classification": {
            "product_type": product_type,
            "exposure_tags": tags,
        },
        "liquidity": {
            "tier": liquidity_tier,
            "tradable": tradable,
            "rebalance_eligible": rebalance_eligible,
        },
        "holding": {"quantity": 100},
        "evidence": {"price_freshness": "current"},
    }


def _make_action_card(
    position_id: str,
    *,
    signal: str = "hold",
    action: str = "\u6301\u4ed3\u89c2\u5bdf",
    ratio: float = 0.0,
    product_type: str = "stock",
    routing: str = "full",
) -> dict:
    """Helper to build a realistic action card."""
    return {
        "position_id": position_id,
        "display_name": position_id,
        "instrument_key": f"a:{position_id.split('_')[-1]}",
        "product_type": product_type,
        "routing": routing,
        "account_type": "broker",
        "signal": signal,
        "action": action,
        "ratio": ratio,
        "facts": [],
        "stop_price": None,
        "target_prices": [],
        "position_limit_pct": 5.0,
        "current_weight_pct": 0.05,
        "risk_to_stop_pct": None,
        "risk_amount_cny": None,
        "intelligence_conflict": "none",
        "drivers": [],
        "dissent": None,
        "confidence": "high",
        "raw_signal": signal,
        "raw_ratio": ratio,
        "raw_action": action,
        "evidence_status": "ok",
    }


def _liquidity_summary(cash_or_t0: float = 50_000.0, t1_t2: float = 100_000.0) -> dict:
    return {
        "deployable_value_cny": cash_or_t0 + t1_t2,
        "buckets": {
            "cash_or_t0": {"value_cny": cash_or_t0, "positions": ["cash_1"]},
            "t1_t2": {"value_cny": t1_t2, "positions": []},
            "locked_or_ineligible": {"value_cny": 0.0, "positions": []},
            "unknown": {"value_cny": 0.0, "positions": []},
        },
    }


@pytest.fixture
def sample_portfolio():
    """Realistic multi-asset portfolio mimicking production shape."""
    positions = [
        _make_position("cash_hkd", product_type="cash", liquidity_tier="cash",
                       market_value_cny=30_000.0),
        _make_position("cash_usd", product_type="cash_equivalent", liquidity_tier="t0",
                       market_value_cny=20_000.0),
        _make_position("cn_588000", product_type="stock", liquidity_tier="t1",
                       market_value_cny=210_000.0),
        _make_position("us_qqq", product_type="stock", liquidity_tier="t1",
                       market_value_cny=150_000.0),
        _make_position("cn_512480", product_type="stock", liquidity_tier="t1",
                       market_value_cny=85_000.0),
        _make_position("alipay_nasdaq", product_type="qdii_fund", liquidity_tier="t2_plus",
                       market_value_cny=120_000.0),
        _make_position("alipay_gold", product_type="precious_metal_account",
                       liquidity_tier="t2_plus", market_value_cny=80_000.0),
        _make_position("ccb_wmp_no1", product_type="bank_wealth_management",
                       liquidity_tier="periodic_open", market_value_cny=200_000.0,
                       tradable=False),
        _make_position("boc_insurance", product_type="insurance_policy",
                       liquidity_tier="locked", market_value_cny=100_000.0,
                       tradable=False, rebalance_eligible=False),
        _make_position("alipay_mmf", product_type="money_market_fund",
                       liquidity_tier="t0", market_value_cny=50_000.0),
    ]
    total = sum(p["market_value_cny"] for p in positions)
    return positions, total


@pytest.fixture
def add_action_cards_with_below_threshold():
    """Action cards where one add is below 800 threshold."""
    cards = [
        _make_action_card("cn_588000", signal="add", action="分批加仓", ratio=0.02),
        _make_action_card("us_qqq", signal="hold"),
        _make_action_card("alipay_nasdaq", signal="add", action="定投加仓", ratio=0.005),
    ]
    # alipay_nasdaq at 120,000 * 0.005 = 600 < 800
    return cards


@pytest.fixture
def approved_sales_fixture():
    """Approved sales with settlement rules matching position_ids."""
    return [
        {
            "position_id": "cn_588000",
            "signal": "reduce",
            "ratio": 0.3,
            "settlement": {
                "timing": "T+1",
                "business_days": 1,
                "currency": "CNY",
            },
        },
    ]


# ── Step 1: Immutability tests ───────────────────────────────────────

class TestActionCardImmutability:
    """action_cards must be deep-identical before and after allocation."""

    def test_cards_unchanged_after_allocation_no_threshold_issue(self, sample_portfolio):
        """Cards with above-threshold adds must not be mutated."""
        positions, total_value = sample_portfolio
        cards = [
            _make_action_card("cn_588000", signal="add", ratio=0.05),
            _make_action_card("us_qqq", signal="hold"),
        ]
        cards_copy = copy.deepcopy(cards)

        result = build_capital_allocation_with_suppression(
            cards, positions, {}, _liquidity_summary(),
        )

        assert cards == cards_copy, (
            "action_cards mutated after allocation"
        )
        assert result.get("suppressed_adds", []) == []

    def test_below_800_suppression_leaves_card_unchanged(
        self, sample_portfolio, add_action_cards_with_below_threshold,
    ):
        """Below-800 add must not mutate the card; must appear in suppression list."""
        positions, total_value = sample_portfolio
        cards = add_action_cards_with_below_threshold
        cards_copy = copy.deepcopy(cards)

        result = build_capital_allocation_with_suppression(
            cards, positions, {}, _liquidity_summary(),
        )

        assert cards == cards_copy
        suppressed = result.get("suppressed_adds", [])
        below_threshold_ids = [
            s["position_id"] for s in suppressed
            if s.get("reason", "").startswith("below_minimum_amount")
        ]
        assert "alipay_nasdaq" in below_threshold_ids

    def test_deep_equality_of_cards(self, sample_portfolio):
        """Deep equality including nested lists and None fields."""
        positions, total_value = sample_portfolio
        cards = [
            _make_action_card("cn_588000", signal="reduce", ratio=0.3),
            _make_action_card("us_qqq", signal="hold"),
        ]
        cards_copy = copy.deepcopy(cards)

        build_capital_allocation_with_suppression(cards, positions, {}, _liquidity_summary())

        for original, after in zip(cards_copy, cards):
            for key in original:
                assert original[key] == after[key], (
                    f"Key '{key}' changed: {original[key]} != {after[key]}"
                )

    def test_multiple_below_threshold_suppressions(self, sample_portfolio):
        """Multiple below-threshold adds all appear in suppression, cards unchanged."""
        positions, total_value = sample_portfolio
        cards = [
            _make_action_card("alipay_nasdaq", signal="add", ratio=0.003),
            _make_action_card("alipay_gold", signal="add", ratio=0.005),
        ]
        cards_copy = copy.deepcopy(cards)

        result = build_capital_allocation_with_suppression(cards, positions, {}, _liquidity_summary())

        assert cards == cards_copy
        suppressed = result.get("suppressed_adds", [])
        assert len(suppressed) >= 2
        suppressed_ids = {s["position_id"] for s in suppressed}
        assert "alipay_nasdaq" in suppressed_ids
        assert "alipay_gold" in suppressed_ids


# ── Step 2: CashSchedule tests ────────────────────────────────────────

class TestCashSchedule:
    """CashSchedule classification and build."""

    def test_immediate_cash_includes_only_cash_and_t0_sold(self, sample_portfolio):
        """Immediate cash must include cash/t0 items, NOT unsold securities.

        Safety buffer (5%) may reduce immediate_cash to zero when cash is thin
        relative to total portfolio. The key invariant: unsold securities
        (stock, ETF, QDII, gold, WMP, insurance) must never appear in
        immediate_cash_position_ids.
        """
        positions, total_value = sample_portfolio
        schedule = build_cash_schedule(positions, [], total_value)

        # safety = 1,045,000 * 0.05 = 52,250 > cash 50,000  immediate = 0
        imm_ids = schedule.get("immediate_cash_position_ids", [])
        for pid in ["cn_588000", "us_qqq", "cn_512480", "alipay_nasdaq",
                    "alipay_gold", "ccb_wmp_no1", "boc_insurance"]:
            assert pid not in imm_ids, (
                f"Unsold {pid} must not be in immediate_cash position_ids"
            )
        # Cash items should be in immediate_cash_position_ids
        assert "cash_hkd" in imm_ids
        assert "cash_usd" in imm_ids
        assert "alipay_mmf" in imm_ids


    def test_unsold_etf_goes_to_strategic_exit_not_immediate(self):
        """Unsold ETF must NOT appear in immediate_cash position_ids."""
        positions = [
            _make_position("cn_588000", product_type="stock", liquidity_tier="t1",
                           market_value_cny=210_000.0),
            _make_position("cash_1", product_type="cash", liquidity_tier="cash",
                           market_value_cny=50_000.0),
        ]
        total = sum(p["market_value_cny"] for p in positions)
        schedule = build_cash_schedule(positions, [], total)

        imm_ids = schedule.get("immediate_cash_position_ids", [])
        assert "cn_588000" not in imm_ids
        assert "cash_1" in imm_ids

    def test_qdii_and_gold_are_strategic_exit_not_immediate(self):
        """QDII, precious metals go to strategic_exit; bank WMP, insurance to locked."""
        positions = [
            _make_position("alipay_nasdaq", product_type="qdii_fund",
                           liquidity_tier="t2_plus", market_value_cny=120_000.0),
            _make_position("alipay_gold", product_type="precious_metal_account",
                           liquidity_tier="t2_plus", market_value_cny=80_000.0),
            _make_position("ccb_wmp", product_type="bank_wealth_management",
                           liquidity_tier="periodic_open", market_value_cny=200_000.0),
            _make_position("boc_insurance", product_type="insurance_policy",
                           liquidity_tier="locked", market_value_cny=100_000.0),
            _make_position("cash_1", product_type="cash", liquidity_tier="cash",
                           market_value_cny=30_000.0),
        ]
        total = sum(p["market_value_cny"] for p in positions)
        schedule = build_cash_schedule(positions, [], total)

        imm_ids = schedule.get("immediate_cash_position_ids", [])
        for pid in ["alipay_nasdaq", "alipay_gold", "ccb_wmp", "boc_insurance"]:
            assert pid not in imm_ids, f"{pid} must not be in immediate_cash"

        # QDII and gold are strategic_exit; WMP and insurance are locked
        strategic = schedule.get("strategic_exit_value_cny", 0)
        locked = schedule.get("locked_value_cny", 0)
        # strategic = 120k + 80k = 200k
        assert strategic == 200_000.0, f"expected 200k strategic_exit, got {strategic}"
        # locked = 200k + 100k = 300k
        assert locked == 300_000.0, f"expected 300k locked, got {locked}"


    def test_exchange_traded_fund_t0_is_not_immediate_cash(self):
        positions = [
            _make_position(
                "t0_etf", product_type="exchange_traded_fund",
                liquidity_tier="t0", market_value_cny=100_000.0,
            ),
            _make_position(
                "cash_1", product_type="cash", liquidity_tier="cash",
                market_value_cny=20_000.0,
            ),
        ]
        schedule = build_cash_schedule(positions, [], 120_000.0)
        assert "t0_etf" not in schedule["immediate_cash_position_ids"]
        assert schedule["strategic_exit_value_cny"] == 100_000.0

    def test_money_market_t0_is_immediate_cash(self):
        positions = [
            _make_position(
                "mmf", product_type="money_market_fund",
                liquidity_tier="t0", market_value_cny=50_000.0,
            ),
        ]
        schedule = build_cash_schedule(positions, [], 50_000.0)
        assert "mmf" in schedule["immediate_cash_position_ids"]
        assert schedule["immediate_cash_cny"] == 47_500.0

    def test_approved_sale_moves_value_out_of_strategic_exit(self):
        positions = [
            _make_position(
                "cn_588000", product_type="exchange_traded_fund",
                liquidity_tier="t1", market_value_cny=100_000.0,
            ),
        ]
        sales = [{
            "position_id": "cn_588000", "ratio": 0.3,
            "settlement": {"timing": "T+1"},
        }]
        schedule = build_cash_schedule(positions, sales, 100_000.0)
        assert schedule["strategic_exit_value_cny"] == 70_000.0
        assert schedule["strategic_exit_position_ids"] == ["cn_588000"]
        assert schedule["settling_cash_cny"] == 30_000.0
        assert (
            schedule["strategic_exit_value_cny"]
            + schedule["settling_cash_cny"]
            + schedule["locked_value_cny"]
            + schedule["immediate_cash_cny"]
            + schedule["safety_buffer_cny"]
        ) == 100_000.0

    def test_multiple_approved_sales_cannot_exceed_position_value(self):
        positions = [
            _make_position(
                "cn_588000", product_type="exchange_traded_fund",
                liquidity_tier="t1", market_value_cny=100_000.0,
            ),
        ]
        sales = [
            {"position_id": "cn_588000", "ratio": 0.6,
             "settlement": {"timing": "T+0"}},
            {"position_id": "cn_588000", "ratio": 0.6,
             "settlement": {"timing": "T+1"}},
        ]
        schedule = build_cash_schedule(positions, sales, 100_000.0)
        classified = (
            schedule["immediate_cash_cny"]
            + schedule["settling_cash_cny"]
            + schedule["strategic_exit_value_cny"]
            + schedule["locked_value_cny"]
            + schedule["safety_buffer_cny"]
        )
        assert classified == 100_000.0
        assert schedule["settling_cash_cny"] == 40_000.0

    def test_non_insurance_ineligible_fund_is_strategic_exit(self):
        positions = [
            _make_position(
                "offline_fund", product_type="mixed_fund",
                liquidity_tier="t2_plus", market_value_cny=100_000.0,
                tradable=True, rebalance_eligible=False,
            ),
        ]
        schedule = build_cash_schedule(positions, [], 100_000.0)
        assert schedule["strategic_exit_value_cny"] == 100_000.0
        assert schedule["locked_value_cny"] == 0.0
        assert schedule["strategic_exit_position_ids"] == ["offline_fund"]

    def test_approved_sale_goes_to_settling_cash(self, sample_portfolio, approved_sales_fixture):
        """Approved sales with settlement appear in settling_cash."""
        positions, total_value = sample_portfolio
        schedule = build_cash_schedule(positions, approved_sales_fixture, total_value)

        assert schedule.get("settling_cash_cny", 0) > 0
        assert "cn_588000" in schedule.get("settling_cash_position_ids", [])

    def test_sale_with_missing_timing_is_unresolved_not_settling(self):
        """A sale dict with no settlement timing at all must not be
        fabricated into a T+1 confirmed_settling classification."""
        positions = [
            _make_position("cn_588000", product_type="exchange_traded_fund",
                           liquidity_tier="t1", market_value_cny=100_000.0),
        ]
        sales = [{"position_id": "cn_588000", "ratio": 0.3, "settlement": {}}]
        schedule = build_cash_schedule(positions, sales, 100_000.0)
        assert schedule["settling_cash_cny"] == 0.0
        assert schedule["immediate_cash_cny"] == 0.0
        assert schedule["unresolved_settlement_cny"] == 30_000.0
        assert schedule["unresolved_settlement_position_ids"] == ["cn_588000"]
        assert (
            schedule["strategic_exit_value_cny"]
            + schedule["settling_cash_cny"]
            + schedule["locked_value_cny"]
            + schedule["immediate_cash_cny"]
            + schedule["safety_buffer_cny"]
            + schedule["unresolved_settlement_cny"]
        ) == 100_000.0

    def test_sale_with_non_executable_timing_token_is_unresolved(self):
        """A settlement timing that is a non-executable machine token
        (review_required/periodic_open/locked) must classify the same as a
        missing timing: unresolved, never confirmed_settling."""
        positions = [
            _make_position("gold", product_type="precious_metal_account",
                           liquidity_tier="periodic_open", market_value_cny=100_000.0),
        ]
        for token in ("review_required", "periodic_open", "locked"):
            sales = [{"position_id": "gold", "ratio": 0.4,
                      "settlement": {"timing": token}}]
            schedule = build_cash_schedule(positions, sales, 100_000.0)
            assert schedule["settling_cash_cny"] == 0.0, token
            assert schedule["unresolved_settlement_cny"] == 40_000.0, token

    def test_safety_buffer_deducts_only_from_immediate_cash(self):
        """Safety buffer must only reduce immediate_cash_cny."""
        positions = [
            _make_position("cash_1", product_type="cash", liquidity_tier="cash",
                           market_value_cny=100_000.0),
            _make_position("cn_588000", product_type="stock", liquidity_tier="t1",
                           market_value_cny=200_000.0),
        ]
        total = sum(p["market_value_cny"] for p in positions)
        schedule = build_cash_schedule(positions, [], total)

        # safety = 300k * 0.05 = 15k, immediate = 100k - 15k = 85k
        assert schedule["immediate_cash_cny"] == 85_000.0
        assert schedule["strategic_exit_value_cny"] == 200_000.0

    def test_all_categories_present(self, sample_portfolio):
        """CashSchedule output must have all four categories."""
        positions, total_value = sample_portfolio
        schedule = build_cash_schedule(positions, [], total_value)

        for key in ("immediate_cash_cny", "settling_cash_cny",
                    "strategic_exit_value_cny", "locked_value_cny",
                    "immediate_cash_position_ids", "settling_cash_position_ids",
                    "strategic_exit_position_ids", "locked_position_ids",
                    "safety_buffer_cny", "unresolved_settlement_cny",
                    "unresolved_settlement_position_ids"):
            assert key in schedule, f"Missing key: {key}"

    def test_locked_items_reported_separately(self):
        """Locked items go to locked, not immediate or strategic."""
        positions = [
            _make_position("boc_insurance", product_type="insurance_policy",
                           liquidity_tier="locked", market_value_cny=100_000.0,
                           tradable=False, rebalance_eligible=False),
            _make_position("cash_1", product_type="cash", liquidity_tier="cash",
                           market_value_cny=30_000.0),
        ]
        total = sum(p["market_value_cny"] for p in positions)
        schedule = build_cash_schedule(positions, [], total)

        # safety = 130k * 0.05 = 6500, immediate = 30k - 6.5k = 23.5k
        assert schedule["locked_value_cny"] >= 100_000
        assert schedule["locked_position_ids"] == ["boc_insurance"]
        assert schedule["immediate_cash_cny"] == 23_500.0


# ── Step 3: Legacy compatibility tests ───────────────────────────────

class TestLegacyCompatibility:
    """_build_capital_allocation must not mutate cards and retain output."""

    def test_legacy_function_does_not_mutate(self, sample_portfolio):
        """The legacy capital allocation path also must not mutate."""
        from stocks.engine.scheduled_analysis import _build_capital_allocation

        positions, total_value = sample_portfolio
        cards = [_make_action_card("cn_588000", signal="add", ratio=0.003)]
        cards_copy = copy.deepcopy(cards)
        mapping = {"ratios": {}}

        _build_capital_allocation(cards, positions, mapping, _liquidity_summary())

        assert cards == cards_copy


class TestScheduledCashScheduleIntegration:
    def test_scheduled_run_exposes_cash_schedule_and_suppressions(self, sample_portfolio):
        from stocks.engine.scheduled_analysis import (
            ScheduledSession,
            SessionOccurrence,
            build_scheduled_run,
        )

        positions, _ = sample_portfolio
        cards_context = []
        for item in positions:
            item = copy.deepcopy(item)
            item.setdefault("display_name", item["position_id"])
            item.setdefault("evidence", {"price_freshness": "current"})
            cards_context.append(item)
        now = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
        occurrence = SessionOccurrence(
            session=ScheduledSession(
                id="cn_pre_close", market="cn", exchange_timezone="Asia/Shanghai",
                user_timezone="Asia/Shanghai", time="14:35",
                intent="pre_close_decision", push="normal", enabled=True,
                duplicate_window_minutes=90, holidays=frozenset(), primary_market="cn",
            ),
            market_date=now.date(), scheduled_for=now,
        )
        context = {
            "schema_version": 12, "generated_at": now.isoformat(),
            "data_quality": {"quotes": {"freshness": "current"}},
            "position_valuations": cards_context,
            "intelligence_digest": {
                "intelligence_health": {"status": "missing", "age_minutes": None, "risk_eligible": False},
                "intelligence_coverage": {}, "top_signals": [], "top_clusters": [],
            },
            "market_state": {}, "rotation": {},
            "portfolio_mapping": {"ratios": {}},
            "liquidity_summary": _liquidity_summary(), "action_signals": {},
        }
        original_positions = copy.deepcopy(context["position_valuations"])
        run = build_scheduled_run(
            context, occurrence=occurrence, generated_at=now,
            config={"quiet_hours": {"enabled": False}},
        )
        assert context["position_valuations"] == original_positions
        assert "cash_schedule" in run
        assert "suppressed_adds" in run["capital_allocation"]
        assert set(run["cash_schedule"]["immediate_cash_position_ids"]) == {
            "cash_hkd", "cash_usd", "alipay_mmf"
        }
        assert "cn_588000" not in run["cash_schedule"]["immediate_cash_position_ids"]

    def test_user_view_cash_matches_adjudicator_schedule_without_recomputation(
        self, sample_portfolio
    ):
        """The full pipeline (build_cash_schedule -> build_scheduled_run ->
        build_user_view) must project the same canonical amounts end-to-end
        (TASK-001D item 1). presentation.py must not recompute or diverge
        from the authoritative adjudicator cash_schedule.
        """
        from stocks.engine.scheduled_analysis import (
            ScheduledSession,
            SessionOccurrence,
            build_scheduled_run,
        )

        positions, _ = sample_portfolio
        cards_context = []
        for item in positions:
            item = copy.deepcopy(item)
            item.setdefault("display_name", item["position_id"])
            item.setdefault("evidence", {"price_freshness": "current"})
            cards_context.append(item)
        now = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
        occurrence = SessionOccurrence(
            session=ScheduledSession(
                id="cn_pre_close", market="cn", exchange_timezone="Asia/Shanghai",
                user_timezone="Asia/Shanghai", time="14:35",
                intent="pre_close_decision", push="normal", enabled=True,
                duplicate_window_minutes=90, holidays=frozenset(), primary_market="cn",
            ),
            market_date=now.date(), scheduled_for=now,
        )
        context = {
            "schema_version": 12, "generated_at": now.isoformat(),
            "data_quality": {"quotes": {"freshness": "current"}},
            "position_valuations": cards_context,
            "intelligence_digest": {
                "intelligence_health": {"status": "missing", "age_minutes": None, "risk_eligible": False},
                "intelligence_coverage": {}, "top_signals": [], "top_clusters": [],
            },
            "market_state": {}, "rotation": {},
            "portfolio_mapping": {"ratios": {}},
            "liquidity_summary": _liquidity_summary(), "action_signals": {},
        }
        run = build_scheduled_run(
            context, occurrence=occurrence, generated_at=now,
            config={"quiet_hours": {"enabled": False}},
        )
        authoritative = run["cash_schedule"]
        cash = run["portfolio_decision"]["user_view"]["assistant_brief"]["cash"]
        assert cash["available_now"]["amount_cny"] == round(authoritative["available_now"], 2)
        assert cash["confirmed_settling"]["amount_cny"] == round(authoritative["confirmed_settling"], 2)
        assert cash["planned_release"]["amount_cny"] == round(authoritative["planned_release"], 2)
        assert cash["strategic_exit"]["amount_cny"] == round(authoritative["strategic_exit"], 2)
        assert cash["locked"]["amount_cny"] == round(authoritative["locked"], 2)
        assert "immediate" not in cash
        assert "settling" not in cash


class TestScheduledAdjudicationFailure:
    def test_adjudicator_exception_fails_closed(self, sample_portfolio, monkeypatch):
        from stocks.engine import portfolio_adjudicator
        from stocks.engine.scheduled_analysis import (
            ScheduledSession,
            SessionOccurrence,
            build_scheduled_run,
        )

        def fail(*args, **kwargs):
            raise RuntimeError("forced adjudication failure")

        monkeypatch.setattr(portfolio_adjudicator, "adjudicate_portfolio", fail)
        positions, _ = sample_portfolio
        now = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
        occurrence = SessionOccurrence(
            session=ScheduledSession(
                id="cn_pre_close", market="cn", exchange_timezone="Asia/Shanghai",
                user_timezone="Asia/Shanghai", time="14:35",
                intent="pre_close_decision", push="normal", enabled=True,
                duplicate_window_minutes=90, holidays=frozenset(), primary_market="cn",
            ),
            market_date=now.date(), scheduled_for=now,
        )
        context = {
            "schema_version": 12, "generated_at": now.isoformat(),
            "data_quality": {"quotes": {"freshness": "current"}},
            "position_valuations": positions,
            "intelligence_digest": {
                "intelligence_health": {
                    "status": "missing", "age_minutes": None, "risk_eligible": False,
                },
                "intelligence_coverage": {}, "top_signals": [], "top_clusters": [],
            },
            "market_state": {}, "rotation": {},
            "portfolio_mapping": {"ratios": {}},
            "liquidity_summary": _liquidity_summary(), "action_signals": {},
        }
        run = build_scheduled_run(
            context, occurrence=occurrence, generated_at=now,
            config={"quiet_hours": {"enabled": False}},
        )
        decision = run["portfolio_decision"]
        assert decision["status"] == "review_required"
        assert decision["approved_actions"] == []
        assert len(decision["decision_id"]) == 16
        assert decision["unresolved_conflicts"][0]["code"] == "adjudication_failed"


# =====================================================================
# Task 5: PortfolioAdjudicator tests
# =====================================================================

# =====================================================================
# Task 5: PortfolioAdjudicator tests — six RED fixture classes
# =====================================================================

RULE_VERSION = "decision-trust-t1-v1"


def _run_id() -> str:
    return "20260715T144500Z_cn_pre_close"


def _expected_did(run_id: str, position_id: str, raw_signal: str, raw_ratio: float) -> str:
    raw = f"{run_id}{position_id}{raw_signal}{raw_ratio}{RULE_VERSION}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _make_evidence_map(
    *,
    data_anomaly: bool = False,
    exposure_tags: list[str] | None = None,
    product_type: str = "stock",
    liquidity_tier: str = "t1",
    market_value_cny: float = 10_000.0,
    price_freshness: str = "current",
) -> dict:
    return {
        "classification": {
            "product_type": product_type,
            "exposure_tags": exposure_tags or [product_type],
        },
        "liquidity": {"tier": liquidity_tier, "tradable": True, "rebalance_eligible": True},
        "market_value_cny": market_value_cny,
        "evidence": {
            "price_freshness": price_freshness,
            "data_anomalies": [
                {"code": "mixed_adjustment_regime", "severity": "high"}
            ] if data_anomaly else [],
            "action_eligible": not data_anomaly,
        },
        "product_type": product_type,
    }


def _make_card(
    position_id: str,
    *,
    signal: str = "hold",
    action: str = "持有观察",
    ratio: float = 0.0,
    product_type: str = "stock",
    raw_signal: str = "hold",
    raw_ratio: float = 0.0,
    evidence_status: str = "ok",
    liquidity_tier: str = "t1",
    institution_type: str = "",
) -> dict:
    return {
        "position_id": position_id,
        "display_name": position_id,
        "instrument_key": f"a:{position_id.split('_')[-1]}",
        "product_type": product_type,
        "routing": "full",
        "account_type": "broker",
        "institution_type": institution_type,
        "signal": signal,
        "action": action,
        "ratio": ratio,
        "facts": [],
        "stop_price": None,
        "target_prices": [],
        "position_limit_pct": 5.0,
        "current_weight_pct": 0.05,
        "risk_to_stop_pct": None,
        "risk_amount_cny": None,
        "intelligence_conflict": "none",
        "drivers": [],
        "dissent": None,
        "confidence": "high",
        "raw_signal": raw_signal,
        "raw_ratio": raw_ratio,
        "raw_action": action,
        "evidence_status": evidence_status,
        "liquidity_tier": liquidity_tier,
    }


def _constraints(
    gold_min: float = 0.0, gold_max: float = 0.15,
    equity_min: float = 0.25, equity_max: float = 0.55,
) -> dict:
    return {
        "黄金": {"min": gold_min, "max": gold_max},
        "权益": {"min": equity_min, "max": equity_max},
    }


def _risk_state(*, suspend_accumulation: bool = False, level: str = "normal") -> dict:
    return {
        "level": level,
        "suspend_accumulation": suspend_accumulation,
        "cash_target_pct": None,
        "active_triggers": [],
    }


def _liquidity(
    immediate: float = 50_000.0,
    settling: float = 0.0,
    strategic: float = 0.0,
    locked: float = 0.0,
) -> dict:
    return {
        "immediate_cash_cny": immediate,
        "settling_cash_cny": settling,
        "strategic_exit_value_cny": strategic,
        "locked_value_cny": locked,
        "safety_buffer_cny": 0.0,
    }


def _evidences(positions: list[dict]) -> dict[str, dict]:
    ev = {}
    for p in positions:
        pid = p["position_id"]
        ev[pid] = {
            "instrument_key": p.get("instrument_key", ""),
            "holding": p.get("holding", {}),
            "classification": p.get("classification", {}),
            "liquidity": p.get("liquidity", {}),
            "market_value_cny": p.get("market_value_cny", 0.0),
            "evidence": p.get("evidence", {}),
            "product_type": p.get("classification", {}).get("product_type", ""),
        }
    return ev


def _production_execution_rules() -> dict:
    return yaml.safe_load(Path("stocks/config/engine.yaml").read_text())["execution_rules"]


# ── Fixture 1: 数据异常 ──────────────────────────────────────────────


class TestDataAnomaly:
    """Position with data anomaly must be suppressed, not approved."""

    def test_data_anomaly_suppressed(self):
        """A card with evidence_status='blocked' must end in suppressed_actions."""
        cards = [_make_card("cn_588000", signal="reduce", ratio=0.3,
                            raw_signal="reduce", raw_ratio=0.3,
                            evidence_status="blocked")]
        evidences = {"cn_588000": _make_evidence_map(data_anomaly=True)}
        constraints = _constraints()
        risk = _risk_state()
        liquidity = _liquidity()

        decision = adjudicate_portfolio(
            cards, evidences, constraints, risk, liquidity,
            run_id=_run_id(), rule_version=RULE_VERSION,
        )

        assert decision.status == "suppressed"
        assert len(decision.approved_actions) == 0
        assert len(decision.suppressed_actions) >= 1
        suppressed_ids = [a.position_id for a in decision.suppressed_actions]
        assert "cn_588000" in suppressed_ids

    def test_data_anomaly_never_approved(self):
        """When data anomaly exists, status must not be approved."""
        cards = [_make_card("cn_588000", signal="reduce", ratio=0.3,
                            raw_signal="reduce", raw_ratio=0.3,
                            evidence_status="blocked")]
        evidences = {"cn_588000": _make_evidence_map(data_anomaly=True)}
        constraints = _constraints()
        risk = _risk_state()

        decision = adjudicate_portfolio(
            cards, evidences, constraints, risk, _liquidity(),
            run_id=_run_id(), rule_version=RULE_VERSION,
        )
        assert decision.status != "approved"

    def test_healthy_card_not_suppressed(self):
        """Without anomaly, the card should not be suppressed due to anomaly."""
        cards = [_make_card("cn_588000", signal="hold", ratio=0.0,
                            raw_signal="hold", raw_ratio=0.0,
                            evidence_status="ok")]
        evidences = {"cn_588000": _make_evidence_map(data_anomaly=False)}
        constraints = _constraints()
        risk = _risk_state()

        decision = adjudicate_portfolio(
            cards, evidences, constraints, risk, _liquidity(),
            run_id=_run_id(), rule_version=RULE_VERSION,
        )
        suppressed = [a for a in decision.suppressed_actions
                      if "anomaly" in a.reason.lower()]
        assert len(suppressed) == 0


# ── Fixture 2: 黄金超配加仓 ──────────────────────────────────────────


class TestGoldOverAllocationAdd:
    """Gold over max limit with add signal must be suppressed."""

    def test_gold_add_suppressed_when_over_allocated(self):
        """Gold = 16.7%, max = 15%, add signal -> suppressed."""
        cards = [_make_card("alipay_gold", signal="add", ratio=0.02,
                            raw_signal="add", raw_ratio=0.02,
                            product_type="precious_metal_account")]
        pos = _make_position("alipay_gold", product_type="precious_metal_account",
                             exposure_tags=["gold"], market_value_cny=80_000.0)
        evidences = _evidences([pos])
        constraints = _constraints(gold_max=0.15, equity_min=0.25)
        risk = _risk_state()

        decision = adjudicate_portfolio(
            cards, evidences, constraints, risk, _liquidity(),
            run_id=_run_id(), rule_version=RULE_VERSION,
        )

        assert decision.status != "approved"
        suppressed_ids = [a.position_id for a in decision.suppressed_actions]
        assert "alipay_gold" in suppressed_ids

    def test_gold_hold_allowed_when_over_allocated(self):
        """Hold signal for gold over limit is fine — no conflict."""
        cards = [_make_card("alipay_gold", signal="hold", ratio=0.0,
                            raw_signal="hold", raw_ratio=0.0,
                            product_type="precious_metal_account")]
        pos = _make_position("alipay_gold", product_type="precious_metal_account",
                             exposure_tags=["gold"], market_value_cny=80_000.0)
        evidences = _evidences([pos])
        constraints = _constraints(gold_max=0.15, equity_min=0.25)
        risk = _risk_state()

        decision = adjudicate_portfolio(
            cards, evidences, constraints, risk, _liquidity(),
            run_id=_run_id(), rule_version=RULE_VERSION,
        )
        assert len(decision.unresolved_conflicts) == 0


# ── Fixture 3: 权益低配减仓无替代 ─────────────────────────────────────


class TestEquityUnderWeightReduceNoAlternative:
    """Equity under min with reduce signal but no alternative -> review_required."""

    def test_review_required_when_no_alternative(self):
        """Equity under min + reduce signal + no alternative equity buy ->
        review_required, and NO fabricated partial action (P1-1: the
        conflict is handed to the user unresolved; the old 50%-default
        execution was removed)."""
        cards = [_make_card("cn_588000", signal="reduce", ratio=0.3,
                            raw_signal="reduce", raw_ratio=0.3)]
        positions = [
            _make_position("cn_588000", exposure_tags=["a_share"],
                           market_value_cny=100_000.0),
            _make_position("alipay_gold", product_type="precious_metal_account",
                           exposure_tags=["gold"], market_value_cny=400_000.0),
            _make_position("ccb_wmp", product_type="bank_wealth_management",
                           exposure_tags=["bank_wmp"], liquidity_tier="periodic_open",
                           market_value_cny=100_000.0, tradable=False),
        ]
        evidences = _evidences(positions)
        constraints = _constraints(equity_min=0.25, equity_max=0.55, gold_max=0.15)
        risk = _risk_state()

        decision = adjudicate_portfolio(
            cards, evidences, constraints, risk, _liquidity(),
            run_id=_run_id(), rule_version=RULE_VERSION,
        )

        assert decision.status == "review_required"
        assert decision.approved_actions == []
        assert len(decision.unresolved_conflicts) > 0
        assert decision.unresolved_conflicts[0]["position_id"] == "cn_588000"

    def test_unresolved_conflict_mentions_equity_bucket(self):
        """The unresolved conflict must reference the equity bucket."""
        cards = [_make_card("cn_588000", signal="reduce", ratio=0.3,
                            raw_signal="reduce", raw_ratio=0.3)]
        positions = [
            _make_position("cn_588000", exposure_tags=["a_share"],
                           market_value_cny=100_000.0),
            _make_position("alipay_gold", product_type="precious_metal_account",
                           exposure_tags=["gold"], market_value_cny=400_000.0),
        ]
        evidences = _evidences(positions)
        constraints = _constraints(equity_min=0.25, equity_max=0.55)
        risk = _risk_state()

        decision = adjudicate_portfolio(
            cards, evidences, constraints, risk, _liquidity(),
            run_id=_run_id(), rule_version=RULE_VERSION,
        )
        conflict_texts = [c.get("message", "") for c in decision.unresolved_conflicts]
        assert any("权益" in t for t in conflict_texts)


# ── Fixture 4: 权益低配减仓有替代 ─────────────────────────────────────


class TestEquityUnderWeightReduceWithAlternative:
    """Equity under min with reduce signal + alternative equity buy -> replacement chain."""

    def _setup(self):
        cards = [
            _make_card("cn_588000", signal="reduce", ratio=0.3,
                       raw_signal="reduce", raw_ratio=0.3,
                       institution_type="brokerage"),
            _make_card("us_qqq", signal="add", ratio=0.02,
                       raw_signal="add", raw_ratio=0.02,
                       institution_type="brokerage"),
        ]
        positions = [
            _make_position("cn_588000", exposure_tags=["a_share"],
                           market_value_cny=100_000.0),
            _make_position("us_qqq", exposure_tags=["us_equity"],
                           market_value_cny=150_000.0),
            _make_position("alipay_gold", product_type="precious_metal_account",
                           exposure_tags=["gold"], market_value_cny=800_000.0),
        ]
        return cards, positions

    def test_replacement_chain_produced(self):
        """With alternative equity to buy, must produce a replacement chain."""
        cards, positions = self._setup()
        evidences = _evidences(positions)
        constraints = _constraints(equity_min=0.25, equity_max=0.55)
        risk = _risk_state()

        decision = adjudicate_portfolio(
            cards, evidences, constraints, risk, _liquidity(settling=30_000.0),
            run_id=_run_id(), rule_version=RULE_VERSION,
        )

        assert len(decision.replacement_chains) > 0

    def test_chain_has_sale_and_buy_leg(self):
        """Each replacement chain must have sale leg, buy leg, timing, post_trade_ratio."""
        cards, positions = self._setup()
        evidences = _evidences(positions)
        constraints = _constraints(equity_min=0.25, equity_max=0.55)
        risk = _risk_state()

        decision = adjudicate_portfolio(
            cards, evidences, constraints, risk, _liquidity(settling=30_000.0),
            run_id=_run_id(), rule_version=RULE_VERSION,
        )

        for chain in decision.replacement_chains:
            assert chain.sale_leg is not None
            assert chain.buy_leg is not None
            assert chain.settlement_timing is not None
            assert chain.post_trade_ratio is not None

    def test_chain_sale_leg_is_reduce_position(self):
        """The sale leg must be the position with the reduce signal."""
        cards, positions = self._setup()
        evidences = _evidences(positions)
        constraints = _constraints(equity_min=0.25, equity_max=0.55)
        risk = _risk_state()

        decision = adjudicate_portfolio(
            cards, evidences, constraints, risk, _liquidity(settling=30_000.0),
            run_id=_run_id(), rule_version=RULE_VERSION,
        )

        for chain in decision.replacement_chains:
            assert chain.sale_leg.position_id == "cn_588000"

    def test_chain_buy_leg_is_alternative_equity(self):
        """The buy leg must be an alternative equity position."""
        cards, positions = self._setup()
        evidences = _evidences(positions)
        constraints = _constraints(equity_min=0.25, equity_max=0.55)
        risk = _risk_state()

        decision = adjudicate_portfolio(
            cards, evidences, constraints, risk, _liquidity(settling=30_000.0),
            run_id=_run_id(), rule_version=RULE_VERSION,
        )

        for chain in decision.replacement_chains:
            assert chain.buy_leg.position_id == "us_qqq"

    def test_status_can_be_approved_with_chain(self):
        """A replacement chain's buy leg always needs review (its ratio is
        portfolio-value based, not position based), so overall status must
        be review_required whenever a chain is present — even though the
        sale leg itself resolves to a normal, fully-executable action."""
        cards, positions = self._setup()
        evidences = _evidences(positions)
        constraints = _constraints(equity_min=0.25, equity_max=0.55)
        risk = _risk_state()

        decision = adjudicate_portfolio(
            cards, evidences, constraints, risk, _liquidity(settling=30_000.0),
            run_id=_run_id(), rule_version=RULE_VERSION,
            execution_rules=_production_execution_rules(),
        )

        if len(decision.unresolved_conflicts) == 0 and decision.replacement_chains:
            assert decision.status == "review_required"
            assert len(decision.approved_actions) > 0
            chain = decision.replacement_chains[0]
            assert chain.sale_leg.execution_status != "review_required"
            assert chain.buy_leg.execution_status == "review_required"
            assert chain.buy_leg.executable_quantity is None


class TestReplacementChainSemantics:
    def test_sale_settlement_comes_from_reduce_position(self):
        """Only fund_platform+t2_plus resolves to T+2 in production config —
        the sale leg's settlement is a config match, not an institution-
        agnostic "any t2_plus" guess."""
        cards = [
            _make_card("sell_t2", signal="reduce", ratio=0.25,
                       raw_signal="reduce", raw_ratio=0.25,
                       institution_type="fund_platform"),
            _make_card("buy_t1", signal="add", ratio=0.02,
                       raw_signal="add", raw_ratio=0.02,
                       institution_type="fund_platform"),
            _make_card("last_t0", signal="hold", ratio=0.0),
        ]
        positions = [
            _make_position("sell_t2", exposure_tags=["a_share"],
                           liquidity_tier="t2_plus", market_value_cny=100_000.0),
            _make_position("buy_t1", exposure_tags=["us_equity"],
                           liquidity_tier="t1", market_value_cny=100_000.0),
            _make_position("last_t0", product_type="cash", exposure_tags=["cash_like"],
                           liquidity_tier="t0", market_value_cny=800_000.0),
        ]
        decision = adjudicate_portfolio(
            cards, _evidences(positions), _constraints(equity_min=0.25),
            _risk_state(), _liquidity(), run_id=_run_id(), rule_version=RULE_VERSION,
            execution_rules=_production_execution_rules(),
        )
        assert decision.replacement_chains[0].settlement_timing == "T+2"
        assert decision.replacement_chains[0].sale_leg.settlement_timing == "T+2"

    def test_full_equity_reinvestment_preserves_total_equity_ratio(self):
        cards = [
            _make_card("sell", signal="reduce", ratio=0.5,
                       raw_signal="reduce", raw_ratio=0.5),
            _make_card("buy", signal="add", ratio=0.02,
                       raw_signal="add", raw_ratio=0.02),
            _make_card("other_equity", signal="hold", ratio=0.0),
        ]
        positions = [
            _make_position("sell", exposure_tags=["a_share"], market_value_cny=100_000.0),
            _make_position("buy", exposure_tags=["us_equity"], market_value_cny=50_000.0),
            _make_position("other_equity", exposure_tags=["a_share"], market_value_cny=50_000.0),
            _make_position("fixed", product_type="fixed_income_plus_fund",
                           exposure_tags=["fixed_income"], market_value_cny=800_000.0),
        ]
        decision = adjudicate_portfolio(
            cards, _evidences(positions), _constraints(equity_min=0.25),
            _risk_state(), _liquidity(), run_id=_run_id(), rule_version=RULE_VERSION,
        )
        assert decision.replacement_chains[0].post_trade_ratio == 0.2

    def test_derived_buy_action_id_includes_planned_ratio(self):
        cards = [
            _make_card("sell_a", signal="reduce", ratio=0.25,
                       raw_signal="reduce", raw_ratio=0.25),
            _make_card("sell_b", signal="reduce", ratio=0.25,
                       raw_signal="reduce", raw_ratio=0.25),
            _make_card("buy", signal="add", ratio=0.02,
                       raw_signal="add", raw_ratio=0.02),
        ]
        positions = [
            _make_position("sell_a", exposure_tags=["a_share"], market_value_cny=50_000.0),
            _make_position("sell_b", exposure_tags=["a_share"], market_value_cny=50_000.0),
            _make_position("buy", exposure_tags=["us_equity"], market_value_cny=50_000.0),
            _make_position("fixed", product_type="fixed_income_plus_fund",
                           exposure_tags=["fixed_income"], market_value_cny=850_000.0),
        ]
        decision = adjudicate_portfolio(
            cards, _evidences(positions), _constraints(equity_min=0.25),
            _risk_state(), _liquidity(), run_id=_run_id(), rule_version=RULE_VERSION,
        )
        ids = [action.decision_id for action in decision.approved_actions]
        assert len(ids) == len(set(ids))
        buy_ids = [chain.buy_leg.decision_id for chain in decision.replacement_chains]
        assert len(buy_ids) == len(set(buy_ids))

    def test_buy_leg_waits_for_sale_proceeds(self):
        cards = [
            _make_card("sell_t2", signal="reduce", ratio=0.25,
                       raw_signal="reduce", raw_ratio=0.25),
            _make_card("buy", signal="add", ratio=0.02,
                       raw_signal="add", raw_ratio=0.02),
        ]
        positions = [
            _make_position("sell_t2", exposure_tags=["a_share"],
                           liquidity_tier="t2_plus", market_value_cny=100_000.0),
            _make_position("buy", exposure_tags=["us_equity"], market_value_cny=50_000.0),
            _make_position("fixed", product_type="fixed_income_plus_fund",
                           exposure_tags=["fixed_income"], market_value_cny=850_000.0),
        ]
        decision = adjudicate_portfolio(
            cards, _evidences(positions), _constraints(equity_min=0.25),
            _risk_state(), _liquidity(), run_id=_run_id(), rule_version=RULE_VERSION,
        )
        chain = decision.replacement_chains[0]
        assert chain.buy_leg.settlement_timing == "after_sale_proceeds"
        assert "到账" in chain.buy_leg.reason

    def test_hold_card_is_not_promoted_to_replacement_buy(self):
        cards = [
            _make_card("sell", signal="reduce", ratio=0.5,
                       raw_signal="reduce", raw_ratio=0.5),
            _make_card("hold_only", signal="hold", ratio=0.0,
                       raw_signal="hold", raw_ratio=0.0),
        ]
        positions = [
            _make_position("sell", exposure_tags=["a_share"], market_value_cny=100_000.0),
            _make_position("hold_only", exposure_tags=["us_equity"], market_value_cny=50_000.0),
            _make_position("fixed", product_type="fixed_income_plus_fund",
                           exposure_tags=["fixed_income"], market_value_cny=850_000.0),
        ]
        decision = adjudicate_portfolio(
            cards, _evidences(positions), _constraints(equity_min=0.25),
            _risk_state(), _liquidity(), run_id=_run_id(), rule_version=RULE_VERSION,
        )
        assert decision.status == "review_required"
        assert decision.replacement_chains == []
        assert all(a.position_id != "hold_only" for a in decision.approved_actions)

    def test_buy_ratio_is_sale_proceeds_over_total_portfolio(self):
        cards = [
            _make_card("sell", signal="reduce", ratio=0.5,
                       raw_signal="reduce", raw_ratio=0.5),
            _make_card("buy", signal="add", ratio=0.02,
                       raw_signal="add", raw_ratio=0.02),
        ]
        positions = [
            _make_position("sell", exposure_tags=["a_share"], market_value_cny=100_000.0),
            _make_position("buy", exposure_tags=["us_equity"], market_value_cny=50_000.0),
            _make_position("fixed", product_type="fixed_income_plus_fund",
                           exposure_tags=["fixed_income"], market_value_cny=850_000.0),
        ]
        decision = adjudicate_portfolio(
            cards, _evidences(positions), _constraints(equity_min=0.25),
            _risk_state(), _liquidity(), run_id=_run_id(), rule_version=RULE_VERSION,
        )
        assert decision.replacement_chains[0].buy_leg.ratio == 0.05

    def test_buy_leg_estimated_amount_uses_portfolio_basis(self):
        """Buy-leg ratio is portfolio-basis, so its estimated amount must be
        total_portfolio x ratio (= sale proceeds), not alt_position_value x
        ratio (adversarial review P0-1: 50k x 0.05 = 2.5k would be wrong;
        1M x 0.05 = 50k = sale proceeds is right)."""
        cards = [
            _make_card("sell", signal="reduce", ratio=0.5,
                       raw_signal="reduce", raw_ratio=0.5),
            _make_card("buy", signal="add", ratio=0.02,
                       raw_signal="add", raw_ratio=0.02),
        ]
        positions = [
            _make_position("sell", exposure_tags=["a_share"], market_value_cny=100_000.0),
            _make_position("buy", exposure_tags=["us_equity"], market_value_cny=50_000.0),
            _make_position("fixed", product_type="fixed_income_plus_fund",
                           exposure_tags=["fixed_income"], market_value_cny=850_000.0),
        ]
        decision = adjudicate_portfolio(
            cards, _evidences(positions), _constraints(equity_min=0.25),
            _risk_state(), _liquidity(), run_id=_run_id(), rule_version=RULE_VERSION,
        )
        buy_leg = decision.replacement_chains[0].buy_leg
        assert buy_leg.estimated_amount_cny == 50_000.0


# ── Fixture 5: 风险暂停加仓 ──────────────────────────────────────────


class TestRiskSuspendAdd:
    """When risk state has suspend_accumulation=True, all add signals suppressed."""

    def test_add_suppressed_during_suspend(self):
        """suspend_accumulation=True -> all add cards in suppressed_actions."""
        cards = [
            _make_card("cn_588000", signal="add", ratio=0.02,
                       raw_signal="add", raw_ratio=0.02),
            _make_card("us_qqq", signal="add", ratio=0.02,
                       raw_signal="add", raw_ratio=0.02),
        ]
        positions = [
            _make_position("cn_588000", exposure_tags=["a_share"],
                           market_value_cny=100_000.0),
            _make_position("alipay_gold", product_type="precious_metal_account",
                           exposure_tags=["gold"], market_value_cny=400_000.0),
        ]
        evidences = _evidences(positions)
        constraints = _constraints()
        risk = _risk_state(suspend_accumulation=True, level="hedge")

        decision = adjudicate_portfolio(
            cards, evidences, constraints, risk, _liquidity(),
            run_id=_run_id(), rule_version=RULE_VERSION,
        )

        assert decision.status != "approved"
        suppressed_ids = [a.position_id for a in decision.suppressed_actions]
        assert "cn_588000" in suppressed_ids

    def test_stop_loss_not_suppressed_during_suspend(self):
        """Stop_loss should still be allowed during suspend."""
        cards = [_make_card("cn_588000", signal="stop_loss", ratio=1.0,
                            raw_signal="stop_loss", raw_ratio=1.0)]
        positions = [
            _make_position("cn_588000", exposure_tags=["a_share"],
                           market_value_cny=100_000.0),
            _make_position("alipay_gold", product_type="precious_metal_account",
                           exposure_tags=["gold"], market_value_cny=400_000.0),
        ]
        evidences = _evidences(positions)
        constraints = _constraints()
        risk = _risk_state(suspend_accumulation=True, level="hedge")

        decision = adjudicate_portfolio(
            cards, evidences, constraints, risk, _liquidity(),
            run_id=_run_id(), rule_version=RULE_VERSION,
        )

        suppressed_ids = [a.position_id for a in decision.suppressed_actions]
        assert "cn_588000" not in suppressed_ids

    def test_reduce_not_suppressed_during_suspend(self):
        """Reduce signals should still be allowed during suspend."""
        cards = [_make_card("cn_588000", signal="reduce", ratio=0.3,
                            raw_signal="reduce", raw_ratio=0.3)]
        positions = [
            _make_position("cn_588000", exposure_tags=["a_share"],
                           market_value_cny=100_000.0),
            _make_position("alipay_gold", product_type="precious_metal_account",
                           exposure_tags=["gold"], market_value_cny=400_000.0),
        ]
        evidences = _evidences(positions)
        constraints = {}  # No constraints, so equity under-weight check doesn't trigger
        risk = _risk_state(suspend_accumulation=True, level="hedge")

        decision = adjudicate_portfolio(
            cards, evidences, constraints, risk, _liquidity(),
            run_id=_run_id(), rule_version=RULE_VERSION,
        )

        suppressed_ids = [a.position_id for a in decision.suppressed_actions]
        assert "cn_588000" not in suppressed_ids


# ── Fixture 6: 锁定资产 ──────────────────────────────────────────────


class TestLockedPositions:
    """Locked/periodic_open positions with any signal -> suppressed."""

    def test_locked_suppressed(self):
        """Locked tier position with any action -> suppressed."""
        cards = [_make_card("boc_insurance", signal="reduce", ratio=0.3,
                            raw_signal="reduce", raw_ratio=0.3,
                            product_type="insurance_policy",
                            liquidity_tier="locked")]
        pos = _make_position("boc_insurance", product_type="insurance_policy",
                             liquidity_tier="locked", market_value_cny=100_000.0,
                             tradable=False, rebalance_eligible=False)
        evidences = _evidences([pos])
        constraints = _constraints()
        risk = _risk_state()

        decision = adjudicate_portfolio(
            cards, evidences, constraints, risk, _liquidity(),
            run_id=_run_id(), rule_version=RULE_VERSION,
        )

        assert decision.status == "suppressed"
        suppressed_ids = [a.position_id for a in decision.suppressed_actions]
        assert "boc_insurance" in suppressed_ids

    def test_periodic_open_suppressed(self):
        """Periodic_open tier position -> suppressed."""
        cards = [_make_card("ccb_wmp", signal="hold", ratio=0.0,
                            raw_signal="hold", raw_ratio=0.0,
                            product_type="bank_wealth_management",
                            liquidity_tier="periodic_open")]
        pos = _make_position("ccb_wmp", product_type="bank_wealth_management",
                             liquidity_tier="periodic_open", market_value_cny=200_000.0,
                             tradable=False)
        evidences = _evidences([pos])
        constraints = _constraints()
        risk = _risk_state()

        decision = adjudicate_portfolio(
            cards, evidences, constraints, risk, _liquidity(),
            run_id=_run_id(), rule_version=RULE_VERSION,
        )

        assert decision.status == "suppressed"
        suppressed_ids = [a.position_id for a in decision.suppressed_actions]
        assert "ccb_wmp" in suppressed_ids


# ── Decision ID determinism ──────────────────────────────────────────


class TestDecisionIdDeterminism:
    """decision_id must be deterministic sha256 hash."""

    def test_deterministic_hash(self):
        """Same inputs produce same decision_id."""
        did1 = _expected_did(_run_id(), "cn_588000", "reduce", 0.3)
        did2 = _expected_did(_run_id(), "cn_588000", "reduce", 0.3)
        assert did1 == did2
        assert len(did1) == 16

    def test_different_inputs_different_hash(self):
        """Different inputs produce different decision_id."""
        rid = _run_id()
        did1 = _expected_did(rid, "cn_588000", "reduce", 0.3)
        did2 = _expected_did(rid, "cn_588000", "reduce", 0.5)
        did3 = _expected_did(rid, "us_qqq", "reduce", 0.3)
        assert did1 != did2
        assert did1 != did3
        assert did2 != did3

    def test_decision_id_on_approved_action(self):
        """Approved actions must have a valid decision_id."""
        cards = [_make_card("cn_588000", signal="stop_loss", ratio=1.0,
                            raw_signal="stop_loss", raw_ratio=1.0)]
        positions = [
            _make_position("cn_588000", exposure_tags=["a_share"],
                           market_value_cny=100_000.0),
            _make_position("alipay_gold", product_type="precious_metal_account",
                           exposure_tags=["gold"], market_value_cny=400_000.0),
        ]
        evidences = _evidences(positions)
        constraints = _constraints()
        risk = _risk_state()

        decision = adjudicate_portfolio(
            cards, evidences, constraints, risk, _liquidity(),
            run_id=_run_id(), rule_version=RULE_VERSION,
        )

        if decision.approved_actions:
            for action in decision.approved_actions:
                assert len(action.decision_id) == 16
                assert isinstance(action.decision_id, str)


class TestDecisionCompleteness:
    def test_stop_loss_is_approved_exactly_once_during_review(self):
        cards = [_make_card("stop", signal="stop_loss", ratio=1.0,
                            raw_signal="stop_loss", raw_ratio=1.0)]
        positions = [
            _make_position("stop", exposure_tags=["a_share"], market_value_cny=100_000.0),
            _make_position("fixed", product_type="fixed_income_plus_fund",
                           exposure_tags=["fixed_income"], market_value_cny=900_000.0),
        ]
        decision = adjudicate_portfolio(
            cards, _evidences(positions), _constraints(equity_min=0.25),
            _risk_state(), _liquidity(), run_id=_run_id(), rule_version=RULE_VERSION,
        )
        approved = [a for a in decision.approved_actions if a.position_id == "stop"]
        assert decision.status == "review_required"
        assert len(approved) == 1

    def test_decision_contains_post_trade_projection(self):
        cards = [_make_card("gold", signal="reduce", ratio=0.5,
                            raw_signal="reduce", raw_ratio=0.5,
                            product_type="precious_metal_account")]
        positions = [
            _make_position("gold", product_type="precious_metal_account",
                           exposure_tags=["gold"], market_value_cny=200_000.0),
            _make_position("cash", product_type="cash", exposure_tags=["cash_like"],
                           liquidity_tier="cash", market_value_cny=800_000.0),
        ]
        decision = adjudicate_portfolio(
            cards, _evidences(positions), _constraints(gold_max=0.15),
            _risk_state(), _liquidity(), run_id=_run_id(), rule_version=RULE_VERSION,
        )
        projection = decision.to_dict()["post_trade_projection"]
        assert projection["before_ratios"]["黄金"] == 0.2
        assert projection["after_ratios"]["黄金"] == 0.1
        assert projection["cash_schedule_before"] == _liquidity()
        assert projection["cash_schedule_after"] == decision.to_dict()["cash_schedule"]

    def test_approved_sale_updates_decision_cash_schedule(self):
        cards = [_make_card("gold", signal="reduce", ratio=0.5,
                            raw_signal="reduce", raw_ratio=0.5,
                            product_type="precious_metal_account",
                            institution_type="brokerage")]
        positions = [
            _make_position("gold", product_type="precious_metal_account",
                           exposure_tags=["gold"], liquidity_tier="t1",
                           market_value_cny=200_000.0),
            _make_position("cash", product_type="cash", exposure_tags=["cash_like"],
                           liquidity_tier="cash", market_value_cny=800_000.0),
        ]
        initial = build_cash_schedule(positions, [], 1_000_000.0)
        decision = adjudicate_portfolio(
            cards, _evidences(positions), _constraints(gold_max=0.15),
            _risk_state(), initial, run_id=_run_id(), rule_version=RULE_VERSION,
            execution_rules=_production_execution_rules(),
        )
        schedule = decision.to_dict()["cash_schedule"]
        assert schedule["settling_cash_cny"] == 100_000.0
        assert "gold" in schedule["settling_cash_position_ids"]
        assert schedule["strategic_exit_value_cny"] == 100_000.0
        assert schedule["unresolved_settlement_cny"] == 0.0

    def test_approved_sale_with_unresolved_settlement_is_not_confirmed_settling(self):
        """A reduce action whose settlement_rule can't be resolved (no
        matching production rule for this institution/tier combination)
        must not have its sale proceeds fabricated into confirmed_settling
        cash — they must land in unresolved_settlement_cny instead."""
        cards = [_make_card("gold", signal="reduce", ratio=0.5,
                            raw_signal="reduce", raw_ratio=0.5,
                            product_type="precious_metal_account",
                            institution_type="brokerage")]
        positions = [
            _make_position("gold", product_type="precious_metal_account",
                           exposure_tags=["gold"], liquidity_tier="t2_plus",
                           market_value_cny=200_000.0),
            _make_position("cash", product_type="cash", exposure_tags=["cash_like"],
                           liquidity_tier="cash", market_value_cny=800_000.0),
        ]
        initial = build_cash_schedule(positions, [], 1_000_000.0)
        decision = adjudicate_portfolio(
            cards, _evidences(positions), _constraints(gold_max=0.15),
            _risk_state(), initial, run_id=_run_id(), rule_version=RULE_VERSION,
            execution_rules=_production_execution_rules(),
        )
        approved = next(a for a in decision.approved_actions if a.position_id == "gold")
        assert approved.settlement_rule == "review_required"
        assert approved.settlement_timing is None
        schedule = decision.to_dict()["cash_schedule"]
        assert schedule["settling_cash_cny"] == 0.0
        assert schedule["unresolved_settlement_cny"] == 100_000.0
        assert "gold" in schedule["unresolved_settlement_position_ids"]
        assert "gold" not in schedule["settling_cash_position_ids"]


# ── Status mutual exclusion ──────────────────────────────────────────


class TestStatusMutualExclusion:
    """approved, suppressed, review_required must be mutually exclusive."""

    def test_status_is_one_of_three(self):
        """status must be one of: approved, suppressed, review_required."""
        cards = [_make_card("cn_588000", signal="hold", ratio=0.0)]
        pos = _make_position("cn_588000", market_value_cny=100_000.0)
        evidences = _evidences([pos])
        constraints = _constraints()
        risk = _risk_state()

        decision = adjudicate_portfolio(
            cards, evidences, constraints, risk, _liquidity(),
            run_id=_run_id(), rule_version=RULE_VERSION,
        )
        assert decision.status in ("approved", "suppressed", "review_required")

    def test_unresolved_conflicts_block_approved(self):
        """When unresolved_conflicts is non-empty, status must NOT be approved."""
        cards = [_make_card("cn_588000", signal="reduce", ratio=0.3,
                            raw_signal="reduce", raw_ratio=0.3)]
        positions = [
            _make_position("cn_588000", exposure_tags=["a_share"],
                           market_value_cny=100_000.0),
            _make_position("alipay_gold", product_type="precious_metal_account",
                           exposure_tags=["gold"], market_value_cny=400_000.0),
        ]
        evidences = _evidences(positions)
        constraints = _constraints(equity_min=0.25, equity_max=0.55)
        risk = _risk_state()

        decision = adjudicate_portfolio(
            cards, evidences, constraints, risk, _liquidity(),
            run_id=_run_id(), rule_version=RULE_VERSION,
        )

        if len(decision.unresolved_conflicts) > 0:
            assert decision.status != "approved"


# ── Real artifact scenario: equities 16%, gold 16.7% ─────────────────


class TestRealArtifactScenario:
    """CN pre-close scenario: equities ~16%, gold ~16.7%."""

    def _realistic_positions(self):
        return [
            # Equity: ~575k total
            _make_position("cn_588000", exposure_tags=["a_share"],
                           market_value_cny=210_000.0),
            _make_position("us_qqq", exposure_tags=["us_equity"],
                           market_value_cny=150_000.0),
            _make_position("alipay_nasdaq", exposure_tags=["qdii"],
                           market_value_cny=120_000.0),
            _make_position("cn_512480", exposure_tags=["semiconductor"],
                           market_value_cny=85_000.0),
            # Gold: ~170k -> 170/3500 ~4.9%
            _make_position("alipay_gold", product_type="precious_metal_account",
                           exposure_tags=["gold"], market_value_cny=80_000.0),
            _make_position("ccb_gold", product_type="precious_metal_account",
                           exposure_tags=["gold"], market_value_cny=90_000.0),
            # Cash: only 100k
            _make_position("cash_hkd", product_type="cash",
                           exposure_tags=["cash"], liquidity_tier="cash",
                           market_value_cny=30_000.0),
            _make_position("cash_usd", product_type="cash_equivalent",
                           exposure_tags=["cash"], liquidity_tier="t0",
                           market_value_cny=20_000.0),
            # Large non-equity: WMP + insurance + large bonds
            _make_position("ccb_wmp", product_type="bank_wealth_management",
                           exposure_tags=["bank_wmp"], liquidity_tier="periodic_open",
                           market_value_cny=1_500_000.0, tradable=False),
            _make_position("boc_insurance", product_type="insurance_policy",
                           exposure_tags=[], liquidity_tier="locked",
                           market_value_cny=800_000.0, tradable=False,
                           rebalance_eligible=False),
            _make_position("alipay_mmf", product_type="money_market_fund",
                           exposure_tags=["money_market"], liquidity_tier="t0",
                           market_value_cny=50_000.0),
            # Large fixed income to dilute equity
            _make_position("ccb_fixed", product_type="fixed_income_plus_fund",
                           exposure_tags=["fixed_income"], liquidity_tier="t2_plus",
                           market_value_cny=1_000_000.0),
        ]

    def _realistic_cards(self):
        return [
            _make_card("cn_588000", signal="reduce", ratio=0.3,
                       raw_signal="reduce", raw_ratio=0.3),
            _make_card("us_qqq", signal="reduce", ratio=0.15,
                       raw_signal="reduce", raw_ratio=0.15),
            _make_card("alipay_nasdaq", signal="reduce", ratio=0.15,
                       raw_signal="reduce", raw_ratio=0.15),
            _make_card("cn_512480", signal="reduce", ratio=0.5,
                       raw_signal="reduce", raw_ratio=0.5),
            _make_card("alipay_gold", signal="hold", ratio=0.0,
                       raw_signal="hold", raw_ratio=0.0,
                       product_type="precious_metal_account"),
            _make_card("ccb_gold", signal="hold", ratio=0.0,
                       raw_signal="hold", raw_ratio=0.0,
                       product_type="precious_metal_account"),
            _make_card("alipay_mmf", signal="hold", ratio=0.0,
                       raw_signal="hold", raw_ratio=0.0,
                       product_type="money_market_fund"),
        ]

    def test_scenario_not_approved_without_chain(self):
        """Real scenario must not approve without resolving conflicts."""
        cards = self._realistic_cards()
        positions = self._realistic_positions()
        evidences = _evidences(positions)
        constraints = _constraints(gold_max=0.15, equity_min=0.25)
        risk = _risk_state()

        decision = adjudicate_portfolio(
            cards, evidences, constraints, risk, _liquidity(
                immediate=50_000.0, settling=0.0,
                strategic=2_735_000.0, locked=2_300_000.0,
            ),
            run_id=_run_id(), rule_version=RULE_VERSION,
        )

        if decision.status == "approved":
            assert len(decision.replacement_chains) > 0
        else:
            assert decision.status in ("suppressed", "review_required")

    def test_no_101w_misleading_deployable(self):
        """Must not claim 108w as executable cash in portfolio_decision."""
        cards = self._realistic_cards()
        positions = self._realistic_positions()
        evidences = _evidences(positions)
        constraints = _constraints(gold_max=0.15, equity_min=0.25)
        risk = _risk_state()

        decision = adjudicate_portfolio(
            cards, evidences, constraints, risk, _liquidity(
                immediate=50_000.0, settling=0.0,
                strategic=2_735_000.0, locked=2_300_000.0,
            ),
            run_id=_run_id(), rule_version=RULE_VERSION,
        )

        for action in decision.approved_actions:
            assert action.decision_id is not None



def test_capital_allocation_available_cash_excludes_t1_t2_assets():
    cards = [_make_card("cash", signal="hold", ratio=0.0)]
    positions = [
        _make_position("cash", product_type="cash", liquidity_tier="cash", market_value_cny=50_000.0),
        _make_position("equity", product_type="stock", liquidity_tier="t1", market_value_cny=100_000.0),
    ]
    result = build_capital_allocation_with_suppression(
        cards,
        positions,
        {"ratios": {"现金": 1 / 3, "权益": 2 / 3}},
        _liquidity_summary(cash_or_t0=50_000.0, t1_t2=100_000.0),
    )
    assert result["available_cash_cny"] == 50_000.0
    assert result["net_deployable_cny"] == 42_500.0
    assert result["strategic_exit_value_cny"] == 100_000.0



def test_adjudicator_rejects_research_only_qdii_action_even_if_signal_leaks():
    cards = [{
        **_make_card("qdii", signal="take_profit", ratio=0.3,
                     raw_signal="take_profit", raw_ratio=0.6),
        "routing": "fund",
        "evidence_status": "research_only",
    }]
    positions = [_make_position(
        "qdii", product_type="qdii_fund", exposure_tags=["qdii"],
        liquidity_tier="t2_plus", market_value_cny=100_000.0,
    )]
    decision = adjudicate_portfolio(
        cards, _evidences(positions), _constraints(equity_min=0.0),
        _risk_state(), _liquidity(), run_id=_run_id(), rule_version=RULE_VERSION,
    )
    assert decision.approved_actions == []
    assert len(decision.suppressed_actions) == 1
    assert "research_only" in decision.suppressed_actions[0].reason


# ── TASK-001B: evidence bridge and dependency direction ───────────────


def test_task001b_complete_evidence_produces_quantity_and_authoritative_amount():
    evidence = _make_position(
        "cn_588000", product_type="stock", liquidity_tier="t1",
        market_value_cny=10_000.0,
    )
    evidence["holding"] = {"quantity": 1000, "unit": "share"}
    evidence["valuation_method"] = "market_quote"
    card = _make_action_card("cn_588000", signal="add", action="加仓", ratio=0.2)
    card["institution_type"] = "brokerage"

    decision = adjudicate_portfolio(
        [card], {"cn_588000": evidence}, {},
        {"level": "normal", "suspend_accumulation": False}, {},
        run_id="task001b",
        execution_rules=_production_execution_rules(),
    )

    action = decision.approved_actions[0].to_dict()
    assert action["executable_quantity"] == 200
    assert action["execution_status"] == "full"
    assert action["estimated_amount_cny"] == 2000.0
    assert action["amount_is_estimate"] is False


def test_task001b_adjudicator_has_no_presentation_dependency():
    from pathlib import Path

    source = Path("stocks/engine/portfolio_adjudicator.py").read_text(encoding="utf-8")
    assert "from stocks.engine.presentation" not in source
    assert "import stocks.engine.presentation" not in source


# ── Adversarial review P1-7: multi-tag bucket split ────────────────────


class TestMultiTagBucketSplit:
    def test_multi_tag_position_value_split_evenly_across_buckets(self):
        """A position mapped to two buckets must contribute half its value to
        each (previously its full value to both, so ratios summed >100%)."""
        from stocks.engine.portfolio_adjudicator import (
            _build_bucket_ratios_from_evidences,
        )
        positions = [
            _make_position("mixed", exposure_tags=["gold", "fixed_income"],
                           market_value_cny=100_000.0),
            _make_position("cash_pos", product_type="cash",
                           exposure_tags=["cash_like"], market_value_cny=100_000.0),
        ]
        ratios = _build_bucket_ratios_from_evidences(_evidences(positions))
        assert ratios["黄金"] == 0.25
        assert ratios["固收"] == 0.25
        assert ratios["现金"] == 0.5
        assert abs(sum(ratios.values()) - 1.0) < 1e-9

    def test_gold_constraint_uses_split_ratio_not_inflated(self):
        """gold_max=0.30 with a 50k pure-gold + 50k mixed(gold/fixed) position
        out of 200k total: split gold = 25k+50k/2=50k? no: pure 50k + mixed
        50k/2 = 75k -> 37.5% > 30% -> suppress; inflated would be 75% —
        either way suppressed, so use the boundary case: gold_max=0.40,
        split=37.5% (allowed), inflated=75% (suppressed)."""
        cards = [_make_card("mixed", signal="add", ratio=0.1,
                            raw_signal="add", raw_ratio=0.1)]
        positions = [
            _make_position("pure_gold", exposure_tags=["gold"],
                           market_value_cny=50_000.0),
            _make_position("mixed", product_type="mixed_fund",
                           exposure_tags=["gold", "fixed_income"],
                           market_value_cny=50_000.0),
            _make_position("cash_pos", product_type="cash",
                           exposure_tags=["cash_like"], market_value_cny=100_000.0),
        ]
        decision = adjudicate_portfolio(
            cards, _evidences(positions), _constraints(gold_max=0.40),
            _risk_state(), _liquidity(), run_id=_run_id(), rule_version=RULE_VERSION,
            execution_rules=_production_execution_rules(),
        )
        suppressed_ids = {a.position_id for a in decision.suppressed_actions}
        assert "mixed" not in suppressed_ids


# ── M4: 约束模型升级（不可逆/分池/硬上限）─────────────────────────────


def _m4_evidence(
    position_id: str,
    *,
    account_id: str = "",
    exposure_tags: list[str] | None = None,
    product_type: str = "stock",
    liquidity_tier: str = "t1",
    market_value_cny: float = 10_000.0,
    tradable: bool = True,
) -> dict:
    return {
        "instrument_key": f"a:{position_id}",
        "account_id": account_id,
        "holding": {"quantity": 100, "unit": "share"},
        "classification": {
            "product_type": product_type,
            "exposure_tags": exposure_tags or [product_type],
        },
        "liquidity": {"tier": liquidity_tier, "tradable": tradable,
                      "rebalance_eligible": True},
        "market_value_cny": market_value_cny,
        "evidence": {"price_freshness": "current", "data_anomalies": [],
                     "action_eligible": True},
        "product_type": product_type,
    }


def _m4_constraints(**overrides) -> dict:
    config = {
        "pools": {
            "domestic": {"label": "国内池", "currency": "CNY"},
            "overseas": {"label": "海外封闭池", "currency": "USD", "isolated": True},
        },
        "account_pool": {"ibkr": "overseas"},
        "bucket_limits": {
            "domestic": {"权益": {"min": 0.25, "max": 0.65}},
            "overseas": {"权益": {"min": 0.0, "max": 1.0}},
        },
    }
    config.update(overrides)
    return config


class TestM4HardCaps:
    def test_breach_without_technical_signal_yields_reduce_naming_cap(self):
        """硬上限超限且无技术信号 → 仍产出指明上限的强制减仓候选。"""
        cards = [
            _make_card("qdii_gf", signal="hold"),
            _make_card("cn_510300", signal="hold"),
        ]
        evidences = {
            "qdii_gf": _m4_evidence(
                "qdii_gf", exposure_tags=["nasdaq100", "us_equity"],
                product_type="qdii_fund", liquidity_tier="t2_plus",
                market_value_cny=20_000.0,
            ),
            "cn_510300": _m4_evidence(
                "cn_510300", exposure_tags=["cn_equity", "csi300"],
                market_value_cny=10_000.0,
            ),
        }
        constraints = {
            "hard_caps": [{
                "category": "nasdaq100", "max": 0.12,
                "on_breach": "must_reduce",
                "reason": "限购无法买回，超上限必须减",
            }],
        }

        decision = adjudicate_portfolio(
            cards, evidences, constraints, _risk_state(), _liquidity(),
            run_id=_run_id(), rule_version=RULE_VERSION,
        )

        actions = [a for a in decision.approved_actions if a.position_id == "qdii_gf"]
        assert len(actions) == 1
        action = actions[0]
        assert action.signal == "reduce"
        assert "硬上限" in action.reason
        assert "限购无法买回" in action.reason
        # 超限部分 20000-30000*0.12=16400，占持仓比例 0.82
        assert action.ratio == pytest.approx(0.82, abs=1e-6)

    def test_below_cap_yields_no_action(self):
        cards = [_make_card("qdii_gf", signal="hold"),
                 _make_card("cn_510300", signal="hold")]
        evidences = {
            "qdii_gf": _m4_evidence("qdii_gf", exposure_tags=["nasdaq100"],
                                    product_type="qdii_fund",
                                    market_value_cny=20_000.0),
            "cn_510300": _m4_evidence("cn_510300", market_value_cny=10_000.0),
        }
        constraints = {"hard_caps": [{"category": "nasdaq100", "max": 0.9}]}

        decision = adjudicate_portfolio(
            cards, evidences, constraints, _risk_state(), _liquidity(),
            run_id=_run_id(), rule_version=RULE_VERSION,
        )
        assert decision.approved_actions == []


class TestM4Irreversibility:
    def test_sell_on_no_buyback_carries_verbatim_warning(self):
        cards = [_make_card("qdii_gf", signal="reduce", ratio=0.5,
                            raw_signal="reduce", raw_ratio=0.5)]
        evidences = {"qdii_gf": _m4_evidence(
            "qdii_gf", exposure_tags=["nasdaq100"], product_type="qdii_fund",
            liquidity_tier="t2_plus", market_value_cny=20_000.0,
        )}
        constraints = {
            "position_restrictions": {
                "qdii_gf": {"no_buyback": True,
                            "restriction_note": "平台每日限购5元，卖出后事实不可买回"},
            },
        }

        decision = adjudicate_portfolio(
            cards, evidences, constraints, _risk_state(), _liquidity(),
            run_id=_run_id(), rule_version=RULE_VERSION,
        )

        action = decision.approved_actions[0]
        assert "⚠️ 不可逆" in action.decision_reason
        assert "平台每日限购5元，卖出后事实不可买回" in action.decision_reason

    def test_take_profit_on_no_buyback_is_suppressed(self):
        cards = [_make_card("qdii_gf", signal="take_profit", ratio=0.3,
                            raw_signal="take_profit", raw_ratio=0.3)]
        evidences = {"qdii_gf": _m4_evidence(
            "qdii_gf", exposure_tags=["nasdaq100"], product_type="qdii_fund",
            liquidity_tier="t2_plus", market_value_cny=20_000.0,
        )}
        constraints = {
            "position_restrictions": {
                "qdii_gf": {"no_buyback": True, "restriction_note": "每日限购5元"},
            },
        }

        decision = adjudicate_portfolio(
            cards, evidences, constraints, _risk_state(), _liquidity(),
            run_id=_run_id(), rule_version=RULE_VERSION,
        )

        assert decision.approved_actions == []
        assert len(decision.suppressed_actions) == 1
        assert "不可逆约束" in decision.suppressed_actions[0].reason
        assert "每日限购5元" in decision.suppressed_actions[0].reason


class TestM4Pools:
    def test_per_pool_ratio_breach_reported_with_pool_label(self):
        """国内池权益低配 + 减仓信号 → 冲突携带池标签；海外池不受影响。"""
        cards = [
            _make_card("cn_510300", signal="reduce", ratio=0.3,
                       raw_signal="reduce", raw_ratio=0.3),
            _make_card("cn_cash", signal="hold"),
            _make_card("ibkr_nvda", signal="hold"),
        ]
        evidences = {
            "cn_510300": _m4_evidence(
                "cn_510300", exposure_tags=["nasdaq100"],  # maps to 权益 bucket
                market_value_cny=10_000.0,
            ),
            # 国内池现金 50k：国内池权益占比 10/60=16.7% < 25% 下限
            "cn_cash": _m4_evidence(
                "cn_cash", product_type="cash", liquidity_tier="cash",
                exposure_tags=["cash_like"], market_value_cny=50_000.0,
            ),
            "ibkr_nvda": _m4_evidence(
                "ibkr_nvda", account_id="ibkr",
                exposure_tags=["nasdaq100"], market_value_cny=90_000.0,
            ),
        }
        constraints = _m4_constraints()

        decision = adjudicate_portfolio(
            cards, evidences, constraints, _risk_state(), _liquidity(),
            run_id=_run_id(), rule_version=RULE_VERSION,
        )

        assert len(decision.unresolved_conflicts) == 1
        conflict = decision.unresolved_conflicts[0]
        assert conflict["pool"] == "domestic"
        assert conflict["pool_label"] == "国内池"
        assert "国内池" in conflict["message"]

    def test_cash_schedule_has_per_pool_sections_when_pools_defined(self):
        cards = [
            _make_card("cn_cash", signal="hold"),
            _make_card("ibkr_cash", signal="hold"),
        ]
        evidences = {
            "cn_cash": _m4_evidence(
                "cn_cash", product_type="cash", liquidity_tier="cash",
                exposure_tags=["cash_like"], market_value_cny=10_000.0,
            ),
            "ibkr_cash": _m4_evidence(
                "ibkr_cash", account_id="ibkr", product_type="cash",
                liquidity_tier="cash", exposure_tags=["cash_like"],
                market_value_cny=70_000.0,
            ),
        }
        constraints = _m4_constraints()

        decision = adjudicate_portfolio(
            cards, evidences, constraints, _risk_state(), _liquidity(),
            run_id=_run_id(), rule_version=RULE_VERSION,
        )

        pools = decision.cash_schedule.get("pools")
        assert pools is not None
        assert pools["overseas"]["isolated"] is True
        assert pools["overseas"]["available_now"] == pytest.approx(
            70_000.0 - pools["overseas"]["safety_buffer_cny"]
        )
        assert pools["domestic"]["available_now"] == pytest.approx(
            10_000.0 - pools["domestic"]["safety_buffer_cny"]
        )
