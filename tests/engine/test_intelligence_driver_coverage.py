"""Regression guard for active-position intelligence driver coverage."""

from datetime import datetime, timezone

from stocks.engine.intelligence_analyzer import LLMIntelligenceAnalyzer
from stocks.engine.news_intelligence_store import IntelligenceSignal
from stocks.engine.quant_action import QuantReview, _build_drivers

# Action-card coverage intentionally excludes the other padding targets:
# - us:QQQ is an exposure proxy, not a separate portfolio position.
# - a:511880 is cash-like and routed as skip.
# - alipay_info is non-trading information-only.
EXCLUDED_NON_ACTION_TARGETS = {"us:QQQ", "a:511880", "alipay_info"}

ACTIVE_POSITIONS = [
    ("us:NEM", ["gold", "mining"]),
    ("a:518880", ["gold"]),
    ("ccb_gold", ["gold"]),
    ("us:NVDA", ["tech", "ai"]),
    ("us:XLE", ["energy", "oil_gas"]),
    ("us:ITA", ["defense", "aerospace"]),
    ("a:510300", ["a_share", "broad_index"]),
    ("a:512890", ["a_share", "dividend_low_vol"]),
    ("a:588000", ["a_share", "star_board"]),
    ("a:512480", ["a_share", "semiconductor"]),
    ("a:561560", ["a_share", "utilities"]),
    ("alipay_gf_nasdaq", ["qdii", "nasdaq100"]),
    ("alipay_dc_nasdaq", ["qdii", "nasdaq100"]),
    ("us:SGOV", ["fixed_income", "short_treasury"]),
    ("a:159110", ["fixed_income", "cash_like"]),
]


def test_all_active_positions_receive_an_intelligence_driver() -> None:
    direct_signals = [
        IntelligenceSignal(
            symbol="NVDA",
            name="NVIDIA",
            direction="buy",
            horizon="short_term",
            rationale="AI demand remains strong",
            falsification="Demand weakens",
            risk_source="llm_analysis",
            confidence=0.8,
            urgency="medium",
            generated_at=datetime.now(timezone.utc),
        )
    ]
    padded = LLMIntelligenceAnalyzer()._pad_category_signals(direct_signals, [])
    padded_targets = {
        signal.symbol for signal in padded if signal.symbol != "NVDA"
    } | {"us:NVDA"}
    active_targets = {instrument_key for instrument_key, _ in ACTIVE_POSITIONS}
    assert padded_targets == active_targets | EXCLUDED_NON_ACTION_TARGETS

    signals = {signal.symbol: signal.to_dict() for signal in padded}
    tech = QuantReview(
        position_id="test",
        signal="hold",
        action="持有",
        ratio=0.0,
        facts=["无技术动作"],
        stop_price=None,
        target_prices=[],
        position_limit_pct=10.0,
        current_weight_pct=1.0,
        risk_to_stop_pct=None,
        risk_amount_cny=None,
    )

    covered = []
    for instrument_key, exposure_tags in ACTIVE_POSITIONS:
        drivers = _build_drivers(
            tech=tech,
            signal="hold",
            action="持有",
            votes=[],
            intelligence_signals=signals,
            position={
                "instrument_key": instrument_key,
                "classification": {"exposure_tags": exposure_tags},
            },
        )
        intelligence = next(driver for driver in drivers if driver["source"] == "intelligence")
        if intelligence["signal"] != "unavailable":
            covered.append(instrument_key)

    assert covered == [instrument_key for instrument_key, _ in ACTIVE_POSITIONS]
