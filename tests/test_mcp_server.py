"""MCP stdio 协议层测试：握手、工具发现、调用、错误映射与 stdout 隔离。"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

from stocks.adapters.mcp import MCPAdapter
from stocks.adapters.mcp_server import (
    DESTRUCTIVE_TOOLS,
    INVALID_PARAMS,
    INVALID_REQUEST,
    LATEST_PROTOCOL_VERSION,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    READ_TOOLS,
    WRITE_TOOLS,
    MCPStdioServer,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeAdapter:
    def __init__(self, response=None, exc=None, noisy=False):
        self.calls = []
        self.response = response if response is not None else {"success": True, "data": {"ok": 1}}
        self.exc = exc
        self.noisy = noisy

    def handle_request(self, request):
        self.calls.append(request)
        if self.noisy:
            print("stray engine output")
        if self.exc:
            raise self.exc
        return self.response


def _server(adapter=None, **kwargs):
    adapter = adapter or FakeAdapter()
    return MCPStdioServer(lambda: adapter, **kwargs), adapter


def _req(method, params=None, request_id=1):
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def test_every_tool_is_classified_once():
    names = {tool["name"] for tool in MCPAdapter(None).list_tools()}
    assert names == READ_TOOLS | WRITE_TOOLS
    assert not READ_TOOLS & WRITE_TOOLS
    assert DESTRUCTIVE_TOOLS <= WRITE_TOOLS


def test_initialize_negotiates_protocol_version():
    server, _ = _server()
    result = server.handle_message(_req("initialize", {"protocolVersion": "2024-11-05"}))["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "stocks-claw"
    assert "agent_status" in result["instructions"]

    unknown = server.handle_message(_req("initialize", {"protocolVersion": "1999-01-01"}))["result"]
    assert unknown["protocolVersion"] == LATEST_PROTOCOL_VERSION


def test_initialize_does_not_build_engine():
    built = []
    server = MCPStdioServer(lambda: built.append(1) or FakeAdapter())
    server.handle_message(_req("initialize", {"protocolVersion": LATEST_PROTOCOL_VERSION}))
    server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"})
    server.handle_message(_req("tools/list", request_id=2))
    assert built == []
    assert server.initialized is True


def test_notifications_and_client_responses_get_no_reply():
    server, _ = _server()
    assert server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.handle_message({"jsonrpc": "2.0", "method": "notifications/cancelled",
                                  "params": {"requestId": 1}}) is None
    assert server.handle_message({"jsonrpc": "2.0", "id": 9, "result": {}}) is None


def test_tools_list_uses_input_schema_and_annotations():
    server, _ = _server()
    tools = {t["name"]: t for t in server.handle_message(_req("tools/list"))["result"]["tools"]}
    assert set(tools) == READ_TOOLS | WRITE_TOOLS
    for tool in tools.values():
        assert tool["inputSchema"]["type"] == "object"
        assert "properties" in tool["inputSchema"]
        assert "parameters" not in tool
    assert tools["agent_status"]["annotations"]["readOnlyHint"] is True
    assert tools["asset_remove"]["annotations"]["readOnlyHint"] is False
    assert tools["asset_remove"]["annotations"]["destructiveHint"] is True
    assert "confirmed" in tools["execution_save"]["inputSchema"]["required"]


def test_read_only_mode_hides_and_rejects_writes():
    server, adapter = _server(read_only=True)
    names = {t["name"] for t in server.handle_message(_req("tools/list"))["result"]["tools"]}
    assert names == READ_TOOLS
    response = server.handle_message(_req("tools/call", {"name": "execution_save",
                                                         "arguments": {"confirmed": True}}))
    assert response["error"]["code"] == INVALID_PARAMS
    assert adapter.calls == []


def test_tools_call_wraps_adapter_result_as_text_content():
    server, adapter = _server(FakeAdapter({"success": True, "data": {"价格": 1.5}}))
    response = server.handle_message(_req("tools/call", {"name": "get_quotes",
                                                         "arguments": {"symbols": ["a:510300"]}}))
    result = response["result"]
    assert result["isError"] is False
    assert result["content"][0]["type"] == "text"
    assert json.loads(result["content"][0]["text"]) == {"success": True, "data": {"价格": 1.5}}
    assert adapter.calls == [{"method": "get_quotes", "params": {"symbols": ["a:510300"]}}]


def test_tool_failures_are_tool_errors_not_protocol_errors():
    server, _ = _server(FakeAdapter({"success": False, "error": "Memory writes require confirmed: true"}))
    result = server.handle_message(_req("tools/call", {"name": "asset_add", "arguments": {}}))["result"]
    assert result["isError"] is True
    assert "confirmed" in result["content"][0]["text"]

    server, _ = _server(FakeAdapter(exc=RuntimeError("provider down")))
    result = server.handle_message(_req("tools/call", {"name": "get_news"}))["result"]
    assert result["isError"] is True
    assert "provider down" in result["content"][0]["text"]


def test_confirmed_flag_is_passed_through_unchanged():
    server, adapter = _server()
    server.handle_message(_req("tools/call", {"name": "asset_add", "arguments": {"code": "510300"}}))
    assert "confirmed" not in adapter.calls[0]["params"]


def test_protocol_errors():
    server, _ = _server()
    assert server.handle_message(_req("tools/call", {"name": "nope"}))["error"]["code"] == INVALID_PARAMS
    assert server.handle_message(_req("tools/call", {"name": "get_news", "arguments": []}))["error"]["code"] == INVALID_PARAMS
    assert server.handle_message(_req("resources/read"))["error"]["code"] == METHOD_NOT_FOUND
    assert server.handle_message({"id": 1, "method": "ping"})["error"]["code"] == INVALID_REQUEST
    assert server.handle_message(_req("ping", request_id="abc")) == {"jsonrpc": "2.0", "id": "abc", "result": {}}


def test_batch_messages():
    server, _ = _server()
    responses = server.handle_message([
        _req("ping", request_id=1),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        _req("ping", request_id=2),
    ])
    assert [r["id"] for r in responses] == [1, 2]
    assert server.handle_message([])["error"]["code"] == INVALID_REQUEST


def test_serve_keeps_stdout_protocol_only():
    server, _ = _server(FakeAdapter(noisy=True))
    lines = "\n".join([
        json.dumps(_req("initialize", {"protocolVersion": LATEST_PROTOCOL_VERSION}, 1)),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        "",
        "{not json",
        json.dumps(_req("tools/call", {"name": "get_news", "arguments": {}}, 2)),
    ]) + "\n"
    out = io.StringIO()
    server.serve(io.StringIO(lines), out)
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r.get("id") for r in replies] == [1, None, 2]
    assert replies[1]["error"]["code"] == PARSE_ERROR
    assert replies[2]["result"]["isError"] is False


def test_module_entry_handshake_over_real_stdio():
    """真实子进程：握手与工具发现不需要构建引擎、读取账户或访问网络。"""
    messages = [
        _req("initialize", {"protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {},
                            "clientInfo": {"name": "test", "version": "0"}}, 1),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        _req("tools/list", request_id=2),
    ]
    for module in ("stocks.adapters.mcp_server", "stocks.adapters.mcp"):
        proc = subprocess.run(
            [sys.executable, "-m", module, "--read-only"],
            input="".join(json.dumps(m) + "\n" for m in messages),
            capture_output=True, text=True, timeout=60, cwd=REPO_ROOT,
        )
        assert proc.returncode == 0, proc.stderr
        replies = [json.loads(line) for line in proc.stdout.splitlines()]
        assert [r["id"] for r in replies] == [1, 2]
        assert replies[0]["result"]["protocolVersion"] == LATEST_PROTOCOL_VERSION
        assert {t["name"] for t in replies[1]["result"]["tools"]} == READ_TOOLS
