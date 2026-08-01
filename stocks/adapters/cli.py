"""CLI 模式适配器 — 命令行交互

使用方式：
    python -m stocks.adapters.cli

功能：
1. 解析命令行参数
2. 调用 engine 获取数据
3. 输出 JSON 或格式化文本
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from stocks.domain.models import FinancialAsset


class CLIAdapter:
    """CLI 适配器 — 将 stocks-claw 引擎能力暴露为命令行工具。"""

    def __init__(self, engine):
        self.engine = engine

    def run(self, args: Optional[list[str]] = None):
        """运行 CLI。"""
        parser = argparse.ArgumentParser(description="stocks-claw 投资顾问")
        parser.add_argument("--assets", help="资产数据 JSON 文件路径")
        parser.add_argument("--constraints", help="约束条件 JSON 文件路径")
        parser.add_argument("--profile", help="投资画像 JSON 文件路径")
        parser.add_argument("--watchlist", help="关注列表 JSON 文件路径")
        parser.add_argument(
            "--output", choices=["json", "text"], default="json",
            help="输出格式（默认 json）"
        )
        parser.add_argument("--save", help="保存结果到文件路径")
        parser.add_argument(
            "--llm-analysis", action="store_true",
            help="启用 LLM 深度分析（生成投资建议报告）"
        )
        parser.add_argument(
            "--openai-key", default=None,
            help="OpenAI 兼容 API Key（覆盖环境变量和 .secret 文件）"
        )
        parser.add_argument(
            "--openai-base-url", default=None,
            help="OpenAI 兼容 API Base URL（覆盖环境变量和 .secret 文件）"
        )
        parser.add_argument(
            "--no-news", action="store_true",
            help="构建上下文时不包含新闻"
        )
        parser.add_argument(
            "--no-quotes", action="store_true",
            help="构建上下文时不包含行情"
        )
        asset_actions = parser.add_mutually_exclusive_group()
        asset_actions.add_argument(
            "--assets-list",
            action="store_true",
            help="列出当前资产并退出",
        )
        asset_actions.add_argument(
            "--asset-add",
            metavar="JSON",
            help="添加资产；值为 FinancialAsset 字段的 JSON 对象",
        )
        asset_actions.add_argument(
            "--asset-update",
            metavar="JSON",
            help='更新资产；格式为 {"name":"目标","changes":{...}}',
        )
        asset_actions.add_argument(
            "--asset-remove",
            metavar="NAME",
            help="按名称删除资产",
        )
        asset_actions.add_argument(
            "--asset-migrate-v2",
            action="store_true",
            help="预览或确认迁移 financial_assets.json 到 schema_version=2",
        )
        asset_actions.add_argument(
            "--asset-intake",
            metavar="TEXT",
            help="自然语言资产登记：生成草稿 + 确认 token（不写文件）",
        )
        asset_actions.add_argument(
            "--asset-intake-confirm",
            action="store_true",
            help="确认资产登记草稿；配合 --draft-json 与 --token 使用",
        )
        parser.add_argument(
            "--draft-json",
            default=None,
            help="--asset-intake 输出的草稿 JSON（配合 --asset-intake-confirm）",
        )
        parser.add_argument(
            "--token",
            default=None,
            help="--asset-intake 输出的 confirmation_token（配合 --asset-intake-confirm）",
        )
        asset_actions.add_argument(
            "--profile-get",
            action="store_true",
            help="读取投资者画像并退出",
        )
        asset_actions.add_argument(
            "--profile-update",
            metavar="JSON",
            help="更新投资者画像字段",
        )
        asset_actions.add_argument(
            "--advice-list",
            action="store_true",
            help="列出已确认保存的建议摘要",
        )
        asset_actions.add_argument(
            "--advice-feedback",
            nargs=2,
            metavar=("REF", "STATUS"),
            help="给建议打结果标记；REF 为 latest 或 created_at 前缀，"
                 "STATUS ∈ accepted|partial|rejected|deferred（需 --confirmed）",
        )
        asset_actions.add_argument(
            "--advice-rollup",
            nargs="?",
            const=7,
            default=None,
            type=int,
            metavar="DAYS",
            help="查看最近 DAYS 天（默认 7）的建议反馈汇总（只读）",
        )
        parser.add_argument(
            "--note",
            default="",
            help="反馈备注（配合 --advice-feedback 使用）",
        )
        asset_actions.add_argument(
            "--advice-save",
            metavar="JSON",
            help="保存一条建议摘要；值为 AdviceRecord 字段的 JSON 对象",
        )
        asset_actions.add_argument(
            "--execution-list",
            action="store_true",
            help="列出已确认记录的执行记录",
        )
        asset_actions.add_argument(
            "--execution-pending",
            metavar="RUN_ID",
            help="列出指定 run 下所有尚未 executed 的 planned action",
        )
        asset_actions.add_argument(
            "--execution-save",
            metavar="JSON",
            help="保存一条执行记录；值为 ExecutionRecord 新 schema 字段的 JSON 对象",
        )
        asset_actions.add_argument(
            "--forecast-list",
            action="store_true",
            help="列出已确认保存的预测记录",
        )
        asset_actions.add_argument(
            "--forecast-save",
            metavar="JSON",
            help="保存一条预测记录；值为 ForecastRecord 字段的 JSON 对象",
        )
        asset_actions.add_argument(
            "--decision-attribution",
            action="store_true",
            help="输出所有决策快照的结构化 JSON",
        )
        asset_actions.add_argument(
            "--decision-attribution-settle",
            nargs="?", const="now", default=None,
            help="结算到期的决策快照。可指定 ISO 时间或留空（默认 now）",
        )
        asset_actions.add_argument(
            "--check-event-triggers",
            action="store_true",
            help="检查是否有经济日历事件触发（仅检查，不执行采集）",
        )
        asset_actions.add_argument(
            "--scheduled-run-due",
            action="store_true",
            help="运行当前到期的定时分析 session（含事件触发检查）",
        )
        asset_actions.add_argument(
            "--scheduled-run-session",
            metavar="SESSION",
            help="手动运行指定定时分析 session",
        )
        asset_actions.add_argument(
            "--scheduled-run-latest",
            metavar="SESSION",
            help="读取指定 session 的最新定时分析 JSON 产物",
        )
        asset_actions.add_argument(
            "--interpret-profile",
            action="store_true",
            help="预览/写入个性化引擎参数。加 --confirmed --params-json '...' 直接写入",
        )
        parser.add_argument(
            "--params-json",
            default=None,
            help="Agent 生成的个性化参数 JSON（配合 --interpret-profile --confirmed 使用）",
        )
        parser.add_argument(
            "--confirmed",
            action="store_true",
            help="确认执行资产或画像写操作",
        )
        parser.add_argument(
            "--now",
            default=None,
            help="测试/补跑用 ISO 时间；用于 scheduled-run due/session",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="跳过 scheduled-run 重复运行保护",
        )

        parsed = parser.parse_args(args)
        asyncio.run(self._execute(parsed))

    async def _execute(self, args: argparse.Namespace):
        """执行命令。"""
        asset_result = await self._handle_asset_action(args)
        if asset_result is not None:
            print(json.dumps(asset_result, ensure_ascii=False, indent=2))
            return

        # 1. 加载外部 JSON 数据（如提供）
        external_assets = self._load_json_file(args.assets)
        external_constraints = self._load_json_file(args.constraints)
        external_profile = self._load_json_file(args.profile)
        external_watchlist = self._load_json_file(args.watchlist)

        # 2. 调用 engine 构建 AnalysisContext
        context = await self.engine.build_context(
            include_news=not args.no_news,
            include_quotes=not args.no_quotes,
            include_history=True,
        )

        # 3. 如启用 LLM 分析，生成报告
        report: Optional[str] = None
        if args.llm_analysis and hasattr(self.engine, "generate_report"):
            try:
                report = await self.engine.generate_report(context)
            except RuntimeError as exc:
                report = f"[LLM 分析未启用或失败: {exc}]"

        # 5. 组装输出
        if args.output == "json":
            payload = {
                "generated_at": datetime.now().isoformat(),
                "context": context.to_dict(),
            }
            if report is not None:
                payload["report"] = report
            if external_assets is not None:
                payload["external_assets"] = external_assets
            if external_constraints is not None:
                payload["external_constraints"] = external_constraints
            if external_profile is not None:
                payload["external_profile"] = external_profile
            if external_watchlist is not None:
                payload["external_watchlist"] = external_watchlist
            output_text = json.dumps(payload, ensure_ascii=False, indent=2)
        else:
            output_text = self._format_text(context, report)

        # 6. 输出或保存
        if args.save:
            Path(args.save).write_text(output_text, encoding="utf-8")
            print(f"结果已保存至: {args.save}")
        else:
            print(output_text)


    @staticmethod
    def _valuation_for_asset(asset, valuation_map: dict) -> dict | None:
        """Find position valuation matching an asset by instrument_key or name."""
        if asset.instrument_key:
            for pv in valuation_map.values():
                if pv.get("instrument_key") == asset.instrument_key:
                    return pv
        name = asset.name or ""
        for pv in valuation_map.values():
            if pv.get("display_name") == name or pv.get("position_id", "").endswith(name):
                return pv
        return None

    async def _handle_asset_action(self, args: argparse.Namespace) -> Optional[dict]:
        if args.decision_attribution:
            return {"success": True, "data": self.engine.decision_attribution()}
        if args.decision_attribution_settle is not None:
            settle = self.engine.decision_attribution_settle(
                now=args.now if args.decision_attribution_settle == "now" else args.decision_attribution_settle
            )
            return {"success": True, "data": settle}
        if args.check_event_triggers:
            return await self.engine.check_event_triggers(now=args.now)
        if args.scheduled_run_due:
            return await self.engine.scheduled_run_due(now=args.now, force=args.force)
        if args.scheduled_run_session:
            return await self.engine.scheduled_run_session(
                args.scheduled_run_session,
                now=args.now,
                force=args.force,
            )
        if args.scheduled_run_latest:
            return self.engine.scheduled_run_latest(args.scheduled_run_latest)
        if args.interpret_profile:
            return await self.engine.interpret_profile(confirmed=args.confirmed)
        if args.profile_get:
            return {"success": True, "data": self.engine.get_profile()}
        if args.advice_list:
            return {"success": True, "data": self.engine.list_advice()}
        if args.advice_rollup is not None:
            return {"success": True, "data": self.engine.advice_feedback_rollup(args.advice_rollup)}
        if args.execution_list:
            return {"success": True, "data": self.engine.list_executions()}
        if args.execution_pending:
            run_id = args.execution_pending
            all_records = self.engine.list_executions()
            pending = [
                r for r in all_records
                if r.get("status") == "planned"
                and r.get("decision_id", "").startswith(run_id)
            ]
            return {"success": True, "data": pending, "run_id": run_id}
        if args.forecast_list:
            return {"success": True, "data": self.engine.list_forecasts()}
        if args.decision_attribution:
            return self.engine.decision_attribution()
        if args.decision_attribution_settle:
            settle_arg = args.decision_attribution_settle
            now_val = None if settle_arg == "now" else settle_arg
            return self.engine.decision_attribution_settle(now=now_val)
        if args.assets_list:
            # Use position valuations from build_context so that quantity-based
            # holdings (e.g. IBKR USD positions) show their market value in CNY.
            context = await self.engine.build_context(
                include_news=False,
                include_quotes=True,
                include_history=False,
            )
            valuation_map = {
                pv.get("position_id", ""): pv
                for pv in (context.position_valuations or [])
            }
            data = []
            for asset in self.engine.load_assets():
                asset_dict = asset.to_dict()
                pv = self._valuation_for_asset(asset, valuation_map)
                if pv is not None and pv.get("market_value_cny") is not None:
                    asset_dict["amount_cny"] = pv["market_value_cny"]
                    asset_dict["conversion_status"] = "ok"
                    asset_dict["conversion_source"] = "market_quote"
                    asset_dict["conversion_rate"] = pv.get("fx_rate")
                    asset_dict["price"] = pv.get("price")
                    asset_dict["price_source"] = pv.get("price_source")
                    asset_dict["as_of"] = pv.get("as_of")
                data.append(asset_dict)
            return {"success": True, "data": data}
        if args.asset_migrate_v2:
            return self.engine.migrate_assets_v2(confirmed=args.confirmed)
        if args.asset_intake:
            return self.engine.asset_intake_draft(args.asset_intake)
        if args.asset_intake_confirm:
            if not args.draft_json or not args.token:
                return {
                    "success": False,
                    "error": "--asset-intake-confirm 需要 --draft-json 与 --token",
                }
            try:
                draft = json.loads(args.draft_json)
            except json.JSONDecodeError as exc:
                return {"success": False, "error": f"--draft-json 解析失败: {exc}"}
            return self.engine.asset_intake_apply(draft, args.token)

        write_requested = any(
            (
                args.asset_add,
                args.asset_update,
                args.asset_remove,
                args.profile_update,
                args.advice_save,
                args.advice_feedback,
                args.execution_save,
                args.execution_pending,
                args.forecast_save,
                args.scheduled_run_due,
                args.scheduled_run_session,
                args.scheduled_run_latest,
                args.interpret_profile,
                args.params_json,
            )
        )
        if not write_requested:
            return None
        if not args.confirmed:
            return {
                "success": False,
                "error": "Memory writes require --confirmed",
            }

        try:
            if args.advice_feedback:
                ref, status = args.advice_feedback
                record = self.engine.mark_advice_feedback(ref, status, note=args.note or "")
                return {"success": True, "data": record, "action": "advice_feedback_marked"}
            if args.advice_save:
                advice = self.engine.save_advice(
                    self._parse_json_object(args.advice_save)
                )
                return {"success": True, "data": advice, "action": "advice_saved"}
            if args.execution_save:
                execution = self.engine.save_execution(
                    self._parse_json_object(args.execution_save)
                )
                return {
                    "success": True,
                    "data": execution,
                    "action": "execution_saved",
                }
            if args.forecast_save:
                forecast = self.engine.save_forecast(
                    self._parse_json_object(args.forecast_save)
                )
                return {
                    "success": True,
                    "data": forecast,
                    "action": "forecast_saved",
                }
            if args.profile_update:
                profile = self.engine.update_profile(
                    self._parse_json_object(args.profile_update)
                )
                return {"success": True, "data": profile, "action": "profile_updated"}
            if args.asset_add:
                data = self._parse_json_object(args.asset_add)
                asset = FinancialAsset(**self._storage_asset_fields(data))
                self.engine.add_asset(asset)
                return {"success": True, "data": asset.name, "action": "added"}
            if args.asset_update:
                data = self._parse_json_object(args.asset_update)
                name = str(data.get("name", "")).strip()
                changes = data.get("changes")
                if not name or not isinstance(changes, dict):
                    raise ValueError("asset_update requires name and changes object")
                updated = self.engine.update_asset(
                    name,
                    **self._storage_asset_fields(changes, partial=True),
                )
                return {"success": updated, "data": name, "action": "updated"}
            removed = self.engine.remove_asset(args.asset_remove)
            return {
                "success": removed,
                "data": args.asset_remove,
                "action": "removed",
            }
        except (TypeError, ValueError) as exc:
            response = {"success": False, "error": str(exc)}
            errors = getattr(exc, "errors", None)
            if errors is not None:
                response["errors"] = errors
            return response

    @staticmethod
    def _parse_json_object(value: str) -> dict:
        data = json.loads(value)
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        return data

    @staticmethod
    def _storage_asset_fields(data: dict, partial: bool = False) -> dict:
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
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"Unsupported asset fields: {sorted(unknown)}")
        fields = {key: value for key, value in data.items() if key in allowed}
        if not partial:
            missing = {"name", "platform", "amount"} - set(fields)
            if missing:
                raise ValueError(f"Missing asset fields: {sorted(missing)}")
        return fields

    @staticmethod
    def _load_json_file(path: Optional[str]) -> Optional[dict]:
        """加载 JSON 文件，返回字典或 None。"""
        if not path:
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            print(f"警告: 无法加载文件 {path}: {exc}", file=sys.stderr)
            return None

    @staticmethod
    def _format_text(context, report: Optional[str] = None) -> str:
        """将 AnalysisContext 格式化为人类可读文本。"""
        lines: list[str] = []
        lines.append("=" * 50)
        lines.append("stocks-claw 投资分析上下文")
        lines.append(f"生成时间: {context.generated_at}")
        lines.append(f"Schema 版本: {context.schema_version}")
        lines.append("=" * 50)

        lines.append("")
        lines.append("【资产概况】")
        lines.append(f"  资产数量: {context.asset_count}")
        total = sum(a.valuation_cny or 0.0 for a in context.assets)
        lines.append(f"  资产总值: {total:,.2f}")
        for asset in context.assets:
            value_cny = asset.valuation_cny
            display = f"{value_cny:,.2f} CNY" if value_cny is not None else "换算失败"
            lines.append(f"  - {asset.name} ({asset.platform}): {display}")

        lines.append("")
        lines.append("【组合映射】")
        pm = context.portfolio_mapping
        lines.append(f"  主导层: {', '.join(pm.dominant_layers) or '无'}")
        lines.append(f"  成长暴露: {pm.growth_exposure}")
        lines.append(f"  缓冲强度: {pm.buffer_strength}")
        lines.append(f"  流动性: {pm.liquidity_status}")
        lines.append(f"  锁定资产: {'是' if pm.locked_assets_present else '否'}")
        for bucket, assets in pm.buckets.items():
            ratio = pm.ratios.get(bucket, 0.0)
            lines.append(f"  [{bucket}] 占比 {ratio:.1%} ({len(assets)} 项)")

        lines.append("")
        lines.append("【市场行情】")
        for market, quotes in context.quotes.items():
            lines.append(f"  [{market}] {len(quotes)} 只标的")
            for q in quotes:
                price = f"{q.price:.2f}" if q.price is not None else "N/A"
                change = f"{q.pct_change:+.2f}%" if q.pct_change is not None else "N/A"
                lines.append(f"    {q.instrument.name} ({q.instrument.code}): {price} {change}")

        lines.append("")
        lines.append("【市场状态】")
        ms = context.market_state
        lines.append(f"  风险偏好: {ms.risk_appetite}")
        lines.append(f"  科技板块: {ms.tech_state}")
        lines.append(f"  避险资产: {ms.safe_haven_state}")
        lines.append(f"  中国资产: {ms.china_state}")
        lines.append(f"  利率状态: {ms.rates_state}")
        lines.append(f"  加密资产: {ms.crypto_state}")
        for summary in ms.cross_asset_summary:
            lines.append(f"  - {summary}")

        lines.append("")
        lines.append("【偏离检查】")
        if context.drift_checks:
            for dc in context.drift_checks:
                lines.append(
                    f"  [{dc.bucket}] 当前 {dc.current_ratio:.1%} "
                    f"目标 [{dc.target_min or '-'}%, {dc.target_max or '-'}%] "
                    f"状态: {dc.status} 偏离: {dc.gap:.1%}"
                )
        else:
            lines.append("  无偏离")

        lines.append("")
        lines.append("【新闻】")
        lines.append(f"  新闻数量: {context.news_count}")
        for item in context.news[:5]:
            lines.append(f"  - {item.title} ({item.source_name})")
        if len(context.news) > 5:
            lines.append(f"  ... 还有 {len(context.news) - 5} 条")

        if report:
            lines.append("")
            lines.append("=" * 50)
            lines.append("【LLM 分析报告】")
            lines.append("=" * 50)
            lines.append(report)

        return "\n".join(lines)


def main():
    """CLI 入口 — 自动导入并构造 engine。"""
    # 延迟导入，避免循环依赖
    # 先解析一次参数，提取 openai 配置传给 engine
    import argparse

    from stocks.engine import StocksEngine  # type: ignore[import-not-found]
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--openai-key", default=None)
    pre_parser.add_argument("--openai-base-url", default=None)
    pre_parser.add_argument("--llm-analysis", action="store_true")
    pre_args, _ = pre_parser.parse_known_args()

    engine = StocksEngine(
        llm_analysis_enabled=pre_args.llm_analysis,
        openai_api_key=pre_args.openai_key,
        openai_base_url=pre_args.openai_base_url,
    )
    adapter = CLIAdapter(engine)
    adapter.run()


if __name__ == "__main__":
    main()
