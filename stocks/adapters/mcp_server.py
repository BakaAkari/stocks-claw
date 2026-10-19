"""符合 Model Context Protocol 的 stdio 服务器。

传输层为换行分隔的 JSON-RPC 2.0（MCP stdio transport）：每行一条消息，
stdout 只写协议消息，日志与第三方输出一律走 stderr。

本模块只负责协议：握手、工具发现、工具调用和错误映射。工具语义仍由
``MCPAdapter.handle_request`` 承担，确认写入的规则不在这里放宽。

启动：
    python -m stocks.adapters.mcp_server [--read-only]
"""

from __future__ import annotations

import json
import sys
import traceback
from contextlib import redirect_stdout
from typing import Any, Callable, Optional, TextIO

SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# 每个工具必须归入其一；新增工具未分类时测试失败。
READ_TOOLS = frozenset({
    "get_analysis_context", "get_quotes", "get_news", "get_portfolio_summary",
    "investment_brief", "assets_list", "asset_intake_draft", "agent_status", "profile_get",
    "advice_list", "execution_list", "decision_list", "decision_review",
    "forecast_list", "statement_list", "cash_movement_list", "personal_mandate",
    "financial_reconcile", "financial_integrity", "financial_readiness",
    "financial_intake_template", "financial_intake_preview",
    "fee_budget_preview", "fee_budget_state",
})
WRITE_TOOLS = frozenset({
    "asset_add", "asset_update", "asset_remove", "asset_migrate_v2",
    "asset_intake_confirm", "profile_update", "advice_save", "execution_save",
    "decision_save", "forecast_save", "statement_save", "cash_movement_save",
    "constraints_replace", "fee_budget_replace",
})
# 覆盖或删除既有金融记忆的工具。
DESTRUCTIVE_TOOLS = frozenset({
    "asset_update", "asset_remove", "asset_migrate_v2", "profile_update",
    "constraints_replace", "fee_budget_replace",
})
# 访问外部行情/新闻/模型的工具。
OPEN_WORLD_TOOLS = frozenset({
    "get_analysis_context", "get_quotes", "get_news", "get_portfolio_summary", "investment_brief",
    "asset_intake_draft",
})

SERVER_INSTRUCTIONS = (
    "stocks-claw 个人投资分析工具：只提供数据、核对和建议上下文，不下单、不承诺收益。"
    "日常投资日报用 investment_brief；先调用 agent_status 查看就绪状态与缺口。写入金融记忆的工具要求 confirmed=true，"
    "只能在用户于对话中明确确认该次写入后设置，不得自行推断确认。"
    "工具返回的错误必须原样告知用户，不得用推断值绕过。"
)


