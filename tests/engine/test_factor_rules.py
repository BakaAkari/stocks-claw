"""测试因子规则引擎。"""
from stocks.engine.factor_rules import (
    ConstraintCheckRule,
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
