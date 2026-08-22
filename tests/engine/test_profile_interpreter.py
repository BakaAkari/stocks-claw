"""Test ProfileInterpreter — parameter semantics, migration, and validation."""

from __future__ import annotations

import pytest

from stocks.engine.profile_interpreter import (
    DEFAULT_PARAMS,
    _load_default_params,
    interpreter_system_prompt,
    load_computed,
    merge_with_defaults,
    save_computed,
    validate_computed,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_old_v1(overrides=None):
    """Build a v1 computed_profile dict with old-style keys."""
    params = {
        "stop_loss_pct": -15.0,
        "trend_confirm_days": 3,
        "add_ladder": [0.02, 0.03],
    }
    if overrides:
        params.update(overrides)
    return {
        "schema_version": 1,
        "generated_at": "2026-07-15T00:00:00+00:00",
        "params": params,
        "reasoning": {"trend_confirm_days": "mock"},
        "style_summary": "左侧交易",
    }


def _make_new_v2(overrides=None):
    """Build a v2 computed_profile dict with new-style keys."""
    params = {
        "stop_loss_pct": -15.0,
        "trend_break_extra_deviation_pct": 1.0,
        "ma20_pullback_add_ratios": [0.02, 0.03],
    }
    if overrides:
        params.update(overrides)
    return {
        "schema_version": 2,
        "generated_at": "2026-07-15T00:00:00+00:00",
        "params": params,
        "reasoning": {"trend_break_extra_deviation_pct": "mock"},
        "style_summary": "左侧交易",
    }


# ===================================================================
# RED Step 1: Lock public parameter defaults and names
# Engine behavior is tested in test_quant_action.py against production code.
# ===================================================================


class TestParameterContract:
    def test_default_params_match_engine_yaml(self):
        """2026-08-22 配置化整改后：DEFAULT_PARAMS 的唯一权威是
        engine.yaml quant_action.defaults，此处断言与 yaml 一致而非硬编码值。"""
        from stocks.engine.config_loader import load_engine_config
        yaml_defaults = ((load_engine_config() or {}).get("quant_action") or {})["defaults"]
        for key in ("stop_loss_pct", "trend_break_extra_deviation_pct",
                    "ma20_pullback_add_ratios", "take_profit_levels"):
            assert DEFAULT_PARAMS[key] == yaml_defaults[key], (
                f"DEFAULT_PARAMS[{key}] 与 engine.yaml 不一致: "
                f"{DEFAULT_PARAMS[key]} != {yaml_defaults[key]}"
            )


class TestProfileInterpreterCRUD:

    """Basic round-trip tests."""

    def test_save_and_load_v1_migration(self, tmp_path):
        # Write a v1 file manually (not via save_computed which writes v2)
        v1 = _make_old_v1()
        path = tmp_path / "computed_profile.json"
        path.write_text(__import__("json").dumps(v1, ensure_ascii=False, indent=2))
        loaded = load_computed(path)
        assert loaded is not None
        assert loaded["schema_version"] == 2
        assert loaded["params"]["trend_break_extra_deviation_pct"] == 1.0
        assert loaded["params"]["ma20_pullback_add_ratios"] == [0.02, 0.03]
        assert "trend_confirm_days" not in loaded["params"]
        assert "add_ladder" not in loaded["params"]

    def test_merge_with_defaults_overrides(self):
        merged = merge_with_defaults({"params": {"trend_break_extra_deviation_pct": 1.0}})
        assert merged["trend_break_extra_deviation_pct"] == 1.0
        assert merged["stop_loss_pct"] == _load_default_params()["stop_loss_pct"]

    def test_merge_with_defaults_none(self):
        merged = merge_with_defaults(None)
        assert merged == _load_default_params()

    def test_validate_rejects_unmigrated_old_params(self):
        computed = _make_old_v1()
        errors = validate_computed(computed)
        assert "trend_confirm_days" in str(errors)
        assert "add_ladder" in str(errors)

    def test_validate_rejects_bad_ma20_pullback_add_ratios(self):
        computed = _make_new_v2({"ma20_pullback_add_ratios": "not_a_list"})
        errors = validate_computed(computed)
        assert len(errors) >= 1

    def test_validate_requires_reasoning(self):
        computed = _make_old_v1()
        computed.pop("reasoning", None)
        errors = validate_computed(computed)
        assert "缺少 reasoning" in errors

    def test_default_params_contain_new_keys(self):
        assert "trend_break_extra_deviation_pct" in DEFAULT_PARAMS
        assert "ma20_pullback_add_ratios" in DEFAULT_PARAMS

    def test_prompt_new_trend_name_mentioned(self):
        assert "trend_break_extra_deviation_pct" in interpreter_system_prompt()

    def test_prompt_new_add_name_mentioned(self):
        assert "ma20_pullback_add_ratios" in interpreter_system_prompt()


# ===================================================================
# RED Step 2: New parameter name tests (will fail until GREEN)
# ===================================================================


    def test_save_and_load_v2_roundtrip(self, tmp_path):
        computed = _make_new_v2()
        path = tmp_path / "computed_profile.json"
        save_computed(computed, path)
        assert path.exists()
        loaded = load_computed(path)
        assert loaded is not None
        assert loaded["schema_version"] == 2
        assert loaded["params"]["trend_break_extra_deviation_pct"] == 1.0
        assert loaded["params"]["ma20_pullback_add_ratios"] == [0.02, 0.03]


class TestRenameMigration:
    """Tests for migration from old param names to new ones."""

    def test_old_trend_confirm_days_3_maps_to_1pct_extra(self):
        """trend_confirm_days=3 -> trend_break_extra_deviation_pct=1.0."""
        extra_deviation_pct = (3 - 1) * 0.5
        assert extra_deviation_pct == pytest.approx(1.0)

    def test_extra_deviation_default_0(self):
        """Default trend_break_extra_deviation_pct=0 -> no extra deviation."""
        extra = 0.0
        adjusted_cutoff = 0.995 - extra / 100
        assert adjusted_cutoff == pytest.approx(0.995)

    def test_extra_deviation_1pct_cutoff_985(self):
        """trend_break_extra_deviation_pct=1.0 -> adjusted_cutoff=0.985."""
        extra = 1.0
        adjusted_cutoff = 0.995 - extra / 100
        assert adjusted_cutoff == pytest.approx(0.985)

    def test_extra_deviation_2pct_cutoff_975(self):
        """trend_break_extra_deviation_pct=2.0 -> adjusted_cutoff=0.975."""
        extra = 2.0
        adjusted_cutoff = 0.995 - extra / 100
        assert adjusted_cutoff == pytest.approx(0.975)

    def test_extra_deviation_never_below_910(self):
        """Clamped at 0.910 regardless of high extra_deviation_pct."""
        extra = 50.0
        adjusted_cutoff = max(0.995 - extra / 100, 0.910)
        assert adjusted_cutoff == pytest.approx(0.910)

    def test_ma20_pullback_add_ratios_behavior_equivalent(self):
        """ma20_pullback_add_ratios works identically to add_ladder."""
        ladder = [0.02, 0.03, 0.05]
        ma20 = 100.0
        price = 98.8
        deviation = (ma20 - price) / ma20
        tier = 0
        if deviation > 0.02 and len(ladder) > 2:
            tier = 2
        elif deviation > 0.01 and len(ladder) > 1:
            tier = 1
        assert ladder[tier] == pytest.approx(0.03)


class TestMigrationOnLoad:
    """Tests for the migration logic in load_computed."""

    def test_v1_file_migrates_to_v2_on_load(self):
        """Loading a v1 file with old keys -> migrated to new keys."""
        extra_deviation = (3 - 1) * 0.5
        assert extra_deviation == pytest.approx(1.0)
        params_migrated = {
            "stop_loss_pct": -15.0,
            "trend_break_extra_deviation_pct": 1.0,
            "ma20_pullback_add_ratios": [0.02, 0.03],
        }
        assert "trend_confirm_days" not in params_migrated
        assert "add_ladder" not in params_migrated

    def test_v2_file_loaded_without_change(self):
        """Loading a v2 file -> no migration needed."""
        params_v2 = {
            "stop_loss_pct": -15.0,
            "trend_break_extra_deviation_pct": 1.0,
            "ma20_pullback_add_ratios": [0.02],
        }
        assert "trend_confirm_days" not in params_v2
        assert "add_ladder" not in params_v2
        assert params_v2["trend_break_extra_deviation_pct"] == 1.0

    def test_conflicting_old_and_new_keys_rejected(self):
        """Both old and new keys in the same profile -> error."""
        params_both = {
            "trend_confirm_days": 3,
            "trend_break_extra_deviation_pct": 1.0,
        }
        old_keys = {"trend_confirm_days", "add_ladder"}
        new_keys = {"trend_break_extra_deviation_pct", "ma20_pullback_add_ratios"}
        given_keys = set(params_both.keys())
        has_conflict = bool(given_keys & old_keys and given_keys & new_keys)
        assert has_conflict


class TestEngineConfigBoundary:
    def test_engine_rejects_deprecated_config_keys(self):
        from stocks.engine.quant_action import QuantActionEngine

        with pytest.raises(ValueError, match="trend_confirm_days"):
            QuantActionEngine({}, {"trend_confirm_days": 3})
        with pytest.raises(ValueError, match="add_ladder"):
            QuantActionEngine({}, {"add_ladder": [0.02]})


class TestMigrationEdgeCases:
    def test_non_overlapping_old_and_new_keys_migrate(self, tmp_path):
        data = _make_old_v1()
        data["params"].pop("add_ladder")
        data["params"]["ma20_pullback_add_ratios"] = [0.04]
        path = tmp_path / "computed_profile.json"
        path.write_text(__import__("json").dumps(data))
        loaded = load_computed(path)
        assert loaded["params"]["trend_break_extra_deviation_pct"] == 1.0
        assert loaded["params"]["ma20_pullback_add_ratios"] == [0.04]

    def test_matching_alias_pair_is_rejected_by_loader(self, tmp_path):
        data = _make_old_v1({"trend_break_extra_deviation_pct": 1.0})
        path = tmp_path / "computed_profile.json"
        path.write_text(__import__("json").dumps(data))
        with pytest.raises(ValueError, match="trend_confirm_days"):
            load_computed(path)

    def test_migration_renames_reasoning_keys(self, tmp_path):
        data = _make_old_v1()
        data["reasoning"]["add_ladder"] = "分批"
        path = tmp_path / "computed_profile.json"
        path.write_text(__import__("json").dumps(data))
        loaded = load_computed(path)
        assert "trend_confirm_days" not in loaded["reasoning"]
        assert "add_ladder" not in loaded["reasoning"]
        assert "系统不跟踪连续天数" in (
            loaded["reasoning"]["trend_break_extra_deviation_pct"]
        )
        assert loaded["reasoning"]["ma20_pullback_add_ratios"] == "分批"

    def test_old_reasoning_does_not_overwrite_new_reasoning(self, tmp_path):
        data = _make_new_v2()
        data["reasoning"] = {
            "trend_confirm_days": "旧解释",
            "trend_break_extra_deviation_pct": "保留新解释",
        }
        path = tmp_path / "computed_profile.json"
        path.write_text(__import__("json").dumps(data))
        loaded = load_computed(path)
        assert loaded["reasoning"]["trend_break_extra_deviation_pct"] == "保留新解释"
        assert "trend_confirm_days" not in loaded["reasoning"]

    def test_v2_params_with_stale_reasoning_are_cleaned(self, tmp_path):
        data = _make_new_v2()
        data["reasoning"] = {
            "trend_confirm_days": "需要连续3天确认",
            "add_ladder": "分批",
        }
        path = tmp_path / "computed_profile.json"
        path.write_text(__import__("json").dumps(data))
        loaded = load_computed(path)
        reasoning = loaded["reasoning"]
        assert "trend_confirm_days" not in reasoning
        assert "add_ladder" not in reasoning
        assert "系统不跟踪连续天数" in reasoning["trend_break_extra_deviation_pct"]
        persisted = __import__("json").loads(path.read_text())
        assert persisted["reasoning"] == reasoning

    def test_v2_file_with_old_key_still_migrates(self, tmp_path):
        data = _make_old_v1()
        data["schema_version"] = 2
        path = tmp_path / "computed_profile.json"
        path.write_text(__import__("json").dumps(data))
        loaded = load_computed(path)
        assert "trend_confirm_days" not in loaded["params"]
        assert loaded["params"]["trend_break_extra_deviation_pct"] == 1.0


class TestValidateNewParams:
    """Validation tests for new parameter names."""

    def test_validate_accepts_new_params(self):
        computed = _make_new_v2()
        errors = validate_computed(computed)
        assert errors == []

    def test_validate_rejects_bad_ma20_pullback_add_ratios(self):
        computed = _make_new_v2({"ma20_pullback_add_ratios": "not_a_list"})
        errors = validate_computed(computed)
        assert len(errors) >= 1

    def test_validate_extra_deviation_must_be_non_negative(self):
        """trend_break_extra_deviation_pct must be >= 0."""
        computed = _make_new_v2({"trend_break_extra_deviation_pct": -1.0})
        errors = validate_computed(computed)
        assert "trend_break_extra_deviation_pct" in str(errors)


class TestDEFAULTConsistency:
    """DEFAULT_PARAMS must contain new names, not old ones."""

    def test_default_has_trend_break_extra_deviation_pct(self):
        assert "trend_break_extra_deviation_pct" in DEFAULT_PARAMS

    def test_default_has_ma20_pullback_add_ratios(self):
        assert "ma20_pullback_add_ratios" in DEFAULT_PARAMS

    def test_default_no_longer_has_trend_confirm_days(self):
        assert "trend_confirm_days" not in DEFAULT_PARAMS

    def test_default_no_longer_has_add_ladder(self):
        assert "add_ladder" not in DEFAULT_PARAMS

    def test_new_params_default_values(self):
        """2026-08-22 起权威值在 engine.yaml，不再硬编码断言旧默认。"""
        yaml_vals = _load_default_params()
        assert DEFAULT_PARAMS["trend_break_extra_deviation_pct"] == yaml_vals["trend_break_extra_deviation_pct"]
        assert DEFAULT_PARAMS["ma20_pullback_add_ratios"] == yaml_vals["ma20_pullback_add_ratios"]


class TestPromptNoLongerMentionsOldNames:
    """The interpreter prompt must use new names."""

    def test_prompt_uses_new_trend_name(self):
        assert "trend_break_extra_deviation_pct" in interpreter_system_prompt()

    def test_prompt_no_longer_mentions_old_confirm_days(self):
        assert "trend_confirm_days" not in interpreter_system_prompt()

    def test_prompt_uses_new_add_name(self):
        assert "ma20_pullback_add_ratios" in interpreter_system_prompt()

    def test_prompt_no_longer_mentions_old_add_ladder(self):
        assert "add_ladder" not in interpreter_system_prompt()
