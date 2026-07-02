"""ConfigLoader 测试 — 覆盖默认值、YAML 加载、环境变量覆盖、深度合并"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from stocks.engine.config_loader import (
    _deep_merge,
    _parse_env_value,
    _set_nested,
    load_engine_config,
)
from stocks.errors import ConfigError

# ------------------------------------------------------------------
# 深度合并测试
# ------------------------------------------------------------------

class TestDeepMerge:
    """_deep_merge 函数测试"""

    def test_simple_override(self):
        """简单值覆盖"""
        base = {"a": 1, "b": 2}
        override = {"b": 3}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 3}

    def test_nested_merge(self):
        """嵌套字典合并"""
        base = {"fetcher": {"max_retries": 1, "retry_delay": 1.0}}
        override = {"fetcher": {"max_retries": 3}}
        result = _deep_merge(base, override)
        assert result["fetcher"]["max_retries"] == 3
        assert result["fetcher"]["retry_delay"] == 1.0

    def test_add_new_key(self):
        """新增键"""
        base = {"a": 1}
        override = {"b": 2}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 2}

    def test_does_not_mutate_base(self):
        """不修改原始字典"""
        base = {"a": 1, "nested": {"x": 10}}
        override = {"nested": {"y": 20}}
        result = _deep_merge(base, override)
        assert base == {"a": 1, "nested": {"x": 10}}
        assert result == {"a": 1, "nested": {"x": 10, "y": 20}}


# ------------------------------------------------------------------
# 环境变量解析测试
# ------------------------------------------------------------------

class TestParseEnvValue:
    """_parse_env_value 函数测试"""

    def test_true_values(self):
        for v in ["true", "True", "TRUE", "1", "yes", "YES"]:
            assert _parse_env_value(v) is True

    def test_false_values(self):
        for v in ["false", "False", "FALSE", "0", "no", "NO"]:
            assert _parse_env_value(v) is False

    def test_int(self):
        assert _parse_env_value("42") == 42
        assert _parse_env_value("-3") == -3

    def test_float(self):
        assert _parse_env_value("3.14") == 3.14
        assert _parse_env_value("-0.5") == -0.5

    def test_string(self):
        assert _parse_env_value("hello") == "hello"
        assert _parse_env_value("3.14.15") == "3.14.15"


# ------------------------------------------------------------------
# 嵌套设置测试
# ------------------------------------------------------------------

class TestSetNested:
    """_set_nested 函数测试"""

    def test_single_level(self):
        d = {}
        _set_nested(d, ["key"], "value")
        assert d == {"key": "value"}

    def test_two_levels(self):
        d = {}
        _set_nested(d, ["fetcher", "max_retries"], 3)
        assert d == {"fetcher": {"max_retries": 3}}

    def test_existing_path(self):
        d = {"fetcher": {"retry_delay": 1.0}}
        _set_nested(d, ["fetcher", "max_retries"], 3)
        assert d == {"fetcher": {"retry_delay": 1.0, "max_retries": 3}}


# ------------------------------------------------------------------
# 配置加载测试
# ------------------------------------------------------------------

class TestLoadEngineConfig:
    """load_engine_config 函数测试"""

    def test_default_config(self):
        """无 YAML 文件时返回代码默认值"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = load_engine_config(config_path=Path(tmpdir) / "nonexistent.yaml")

        assert config["fetcher"]["max_retries"] == 1
        assert config["fetcher"]["retry_delay"] == 1.0
        assert config["providers"]["tencent_a"]["enabled"] is True
        assert config["cache"]["quote_ttl"] == 1800
        assert config["cache"]["history_dir"] is None
        assert config["llm"]["enhancer_enabled"] is True
        assert config["llm"]["analysis_enabled"] is False
        assert config["llm"]["validate_models"] is False

    def test_yaml_override(self):
        """YAML 文件覆盖默认值"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "engine.yaml"
            yaml_path.write_text(
                "fetcher:\n  max_retries: 5\n  retry_delay: 2.0\n",
                encoding="utf-8",
            )
            config = load_engine_config(config_path=yaml_path)

        assert config["fetcher"]["max_retries"] == 5
        assert config["fetcher"]["retry_delay"] == 2.0
        # 未覆盖的值保持默认
        assert config["providers"]["tencent_a"]["enabled"] is True

    def test_yaml_nested_merge(self):
        """YAML 只覆盖部分嵌套键"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "engine.yaml"
            yaml_path.write_text(
                "providers:\n  tencent_a:\n    enabled: false\n",
                encoding="utf-8",
            )
            config = load_engine_config(config_path=yaml_path)

        assert config["providers"]["tencent_a"]["enabled"] is False
        assert config["providers"]["tencent_a"]["timeout"] == 20  # 默认值
        assert config["providers"]["eastmoney_a"]["enabled"] is True  # 未覆盖

    def test_env_override(self, monkeypatch):
        """环境变量覆盖 YAML 和默认值"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "engine.yaml"
            yaml_path.write_text(
                "fetcher:\n  max_retries: 3\n",
                encoding="utf-8",
            )
            monkeypatch.setenv("STOCKS_FETCHER__MAX_RETRIES", "10")
            config = load_engine_config(config_path=yaml_path)

        assert config["fetcher"]["max_retries"] == 10

    def test_env_bool(self, monkeypatch):
        """环境变量布尔值解析"""
        monkeypatch.setenv("STOCKS_PROVIDERS__TENCENT_A__ENABLED", "false")
        config = load_engine_config(config_path=Path("/nonexistent"))
        assert config["providers"]["tencent_a"]["enabled"] is False

    def test_env_nested(self, monkeypatch):
        """环境变量多级嵌套路径"""
        monkeypatch.setenv("STOCKS_CACHE__QUOTE_TTL", "3600")
        config = load_engine_config(config_path=Path("/nonexistent"))
        assert config["cache"]["quote_ttl"] == 3600

    def test_yaml_error(self):
        """YAML 格式错误时抛出 ConfigError"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "engine.yaml"
            yaml_path.write_text("invalid: yaml: [", encoding="utf-8")
            with pytest.raises(ConfigError) as exc_info:
                load_engine_config(config_path=yaml_path)

            assert "engine.yaml" in str(exc_info.value)

    def test_paths_section(self):
        """paths 配置正确加载"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "engine.yaml"
            yaml_path.write_text(
                "paths:\n  config_dir: /custom/config\n  data_dir: /custom/data\n",
                encoding="utf-8",
            )
            config = load_engine_config(config_path=yaml_path)

        assert config["paths"]["config_dir"] == "/custom/config"
        assert config["paths"]["data_dir"] == "/custom/data"
        assert config["paths"]["local_data_dir"] is None  # 默认值

    def test_llm_config(self):
        """LLM 配置正确加载"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "engine.yaml"
            yaml_path.write_text(
                "llm:\n  enhancer_enabled: false\n  analysis_enabled: true\n  enhancer_model: custom-model\n",
                encoding="utf-8",
            )
            config = load_engine_config(config_path=yaml_path)

        assert config["llm"]["enhancer_enabled"] is False
        assert config["llm"]["analysis_enabled"] is True
        assert config["llm"]["enhancer_model"] == "custom-model"
        assert config["llm"]["analysis_model"] == "kimi-k2.6"  # 默认值
        assert config["llm"]["validate_models"] is False

    def test_logging_config(self):
        """日志配置正确加载"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "engine.yaml"
            yaml_path.write_text(
                "logging:\n  level: DEBUG\n  desensitize: false\n",
                encoding="utf-8",
            )
            config = load_engine_config(config_path=yaml_path)

        assert config["logging"]["level"] == "DEBUG"
        assert config["logging"]["desensitize"] is False


# ------------------------------------------------------------------
# 集成测试：环境变量 + YAML
# ------------------------------------------------------------------

class TestConfigIntegration:
    """配置加载集成测试"""

    def test_priority_order(self, monkeypatch):
        """验证优先级：环境变量 > YAML > 默认值"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "engine.yaml"
            yaml_path.write_text(
                "fetcher:\n  max_retries: 5\ncache:\n  quote_ttl: 3600\n",
                encoding="utf-8",
            )
            # 环境变量覆盖 YAML
            monkeypatch.setenv("STOCKS_FETCHER__MAX_RETRIES", "10")
            # 环境变量覆盖默认值（YAML 中未设置）
            monkeypatch.setenv("STOCKS_CACHE__NEWS_TTL", "7200")
            config = load_engine_config(config_path=yaml_path)

        # 环境变量覆盖 YAML
        assert config["fetcher"]["max_retries"] == 10
        # YAML 覆盖默认值
        assert config["cache"]["quote_ttl"] == 3600
        # 环境变量覆盖默认值
        assert config["cache"]["news_ttl"] == 7200
        # 默认值未改变
        assert config["fetcher"]["retry_delay"] == 1.0
