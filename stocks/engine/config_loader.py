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
        # base_url: 各 Provider 的默认端点，可在 engine.yaml 或环境变量
        # STOCKS_PROVIDER_<NAME>_BASE_URL 覆盖（见 provider_base_url()）。
        "tencent_a": {"enabled": True, "base_url": "https://qt.gtimg.cn"},
        "eastmoney_a": {"enabled": True, "base_url": "https://push2.eastmoney.com"},
        "finnhub": {"enabled": True, "base_url": "https://finnhub.io/api/v1"},
        "binance": {"enabled": True, "base_url": "https://api.binance.com/api/v3"},
        "polygon": {"enabled": True, "base_url": "https://api.polygon.io"},
        # fund_nav 端点即完整接口路径（query 参数另拼）
        "fund_nav": {"enabled": True, "base_url": "https://api.fund.eastmoney.com/f10/lsjz"},
        # rss_news 的 base_url 即默认 feed URL
        "rss_news": {"enabled": True, "base_url": "https://www.chinanews.com.cn/rss/finance.xml"},
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
    # 新闻→市场事件提取器的关键词/情绪词典（market_events.py 的唯一数据源）。
    # 调词即调行为：在此处增删关键词，无需改动引擎代码。
    "market_events": {
        "event_keywords": {
            "monetary_policy": [
                "美联储", "fed", "fomc", "降息", "加息", "利率", "缩表", "扩表", "央行",
                "逆回购", "流动性", "准备金率", "mlf", "lpr",
            ],
            "macro_policy": [
                "政策", "财政", "发改委", "国务院", "刺激", "补贴", "消费券", "地产政策",
                "监管", "证监会", "税收", "关税",
            ],
            "earnings": [
                "财报", "业绩", "利润", "营收", "eps", "guidance", "预告", "亏损", "盈利",
            ],
            "geopolitical": [
                "地缘", "战争", "制裁", "出口管制", "禁令", "关税", "贸易战", "中东", "台海",
            ],
            "industry_theme": [
                "ai", "人工智能", "芯片", "半导体", "算力", "新能源", "军工", "机器人", "医药",
                "银行", "券商", "保险", "消费电子", "云计算", "数据中心",
            ],
            "market_movement": [
                "大涨", "大跌", "反弹", "跳水", "收涨", "收跌", "创新高", "新低", "暴跌", "暴涨",
                "纳指", "标普", "道指", "沪指", "创业板", "科创板",
            ],
        },
        "theme_keywords": {
            "AI": ["ai", "人工智能", "大模型", "算力", "gpu", "英伟达", "nvidia"],
            "半导体": ["芯片", "半导体", "晶圆", "光刻", "存储", "英伟达", "nvidia", "高通", "qcom"],
            "军工": ["军工", "国防", "航天", "导弹", "无人机"],
            "金融": ["银行", "券商", "保险", "金融", "平安银行", "利差"],
            "新能源": ["新能源", "光伏", "锂电", "储能", "电动车", "tesla", "特斯拉"],
            "医药": ["医药", "创新药", "医疗", "药品", "fda"],
            "消费": ["消费", "零售", "白酒", "旅游", "餐饮"],
            "地产": ["地产", "房地产", "房贷", "按揭", "销售面积"],
            "汇率": ["人民币", "汇率", "美元指数", "dxy", "usdcny"],
            "利率": ["利率", "美债", "收益率", "降息", "加息", "流动性"],
        },
        "positive_keywords": [
            "利好", "上调", "超预期", "增长", "创新高", "批准", "放宽", "降息", "刺激", "回购",
            "beat", "surge", "record high", "approval",
        ],
        "negative_keywords": [
            "利空", "下调", "不及预期", "下滑", "亏损", "制裁", "禁令", "暴跌", "加息", "收紧",
            "miss", "plunge", "ban", "sanction",
        ],
        "immediate_keywords": ["突发", "刚刚", "盘前", "盘中", "after-hours", "pre-market", "紧急"],
        "high_urgency_keywords": ["大涨", "大跌", "暴涨", "暴跌", "制裁", "禁令", "降息", "加息", "财报"],
        "market_keywords": {
            "a": ["a股", "沪指", "深成指", "创业板", "科创板", "北向", "人民币", "央行", "证监会"],
            "us": [
                "美股", "纳指", "标普", "道指", "美联储", "美元", "美债", "sec", "nasdaq", "s&p",
                "dow", "nvidia", "microsoft", "apple", "qcom", "qualcomm",
            ],
            "hk": ["港股", "恒生", "恒指", "h股"],
            "global": ["全球", "原油", "黄金", "地缘", "战争", "关税", "美元指数"],
        },
    },
    "llm": {
        "analysis_enabled": False,
        "analysis_model": "deepseek-v4-flash",
        "api_key_env": "OPENAI_API_KEY",
        "base_url_env": "OPENAI_BASE_URL",
        "fallback_base_url": "",  # 留空=未配置；必须经 base_url_env / .secret 提供，禁止默认内网地址
        "outlook": {
            "enabled": True,
            # 2026-08-13: 主模型 -> deepseek-v4-flash(大 snapshot prompt 下 v4-pro
            # 超时,flash 更快)。engine.yaml 可覆盖。
            "model": "deepseek-v4-flash",
            "fallback_models": [],  # 主模型失败时按顺序尝试的备用模型（共用端点/密钥）
            "retry_attempts": 0,    # 每个模型的额外重试次数
            "api_key_env": "OPENAI_COMPATIBLE_API_KEY",
            "base_url_env": "OPENAI_COMPATIBLE_BASE_URL",
            "fallback_base_url": "",  # 留空=未配置；必须经 base_url_env / .secret 提供，禁止默认内网地址
            "timeout_seconds": 120,
            "temperature": 0.2,
            "max_tokens": 8000,
            "cache_dir": ".local/outlook_cache",
        },
        # M2: advisory mainline replaces the constrained OutlookSynthesizer
        # as the source of structured_outlook for primary sessions.
        "advisory_mainline": {"enabled": True},
    },
    "risk_state": {
        "critical_ttl_minutes": 360,
        "hedge_independent_evidence": 2,
        "hedge_confirmations": 2,
        "deescalation_confirmations": 2,
        "state_path": ".local/risk_state.json",
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
    "execution_rules": {
        # Fail closed by default. Production rules are declared in engine.yaml.
        "settlement_rules": [],
        "quantity_rules": [],
        "redemption_rule_map": {},
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
        # Signal thresholds used by action_signals.py; add per-market overrides here.
        "thresholds": {
            "knife_r5": -3.0,
            "knife_rsi": 38.0,
            "reduce_r20": -5.0,
            "pullback_r20": 5.0,
            "pullback_rsi": 65.0,
            "pullback_position": 85.0,
            "accumulate_r20": 2.0,
            "accumulate_rsi_low": 40.0,
            "accumulate_rsi_high": 65.0,
            "accumulate_r20_max": 15.0,
            "left_bottom_rsi_max": 40.0,
            "left_bottom_price_position_max": 25.0,
            "left_bottom_r20_max": -10.0,
            "left_bottom_r5_floor": -5.0,
            "left_bottom_pullback_cooldown": 0.02,
        },
        "rank_weights": {
            "r20": 0.40,
            "rsi_zone": 0.30,
            "price_pos": 0.20,
            "volume": 0.10,
        },
        # Intelligence signal symbol → position proxy mapping. Used by quant_action
        # and intelligence_analyzer to associate external signals (e.g. QQQ, GLD)
        # with the user's actual positions. Add/modify mappings here without code changes.
        "intel_signal_proxy": {
            # 2026-08-13 修正: 旧表含过时代码(alipay_gf_nasdaq/alipay_info/
            # ccb_gold/a_510300/a_512890),与当前持仓 instrument_key(us:QQQ/
            # a:510300 等)不匹配,proxy 匹配永远失败。value 为持仓 symbol
            # (不含 market: 前缀),匹配 inst_key.endswith(":symbol")。
            "USO": "XLE",
            "GLD": "NEM",
            "NEM": "NEM",
            "GOLD": "NEM",
            "QQQ": "QQQ",
            "SPY": "SPY",
            "ITA": "ITA",
            "NVDA": "NVDA",
            "XLE": "XLE",
            "KWEB": "512480",
            "FXI": "510300",
            "ASHR": "510300",
            "GDX": "NEM",
            "SLV": "NEM",
            "GC=F": "518880",
            "XAU": "518880",
            "GC": "518880",
            "518880": "518880",
            "TLT": "SGOV",
            "SHY": "SGOV",
            "SGOV": "SGOV",
            "IWM": "512890",
            # BTCUSDT 删除: Kari 无加密货币持仓,旧映射 alipay_info 无意义。
        },
        # Event theme → exposure bucket tags mapping. Used by finalize_decision to map
        # macro events to portfolio exposure buckets.
        "theme_to_exposure": {
            # 2026-08-13 扩展: 与 LLM prompt 的 17 主题对齐,加入产业细分主题
            # (semiconductor/new_energy/consumer/defense/utilities 等),让"消费白酒"
            # "半导体库存""军工订单"等产业细分情报能关联到对应持仓(旧表只有
            # 宏观大类,粒度太粗)。标签均来自持仓 classification.exposure_tags。
            "geopolitics": ["energy", "defense", "gold", "oil_gas", "aerospace", "mining", "military", "commodity"],
            "monetary_policy": ["gold", "fixed_income", "us_rates", "cash_like", "money_market", "bank_wmp", "credit_plus", "us_equity", "qdii", "commodity"],
            "macro_data": ["a_share", "broad_index", "blue_chip", "us_equity", "qdii"],
            "china_policy": ["a_share", "broad_index", "blue_chip", "dividend_low_vol", "high_dividend", "active_equity", "star_board", "utilities", "power", "consumer", "liquor", "chemical", "cyclical", "military"],
            "earnings": ["tech", "ai", "semiconductor", "nasdaq100", "us_equity", "qdii", "consumer_tech", "consumer", "liquor"],
            "energy": ["energy", "oil_gas", "chemical", "cyclical"],
            "technology": ["tech", "ai", "semiconductor", "nasdaq100", "us_equity", "qdii", "consumer_tech", "star_board"],
            "semiconductor": ["semiconductor", "ai", "tech", "star_board"],
            "new_energy": ["energy", "power", "utilities", "chemical", "oil_gas"],
            "consumer": ["consumer", "liquor", "consumer_tech"],
            "healthcare": ["healthcare", "bio"],
            "financials": ["financials"],
            "real_estate": ["real_estate"],
            "defense": ["defense", "aerospace", "military"],
            "utilities": ["utilities", "power"],
            "crypto": ["crypto"],
            "general": [],
        },
        # Exposure bucket tag → constraint category mapping. Kept here as a single
        # source of truth for exposure aggregation.
        "tag_to_bucket": {
            "gold": "黄金", "mining": "黄金",
            "a_share": "权益", "us_equity": "权益", "tech": "权益",
            "nasdaq100": "权益", "qdii": "权益", "semiconductor": "权益",
            "star_board": "权益", "blue_chip": "权益", "dividend_low_vol": "权益",
            "high_dividend": "权益", "active_equity": "权益",
            "energy": "权益", "oil_gas": "权益", "defense": "权益",
            "aerospace": "权益", "ai": "权益",
            "fixed_income": "固收", "credit_plus": "固收", "us_rates": "固收",
            "bank_wmp": "固收", "short_treasury": "固收",
            "cash_like": "现金", "money_market": "现金",
        },
    },
    "intelligence": {
        # Theme → market/asset mapping for the keyword-rules analyzer.
        "theme_markets": {
            "geopolitics": ["equity", "oil", "gold", "dxy"],
            "monetary_policy": ["equity", "bond", "dxy", "gold"],
            "macro_data": ["equity", "bond", "dxy", "gold"],
            "china_policy": ["equity", "china_assets"],
            "earnings": ["equity", "tech"],
            "energy": ["oil", "equity", "energy"],
            "technology": ["equity", "tech"],
            "semiconductor": ["equity", "tech"],
            "new_energy": ["equity", "energy", "tech"],
            "consumer": ["equity"],
            "healthcare": ["equity"],
            "financials": ["equity"],
            "real_estate": ["equity", "bond"],
            "defense": ["equity", "gold"],
            "utilities": ["equity", "energy"],
            "crypto": ["crypto", "equity"],
        },
        # Theme → exposure bucket tags for category padding in LLM analyzer.
        "category_to_positions": {
            "gold": ["us:NEM", "a:518880", "ccb_gold"],
            "us_tech": ["us:NVDA", "us:QQQ"],
            "us_energy": ["us:XLE"],
            "us_defense": ["us:ITA"],
            "china_broad": ["a:510300", "a:512890", "a:511880", "a:516020"],
            "china_sci": ["a:588000", "a:512480", "a:561560"],
            "qdii": ["alipay_gf_nasdaq", "alipay_dc_nasdaq"],
            "active": ["alipay_info"],
            "bonds": ["us:SGOV", "a:159110"],
        },
        "category_to_themes": {
            "gold": ["geopolitics", "monetary_policy", "macro_data"],
            "us_energy": ["geopolitics", "energy"],
            "us_tech": ["technology", "earnings", "monetary_policy"],
            "us_defense": ["geopolitics"],
            "china_broad": ["macro_data", "monetary_policy", "china_policy"],
            "china_sci": ["technology", "china_policy"],
            "qdii": ["technology", "monetary_policy"],
            "active": ["technology", "earnings"],
            "bonds": ["monetary_policy", "macro_data"],
        },
        # Symbol → asset class mapping for market impact scoring.
        "symbol_to_asset": {
            "SPY": "equity", "QQQ": "equity", "NVDA": "equity", "IWM": "equity",
            "XLE": "oil", "USO": "oil",
            "GLD": "gold", "NEM": "gold", "IAU": "gold",
            "TLT": "bond", "SGOV": "bond", "SHY": "bond",
            "UUP": "dxy",
            "KWEB": "china_assets", "FXI": "china_assets", "ASHR": "china_assets",
        },
        # Known valid ticker/ETF symbols accepted by the keyword-rules analyzer.
        "known_symbols": [
            "SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "IVV", "VWO", "EFA",
            "GLD", "SLV", "GDX", "NEM", "XAU",
            "USO", "XLE", "XOM", "CVX", "OIH",
            "TLT", "IEF", "SHY", "AGG", "LQD", "HYG", "BND", "SGOV",
            "EEM", "FXI", "KWEB", "ASHR", "MCHI",
            "NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "AVGO",
            "AMD", "INTC", "QCOM", "MU", "ARM", "SMCI",
            "JPM", "GS", "BAC", "WFC", "C", "MS", "BLK", "V", "MA",
            "XLV", "XBI", "XLP", "XLY", "XLF", "XLI", "XLK", "XLU", "XLB",
            "ITA", "NOC", "LMT", "RTX", "PPA",
            "SOXX", "SMH", "SOXL", "SOXS",
            "BTC", "ETH", "BTCUSDT", "ETHUSDT",
            "VIX", "VIXY", "UVXY", "VXX", "SVXY",
            "GC", "CL", "NG", "SI", "HG", "ZC", "ZS", "ZW",
        ],
        # Theme keywords for the keyword-rules analyzer (English, lower-case matching).
        "theme_keywords": {
            "geopolitics": ["war", "conflict", "tension", "sanction", "iran", "israel", "ukraine", "russia", "taiwan", "military", "strike", "drone", "attack"],
            "monetary_policy": ["fed", "federal reserve", "interest rate", "rate hike", "rate cut", "powell", "fomc", "central bank", " ECB ", "BOJ", "PBOC", "yield"],
            "macro_data": ["CPI", "inflation", "PPI", "GDP", "nonfarm", "unemployment", "jobs report", "retail sales", "PMI", "industrial production"],
            "china_policy": ["PBOC", "NDRC", "CSRC", "state council", "stimulus", "subsidy", "regulatory", "china policy", "reform", "dual circulation"],
            "earnings": ["earnings", "revenue", "profit", "guidance", "beat", "miss", "EPS", "quarterly", "reported", "results"],
            "energy": ["oil", "crude", "energy", "OPEC", "gas", "petroleum", "natural gas"],
            "technology": ["AI", "artificial intelligence", "big tech", "magnificent seven", "tech stock", "cloud", "data center", "software"],
            "semiconductor": ["semiconductor", "chip", "foundry", "wafer", "tsmc", "nvidia", "gpu", "memory", "dram", "hbm", "export control"],
            "new_energy": ["solar", "photovoltaic", "ev", "electric vehicle", "lithium", "battery", "energy storage", "wind power", "tesla", "renewable"],
            "consumer": ["consumer", "retail", "luxury", "baijiu", "liquor", "moutai", "e-commerce", "food", "beverage", "consumption"],
            "healthcare": ["pharma", "biotech", "drug", "medicine", "FDA", "clinical", "vaccine", "hospital", "medical device"],
            "financials": ["bank", "broker", "insurance", "financial", "NPL", "interest margin", "securities", "asset management"],
            "real_estate": ["real estate", "property", "housing", "mortgage", "developer", "construction", "home sales"],
            "defense": ["defense", "military", "weapon", "aerospace", "missile", "drone", "navy", "army", "defence"],
            "utilities": ["utility", "power grid", "electricity", "power plant", "nuclear", "gas power", "hydropower"],
            "crypto": ["bitcoin", "crypto", "stablecoin", "ethereum", "blockchain", "digital currency"],
        },
        # Sentiment keywords for the keyword-rules analyzer.
        "positive_keywords": ["surge", "rally", "jump", "soar", "gain", "rise", "record high", "bullish", "strong", "beat", "raise guidance", "optimistic"],
        "negative_keywords": ["plunge", "crash", "tumble", "slump", "drop", "fall", "bearish", "recession", "miss", "cut guidance", "fear", "panic", "sell-off"],
    },
    "logging": {
        "level": "INFO",
        "desensitize": True,  # 脱敏日志中的金额和 API Key
    },
    "portfolio_layering": {
        # Minimum CNY amount for an add action to be considered executable.
        # Values smaller than this are suppressed to "observe only".
        "min_add_amount_cny": 800.0,
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


def provider_base_url(provider_name: str, default: str) -> str:
    """解析 Provider 端点：环境变量 > 引擎配置 > 代码默认值。

    Args:
        provider_name: Provider 名（如 "tencent_a"），对应
            DEFAULT_ENGINE_CONFIG["providers"][name]["base_url"]。
        default: 代码内兜底 URL（当前实现的历史值）。

    环境变量格式：``STOCKS_PROVIDER_<NAME>_BASE_URL``（如
    ``STOCKS_PROVIDER_TENCENT_A_BASE_URL``）。返回结果去掉尾部 ``/``。
    """
    env_key = f"STOCKS_PROVIDER_{provider_name.upper()}_BASE_URL"
    env_value = os.environ.get(env_key, "").strip()
    if env_value:
        return env_value.rstrip("/")
    configured = (
        DEFAULT_ENGINE_CONFIG.get("providers", {})
        .get(provider_name, {})
        .get("base_url")
    )
    return str(configured or default).rstrip("/")


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
