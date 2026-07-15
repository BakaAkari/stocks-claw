"""Tests for Task 4: Action Card Immutability and CashSchedule.

Strict TDD — these tests must fail RED before implementation.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone

import pytest

from stocks.engine.portfolio_adjudicator import (
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
) -> dict:
    """Helper to build minimal position_valuation dicts."""
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
            "exposure_tags": [product_type],
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

    def test_approved_sale_goes_to_settling_cash(self, sample_portfolio, approved_sales_fixture):
        """Approved sales with settlement appear in settling_cash."""
        positions, total_value = sample_portfolio
        schedule = build_cash_schedule(positions, approved_sales_fixture, total_value)

        assert schedule.get("settling_cash_cny", 0) > 0
        assert "cn_588000" in schedule.get("settling_cash_position_ids", [])

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
                    "safety_buffer_cny"):
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
