"""MCP (Model Context Protocol) 适配器

MCP 是 Anthropic 推出的协议，允许 LLM 调用外部工具。
本适配器将 stocks-claw 暴露为 MCP 工具。

功能：
1. 提供 `get_analysis_context` 工具
2. 提供 `get_quotes` 工具
3. 提供 `get_news` 工具
4. 提供 `get_portfolio_summary` 工具

使用方式：
    由支持 MCP 的 Agent（如 Claude Desktop）自动发现并调用。
    也可通过 python -m stocks.adapters.mcp 启动 stdio 服务器。
"""

from __future__ import annotations

import json
from typing import Optional

from stocks.domain.models import FinancialAsset


def _next_actions(integrity: dict, mandate: dict) -> list[str]:
    """Translate read-only readiness into deterministic Hermes actions."""
    actions: list[str] = []
    if integrity.get("status") != "matched":
        actions.append("collect_or_correct_confirmed_account_facts")
    if not mandate.get("sources", {}).get("profile", {}).get("confirmation_event_id"):
        actions.append("request_and_confirm_profile")
    if not mandate.get("sources", {}).get("constraints", {}).get("confirmation_event_id"):
        actions.append("request_and_confirm_constraints")
    if not actions:
        actions.append("build_read_only_analysis_context")
    return actions


