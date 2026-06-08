"""Adapters 包 — CLI / MCP / HTTP 三种接入模式"""

from stocks.adapters.cli import CLIAdapter
from stocks.adapters.http import HTTPAdapter
from stocks.adapters.mcp import MCPAdapter

__all__ = ["CLIAdapter", "MCPAdapter", "HTTPAdapter"]
