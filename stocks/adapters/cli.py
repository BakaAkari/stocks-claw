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
            "--llm-enhancer", action="store_true",
            help="启用 LLM 数据增强（行情摘要、新闻分级等）"
        )
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

        parsed = parser.parse_args(args)
        asyncio.run(self._execute(parsed))

    async def _execute(self, args: argparse.Namespace):
        """执行命令。"""
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
        total = sum(a.amount for a in context.assets)
        lines.append(f"  资产总值: {total:,.2f}")
        for asset in context.assets:
            lines.append(f"  - {asset.name} ({asset.platform}): {asset.amount:,.2f}")

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
        if context.llm_enhancer_enabled:
            lines.append(f"  LLM 增强: 已启用 ({context.llm_enhancer_model})")
            lines.append(f"  增强新闻数: {context.enhanced_news_count}")
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
    pre_parser.add_argument("--llm-enhancer", action="store_true")
    pre_parser.add_argument("--llm-analysis", action="store_true")
    pre_args, _ = pre_parser.parse_known_args()

    engine = StocksEngine(
        llm_enhancer_enabled=pre_args.llm_enhancer,
        llm_analysis_enabled=pre_args.llm_analysis,
        openai_api_key=pre_args.openai_key,
        openai_base_url=pre_args.openai_base_url,
    )
    adapter = CLIAdapter(engine)
    adapter.run()


if __name__ == "__main__":
    main()
