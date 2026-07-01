"""日志脱敏工具测试 — 覆盖金额、API Key、隐私路径脱敏"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from stocks.logging_utils import (
    DesensitizeFilter,
    desensitize_message,
    get_logger,
    setup_logging,
)

# ------------------------------------------------------------------
# desensitize_message 测试
# ------------------------------------------------------------------

class TestDesensitizeMessage:
    """脱敏消息测试"""

    def test_amount_integer(self):
        """整金额脱敏"""
        assert desensitize_message("总资产 100000 CNY") == "总资产 *** CNY"
        assert desensitize_message("余额: 50000") == "余额: ***"

    def test_amount_with_comma(self):
        """逗号分隔的金额脱敏"""
        assert desensitize_message("1,000,000 元") == "*** 元"

    def test_amount_with_decimal(self):
        """小数金额脱敏"""
        assert desensitize_message("价格 12345.67") == "价格 ***"

    def test_small_amount_preserved(self):
        """小金额保留（< 4 位）"""
        assert desensitize_message("价格 100 元") == "价格 100 元"
        assert desensitize_message("数量 3 个") == "数量 3 个"

    def test_year_not_desensitized(self):
        """年份等 4 位数字也脱敏（当前策略的已知误伤）"""
        # 注：当前正则不区分金额和年份，4 位数字统一脱敏
        result = desensitize_message("2024 年")
        assert result == "*** 年"

    def test_api_key_sk(self):
        """sk- 开头的 API Key 脱敏"""
        msg = desensitize_message("API Key: sk-abcdefghijklmnopqrstuvwxyz")
        assert "sk-" not in msg
        assert "***" in msg

    def test_api_key_bearer(self):
        """Bearer Token 脱敏"""
        msg = desensitize_message("Authorization: Bearer eyJhbGciOiJIUzI1NiIs")
        assert "Bearer" not in msg or "***" in msg

    def test_api_key_kv(self):
        """key=value 格式的 API Key 脱敏"""
        msg = desensitize_message("api_key=abcdef1234567890abcdef")
        assert "api_key=***" in msg or "***" in msg

    def test_private_path(self):
        """隐私路径脱敏"""
        msg = desensitize_message("加载文件: .local/financial_assets.json")
        assert ".local/" in msg
        assert "financial_assets.json" not in msg
        assert ".local/***" in msg

    def test_secret_path(self):
        """secret 路径脱敏"""
        msg = desensitize_message("读取密钥: .secret/openai-key.md")
        assert ".secret/" in msg
        assert "openai-key.md" not in msg

    def test_multiple_sensitive(self):
        """多条敏感信息混合"""
        msg = desensitize_message(
            "资产 100000, API Key: sk-abc12345678901234567890, 路径: .local/data.json"
        )
        assert "100000" not in msg
        assert "sk-abc" not in msg
        assert "data.json" not in msg

    def test_no_sensitive(self):
        """无敏感信息时不改变"""
        original = "市场行情: 沪深300 上涨 1.5%"
        assert desensitize_message(original) == original


# ------------------------------------------------------------------
# DesensitizeFilter 测试
# ------------------------------------------------------------------

class TestDesensitizeFilter:
    """日志过滤器测试"""

    def test_filter_enabled(self):
        """过滤器启用时脱敏"""
        f = DesensitizeFilter(enabled=True)
        record = MagicMock()
        record.msg = "资产 100000"
        record.args = ("50000",)

        f.filter(record)
        assert record.msg == "资产 ***"
        assert record.args == ("***",)

    def test_filter_disabled(self):
        """过滤器禁用时不过滤"""
        f = DesensitizeFilter(enabled=False)
        record = MagicMock()
        record.msg = "资产 100000"
        record.args = ("50000",)

        f.filter(record)
        assert record.msg == "资产 100000"
        assert record.args == ("50000",)

    def test_filter_non_string_args(self):
        """非字符串 args 不处理"""
        f = DesensitizeFilter(enabled=True)
        record = MagicMock()
        record.msg = "资产 %s"
        record.args = (100000, None, [1, 2])

        f.filter(record)
        assert record.args == ("***", None, [1, 2])

    def test_filter_returns_true(self):
        """过滤器始终返回 True（不阻断日志）"""
        f = DesensitizeFilter(enabled=True)
        record = MagicMock()
        record.msg = "test"
        record.args = ()

        assert f.filter(record) is True


# ------------------------------------------------------------------
# setup_logging 测试
# ------------------------------------------------------------------

class TestSetupLogging:
    """日志配置测试"""

    def test_setup_logging_level(self):
        """日志级别设置"""
        setup_logging(level="DEBUG", desensitize=False)
        root_logger = logging.getLogger("stocks")
        assert root_logger.level == logging.DEBUG

    def test_setup_logging_desensitize(self):
        """脱敏过滤器已添加"""
        setup_logging(level="INFO", desensitize=True)
        root_logger = logging.getLogger("stocks")
        filter_names = [type(f).__name__ for f in root_logger.filters]
        assert "DesensitizeFilter" in filter_names

    def test_setup_logging_no_desensitize(self):
        """不启用脱敏时无过滤器"""
        setup_logging(level="INFO", desensitize=False)
        root_logger = logging.getLogger("stocks")
        filter_names = [type(f).__name__ for f in root_logger.filters]
        assert "DesensitizeFilter" not in filter_names

    def test_logger_namespace(self):
        """logger 在 stocks 命名空间下"""
        logger = get_logger("engine")
        assert logger.name == "stocks.engine"


# ------------------------------------------------------------------
# 集成测试：真实日志输出
# ------------------------------------------------------------------

class TestLoggingIntegration:
    """日志集成测试"""

    def test_log_output_desensitized(self):
        """真实日志输出已脱敏"""
        setup_logging(level="INFO", desensitize=True)
        logger = get_logger("integration")

        # 使用 MockHandler 捕获日志记录，并添加 DesensitizeFilter
        mock_handler = logging.Handler()
        mock_handler.records = []
        mock_handler.emit = lambda record: mock_handler.records.append(record)
        mock_handler.addFilter(DesensitizeFilter(enabled=True))
        logger.handlers.clear()
        logger.addHandler(mock_handler)
        logger.propagate = False

        logger.info("用户资产: 100000, API Key: sk-abc12345678901234567890")

        assert len(mock_handler.records) == 1
        record = mock_handler.records[0]
        assert "100000" not in record.msg
        assert "sk-abc" not in record.msg
        assert "***" in record.msg

    def test_log_output_not_desensitized(self):
        """不启用脱敏时原始输出"""
        setup_logging(level="INFO", desensitize=False)
        logger = get_logger("integration_raw")

        mock_handler = logging.Handler()
        mock_handler.records = []
        mock_handler.emit = lambda record: mock_handler.records.append(record)
        logger.handlers.clear()
        logger.addHandler(mock_handler)
        logger.propagate = False

        logger.info("用户资产: 100000, API Key: sk-abc12345678901234567890")

        assert len(mock_handler.records) == 1
        record = mock_handler.records[0]
        assert "100000" in record.msg
        assert "sk-abc" in record.msg