class MCPAdapter:
    """MCP 适配器 — 将 stocks-claw 引擎能力暴露为 MCP 工具集。"""

    def __init__(self, engine):
        self.engine = engine

    def handle_request(self, request: dict) -> dict:
        """处理 MCP 请求。

        Args:
            request: MCP JSON-RPC 风格请求字典，至少包含 ``method`` 和 ``params``。

        Returns:
            响应字典。出错时包含 ``error`` 字段。
        """
        method = request.get("method")
        params = request.get("params", {})

        if method == "get_analysis_context":
            return self._get_analysis_context(params)
        elif method == "get_quotes":
            return self._get_quotes(params)
        elif method == "get_news":
            return self._get_news(params)
        elif method == "get_portfolio_summary":
            return self._get_portfolio_summary(params)
        elif method == "assets_list":
            return self._assets_list()
        elif method == "asset_add":
            return self._asset_add(params)
        elif method == "asset_update":
            return self._asset_update(params)
        elif method == "asset_remove":
            return self._asset_remove(params)
        elif method == "asset_migrate_v2":
            return self._asset_migrate_v2(params)
        elif method == "asset_intake_draft":
            return self._asset_intake_draft(params)
        elif method == "asset_intake_confirm":
            return self._asset_intake_confirm(params)
        elif method == "agent_status":
            return self._agent_status(params)
        elif method == "profile_get":
            return {"success": True, "data": self.engine.get_profile()}
        elif method in {"statement_save", "cash_movement_save", "statement_list", "cash_movement_list",
                        "financial_reconcile", "financial_integrity", "personal_mandate", "constraints_replace"}:
            return self._financial_facts(method, params)
        elif method == "profile_update":
            return self._profile_update(params)
        elif method == "advice_list":
            return {"success": True, "data": self.engine.list_advice()}
        elif method == "advice_save":
            return self._advice_save(params)
        elif method == "execution_list":
            return {"success": True, "data": self.engine.list_executions()}
        elif method == "execution_save":
            return self._execution_save(params)
        elif method == "decision_save":
            return self._decision_save(params)
        elif method == "decision_list":
            return {"success": True, "data": self.engine.list_user_decisions()}
        elif method == "decision_review":
            try:
                review = self.engine.decision_review(
                    params.get("decision_id"), as_of=params.get("as_of"),
                    evaluation_database=params.get("evaluation_database"))
                return {"success": True, "data": review}
            except (TypeError, ValueError) as exc:
                return {"success": False, "error": str(exc)}
        elif method == "forecast_list":
            return {"success": True, "data": self.engine.list_forecasts()}
        elif method == "forecast_save":
            return self._forecast_save(params)
        else:
            return {"error": f"Unknown method: {method}"}

    def _get_analysis_context(self, params: dict) -> dict:
        """获取完整分析上下文。

        返回包含用户资产、市场行情、新闻、组合映射、市场状态、
        偏离检查等全部信息的 AnalysisContext。
        """
        import asyncio
        try:
            context = asyncio.run(self.engine.build_context(
                include_news=params.get("include_news", True),
                include_quotes=params.get("include_quotes", True),
                include_history=params.get("include_history", True),
            ))
            return {
                "success": True,
                "data": context.to_dict(),
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _assets_list(self) -> dict:
        return {
            "success": True,
            "data": [asset.to_dict() for asset in self.engine.load_assets()],
        }

    @staticmethod
    def _confirmed(params: dict) -> Optional[dict]:
        if params.get("confirmed") is True:
            return None
        return {
            "success": False,
            "error": "Memory writes require confirmed: true",
        }

    def _asset_add(self, params: dict) -> dict:
        confirmation_error = self._confirmed(params)
        if confirmation_error:
            return confirmation_error
        try:
            fields = {
                key: value
                for key, value in params.items()
                if key in {
                    "name",
                    "platform",
                    "amount",
                    "asset_type",
                    "notes",
                    "currency",
                    "instrument_key",
                    "quantity",
                    "tradable",
                }
            }
            asset = FinancialAsset(**fields)
            self.engine.add_asset(asset)
            return {"success": True, "data": asset.name, "action": "added"}
        except (TypeError, ValueError) as exc:
            return {"success": False, "error": str(exc)}

    def _asset_update(self, params: dict) -> dict:
        confirmation_error = self._confirmed(params)
        if confirmation_error:
            return confirmation_error
        name = str(params.get("name", "")).strip()
        changes = params.get("changes")
        if not name or not isinstance(changes, dict):
            return {"success": False, "error": "name and changes are required"}
        allowed = {
            "name",
            "platform",
            "amount",
            "asset_type",
            "notes",
            "confirmed",
            "currency",
            "instrument_key",
            "quantity",
            "tradable",
        }
        unknown = set(changes) - allowed
        if unknown:
            return {"success": False, "error": f"Unsupported asset fields: {sorted(unknown)}"}
        try:
            updated = self.engine.update_asset(name, **changes)
            return {"success": updated, "data": name, "action": "updated"}
        except (TypeError, ValueError) as exc:
            return {"success": False, "error": str(exc)}

    def _asset_remove(self, params: dict) -> dict:
        confirmation_error = self._confirmed(params)
        if confirmation_error:
            return confirmation_error
        name = str(params.get("name", "")).strip()
        if not name:
            return {"success": False, "error": "name is required"}
        removed = self.engine.remove_asset(name)
        return {"success": removed, "data": name, "action": "removed"}

    def _asset_migrate_v2(self, params: dict) -> dict:
        try:
            result = self.engine.migrate_assets_v2(
                confirmed=params.get("confirmed") is True,
                confirmation_channel="mcp" if params.get("confirmed") is True else None,
            )
            return result
        except (TypeError, ValueError) as exc:
            return {"success": False, "error": str(exc)}

    def _asset_intake_draft(self, params: dict) -> dict:
        text = params.get("text")
        if not isinstance(text, str) or not text.strip():
            return {"success": False, "error": "intake text must be a non-empty string"}
        try:
            return self.engine.asset_intake_draft(text)
        except (TypeError, ValueError) as exc:
            return {"success": False, "error": str(exc)}

    def _asset_intake_confirm(self, params: dict) -> dict:
        if params.get("confirmed") is not True:
            return self._confirmed(params)
        draft = params.get("draft")
        token = params.get("confirmation_token")
        if not isinstance(draft, dict) or not isinstance(token, str) or not token:
            return {"success": False, "error": "draft and confirmation_token are required"}
        try:
            return self.engine.asset_intake_apply(
                draft, token, confirmation_channel="mcp"
            )
        except (TypeError, ValueError) as exc:
            return {"success": False, "error": str(exc)}

    def _agent_status(self, params: dict) -> dict:
        """Return the read-only checkpoints Hermes must inspect before acting."""
        try:
            integrity = self.engine.financial_integrity(as_of=params.get("as_of"))
        except (TypeError, ValueError) as exc:
            integrity = {"status": "incomplete", "gaps": [str(exc)]}
        mandate = self.engine.personal_mandate()
        return {
            "success": True,
            "data": {
                "contract": "hermes-operation-v1",
                "read_only": True,
                "financial_integrity": integrity,
                "mandate": mandate,
                "next_actions": _next_actions(integrity, mandate),
                "writes_require_user_confirmation": True,
                "analysis_never_writes_financial_memory": True,
            },
        }

    def _profile_update(self, params: dict) -> dict:
        confirmation_error = self._confirmed(params)
        if confirmation_error:
            return confirmation_error
        updates = params.get("profile")
        if not isinstance(updates, dict):
            return {"success": False, "error": "profile object is required"}
        try:
            profile = self.engine.update_profile(updates, confirmation_channel="mcp")
            return {"success": True, "data": profile, "action": "profile_updated"}
        except (TypeError, ValueError) as exc:
            return {"success": False, "error": str(exc)}

    def _advice_save(self, params: dict) -> dict:
        confirmation_error = self._confirmed(params)
        if confirmation_error:
            return confirmation_error
        advice = params.get("advice")
        if not isinstance(advice, dict):
            return {"success": False, "error": "advice object is required"}
        try:
            record = self.engine.save_advice(advice)
            return {"success": True, "data": record, "action": "advice_saved"}
        except (TypeError, ValueError) as exc:
            response = {"success": False, "error": str(exc)}
            errors = getattr(exc, "errors", None)
            if errors is not None:
                response["errors"] = errors
            return response

    def _decision_save(self, params: dict) -> dict:
        confirmation_error = self._confirmed(params)
        if confirmation_error:
            return confirmation_error
        try:
            record = self.engine.save_user_decision(params.get("decision"))
            return {"success": True, "data": record, "action": "decision_saved"}
        except (TypeError, ValueError) as exc:
            return {"success": False, "error": str(exc)}

    def _execution_save(self, params: dict) -> dict:
        confirmation_error = self._confirmed(params)
        if confirmation_error:
            return confirmation_error
        execution = params.get("execution")
        if not isinstance(execution, dict):
            return {"success": False, "error": "execution object is required"}
        try:
            record = self.engine.save_execution(execution, confirmation_channel="mcp")
            return {"success": True, "data": record, "action": "execution_saved"}
        except (TypeError, ValueError) as exc:
            return {"success": False, "error": str(exc)}

    def _financial_facts(self, method: str, params: dict) -> dict:
        if method in {"statement_save", "cash_movement_save", "constraints_replace"}:
            error = self._confirmed(params)
            if error:
                return error
        try:
            if method == "constraints_replace":
                data = self.engine.replace_portfolio_constraints(params.get("constraints"), confirmation_channel="mcp")
            elif method == "statement_save":
                data = self.engine.save_account_statement(params.get("statement"), confirmation_channel="mcp")
            elif method == "cash_movement_save":
                data = self.engine.save_cash_movement(params.get("movement"), confirmation_channel="mcp")
            elif method == "statement_list":
                data = self.engine.list_account_statements()
            elif method == "cash_movement_list":
                data = self.engine.list_cash_movements()
            elif method == "financial_reconcile":
                data = self.engine.financial_reconcile(params.get("opening_id"), params.get("closing_id"),
                                                       as_of=params.get("as_of"))
            elif method == "financial_integrity":
                data = self.engine.financial_integrity(as_of=params.get("as_of"))
            else:
                data = self.engine.personal_mandate()
            return {"success": True, "data": data}
        except (TypeError, ValueError) as exc:
            return {"success": False, "error": str(exc)}

    def _forecast_save(self, params: dict) -> dict:
        confirmation_error = self._confirmed(params)
        if confirmation_error:
            return confirmation_error
        forecast = params.get("forecast")
        if not isinstance(forecast, dict):
            return {"success": False, "error": "forecast object is required"}
        try:
            record = self.engine.save_forecast(forecast)
            return {"success": True, "data": record, "action": "forecast_saved"}
        except (TypeError, ValueError) as exc:
            return {"success": False, "error": str(exc)}

    def _get_quotes(self, params: dict) -> dict:
        """获取行情数据。

        Params:
            market: 市场代码，如 ``"a"``、``"us"`` 或 ``None``（全部）。
        """
        import asyncio
        try:
            market = params.get("market")
            quotes = asyncio.run(self.engine.fetch_quotes(market))
            return {
                "success": True,
                "data": {
                    market or "all": [q.to_dict() for q in quotes.get(market, [])]
                    if market
                    else {k: [q.to_dict() for q in v] for k, v in quotes.items()}
                },
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _get_news(self, params: dict) -> dict:
        """获取新闻数据。

        Params:
            sources: 新闻源列表，``None`` 则按配置获取所有源。
            limit: 每源获取条数（默认 10）。
        """
        import asyncio
        try:
            sources = params.get("sources")
            limit = params.get("limit", 10)
            news = asyncio.run(self.engine.fetch_news(sources=sources, limit=limit))
            return {
                "success": True,
                "data": [n.to_dict() for n in news],
                "count": len(news),
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _get_portfolio_summary(self, params: dict) -> dict:
        """获取组合摘要。

        返回资产列表、组合映射、偏离检查等组合层面的结构化摘要。
        """
        try:
            assets = self.engine.load_assets()
            mapping = self.engine.analyze_portfolio(assets)
            constraints = params.get("constraints")
            drift_checks = self.engine.detect_drift(mapping, constraints)

            total = sum(a.valuation_cny or 0.0 for a in assets)
            return {
                "success": True,
                "data": {
                    "assets": [a.to_dict() for a in assets],
                    "asset_count": len(assets),
                    "total_value": total,
                    "portfolio_mapping": mapping.to_dict(),
                    "drift_checks": [d.to_dict() for d in drift_checks],
                },
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def list_tools(self) -> list[dict]:
        """列出所有可用工具 — 供 MCP 客户端发现。

        Returns:
            工具描述列表，每个元素包含 ``name``、``description`` 和 ``parameters``。
        """
        return [
            {
                "name": "get_analysis_context",
                "description": (
                    "获取 stocks-claw 的完整金融分析上下文，"
                    "包含资产、行情、新闻、组合映射、市场状态和偏离检查。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "include_news": {
                            "type": "boolean",
                            "default": True,
                            "description": "是否包含新闻",
                        },
                        "include_quotes": {
                            "type": "boolean",
                            "default": True,
                            "description": "是否包含行情",
                        },
                        "include_history": {
                            "type": "boolean",
                            "default": True,
                            "description": "是否包含历史快照",
                        },
                    },
                },
            },
            {
                "name": "get_quotes",
                "description": "获取监控列表的实时行情数据。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "market": {
                            "type": "string",
                            "description": "市场代码，如 'a'、'us'，不传则获取全部",
                        },
                    },
                },
            },
            {
                "name": "get_news",
                "description": "获取最新财经新闻。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sources": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "指定新闻源列表",
                        },
                        "limit": {
                            "type": "integer",
                            "default": 10,
                            "description": "每源获取条数",
                        },
                    },
                },
            },
            {
                "name": "get_portfolio_summary",
                "description": "获取用户投资组合摘要，包含资产、组合映射和偏离检查。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "constraints": {
                            "type": "object",
                            "description": "自定义约束条件（可选）",
                        },
                    },
                },
            },
            {
                "name": "assets_list",
                "description": "列出金融资产记忆。",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "asset_add",
                "description": "在用户明确确认后添加金融资产。",
                "parameters": self._asset_write_schema("add"),
            },
            {
                "name": "asset_update",
                "description": "在用户明确确认后更新金融资产。",
                "parameters": self._asset_write_schema("update"),
            },
            {
                "name": "asset_remove",
                "description": "在用户明确确认后按名称删除金融资产。",
                "parameters": {
                    "type": "object",
                    "required": ["name", "confirmed"],
                    "properties": {
                        "name": {"type": "string"},
                        "confirmed": {"type": "boolean", "const": True},
                    },
                },
            },
            {
                "name": "asset_migrate_v2",
                "description": (
                    "预览或确认迁移金融资产文件到 schema_version=2；"
                    "confirmed=false 只返回完整预览，confirmed=true 才写入本地 .local 文件。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "confirmed": {"type": "boolean", "default": False},
                    },
                },
            },
            {
                "name": "asset_intake_draft",
                "description": "把 Hermes 收到的账户/持仓描述转换为不写入的结构化草稿；必须先检查歧义和来源，再请求用户确认。",
                "parameters": {
                    "type": "object", "required": ["text"],
                    "properties": {"text": {"type": "string"}},
                },
            },
            {
                "name": "asset_intake_confirm",
                "description": "仅在用户确认同一份草稿后写入资产账本；过期 token、记忆变化或歧义会拒绝，失败不应重试不同内容。",
                "parameters": {
                    "type": "object", "required": ["draft", "confirmation_token", "confirmed"],
                    "properties": {
                        "draft": {"type": "object"}, "confirmation_token": {"type": "string"},
                        "confirmed": {"type": "boolean", "const": True},
                    },
                },
            },
            {
                "name": "agent_status",
                "description": "只读返回 Hermes 当前可执行检查点、金融核对状态、个人约束来源和下一步；不产生任何写入。",
                "parameters": {"type": "object", "properties": {"as_of": {"type": "string"}}},
            },
            {
                "name": "profile_get",
                "description": "读取投资者偏好记忆。",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "profile_update",
                "description": "在用户明确确认后更新投资者偏好记忆。",
                "parameters": {
                    "type": "object",
                    "required": ["profile", "confirmed"],
                    "properties": {
                        "profile": {"type": "object"},
                        "confirmed": {"type": "boolean", "const": True},
                    },
                },
            },
            {
                "name": "advice_list",
                "description": "列出已确认保存的建议摘要。",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "advice_save",
                "description": "在用户明确确认后保存建议摘要。",
                "parameters": {
                    "type": "object",
                    "required": ["advice", "confirmed"],
                    "properties": {
                        "advice": {"type": "object"},
                        "confirmed": {"type": "boolean", "const": True},
                    },
                },
            },
            {
                "name": "execution_list",
                "description": "列出已确认记录的执行记录。",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "execution_save",
                "description": "在用户明确确认后保存建议执行记录。",
                "parameters": {
                    "type": "object",
                    "required": ["execution", "confirmed"],
                    "properties": {
                        "execution": {"type": "object"},
                        "confirmed": {"type": "boolean", "const": True},
                    },
                },
            },
            {
                "name": "decision_save",
                "description": "保存用户确认的建议决定；接受或部分采纳不代表实际成交",
                "parameters": {
                    "type": "object", "required": ["decision", "confirmed"],
                    "properties": {"decision": {"type": "object"},
                                   "confirmed": {"type": "boolean", "const": True}},
                },
            },
            *[
                {"name": name, "description": description,
                 "parameters": {"type": "object", "required": [field, "confirmed"],
                                "properties": {field: {"type": "object"},
                                               "confirmed": {"type": "boolean", "const": True}}}}
                for name, field, description in (
                    ("constraints_replace", "constraints", "确认后整体替换个人组合约束；空对象表示明确无配置约束"),
                    ("statement_save", "statement", "用户确认后记录账户时点现金与持仓；不覆盖资产记忆"),
                    ("cash_movement_save", "movement", "用户确认后记录非交易现金变动"),
                )
            ],
            *[
                {"name": name, "description": description,
                 "parameters": {"type": "object", "properties": {}}}
                for name, description in (
                    ("statement_list", "读取账户时点记录和读取错误"),
                    ("cash_movement_list", "读取非交易现金变动和读取错误"),
                    ("personal_mandate", "读取已确认的个人约束和未知项"),
                )
            ],
            {"name": "financial_reconcile", "description": "只读核对账户两个确认时点之间的现金与数量",
             "parameters": {"type": "object", "required": ["opening_id", "closing_id"],
                            "properties": {"opening_id": {"type": "string"}, "closing_id": {"type": "string"},
                                           "as_of": {"type": "string"}}}},
            {"name": "financial_integrity", "description": "只读核对最新账单与当前资产记忆的一致性",
             "parameters": {"type": "object", "properties": {"as_of": {"type": "string"}}}},
            {
                "name": "decision_list", "description": "读取全部用户决定历史",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "decision_review", "description": "只读复盘真实建议、用户决定、逐笔成交与冻结行情",
                "parameters": {
                    "type": "object", "properties": {
                        "decision_id": {"type": "string"}, "as_of": {"type": "string"},
                        "evaluation_database": {"type": "string"},
                    },
                },
            },
            {
                "name": "forecast_list",
                "description": "列出已确认保存的预测记录。",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "forecast_save",
                "description": "在用户明确确认后保存预测记录。",
                "parameters": {
                    "type": "object",
                    "required": ["forecast", "confirmed"],
                    "properties": {
                        "forecast": {"type": "object"},
                        "confirmed": {"type": "boolean", "const": True},
                    },
                },
            },
        ]

    @staticmethod
    def _asset_write_schema(action: str) -> dict:
        properties = {
            "name": {"type": "string"},
            "platform": {"type": "string"},
            "amount": {"type": "number"},
            "asset_type": {"type": "string"},
            "notes": {"type": ["string", "null"]},
            "currency": {"type": "string"},
            "instrument_key": {"type": ["string", "null"]},
            "quantity": {"type": ["number", "null"]},
            "tradable": {"type": ["boolean", "null"]},
            "confirmed": {"type": "boolean", "const": True},
        }
        if action == "update":
            return {
                "type": "object",
                "required": ["name", "changes", "confirmed"],
                "properties": {
                    "name": {"type": "string"},
                    "changes": {"type": "object"},
                    "confirmed": properties["confirmed"],
                },
            }
        return {
            "type": "object",
            "required": ["name", "platform", "amount", "confirmed"],
            "properties": properties,
        }


def _stdio_loop(adapter: MCPAdapter) -> None:
    """基于标准输入输出的简单 MCP 消息循环。"""
    import sys

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            response = {"error": "Invalid JSON"}
            print(json.dumps(response, ensure_ascii=False))
            continue

        response = adapter.handle_request(request)
        print(json.dumps(response, ensure_ascii=False))
        sys.stdout.flush()


def main():
    """MCP stdio 服务器入口。"""
    import argparse

    parser = argparse.ArgumentParser(description="stocks-claw MCP 服务器")
    parser.add_argument("--llm-analysis", action="store_true", help="启用 LLM 深度分析")
    parser.add_argument("--openai-key", default=None, help="OpenAI 兼容 API Key")
    parser.add_argument("--openai-base-url", default=None, help="OpenAI 兼容 API Base URL")
    args = parser.parse_args()

    from stocks.engine import StocksEngine  # type: ignore[import-not-found]

    engine = StocksEngine(
        llm_analysis_enabled=args.llm_analysis,
        openai_api_key=args.openai_key,
        openai_base_url=args.openai_base_url,
    )
    adapter = MCPAdapter(engine)
    _stdio_loop(adapter)


if __name__ == "__main__":
    main()
