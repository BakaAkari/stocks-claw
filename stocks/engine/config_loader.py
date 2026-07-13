"""配置加载器 — 从 engine.yaml 读取配置，与环境变量和传参合并

优先级：传参 > 环境变量 > YAML 文件 > 代码默认值
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError:
    yaml = None

from stocks.errors import ConfigError

# 默认配置（代码硬编码的默认值）
DEFAULT_ENGINE_CONFIG = {
    "paths": {
        "config_dir": None,  # 使用 StocksEngine 的默认
        "data_dir": None,
        "local_data_dir": None,
        "secret_dir": None,
        "secret_env_file": None,  # 自定义 .env 文件路径（用于 LLM API key 加载）
    },
    "providers": {
        "tencent_a": {"enabled": True},
        "eastmoney_a": {"enabled": True},
        "finnhub": {"enabled": True},
        "binance": {"enabled": True},
        "fallback": {
            "a": ["eastmoney_a", "tencent_a"],
            "us": [],
            "crypto": ["binance"],
        },
    },
    "fetcher": {
        "max_retries": 1,
        "retry_delay": 1.0,
    },
    "cache": {
        "enabled": True,
        "history_ttl": 7776000,  # 90 天
        "history_dir": None,  # 默认写入 .local/history，避免污染源码目录
        "max_snapshots": 30,
        "save_to_file": True,
    },
    "calendar": {
        "enabled": True,
        "lookahead_days": 14,
        "earnings": {"enabled": True},
    },
    "filings": {
        "enabled": True,
        "sec": {"enabled": True},
        "cninfo": {"enabled": True},
    },
    "news": {"watchlist_templates_enabled": True},
    "llm": {
        "analysis_enabled": False,
        "analysis_model": "kimi-k2.6",
        "api_key_env": "OPENAI_API_KEY",
        "base_url_env": "OPENAI_BASE_URL",
        "fallback_base_url": "http://100.121.167.1:8317/v1",
    },
    "risk_warning": {
        # VIX 阈值
        "vix_hedge": 35,
        "vix_reduce": 25,
        "vix_watch": 20,
        # 情报集群触发
        "critical_cluster_trigger": 1,
        "negative_cluster_trigger": 3,
        # 地缘政治
        "geopolitical_action": "hedge",
        # 持仓回撤阈值（%）
        "drawdown_hedge_pct": 12,
        "drawdown_reduce_pct": 8,
        # 现金目标比例
        "cash_target_hedge": 0.15,
        "cash_target_reduce": 0.10,
        # 各风险等级推荐操作
        "hedge_actions": ["暂停全部加仓", "评估对冲工具", "检查止损线"],
        "reduce_actions": ["暂停权益加仓", "关注防御板块", "检查高beta暴露"],
        "watch_actions": ["关注风险指标", "不新增高beta仓位"],
    },
    "quant_action": {
        "stop_loss_pct": -12.0,
        "mid_stop_pct": -10.0,
        "mid_stop_ratio": 0.3,
        "warning_loss_pct": -8.0,
        "take_profit_levels": [[10.0, 0.25], [20.0, 0.25], [30.0, 0.50]],
        "profit_pullback_pct": -2.0,
        "profit_pullback_min_pnl": 3.0,
        "trend_ma20_break_cutoff": 0.995,
        "trend_break_ladder": [[0.995, 0.25], [0.980, 0.50], [0.950, 0.75], [0.850, 1.0]],
        "default_position_limit_pct": 5.0,
        "trend_confirmed_limit_pct": 10.0,
        "left_add_max_rsi": 65.0,
        "left_add_min_rsi": 40.0,
    },
    "logging": {
        "level": "INFO",
        "desensitize": True,  # 脱敏日志中的金额和 API Key
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并两个字典，override 覆盖 base"""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_engine_config(
    config_path: Optional[Path] = None,
    env_prefix: str = "STOCKS_",
) -> dict:
    """加载 engine.yaml 配置，并与环境变量合并

    Args:
        config_path: engine.yaml 文件路径，默认搜索 stocks/config/engine.yaml
        env_prefix: 环境变量前缀，如 STOCKS_FETCHER_MAX_RETRIES=3

    Returns:
        合并后的配置字典

    Raises:
        ConfigError: YAML 文件格式错误或解析失败
    """
    # 1. 从代码默认值开始
    config = _deep_merge({}, DEFAULT_ENGINE_CONFIG)

    # 2. 从 YAML 文件加载（如果存在）
    yaml_path = config_path
    if yaml_path is None:
        # 搜索默认路径：项目根目录的 stocks/config/engine.yaml
        candidates = [
            Path(__file__).resolve().parents[1] / "config" / "engine.yaml",
            Path(__file__).resolve().parents[2] / "config" / "engine.yaml",
        ]
        for candidate in candidates:
            if candidate.exists():
                yaml_path = candidate
                break

    if yaml_path and yaml_path.exists():
        if yaml is None:
            raise ConfigError(
                "需要 pyyaml 才能解析 engine.yaml，请安装: pip install pyyaml",
                source="config_loader",
            )
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                yaml_config = yaml.safe_load(f)
            if yaml_config and isinstance(yaml_config, dict):
                config = _deep_merge(config, yaml_config)
        except yaml.YAMLError as e:
            raise ConfigError(
                f"engine.yaml 解析错误: {e}",
                source="config_loader",
                detail=str(e),
            )
        except OSError as e:
            raise ConfigError(
                f"无法读取 engine.yaml: {e}",
                source="config_loader",
                detail=str(e),
            )

    # 3. 从环境变量加载（覆盖 YAML）
    # 格式：STOCKS_FETCHER__MAX_RETRIES=3 → config["fetcher"]["max_retries"] = 3
    # 使用 __ 作为层级分隔符，_ 保留为键名的一部分
    for key, value in os.environ.items():
        if not key.startswith(env_prefix):
            continue
        # STOCKS_FETCHER__MAX_RETRIES → fetcher.max_retries
        path = key[len(env_prefix):].lower().split("__")
        _set_nested(config, path, _parse_env_value(value))

    return config


def _set_nested(d: dict, path: list[str], value: Any) -> None:
    """在嵌套字典中设置值，如 path=["fetcher", "max_retries"], value=3"""
    for key in path[:-1]:
        d = d.setdefault(key, {})
    d[path[-1]] = value


def _parse_env_value(value: str) -> Any:
    """解析环境变量值为合适类型"""
    value = value.strip()
    # 布尔值
    if value.lower() in ("true", "1", "yes"):
        return True
    if value.lower() in ("false", "0", "no"):
        return False
    # 整数
    try:
        return int(value)
    except ValueError:
        pass
    # 浮点数
    try:
        return float(value)
    except ValueError:
        pass
    # 字符串（默认）
    return value