def _server_version() -> str:
    try:
        from importlib.metadata import version
        return version("stocks-claw")
    except Exception:
        pass
    try:  # 源码 checkout 未安装为分发包时读取 pyproject
        import tomllib
        from pathlib import Path
        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        return str(tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"])
    except Exception:
        return "unknown"


class MCPStdioServer:
    """MCP 协议层。``adapter_factory`` 延迟到首次工具调用才构建引擎，
    使握手不依赖网络、密钥或引擎初始化耗时。"""

    def __init__(self, adapter_factory: Callable[[], Any], *, read_only: bool = False):
        self._adapter_factory = adapter_factory
        self._adapter: Any = None
        self.read_only = read_only
        self.initialized = False

    # ------------------------------------------------------------------
    # 工具定义
    # ------------------------------------------------------------------

    def _get_adapter(self) -> Any:
        if self._adapter is None:
            self._adapter = self._adapter_factory()
        return self._adapter

    def _tool_specs(self) -> list[dict]:
        from stocks.adapters.mcp import MCPAdapter
        # list_tools 只读静态定义，不需要引擎。
        return MCPAdapter(None).list_tools()

    def _exposed(self, name: str) -> bool:
        return not (self.read_only and name in WRITE_TOOLS)

    def list_tools(self) -> list[dict]:
        tools = []
        for spec in self._tool_specs():
            name = spec["name"]
            if not self._exposed(name):
                continue
            schema = dict(spec.get("parameters") or {})
            schema.setdefault("type", "object")
            schema.setdefault("properties", {})
            tools.append({
                "name": name,
                "description": spec.get("description", ""),
                "inputSchema": schema,
                "annotations": {
                    "readOnlyHint": name in READ_TOOLS,
                    "destructiveHint": name in DESTRUCTIVE_TOOLS,
                    "idempotentHint": name in READ_TOOLS,
                    "openWorldHint": name in OPEN_WORLD_TOOLS,
                },
            })
        return tools

    # ------------------------------------------------------------------
    # 消息处理
    # ------------------------------------------------------------------

    def handle_message(self, message: Any) -> Optional[Any]:
        """处理一条已解析的消息；通知和客户端响应返回 None。"""
        if isinstance(message, list):
            if not message:
                return _error(None, INVALID_REQUEST, "Empty batch")
            responses = [r for r in (self.handle_message(m) for m in message) if r is not None]
            return responses or None
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error(_id_of(message), INVALID_REQUEST, "Invalid JSON-RPC 2.0 message")

        method = message.get("method")
        has_id = "id" in message
        if method is None:
            # 客户端对服务器请求的响应；本服务器不发请求，忽略。
            return None
        if not isinstance(method, str):
            return _error(_id_of(message), INVALID_REQUEST, "method must be a string")
        if not has_id:
            if method == "notifications/initialized":
                self.initialized = True
            return None

        request_id = message["id"]
        params = message.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _error(request_id, INVALID_PARAMS, "params must be an object")

        try:
            if method == "initialize":
                return _result(request_id, self._initialize(params))
            if method == "ping":
                return _result(request_id, {})
            if method == "tools/list":
                return _result(request_id, {"tools": self.list_tools()})
            if method == "tools/call":
                return self._tools_call(request_id, params)
            if method in {"resources/list", "prompts/list"}:
                return _error(request_id, METHOD_NOT_FOUND, f"Capability not supported: {method}")
            return _error(request_id, METHOD_NOT_FOUND, f"Method not found: {method}")
        except Exception as exc:  # 协议层兜底，不让单条请求终止服务器
            traceback.print_exc(file=sys.stderr)
            return _error(request_id, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")

    def _initialize(self, params: dict) -> dict:
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "stocks-claw", "version": _server_version()},
            "instructions": SERVER_INSTRUCTIONS,
        }

    def _tools_call(self, request_id: Any, params: dict) -> dict:
        name = params.get("name")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(name, str) or not name:
            return _error(request_id, INVALID_PARAMS, "tools/call requires a tool name")
        if not isinstance(arguments, dict):
            return _error(request_id, INVALID_PARAMS, "tools/call arguments must be an object")
        if name not in READ_TOOLS and name not in WRITE_TOOLS:
            return _error(request_id, INVALID_PARAMS, f"Unknown tool: {name}")
        if not self._exposed(name):
            return _error(request_id, INVALID_PARAMS, f"Tool disabled in read-only mode: {name}")

        try:
            response = self._get_adapter().handle_request({"method": name, "params": arguments})
        except Exception as exc:
            # 工具执行失败属于工具结果，交给模型看到原始错误。
            traceback.print_exc(file=sys.stderr)
            response = {"success": False, "error": f"{type(exc).__name__}: {exc}"}

        is_error = not isinstance(response, dict) or response.get("success") is False or (
            "error" in response and response.get("success") is not True)
        text = json.dumps(response, ensure_ascii=False, default=str)
        return _result(request_id, {"content": [{"type": "text", "text": text}], "isError": is_error})

    # ------------------------------------------------------------------
    # stdio 循环
    # ------------------------------------------------------------------

    def serve(self, stdin: TextIO, stdout: TextIO) -> None:
        """逐行读取请求直到 EOF。运行期间把 sys.stdout 重定向到 stderr，
        防止引擎或第三方库的 print 破坏协议流。"""
        with redirect_stdout(sys.stderr):
            for line in stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError as exc:
                    response: Any = _error(None, PARSE_ERROR, f"Parse error: {exc.msg}")
                else:
                    response = self.handle_message(message)
                if response is not None:
                    stdout.write(json.dumps(response, ensure_ascii=False, default=str) + "\n")
                    stdout.flush()


def _id_of(message: Any) -> Any:
    return message.get("id") if isinstance(message, dict) else None


def _result(request_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def main(argv: Optional[list[str]] = None) -> None:
    """MCP stdio 服务器入口。"""
    import argparse

    parser = argparse.ArgumentParser(description="stocks-claw MCP 服务器（stdio）")
    parser.add_argument("--read-only", action="store_true",
                        help="只暴露只读工具，隐藏并拒绝所有金融记忆写入工具")
    parser.add_argument("--llm-analysis", action="store_true", help="启用 LLM 深度分析")
    parser.add_argument("--openai-key", default=None, help="OpenAI 兼容 API Key")
    parser.add_argument("--openai-base-url", default=None, help="OpenAI 兼容 API Base URL")
    args = parser.parse_args(argv)

    def adapter_factory():
        from stocks.adapters.mcp import MCPAdapter
        from stocks.engine import StocksEngine  # type: ignore[import-not-found]

        engine = StocksEngine(
            llm_analysis_enabled=args.llm_analysis,
            openai_api_key=args.openai_key,
            openai_base_url=args.openai_base_url,
        )
        return MCPAdapter(engine)

    stdin = open(sys.stdin.fileno(), encoding="utf-8", closefd=False)
    stdout = open(sys.stdout.fileno(), "w", encoding="utf-8", closefd=False)
    MCPStdioServer(adapter_factory, read_only=args.read_only).serve(stdin, stdout)


if __name__ == "__main__":
    main()
