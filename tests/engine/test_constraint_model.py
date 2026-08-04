"""M4 constraint model tests: schema validation + runtime helpers."""
from __future__ import annotations

import pytest

from stocks.engine.constraint_model import (
    ConstraintConfigError,
    ConstraintModel,
    validate_constraints,
)


class TestValidation:
    def test_legacy_buckets_pass_unchanged(self):
        data = {"权益": {"min": 0.25, "max": 0.65}, "黄金": {"min": 0.0, "max": 0.15}}
        out = validate_constraints(data)
        assert out["权益"] == {"min": 0.25, "max": 0.65}
        assert out["pools"] == {}
        assert out["hard_caps"] == []
        assert out["position_restrictions"] == {}

    def test_empty_config_is_valid(self):
        out = validate_constraints({})
        assert out["pools"] == {} and out["hard_caps"] == []

    def test_unknown_top_level_m4_key_fails(self):
        with pytest.raises(ConstraintConfigError, match="unknown keys"):
            validate_constraints({"pools": {"domestic": {"label": "x", "evil": 1}}})

    def test_undefined_pool_reference_fails(self):
        with pytest.raises(ConstraintConfigError, match="undefined pool"):
            validate_constraints({
                "pools": {"domestic": {"label": "默认池"}},
                "account_pool": {"ibkr": "overseas"},
            })

    def test_bucket_limits_undefined_pool_fails(self):
        with pytest.raises(ConstraintConfigError, match="undefined pool"):
            validate_constraints({
                "pools": {"domestic": {"label": "默认池"}},
                "bucket_limits": {"mars": {"权益": {"min": 0.1}}},
            })

    def test_hard_cap_bad_max_fails(self):
        with pytest.raises(ConstraintConfigError, match="max"):
            validate_constraints({
                "hard_caps": [{"category": "nasdaq100", "max": 1.5}],
            })

    def test_hard_cap_unknown_key_fails(self):
        with pytest.raises(ConstraintConfigError, match="unknown keys"):
            validate_constraints({
                "hard_caps": [{"category": "x", "max": 0.1, "severity": "high"}],
            })

    def test_restriction_bad_type_fails(self):
        with pytest.raises(ConstraintConfigError, match="no_buyback must be bool"):
            validate_constraints({
                "position_restrictions": {"p1": {"no_buyback": "yes"}},
            })

    def test_default_pool_must_exist_when_pools_defined(self):
        with pytest.raises(ConstraintConfigError, match="default pool"):
            validate_constraints({"pools": {"overseas": {"label": "x"}}})

    def test_comment_keys_ignored(self):
        out = validate_constraints({"_comment": "doc", "权益": {"min": 0.1, "max": 0.5}})
        assert out["权益"]["min"] == 0.1

    def test_full_m4_config_validates(self):
        out = validate_constraints({
            "权益": {"min": 0.25, "max": 0.65},
            "pools": {
                "domestic": {"label": "国内池", "currency": "CNY"},
                "overseas": {"label": "海外封闭池", "currency": "USD", "isolated": True},
            },
            "account_pool": {"ibkr": "overseas"},
            "bucket_limits": {
                "domestic": {"权益": {"min": 0.25, "max": 0.65}},
                "overseas": {"权益": {"min": 0.0, "max": 1.0}},
            },
            "hard_caps": [
                {"pool": "domestic", "category": "nasdaq100", "max": 0.12,
                 "on_breach": "must_reduce", "reason": "限购无法买回"},
            ],
            "position_restrictions": {
                "qdii_1": {"no_buyback": True, "restriction_note": "每日限购5元"},
            },
        })
        assert out["pools"]["overseas"]["isolated"] is True
        assert out["hard_caps"][0]["max"] == 0.12


def _model(**overrides) -> ConstraintModel:
    config = {
        "pools": {
            "domestic": {"label": "国内池", "currency": "CNY"},
            "overseas": {"label": "海外封闭池", "currency": "USD", "isolated": True},
        },
        "account_pool": {"ibkr": "overseas"},
        "position_pool": {"special_pos": "overseas"},
        "bucket_limits": {"domestic": {"权益": {"min": 0.25}}, "overseas": {"权益": {}}},
        "position_restrictions": {
            "qdii_1": {"no_buyback": True, "restriction_note": "每日限购5元"},
            "a:999999": {"no_buyback": True, "restriction_note": "按键控"},
        },
    }
    config.update(overrides)
    return ConstraintModel.from_config(config)


class TestConstraintModel:
    def test_pool_resolution_position_over_account_over_default(self):
        m = _model()
        assert m.pool_of("special_pos", "alipay") == "overseas"
        assert m.pool_of("ibkr_nvda", "ibkr") == "overseas"
        assert m.pool_of("cn_etf", "cn_broker") == "domestic"

    def test_pool_resolution_without_pools_defaults_domestic(self):
        m = ConstraintModel.from_config({})
        assert m.pool_of("anything", "anyaccount") == "domestic"
        assert m.has_pools is False

    def test_isolated_and_labels(self):
        m = _model()
        assert m.is_isolated("overseas") is True
        assert m.is_isolated("domestic") is False
        assert m.pool_label("overseas") == "海外封闭池"

    def test_bucket_rules_per_pool_with_legacy_fallback(self):
        m = _model()
        assert m.bucket_rules_for("domestic") == {"权益": {"min": 0.25, "max": None}}
        # 未定义 bucket_limits 时回退到 legacy 全局桶
        m2 = _model(**{"权益": {"min": 0.2, "max": 0.6}}, bucket_limits={})
        assert m2.bucket_rules_for("domestic") == {"权益": {"min": 0.2, "max": 0.6}}

    def test_restriction_lookup_by_position_id_and_instrument_key(self):
        m = _model()
        assert m.restriction_for("qdii_1")["no_buyback"] is True
        assert m.restriction_for("some_pos", "a:999999")["restriction_note"] == "按键控"
        assert m.restriction_for("none")["no_buyback"] is False

    def test_category_matching_by_tag_or_bucket(self):
        m = _model()
        assert m.category_matches("nasdaq100", ["nasdaq100", "us_equity"], []) is True
        assert m.category_matches("黄金", ["gold"], ["黄金"]) is True
        assert m.category_matches("nasdaq100", ["csi300"], ["权益"]) is False
