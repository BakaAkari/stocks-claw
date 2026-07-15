"""测试因子规则引擎。"""
from stocks.engine.factor_rules import (
    ConstraintCheckRule,
    DataFreshnessRule,
    FactorVote,
    adjudicate,
    collect_votes,
)

# 使用实际的 tag→bucket 映射
_FULL_CONSTRAINTS = {
    "权益": {"max": 60.0, "min": 20.0},
}
_PORTFOLIO_OVER = {"权益": 65.0}   # 超 60%
_PORTFOLIO_OK = {"权益": 30.0}     # 正常


def test_constraint_check_returns_vote():
    rule = ConstraintCheckRule()
    vote = rule.evaluate({}, current_signal="hold", current_ratio=0.05)
    assert isinstance(vote, FactorVote)
    assert vote.factor_name == "constraint_check"


def test_constraint_over_max_triggers_hold():
    rule = ConstraintCheckRule()
    vote = rule.evaluate(
        {"classification": {"exposure_tags": ["a_share"]}},
        current_signal="add", current_ratio=0.10,
        constraints=_FULL_CONSTRAINTS,
        portfolio_ratios=_PORTFOLIO_OVER,
    )
    assert vote.direction == "hold"
    assert vote.ratio_modifier == 0.0


def test_collect_votes_with_over_limit():
    votes = collect_votes(
        {"classification": {"exposure_tags": ["a_share"]}},
        current_signal="add", current_ratio=0.10,
        constraints=_FULL_CONSTRAINTS,
        portfolio_ratios=_PORTFOLIO_OVER,
    )
    assert len(votes) >= 1
    assert any(v.factor_name == "constraint_check" for v in votes)


def test_collect_votes_normal_ok():
    votes = collect_votes(
        {},
        current_signal="hold", current_ratio=0.05,
        constraints=_FULL_CONSTRAINTS,
        portfolio_ratios=_PORTFOLIO_OK,
    )
    assert isinstance(votes, list)


def test_adjudicate_preserves_signal():
    votes = [FactorVote("test", "hold", 1.0)]
    result = adjudicate("hold", "hold", 0.05, votes)
    assert result["signal"] == "hold"


# ── DataFreshnessRule 逐持仓新鲜度 ──

def test_data_freshness_fresh_no_modifier():
    """fresh 数据不应降权。"""
    rule = DataFreshnessRule()
    vote = rule.evaluate({}, current_signal="add", current_ratio=0.10,
                         data_freshness="fresh")
    assert vote.ratio_modifier == 1.0
    assert vote.facts == []


def test_data_freshness_stale_reduces_ratio():
    """stale 数据对非 hold/wait 信号 ×0.5。"""
    rule = DataFreshnessRule()
    vote = rule.evaluate({}, current_signal="reduce", current_ratio=0.10,
                         data_freshness="stale")
    assert vote.ratio_modifier == 0.5


def test_data_freshness_very_stale_blocks_action():
    """very_stale/unknown 阻断非止损信号。"""
    rule = DataFreshnessRule()
    vote = rule.evaluate({}, current_signal="add", current_ratio=0.10,
                         data_freshness="very_stale")
    assert vote.signal_override == "hold"
    assert vote.ratio_modifier == 0.0


def test_data_freshness_missing_blocks_non_stop_loss():
    """missing 数据应阻断非硬纪律动作（与 very_stale 同处理）。"""
    rule = DataFreshnessRule()
    vote = rule.evaluate({}, current_signal="add", current_ratio=0.10,
                         data_freshness="missing")
    assert vote.signal_override == "hold"
    assert vote.ratio_modifier == 0.0


def test_data_freshness_missing_does_not_block_stop_loss():
    """missing 数据不应阻断止损信号。"""
    rule = DataFreshnessRule()
    vote = rule.evaluate({}, current_signal="stop_loss", current_ratio=1.0,
                         data_freshness="missing")
    assert vote.signal_override == ""
    assert vote.ratio_modifier == 1.0


def test_data_freshness_previous_close_reduces_intraday_actions():
    """previous_close 对盘中精度需要的 action 降权。"""
    rule = DataFreshnessRule()
    # reduce 需要盘中精度 → 降权
    vote = rule.evaluate({}, current_signal="reduce", current_ratio=0.10,
                         data_freshness="previous_close")
    assert vote.ratio_modifier == 0.5
    # hold/wait 不需要盘中精度 → 不降权
    vote = rule.evaluate({}, current_signal="hold", current_ratio=0.0,
                         data_freshness="previous_close")
    assert vote.ratio_modifier == 1.0
