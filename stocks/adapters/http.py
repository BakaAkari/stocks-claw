"""HTTP API 适配器 — 标准库 http.server 实现

提供简单的 JSON HTTP API，不依赖 FastAPI 或任何第三方 Web 框架：
- POST /api/v1/analysis/context
- POST /api/v1/quotes
- POST /api/v1/news
- POST /api/v1/portfolio/summary
- GET  /api/v1/health

使用方式：
    python -m stocks.adapters.http --host 127.0.0.1 --port 8080
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional
from urllib.parse import parse_qs, urlparse


class HTTPAdapter:
    """HTTP 适配器 — 使用标准库 http.server 启动 JSON API 服务。"""

    def __init__(self, engine, host: str = "localhost", port: int = 8080):
        self.engine = engine
        self.host = host
        self.port = port
        self._server: Optional[HTTPServer] = None

    def start(self):
        """启动 HTTP 服务器（阻塞）。"""
        handler_factory = _make_request_handler(self.engine)
        self._server = HTTPServer((self.host, self.port), handler_factory)
        print(f"stocks-claw HTTP 服务已启动: http://{self.host}:{self.port}")
        try:
            self._server.serve_forever()
        except KeyboardInterrupt:
            print("\n收到中断信号，正在关闭...")
        finally:
            self.stop()

    def stop(self):
        """停止 HTTP 服务器。"""
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
            print("HTTP 服务已停止。")


def _make_request_handler(engine):
    """工厂函数：创建绑定 engine 的 RequestHandler 子类。"""

    class _RequestHandler(BaseHTTPRequestHandler):
        """HTTP 请求处理器 — 路由到 engine 方法并返回 JSON。"""

        _engine = engine

        def log_message(self, format, *args):
            """覆盖默认日志，减少噪音。"""
            pass

        def _send_json(self, status: int, data: dict):
            """发送 JSON 响应。"""
            body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json_body(self) -> dict:
            """读取并解析请求体 JSON。"""
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length == 0:
                return {}
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                return json.loads(body)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON body: {exc}")

        def _route_post(self, path: str, body: dict) -> tuple[int, dict]:
            """路由 POST 请求到 engine 方法。"""
            import asyncio
            if path == "/api/v1/analysis/context":
                context = asyncio.run(self._engine.build_context(
                    include_news=body.get("include_news", True),
                    include_quotes=body.get("include_quotes", True),
                    include_history=body.get("include_history", True),
                ))
                return 200, {"success": True, "data": context.to_dict()}

            if path == "/api/v1/quotes":
                market = body.get("market")
                quotes = asyncio.run(self._engine.fetch_quotes(market))
                if market:
                    data = {market: [q.to_dict() for q in quotes.get(market, [])]}
                else:
                    data = {k: [q.to_dict() for q in v] for k, v in quotes.items()}
                return 200, {"success": True, "data": data}

            if path == "/api/v1/news":
                sources = body.get("sources")
                limit = body.get("limit", 10)
                news = asyncio.run(self._engine.fetch_news(sources=sources, limit=limit))
                return 200, {
                    "success": True,
                    "data": [n.to_dict() for n in news],
                    "count": len(news),
                }

            if path == "/api/v1/portfolio/summary":
                assets = self._engine.load_assets()
                mapping = self._engine.analyze_portfolio(assets)
                constraints = body.get("constraints")
                drift_checks = self._engine.detect_drift(mapping, constraints)
                total = sum(a.amount for a in assets)
                return 200, {
                    "success": True,
                    "data": {
                        "assets": [a.to_dict() for a in assets],
                        "asset_count": len(assets),
                        "total_value": total,
                        "portfolio_mapping": mapping.to_dict(),
                        "drift_checks": [d.to_dict() for d in drift_checks],
                    },
                }

            return 404, {"success": False, "error": f"Unknown endpoint: {path}"}

        def do_POST(self):
            """处理 POST 请求。"""
            parsed = urlparse(self.path)
            path = parsed.path
            try:
                body = self._read_json_body()
                status, response = self._route_post(path, body)
                self._send_json(status, response)
            except ValueError as exc:
                self._send_json(400, {"success": False, "error": str(exc)})
            except Exception as exc:
                self._send_json(500, {"success": False, "error": str(exc)})

        def do_GET(self):
            """处理 GET 请求。"""
            parsed = urlparse(self.path)
            path = parsed.path

            if path == "/api/v1/health":
                try:
                    health = self._engine.health_check()
                    self._send_json(200, {"success": True, "data": health})
                except Exception as exc:
                    self._send_json(500, {"success": False, "error": str(exc)})
                return

            self._send_json(404, {"success": False, "error": f"Unknown endpoint: {path}"})

    return _RequestHandler


def main():
    """HTTP 服务器入口。"""
    import argparse

    parser = argparse.ArgumentParser(description="stocks-claw HTTP 服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8080, help="监听端口")
    parser.add_argument("--llm-enhancer", action="store_true", help="启用 LLM 数据增强")
    parser.add_argument("--llm-analysis", action="store_true", help="启用 LLM 深度分析")
    parser.add_argument("--openai-key", default=None, help="OpenAI 兼容 API Key")
    parser.add_argument("--openai-base-url", default=None, help="OpenAI 兼容 API Base URL")
    args = parser.parse_args()

    from stocks.engine import StocksEngine  # type: ignore[import-not-found]

    engine = StocksEngine(
        llm_enhancer_enabled=args.llm_enhancer,
        llm_analysis_enabled=args.llm_analysis,
        openai_api_key=args.openai_key,
        openai_base_url=args.openai_base_url,
    )
    adapter = HTTPAdapter(engine, host=args.host, port=args.port)
    adapter.start()


if __name__ == "__main__":
    main()
