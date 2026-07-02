"""HTTP adapter 最小安全边界测试。"""

from unittest.mock import Mock

import pytest

from stocks.adapters.http import HTTPAdapter, _make_request_handler, _without_precise_amounts


def test_remote_bind_requires_explicit_flag_and_token(tmp_path):
    engine = Mock()
    missing_token = tmp_path / "missing-token"

    with pytest.raises(ValueError, match="allow-remote"):
        HTTPAdapter(engine, host="0.0.0.0", token_path=missing_token)
    with pytest.raises(ValueError, match="http-token"):
        HTTPAdapter(
            engine,
            host="0.0.0.0",
            allow_remote=True,
            token_path=missing_token,
        )


def test_bearer_token_is_checked():
    handler_type = _make_request_handler(Mock(), bearer_token="secret", require_auth=True)
    handler = object.__new__(handler_type)
    handler.headers = {"Authorization": "Bearer wrong"}
    assert handler._is_authorized() is False
    handler.headers = {"Authorization": "Bearer secret"}
    assert handler._is_authorized() is True


def test_precise_amounts_are_removed_recursively():
    data = {
        "assets": [{"name": "现金", "amount": 1000, "amount_cny": 1000}],
        "portfolio_mapping": {
            "buckets": {"现金": [{"name": "现金", "amount": 1000}]},
        },
        "total_value": 1000,
    }

    redacted = _without_precise_amounts(data)

    assert "amount" not in redacted["assets"][0]
    assert "amount_cny" not in redacted["assets"][0]
    assert "amount" not in redacted["portfolio_mapping"]["buckets"]["现金"][0]
    assert "total_value" not in redacted


def test_internal_error_response_is_generic():
    handler_type = _make_request_handler(Mock())
    handler = object.__new__(handler_type)
    handler.path = "/api/v1/analysis/context"
    handler.headers = {}
    handler._read_json_body = Mock(return_value={})
    handler._route_post = Mock(side_effect=RuntimeError("/private/secret/path"))
    handler._send_json = Mock()

    handler.do_POST()

    handler._send_json.assert_called_once_with(
        500,
        {"success": False, "error": "Internal server error"},
    )
